"""Turn a scanned psychotropic ledger page into rows.

The pipeline for one page:

1. ``grid_detector.detect`` deskews the page and finds the printed column rules.
2. ``form_layout.identify`` names the form from the proportions of those
   columns, because the Thai headings cannot be read (see that module).
3. The deskewed page is OCR'd once, and every box is dropped into the column its
   centre falls in.
4. Rows are laid out from the *anchor* cells only - the date and the four
   quantities - and every other box is then assigned to the row whose band it
   falls in.

Step 4 is what makes the rows come out straight. The obvious approach, grouping
all of a line's boxes by their y, fails on these forms for two reasons: the drug
name is wrapped over two or three lines inside one entry ("Lorazepam" above
"1 mg"), so the name column produces more lines than there are entries; and the
printed Thai header clusters into bands of its own that look like data. Anchors
avoid both. A cell holding a date or a quantity contains digits, occurs exactly
once per entry and is never wrapped, so anchors are in one-to-one correspondence
with the entries - and the printed header has no digits in those columns, so it
contributes no anchors and drops out without needing to be recognised.

No state is carried between pages: each page of these forms repeats its own
header and its entries are self-contained.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

from workers import balance_normalizer as norm
from workers import form_layout
from workers.form_layout import FormLayout, FormNotRecognised
from workers.grid_detector import Grid, GridNotFound, cluster_by_y, detect

log = logging.getLogger(__name__)


@dataclass(slots=True)
class LedgerRow:
    """One handwritten entry, with its cells keyed by ``form_layout`` role."""

    page_number: int
    row_index: int
    form_code: str

    #: role -> cleaned cell text, as read.
    cells: dict[str, str] = field(default_factory=dict)
    #: role -> quantity, for the four balance columns. None is a dash.
    quantities: dict[str, int | None] = field(default_factory=dict)

    entry_date: object | None = None  # datetime.date
    confidence: float | None = None
    needs_review: bool = False
    review_notes: list[str] = field(default_factory=list)
    raw_text: str = ""

    def get(self, role: str, default: str = "") -> str:
        return self.cells.get(role, default)

    def flag(self, note: str) -> None:
        self.needs_review = True
        if note not in self.review_notes:
            self.review_notes.append(note)


@dataclass(slots=True)
class PageParse:
    page_number: int
    form: FormLayout
    rows: list[LedgerRow]
    #: Everything OCR'd above the table. Kept verbatim for traceability: it is
    #: where the licence number and the premises are written, and none of it is
    #: readable without a Thai model.
    header_text: str
    skew_angle: float
    confidence: float | None


def parse_page(image, page_number: int, ocr) -> PageParse:
    """Parse one rendered page.

    ``image`` is a BGR array of the page. ``ocr`` is called with the *deskewed*
    image and must return an object with ``lines`` carrying ``text``, ``bbox``
    and ``confidence`` - ``ocr_engine.recognize_image`` already does.
    """
    straight, grid = detect(image)
    page = ocr(straight)
    form = form_layout.identify(grid.column_widths)

    inside = [box for box in page.lines if _in_table(box, grid)]
    bands = _row_bands(inside, grid, form)

    rows: list[LedgerRow] = []
    for index, band in enumerate(bands):
        cells = _cells(band, grid, form)
        row = _build_row(cells, page_number, index, form, band)
        if row is not None:
            rows.append(row)

    log.info(
        "page parsed",
        extra={
            "step": "balance_parse",
            "page": page_number,
            "form": form.code,
            "rows": len(rows),
        },
    )
    return PageParse(
        page_number=page_number,
        form=form,
        rows=rows,
        header_text=_header_text(page.lines, grid),
        skew_angle=grid.skew_angle,
        confidence=page.confidence,
    )


#: A name cell counts as a drug name only once it has this many alphanumeric
#: characters; below it, a stray mark would keep the "รวม" total line as a row.
_MIN_NAME_CHARS = 3


def _in_table(box, grid: Grid) -> bool:
    center_y = (box.bbox[1] + box.bbox[3]) / 2
    center_x = (box.bbox[0] + box.bbox[2]) / 2
    return grid.inside(center_y) and grid.column_for(center_x) is not None


#: Columns that define where the rows are. See the module docstring.
#:
#: The four quantities only - deliberately not the date. The quantities of one
#: entry are written on a single baseline (they are copied across from the
#: previous line's closing balance), but the writers put the date noticeably
#: higher than them, by most of a line on some pages. Including the date widens
#: the tolerance a row needs until it swallows the next entry.
_ANCHOR_ROLES = form_layout.NUMERIC_ROLES

#: Vertical tolerance for grouping anchors, as a multiple of their median
#: height. Measured on ``ข้อมูล OCR.PDF``: the quantities of one entry span up
#: to 0.65 of a box height, while consecutive entries are at least 1.5 apart.
_ANCHOR_TOLERANCE = 0.8


def _row_bands(boxes, grid: Grid, form: FormLayout) -> list[list]:
    """Split the table's boxes into one list per entry.

    The row boundaries come from the anchor cells; every other box - the drug
    name and its wrapped continuation, the recipient, the remark - is then put
    in the row whose band contains it. Boxes above the first anchor are the
    printed header and are dropped.
    """
    anchors = [box for box in boxes if _is_anchor(box, grid, form)]
    bands = cluster_by_y(anchors, _ANCHOR_TOLERANCE)
    if not bands:
        return []

    centers = [
        sum((box.bbox[1] + box.bbox[3]) / 2 for box in band) / len(band)
        for band in bands
    ]
    # A row owns the space down to halfway to the next row's centre. Above the
    # first centre the band is closed off at half a row's height, so the printed
    # header does not get swept into the first entry.
    spacing = (
        (centers[-1] - centers[0]) / (len(centers) - 1) if len(centers) > 1 else None
    )
    edges = [
        centers[0] - (spacing / 2 if spacing else (centers[0] - grid.top))
    ]
    edges += [(centers[i] + centers[i + 1]) / 2 for i in range(len(centers) - 1)]
    edges.append(grid.bottom)

    rows: list[list] = [[] for _ in centers]
    for box in boxes:
        center = (box.bbox[1] + box.bbox[3]) / 2
        for index in range(len(centers)):
            if edges[index] <= center < edges[index + 1]:
                rows[index].append(box)
                break

    for row in rows:
        row.sort(key=lambda box: box.bbox[0])
    return rows


def _is_anchor(box, grid: Grid, form: FormLayout) -> bool:
    """True for a quantity cell that actually holds a figure.

    The digit test is what keeps the printed header out: its Thai column titles
    sit in these same columns but contain no digits, however badly they are
    recognised.
    """
    role = form.role_at(grid.column_for((box.bbox[0] + box.bbox[2]) / 2))
    return role in _ANCHOR_ROLES and any(char.isdigit() for char in box.text)


def _cells(boxes, grid: Grid, form: FormLayout) -> dict[str, str]:
    """Join the boxes of one band into one string per column role."""
    buckets: dict[str, list] = {}
    for box in boxes:
        center = (box.bbox[0] + box.bbox[2]) / 2
        index = grid.column_for(center)
        if index is None:
            continue
        role = form.role_at(index)
        if role is None:
            continue
        buckets.setdefault(role, []).append(box)

    cells: dict[str, str] = {}
    for role, items in buckets.items():
        items.sort(key=lambda box: box.bbox[0])
        cells[role] = norm.clean_text(" ".join(box.text for box in items))
    return cells


def _build_row(
    cells: dict[str, str], page_number: int, row_index: int, form: FormLayout, boxes
) -> LedgerRow | None:
    """Validate and normalise one band, or return None if it is not a data row.

    The printed header is already gone - it produced no anchors - but the "รวม"
    total line below the entries did, because it carries a closing balance. It
    has neither a date nor a drug name, which is the test used here. Requiring a
    date alone would drop entries whose date the recogniser mangled beyond
    repair, and those are worth keeping for review rather than losing.
    """
    entry_date, date_certain = norm.clean_date(cells.get(form_layout.DATE, ""))
    name = cells.get(form_layout.GENERIC_NAME, "")
    has_name = (
        len([char for char in name if char.isalnum()]) >= _MIN_NAME_CHARS
        and not norm.looks_unreadable(name)
    )
    if entry_date is None and not has_name:
        return None

    row = LedgerRow(
        page_number=page_number,
        row_index=row_index,
        form_code=form.code,
        cells=dict(cells),
        entry_date=entry_date,
        raw_text=" | ".join(f"{role}={value}" for role, value in cells.items()),
    )

    confidences = [box.confidence for box in boxes if box.confidence is not None]
    row.confidence = (
        round(sum(confidences) / len(confidences), 4) if confidences else None
    )

    if form_layout.BATCH_NO in cells:
        batch, certain = norm.clean_batch_no(cells[form_layout.BATCH_NO])
        row.cells[form_layout.BATCH_NO] = batch
        if not certain:
            row.flag("batch number not in the expected form")

    for role in form.columns:
        if role not in form_layout.NUMERIC_ROLES:
            continue
        value, certain = norm.clean_quantity(cells.get(role, ""))
        row.quantities[role] = value
        if not certain:
            row.flag(f"{role} could not be read as a number")

    if entry_date is None:
        row.flag("date could not be read")
    elif not date_certain:
        row.flag("date reconstructed from an unclear cell")

    if not _reads_thai():
        for role in form_layout.THAI_ROLES:
            if cells.get(role):
                row.flag(f"{role} is written in Thai, which this engine cannot read")

    _check_balance(row)
    return row


def _reads_thai() -> bool:
    """Whether the configured OCR engine can read the Thai cells at all."""
    from django.conf import settings

    return settings.OCR["LANGUAGE"] in form_layout.THAI_CAPABLE_LANGUAGES


def _check_balance(row: LedgerRow) -> None:
    """ยอดยกมา + รับ - จ่าย should equal คงเหลือ.

    The forms carry their own arithmetic, which is the one independent check
    available on handwriting: when it holds, the four quantities confirm each
    other, and when it does not, at least one of them was misread.
    """
    brought = row.quantities.get(form_layout.BALANCE_BROUGHT)
    received = row.quantities.get(form_layout.RECEIVED) or 0
    issued = row.quantities.get(form_layout.ISSUED) or 0
    balance = row.quantities.get(form_layout.BALANCE)

    if brought is None or balance is None:
        return
    if brought + received - issued != balance:
        row.flag(
            f"balance does not add up: {brought} + {received} - {issued} != {balance}"
        )


def _header_text(boxes, grid: Grid) -> str:
    """Everything OCR'd above the table, in reading order."""
    above = [box for box in boxes if box.bbox[3] <= grid.top]
    above.sort(key=lambda box: (round(box.bbox[1], -1), box.bbox[0]))
    return "\n".join(box.text for box in above)


__all__ = [
    "FormNotRecognised",
    "GridNotFound",
    "LedgerRow",
    "PageParse",
    "parse_page",
]
