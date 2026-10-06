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

import datetime as dt
import logging
import re
from collections import Counter
from dataclasses import dataclass, field, replace

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
    #: role -> quantity, for the four balance columns. None is a dash, or a
    #: cell that could not be read - ``unread`` is what tells the two apart.
    quantities: dict[str, int | None] = field(default_factory=dict)
    #: Quantity roles whose cell held something that was not a number. A role
    #: that is None and *not* here was a dash, which on these forms means a
    #: genuine zero, and the reconciliation treats the two very differently.
    unread: set[str] = field(default_factory=set)

    entry_date: object | None = None  # datetime.date
    #: True when the date cell read cleanly, with no glyph substituted and no
    #: separator reconstructed. Only these are trusted to say which month the
    #: page covers.
    date_certain: bool = False
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


@dataclass(slots=True)
class _TextLayerPage:
    """What the parser needs of a page, when its text came from the file."""

    lines: list
    #: The file's text is not a reading, so there is no reading to doubt.
    confidence: float | None = 1.0


def parse_page(image, page_number: int, ocr, text_layer=None) -> PageParse:
    """Parse one rendered page.

    ``image`` is a BGR array of the page.

    ``text_layer``, when the file carries one, is the page's own text as boxes
    in the pixels of that render - ``pdf_extractor.text_boxes``. It is used in
    place of OCR, because it is not a reading of the page but the page itself:
    correct, Thai included, down to the character. ``ocr`` is then never called,
    which also skips loading the model.

    Without it, ``ocr`` is called with the *deskewed* image and must return an
    object whose ``lines`` carry ``text``, ``bbox`` and ``confidence`` -
    ``ocr_engine.recognize_image`` does.
    """
    straight, grid = detect(image)

    if text_layer is not None:
        # The rules were measured after the page was straightened; the file's
        # own coordinates were not, so they have to be turned to match.
        boxes = [
            replace(box, bbox=grid.deskew_box(box.bbox)) for box in text_layer
        ]
        page = _TextLayerPage(boxes)
    else:
        page = ocr(straight)

    form = form_layout.identify_by_title(
        [box.text for box in page.lines]
    ) or form_layout.identify(grid.column_widths)

    header_bottom = _printed_header_bottom(page.lines, grid)
    inside = [
        box
        for box in page.lines
        if _in_table(box, grid) and box.bbox[3] > header_bottom
    ]
    bands = _row_bands(inside, grid, form)

    rows: list[LedgerRow] = []
    for index, band in enumerate(bands):
        cells, corrected = _cells(band, grid, form)
        row = _build_row(cells, page_number, index, form, band, corrected)
        if row is not None:
            rows.append(row)

    # The arithmetic links the lines of a page, so it is applied once the whole
    # page is read, not line by line.
    reconcile(rows)

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


#: A drug name is a word, so the cell has to contain an unbroken run of letters
#: this long. Counting alphanumerics instead let "1 MX" - the tail of a wrapped
#: "1 mg" that drifted down into the "รวม" total line - pass as a name and keep
#: the total as an entry. The substances on these ledgers are written in Latin
#: script ("Lorazepam", "Lorazep"), and the recogniser keeps enough of a long
#: word even when it misreads letters: "Loraztpqm", "Lorsztpam".
_NAME_WORD = re.compile(r"[A-Za-z]{4,}")


def _looks_like_a_name(text: str) -> bool:
    return bool(_NAME_WORD.search(text or ""))

#: Quantity cells holding a figure that make a band an entry on their own, when
#: neither the date nor the name could be read. Two, because every entry
#: restates a balance and then changes it, while the total line gives one
#: closing figure and dashes.
_MIN_FIGURES = 2


#: Share of the table's height to look for column titles in. The header of
#: these forms is one or two bands at the top; half the table clears them and
#: still stops well short of the footnotes.
_HEADER_SEARCH_SHARE = 0.5


def _printed_header_bottom(boxes, grid: Grid) -> float:
    """y below which the entries start, read off the printed column titles.

    The grid's own answer is a guess from the horizontal rules, and on a page
    that rules a note in its margin it points at that note instead of the table.
    Where the text can be read the titles say it exactly: the lowest of them is
    the last thing above the first entry.

    Falls back to the grid when nothing recognisable is found, which is every
    handwritten scan - there the titles are Thai and unreadable.
    """
    # Only inside the table, and only in its upper half: "หมายเหตุ" heads a
    # column *and* the footnotes under the page, and the footnotes are lower
    # than every entry.
    limit = grid.top + (grid.bottom - grid.top) * _HEADER_SEARCH_SHARE
    bottoms = [
        box.bbox[3]
        for box in boxes
        if _in_table(box, grid)
        and (box.bbox[1] + box.bbox[3]) / 2 < limit
        and any(
            word in form_layout.normalise(box.text)
            for word in form_layout.HEADER_WORDS
        )
    ]
    return max(bottoms) if bottoms else grid.header_bottom


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
    anchors = [
        box
        for box in boxes
        if _is_anchor(box, grid, form) and box.bbox[3] > grid.header_bottom
    ]
    bands = cluster_by_y(
        anchors,
        _ANCHOR_TOLERANCE,
        column_of=lambda box: grid.column_for((box.bbox[0] + box.bbox[2]) / 2),
    )
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
        if box.bbox[3] <= grid.header_bottom:
            continue  # a printed column title, not anybody's entry
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
    corrected: set[str] = set()
    for role, items in buckets.items():
        cells[role] = norm.clean_text(_join(items))
        if role in form_layout.NUMERIC_ROLES and norm.holds_two_figures(cells[role]):
            corrected.add(role)
    return cells, corrected


#: Vertical tolerance for telling one line of a wrapped cell from the next.
_CELL_LINE_TOLERANCE = 1.0

#: Two fragments on one line closer than this share of their height are one
#: word. Thai is written without spaces and a PDF breaks its text wherever it
#: likes, so "ม.ค.68" arrives as five fragments whose boxes touch or overlap,
#: while "20 มค 68" leaves a small but real gap at each space. The threshold
#: sits between the two, which is tight: a space the file did not record - and
#: these files drop the one in "ต้นตาล คล่องแคล่ว", whose boxes overlap - cannot
#: be recovered here at all.
_WORD_GAP = 0.06


def _join(items) -> str:
    """Put a cell's fragments back together as they were set on the page.

    Two things have to be got right, and getting either wrong makes the cell
    unparseable:

    *Order.* A cell wraps - "Methylphenidate HCl tablets 10 mg" over two lines -
    and its fragments have to be read line by line. Sorting them all by x alone
    interleaves the lines into "tablets Methylphenidate HCl 10 mg".

    *Spacing.* Joining every fragment with a space turns "3 ม.ค.68" into
    "3 ม . ค .68" and "ด.ญ.พิมพ์" into "ด . ญ . พิมพ์"; joining with nothing
    runs separate words together. The gap between two fragments says which was
    meant, and the gap is in the boxes.
    """
    # A generous tolerance: everything here is inside one cell, so the only
    # thing to separate is a genuine wrap, and the lines of a wrap are a whole
    # row-height apart. The default would split "ม" from "." on a page that
    # still carries a fraction of a degree of tilt.
    lines = cluster_by_y(items, _CELL_LINE_TOLERANCE)
    if not lines:
        return ""
    heights = sorted(box.bbox[3] - box.bbox[1] for box in items)
    limit = (heights[len(heights) // 2] or 1.0) * _WORD_GAP

    out: list[str] = []
    for line in lines:
        if out:
            out.append(" ")  # a wrap is a word break
        out.append(line[0].text)
        for previous, box in zip(line, line[1:]):
            gap = box.bbox[0] - previous.bbox[2]
            out.append("" if gap <= limit else " ")
            out.append(box.text)
    return "".join(out)


def _build_row(
    cells: dict[str, str],
    page_number: int,
    row_index: int,
    form: FormLayout,
    boxes,
    corrected: set[str] = frozenset(),
) -> LedgerRow | None:
    """Validate and normalise one band, or return None if it is not a data row.

    What has to be rejected is the "รวม" total line: it is below the entries,
    it produced anchors because it carries a closing balance, and it is not an
    entry. What must *not* be rejected is an entry the recogniser read badly.

    So the test is on the ledger arithmetic, not on the identifying text. An
    entry carries at least two figures across ยอดยกมา / รับ / จ่าย / คงเหลือ,
    because each line restates the previous balance and then changes it. The
    total line carries one, the closing balance, with dashes beside it. Judging
    instead by whether the date or the drug name could be read threw away every
    entry whose date was mangled and whose name wrapped out of its band - five
    of the eleven rows on page 1 of the sample, each with its quantities intact.
    """
    entry_date, date_certain = norm.clean_date(cells.get(form_layout.DATE, ""))
    has_name = _looks_like_a_name(cells.get(form_layout.GENERIC_NAME, ""))
    figures = sum(
        1
        for role in form_layout.NUMERIC_ROLES
        if any(char.isdigit() for char in cells.get(role, ""))
    )
    if entry_date is None and not has_name and figures < _MIN_FIGURES:
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
        if role in corrected:
            row.quantities[role] = None
            row.unread.add(role)
            row.flag(
                f"{role} holds two figures - the line was corrected by hand and "
                f"which one is current cannot be told from the scan"
            )
            continue

        value, certain = norm.clean_quantity(cells.get(role, ""))
        row.quantities[role] = value
        if not certain:
            # None with certain=True is a dash, which means a real zero; None
            # with certain=False is a cell nobody could read. Only the second
            # is a gap the reconciliation may fill.
            if value is None:
                row.unread.add(role)
            row.flag(f"{role} could not be read as a number")

    row.date_certain = entry_date is not None and date_certain
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


def reconcile(rows: list[LedgerRow]) -> None:
    """Fill the gaps the recogniser left, using the ledger's own arithmetic.

    A page of these ledgers is one running account, and that gives two
    independent relations the extraction can lean on:

    * within a line, ``ยอดยกมา + รับ - จ่าย = คงเหลือ``;
    * between lines, this line's ยอดยกมา is the previous line's คงเหลือ.

    Both are used only to fill a cell that could not be read at all. A value the
    recogniser did produce is never replaced, however unlikely it looks: a wrong
    figure that the caller can see disagreeing with the arithmetic is more
    useful than a plausible one this code invented, and the check below reports
    exactly that disagreement.

    A filled cell is always flagged, so nothing derived is mistaken for
    something read off the page.
    """
    carried: int | None = None
    for row in rows:
        _carry_forward(row, carried)
        _derive_missing(row)
        _check_balance(row)
        balance = row.quantities.get(form_layout.BALANCE)
        carried = balance if balance is not None else None


#: Thin strokes are what the recogniser loses first, and the slashes of a date
#: are the thinnest marks on these forms. It reports them as nothing, or as a
#: "1" or a "7", so "16/3/69" comes back as "16369", "1631 69" or "163769" and
#: splitting the run of digits on its own is guesswork.
#:
#: These are monthly returns, which removes the guesswork: every entry on a page
#: falls in the month the page is for, and the entries run down the page in
#: date order. So only the day has to be recovered, and it has to fit between
#: the day above it and 31 - which usually leaves exactly one possibility.
def settle_dates(rows: list[LedgerRow]) -> None:
    """Drop dates from the wrong month, and recover days that did not parse.

    Run over the whole document, not one page at a time. A page can easily
    contain no cleanly read date at all - page 4 of the sample contains none -
    and then nothing on that page says which month it is. Across the document
    there is nearly always one, and these submissions are a set of returns for
    the same month, so one clean reading settles every page.

    The ordering is still per page: each page restarts at the beginning of the
    month.
    """
    settled = [row.entry_date for row in rows if row.date_certain]
    if not settled:
        return
    month_year = Counter((d.month, d.year) for d in settled).most_common(1)[0][0]
    month, year = month_year

    for row in rows:
        date = row.entry_date
        if date is not None and (date.month, date.year) != month_year:
            row.flag(
                f"date read as {date.isoformat()}, which is not in the month "
                f"these returns cover ({year}-{month:02d}); discarded"
            )
            row.entry_date = None
            row.date_certain = False

    for page in sorted({row.page_number for row in rows}):
        previous_day = 0
        for row in [r for r in rows if r.page_number == page]:
            if row.entry_date is not None:
                previous_day = row.entry_date.day
                continue
            day = _only_possible_day(row.get(form_layout.DATE), previous_day, month)
            if day is None:
                continue
            row.entry_date = dt.date(year, month, day)
            row.flag(
                "day recovered from the cell's digits; month and year taken "
                "from the other returns in this document"
            )
            previous_day = day


def _only_possible_day(text: str, previous_day: int, month: int) -> int | None:
    """The day a cell can mean, when it can only mean one.

    The day is written first, so it is the leading one or two digits. A reading
    has to be a real day and not fall earlier than the entry above it.

    When both the one-digit and the two-digit reading survive that, the month
    decides: it is written straight after the day, so for the right reading the
    month's digit turns up at once in what is left - allowing for one stray
    digit, because that is what a slash comes back as. In "287376" the 28 leaves
    "7376" and the 3 is right there; the 2 leaves "87376" and it is not.

    If that still leaves both, the cell stays empty. "3169" is the 3rd or the
    31st and nothing here chooses between them; an invented date would be worse
    than none, because it is the field a reader is least able to check.
    """
    digits = re.match(r"\d+", re.sub(r"\D", "", norm.normalise_digits(text)))
    if digits is None:
        return None
    run = digits.group()

    candidates = {
        int(run[:width])
        for width in (1, 2)
        if len(run) >= width and previous_day <= int(run[:width]) <= 31
    }
    if len(candidates) > 1:
        candidates = {
            day
            for day in candidates
            if str(month) in run[len(str(day)) : len(str(day)) + 2]
        }
    return candidates.pop() if len(candidates) == 1 else None


def _carry_forward(row: LedgerRow, carried: int | None) -> None:
    if carried is None or form_layout.BALANCE_BROUGHT not in row.unread:
        return
    row.quantities[form_layout.BALANCE_BROUGHT] = carried
    row.unread.discard(form_layout.BALANCE_BROUGHT)
    row.flag(
        "balance_brought taken from the previous line's closing balance, "
        "because the cell could not be read"
    )


def _derive_missing(row: LedgerRow) -> None:
    """Recover the one unreadable figure of a line from the other three."""
    if len(row.unread) != 1:
        return
    missing = next(iter(row.unread))

    quantities = row.quantities
    brought = quantities.get(form_layout.BALANCE_BROUGHT)
    received = quantities.get(form_layout.RECEIVED) or 0
    issued = quantities.get(form_layout.ISSUED) or 0
    balance = quantities.get(form_layout.BALANCE)

    if missing == form_layout.BALANCE and brought is not None:
        value = brought + received - issued
    elif missing == form_layout.BALANCE_BROUGHT and balance is not None:
        value = balance - received + issued
    elif missing == form_layout.ISSUED and None not in (brought, balance):
        value = brought + received - balance
    elif missing == form_layout.RECEIVED and None not in (brought, balance):
        value = balance - brought + issued
    else:
        return

    if value < 0:  # the other three disagree; do not invent a negative stock
        return

    quantities[missing] = value
    row.unread.discard(missing)
    row.flag(f"{missing} derived from the other three figures on the line")


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
