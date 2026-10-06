"""Ruled-table detection for scanned forms.

The psychotropic report forms (ร.ว.จ ๗/๔, บ.ว.จ ๗/๔-ขพ and ร.ค.-๔) are printed
grids filled in by hand. The printed rules are the only reliable structure on
the page: the handwriting wanders across cell boundaries, so grouping OCR boxes
into columns by their position relative to each other puts a long name in the
wrong column as soon as the writer overshoots a rule. Detecting the rules
themselves gives boundaries that do not move with the handwriting.

Two properties of these forms shape the approach:

* The **vertical** rules are complete and run the full height of the table, so
  they give exact column boundaries.
* The **horizontal** rules are not. Both forms rule the header band and the
  bottom of the box, but leave the data area open - the writer puts one entry
  per line with nothing drawn between them. Rows therefore cannot come from the
  grid; ``balance_parser`` clusters them from the OCR boxes instead.

The scans are also slightly rotated, which destroys long-run morphology: a 1.5°
tilt smears a 2000 px rule over 50 px of scanlines and it no longer survives the
opening. Pages are deskewed first.

The page is OCR'd once as a whole and the boxes are distributed over the
columns; cropping and OCR-ing each cell separately would be ~150 engine calls
per page for no gain, because PaddleOCR's detector already finds the
handwritten lines.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

try:
    import cv2
    import numpy as np
except ImportError:  # pragma: no cover - the API image does not need OpenCV
    cv2 = None
    np = None

log = logging.getLogger(__name__)


class GridNotFound(Exception):
    """Raised when no usable table grid could be detected on the page."""

    error_code = "TABLE_PARSE_FAILED"


@dataclass(slots=True)
class Grid:
    """Column boundaries and table extent of one detected table, in px."""

    #: x of every vertical rule, left to right. One more than the column count.
    x_rules: list[float] = field(default_factory=list)
    #: y of every horizontal rule found, top to bottom. Sparse and unreliable by
    #: design - see the module docstring - so only its extremes are used.
    y_rules: list[float] = field(default_factory=list)
    #: Rotation removed from the page, in degrees. Needed to map OCR boxes taken
    #: from the deskewed image back to the original, and vice versa.
    skew_angle: float = 0.0

    @property
    def columns(self) -> int:
        return max(len(self.x_rules) - 1, 0)

    def column_for(self, x: float) -> int | None:
        """Index of the column containing ``x``, or None outside the table."""
        rules = self.x_rules
        if len(rules) < 2 or x < rules[0] or x >= rules[-1]:
            return None
        for index in range(len(rules) - 1):
            if rules[index] <= x < rules[index + 1]:
                return index
        return None

    def column_bounds(self, index: int) -> tuple[float, float]:
        return self.x_rules[index], self.x_rules[index + 1]

    @property
    def top(self) -> float:
        return self.y_rules[0]

    @property
    def bottom(self) -> float:
        return self.y_rules[-1]

    @property
    def header_bottom(self) -> float:
        """y below which the entries start; the column titles are above it.

        Taken as the lowest rule still in the top third of the table. The
        horizontal rules cannot be trusted to be complete - see the module
        docstring - so this is used only to discard what is *entirely* above it,
        never to decide where a row begins. On the forms here the header is one
        or two ruled bands, and both land in that third.

        Without it the printed column titles become an entry: they sit in the
        quantity columns, and read by a Latin model the Thai reads as plausible
        letters, so nothing about the text itself gives them away.
        """
        if len(self.y_rules) < 3:
            return self.top
        limit = self.top + (self.bottom - self.top) * _HEADER_BAND_SHARE
        below = [value for value in self.y_rules[1:-1] if value <= limit]
        return below[-1] if below else self.top

    def inside(self, y: float) -> bool:
        """True when ``y`` falls between the table's top and bottom rules.

        The split between the header band and the data is *not* decided here:
        the horizontal rules are too unreliable on these scans to locate it, so
        ``balance_parser`` finds the first data row by its content instead.
        """
        return self.top <= y < self.bottom

    @property
    def column_widths(self) -> list[float]:
        return [
            self.x_rules[i + 1] - self.x_rules[i] for i in range(self.columns)
        ]


#: Fraction of the table's height within which the header's ruled band(s) sit.
#: The forms here use one or two bands; a third of the table clears both and
#: still stays well above the first entry.
_HEADER_BAND_SHARE = 0.35


def _require_cv2():
    if cv2 is None:  # pragma: no cover - deployment issue
        raise GridNotFound("OpenCV is not installed")
    return cv2


# --- deskew ----------------------------------------------------------------

#: Scans are near-upright; anything beyond this is a mis-estimate, not tilt.
_MAX_SKEW_DEGREES = 5.0


def deskew(image) -> tuple["np.ndarray", float]:
    """Rotate the page so its printed rules are axis-aligned.

    The angle is taken from the long straight edges found by a probabilistic
    Hough transform, using the median of the near-horizontal ones. The median
    rejects the handwriting strokes and the page border that also produce
    segments.
    """
    _require_cv2()
    gray = _to_gray(image)
    edges = cv2.Canny(gray, 50, 150, apertureSize=3)
    min_length = max(gray.shape[1] // 6, 100)
    segments = cv2.HoughLinesP(
        edges,
        rho=1,
        theta=np.pi / 1800,  # 0.1 deg: a 0.5 deg error still breaks a long rule
        threshold=120,
        minLineLength=min_length,
        maxLineGap=12,
    )
    if segments is None:
        return image, 0.0

    angles = []
    for x0, y0, x1, y1 in segments[:, 0]:
        angle = np.degrees(np.arctan2(float(y1 - y0), float(x1 - x0)))
        if abs(angle) <= _MAX_SKEW_DEGREES:
            angles.append(angle)
    if not angles:
        return image, 0.0

    angle = float(np.median(angles))
    if abs(angle) < 0.05:  # already straight; skip the resampling blur
        return image, 0.0

    height, width = gray.shape[:2]
    matrix = cv2.getRotationMatrix2D((width / 2, height / 2), angle, 1.0)
    rotated = cv2.warpAffine(
        image,
        matrix,
        (width, height),
        flags=cv2.INTER_CUBIC,
        borderMode=cv2.BORDER_REPLICATE,
    )
    return rotated, angle


# --- grid ------------------------------------------------------------------


def detect(image, *, min_columns: int = 6) -> tuple["np.ndarray", Grid]:
    """Find the table on ``image`` and return the deskewed page with its grid.

    Only the column count is checked: the data area of these forms carries no
    horizontal rules, so a row requirement would reject every valid page.
    """
    _require_cv2()
    straight, angle = deskew(image)
    gray = _to_gray(straight)
    binary = _binarize(gray)

    height, width = binary.shape[:2]
    vertical = _rules(binary, axis="v", length=max(height // 30, 20))
    horizontal = _rules(binary, axis="h", length=max(width // 30, 20))

    x_rules = _rule_positions(vertical, axis="v", span=height, coverage=0.12)
    x_rules = _drop_page_border(x_rules, extent=width)

    if len(x_rules) - 1 < min_columns:
        raise GridNotFound(
            f"no table grid: found {max(len(x_rules) - 1, 0)} columns (need {min_columns})"
        )

    # Only the outermost horizontal rules are used, for the table's top and
    # bottom. The ones in between cannot be trusted to mean anything: a residual
    # fraction of a degree of skew smears a 2000 px rule over several scanlines
    # and it drops below any coverage threshold, while the ties inside a merged
    # header cell ("ขายให้แก่" over two sub-columns on บ.ว.จ ๗/๔-ขพ) pass one.
    y_rules = _rule_positions(horizontal, axis="h", span=width, coverage=0.30)
    y_rules = _drop_page_border(y_rules, extent=height)

    if len(y_rules) < 2:
        raise GridNotFound("no table grid: the table box has no horizontal rules")

    grid = Grid(x_rules=x_rules, y_rules=y_rules, skew_angle=angle)
    log.debug(
        "grid detected",
        extra={
            "step": "grid_detect",
            "columns": grid.columns,
            "skew": round(angle, 2),
            "extent": (round(grid.top), round(grid.bottom)),
        },
    )
    return straight, grid


def _to_gray(image):
    if image.ndim == 3:
        return cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    return image


def _binarize(gray):
    """Ink -> 255, paper -> 0.

    Adaptive thresholding rather than Otsu: these are flatbed scans with uneven
    lighting, and a single global threshold loses the rules on the darker half
    of the page.
    """
    return cv2.adaptiveThreshold(
        gray, 255, cv2.ADAPTIVE_THRESH_MEAN_C, cv2.THRESH_BINARY_INV, 25, 15
    )


def _rules(binary, *, axis: str, length: int):
    """Keep only runs of ink at least ``length`` px long on the given axis."""
    shape = (length, 1) if axis == "h" else (1, length)
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, shape)
    opened = cv2.morphologyEx(binary, cv2.MORPH_OPEN, kernel, iterations=1)
    # Close small gaps where the handwriting or a fold broke the printed rule.
    return cv2.dilate(opened, kernel, iterations=1)


#: Two detected rules closer than this (px at 200 dpi) are the two edges of one
#: drawn line, not two columns.
_MERGE_TOLERANCE = 12.0


def _rule_positions(mask, *, axis: str, span: int, coverage: float) -> list[float]:
    """Collapse a rule mask into one coordinate per printed line.

    ``coverage`` is the fraction of the perpendicular extent a rule must cover.
    It differs per axis: a vertical rule only spans the table's height (a
    fraction of the page), while a horizontal rule spans the table's full width.
    """
    # Project onto the perpendicular axis: each entry is the ink-pixel count on
    # that scanline.
    profile = mask.sum(axis=1 if axis == "h" else 0) / 255.0
    threshold = max(span * coverage, 10.0)

    positions = [float(i) for i, value in enumerate(profile) if value >= threshold]
    return _merge_adjacent(positions)


def _merge_adjacent(positions: list[float]) -> list[float]:
    """Average runs of consecutive coordinates into one position per rule."""
    if not positions:
        return []
    merged: list[float] = []
    run = [positions[0]]
    for value in positions[1:]:
        if value - run[-1] <= _MERGE_TOLERANCE:
            run.append(value)
        else:
            merged.append(sum(run) / len(run))
            run = [value]
    merged.append(sum(run) / len(run))
    return merged


#: A rule within this fraction of the page edge is the scan border, not a cell
#: boundary. Keeping it would add a column of margin noise at each side.
_BORDER_MARGIN = 0.02


def _drop_page_border(rules: list[float], *, extent: int) -> list[float]:
    margin = extent * _BORDER_MARGIN
    return [value for value in rules if margin <= value <= extent - margin]




# --- rows ------------------------------------------------------------------

#: Two boxes whose vertical centres are closer than this multiple of the median
#: box height belong to the same written line.
_ROW_TOLERANCE = 0.6

#: Two boxes in the same column are the same written value - one numeral the
#: recogniser boxed in pieces - only if their centres are this close, again as a
#: multiple of the median height. Further apart, they are two entries.
_SAME_VALUE_TOLERANCE = 0.35


def cluster_by_y(
    boxes, tolerance_ratio: float = _ROW_TOLERANCE, column_of=None
) -> list[list]:
    """Group boxes that sit on the same written line, top to bottom.

    ``boxes`` is any sequence of objects with a ``bbox`` of (x0, y0, x1, y1).
    The tolerance is derived from the median box height so it adapts to the
    writer's hand and to the render dpi instead of assuming a pixel gap.

    The caller decides *which* boxes to cluster. ``balance_parser`` passes only
    the quantities: the data area carries no horizontal rules, so a row is
    defined by the compact cells that occur once per entry, never by the
    drug-name cell, which the writer wraps over two or three lines.

    ``column_of`` maps a box to the column it sits in. Given one, a group may
    hold at most one box per column, which is what stops a run of tightly
    written entries from chaining into a single group: the tolerance is measured
    against the group's *running* centre, so each new box drags the centre down
    and the next box still falls within reach, and three entries 60 px apart
    merge under a 45 px tolerance. A repeated column cannot happen inside one
    entry - a ledger line has one ยอดยกมา, one รับ, one จ่าย, one คงเหลือ - so
    seeing one is proof the group has run past the end of its row.
    """
    ordered = sorted(boxes, key=_center_y)
    if not ordered:
        return []

    heights = sorted(box.bbox[3] - box.bbox[1] for box in ordered)
    median_height = heights[len(heights) // 2] or 1.0
    tolerance = median_height * tolerance_ratio
    same_value = median_height * _SAME_VALUE_TOLERANCE

    groups: list[list] = [[ordered[0]]]
    for box in ordered[1:]:
        last = groups[-1]
        reference = sum(_center_y(item) for item in last) / len(last)
        near = _center_y(box) - reference <= tolerance
        if near and not _occupied(last, box, column_of, same_value):
            last.append(box)
        else:
            groups.append([box])

    for group in groups:
        group.sort(key=lambda box: box.bbox[0])
    return groups


def _occupied(group, box, column_of, same_value: float) -> bool:
    """True when ``box``'s column already holds a different value in ``group``.

    A number the recogniser split into two boxes side by side ("45" and "0" for
    450) lands in one column twice at the same height; that is one value, not a
    clash, so only a box at a genuinely different height counts.
    """
    if column_of is None:
        return False
    column = column_of(box)
    if column is None:
        return False
    center = _center_y(box)
    return any(
        column_of(other) == column and abs(_center_y(other) - center) > same_value
        for other in group
    )


def _center_y(box) -> float:
    return (box.bbox[1] + box.bbox[3]) / 2
