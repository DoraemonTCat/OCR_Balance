"""Read a ruled, typed table one cell at a time.

The pharmacy ledgers (``form_layout.BVJ_SALE``) are typed into a table that
rules a line under every entry. On such a page the rules already say where every
cell is, so there is nothing left for a text *detector* to find: each cell is
cut out along its rules and only *recognised*. That is both faster - a few
hundred small crops through the recognition model instead of a detector over a
3000 px page - and more accurate, because nothing a detector does wrong can
happen. It merges two words into one box and drops the space; it misses the
light header line with the drug's name on it; it boxes a dotted leader together
with the value typed over it.

What the detector did is done here from the ink itself:

* **Lines.** A cell is split into text lines where a horizontal band of it is
  blank. Thai stacks marks above and below the letters, and a tone mark can be
  separated from its line by a blank scanline or two, so a band too short to be
  a line of its own is merged into the nearest one.
* **Words.** A line is split where the gap between inked columns is wide - a
  typed space - and each piece is recognised separately. The recogniser drops
  spaces that the page plainly shows ("นาย ไหม อ้วนกลม" read as "นาย ไหมอ้วนกลม"),
  so the spacing is taken from the page, not from it.
* **Pen marks.** The ticks and circles reviewers add in blue are not the
  document's text; pixels that are clearly blue are not ink.
* **Leaders.** In the header, the dotted runs printed to be typed over are
  erased before the line is split, so the value comes out on its own.

The result is the same ``TextLine`` boxes the page recogniser returns, in page
coordinates, so ``balance_parser`` places them in columns and rows exactly as it
does any other box.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Callable, Sequence

try:
    import cv2
    import numpy as np
except ImportError:  # pragma: no cover - the API image does not need OpenCV
    cv2 = None
    np = None

from workers.grid_detector import Grid
from workers.pdf_extractor import TextLine

log = logging.getLogger(__name__)

#: Recognises a batch of crops: BGR arrays in, (text, confidence) pairs out.
Recognizer = Callable[[Sequence["np.ndarray"]], list[tuple[str, float]]]


@dataclass(slots=True)
class CellPage:
    text: str
    lines: list[TextLine]
    confidence: float | None


#: A page whose table rules at least this many lines is ruled row by row. The
#: handwritten forms rule only their header and the box around the data.
MIN_RULED_ROWS = 8


def is_ruled(grid: Grid) -> bool:
    """True when the table has a rule under every entry."""
    return len(grid.y_rules) >= MIN_RULED_ROWS


# --- tuning, as fractions of a text line's height --------------------------

#: Pixels kept clear of each column rule when a cell is cut out.
_INSET = 5
#: How far above its top rule a cell is cut. The typed rows sit so tight under
#: their rule that the vowels and tone marks above the letters (ิ ี ่ ้ ำ) touch
#: it or poke through it; cut below the rule and they are lost, and with them
#: the difference between ศิริรำ and ศรีรา.
_ABOVE_RULE = 7
#: A gap along a line at least this wide is a space between words. Inside a
#: typed Thai word the glyphs sit 1-4 px apart at 200 dpi; a space is 9 or more
#: on a line about 30 px high.
_WORD_GAP = 0.22
#: Ink blobs smaller than this many pixels are scanner dust.
_SPECK = 6
#: A blank band of this many scanlines separates two lines of text.
_LINE_GAP = 2
#: A line band shorter than this share of the tallest one is a row of marks
#: belonging to a neighbouring line, not a line of its own.
_MARK_BAND = 0.45
#: Padding put back around a word before it is recognised.
_PAD = 4


def recognize(
    image,
    grid: Grid,
    recognizer: Recognizer,
    header_reader=None,
    line_reader=None,
    line_columns: Sequence[int] = (),
) -> CellPage:
    """Recognise every inked cell of a ruled table, and the page header.

    ``header_reader``, when given, reads the header - everything above the table
    - as a block of free text: it is called with that part of the page, its
    dotted leaders erased, and returns boxes like ``ocr_engine.recognize_image``.
    The header is not ruled, its lines are long and packed close, and a text
    detector separates them better than blank scanlines do.

    ``line_reader``, when given, reads the cells of ``line_columns`` a whole
    line at a time instead of word by word: it takes a batch of line images and
    returns one (text, confidence) pair each. The buyer's name goes this way, through
    ``thai_names.read``, which needs the line whole to compare its readings.
    """
    _require_cv2()
    ink, clean = _ink(image)
    height, width = ink.shape
    header_lines: list[TextLine] = []
    #: (group, box, ink) - a group is one line of one cell.
    pieces: list[tuple[tuple, tuple[int, int, int, int], "np.ndarray"]] = []
    #: (group, box) of the lines handed to ``line_reader``.
    whole_lines: list[tuple[tuple, tuple[int, int, int, int]]] = []

    # The header: everything above the table, as a block of free text.
    top = int(grid.top) - _INSET
    if top > 0 and header_reader is not None:
        leaders = ink[:top, :] - _erase_leaders(ink[:top, :])
        block = image[:top].copy()
        block[leaders > 0] = 255
        if block.ndim == 3:
            # A reviewer's ticks and notes are not part of the form.
            block[_pen(block)] = 255
        header_lines = list(header_reader(block).lines)

    # The table, cell by cell along its rules.
    rows = list(zip(grid.y_rules, grid.y_rules[1:]))
    cols = list(zip(grid.x_rules, grid.x_rules[1:]))
    for row_index, (top_rule, bottom_rule) in enumerate(rows):
        for col_index, (left_rule, right_rule) in enumerate(cols):
            whole = line_reader is not None and col_index in line_columns
            if whole:
                # A name keeps the marks that poke up through its rule.
                y_from = max(int(top_rule) - _ABOVE_RULE, 0)
                y_to = int(bottom_rule) - 2
            else:
                # Figures, dates and IDs: clear of the rules, which would
                # otherwise come back as "--" from the recogniser.
                y_from, y_to = int(top_rule) + _INSET, int(bottom_rule) - _INSET
            x_from, x_to = int(left_rule) + _INSET, int(right_rule) - _INSET
            if x_to - x_from < 6 or y_to - y_from < 6:
                continue
            cell = ink[y_from:y_to, x_from:x_to]
            # Text of its own, below its rule: what the strip above the rule
            # holds alone is the tail of a letter from the row above.
            below = max(int(top_rule) - y_from + 3, 0)
            if cell[below:].sum() < 255 * 20:
                continue
            for y0, y1 in _line_bands(cell):
                if whole:
                    spans = _word_spans(cell[y0:y1, :], y1 - y0)
                    if spans:
                        box = (
                            x_from + spans[0][0], y_from + y0,
                            x_from + spans[-1][1], y_from + y1,
                        )
                        whole_lines.append((("c", row_index, col_index, y0), box))
                    continue
                for x0, x1 in _word_spans(cell[y0:y1, :], y1 - y0):
                    box = (x_from + x0, y_from + y0, x_from + x1, y_from + y1)
                    group = ("c", row_index, col_index, y0)
                    pieces.append((group, box, cell[y0:y1, x0:x1]))

    crops = [_as_crop(clean, box) for _, box, _ in pieces]
    readings = recognizer(crops) if crops else []

    groups: dict[tuple, list[TextLine]] = {}
    for (group, box, _), (text, confidence) in zip(pieces, readings):
        text = (text or "").strip()
        if text:
            groups.setdefault(group, []).append(
                TextLine(text=text, bbox=tuple(float(v) for v in box), confidence=confidence)
            )

    if whole_lines:
        readings = line_reader([_as_crop(clean, box) for _, box in whole_lines])
        for (group, box), (text, confidence) in zip(whole_lines, readings):
            if text:
                groups[group] = [
                    TextLine(
                        text=text, bbox=tuple(float(v) for v in box), confidence=confidence
                    )
                ]

    lines = _merge_words(groups) + header_lines
    lines.sort(key=lambda line: (round(line.bbox[1], 1), line.bbox[0]))
    for index, line in enumerate(lines):
        line.order = index
    confidences = [line.confidence for line in lines if line.confidence is not None]
    log.debug(
        "cells recognised",
        extra={"step": "cell_ocr", "crops": len(crops), "lines": len(lines)},
    )
    return CellPage(
        text="\n".join(line.text for line in lines),
        lines=lines,
        confidence=round(sum(confidences) / len(confidences), 4) if confidences else None,
    )


def _require_cv2():
    if cv2 is None:  # pragma: no cover
        raise RuntimeError("OpenCV is required for cell recognition")


def _ink(image):
    """Printed ink as 255 on 0, with pen marks and the table's rules removed.

    Also returns the page in grey with the same pen marks and rules painted
    out, which is what the words are cut from for recognition: the recogniser
    was trained on scans, and reads the grey edges of a glyph better than a
    hard black-and-white copy of it.
    """
    coloured = image.ndim == 3
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) if coloured else image
    binary = cv2.adaptiveThreshold(
        gray, 255, cv2.ADAPTIVE_THRESH_MEAN_C, cv2.THRESH_BINARY_INV, 25, 15
    )
    if coloured:
        # Pen, in whatever colour - blue ticks, an orange highlighter dot: the
        # printed text is black or grey, balanced across the channels.
        pen = _pen(image)
        binary[pen] = 0
    clean = gray.copy()
    if coloured:
        clean[pen] = 255
    height, width = binary.shape
    for shape in ((max(width // 40, 25), 1), (1, max(height // 40, 25))):
        kernel = cv2.getStructuringElement(cv2.MORPH_RECT, shape)
        rules = cv2.morphologyEx(binary, cv2.MORPH_OPEN, kernel)
        # Only the rule's own pixels go, not a margin around it: a mark that
        # touches the rule keeps everything but the overlap.
        binary = cv2.subtract(binary, rules)
        # In the grey copy the rule's soft edge goes too, across the rule only.
        across = (3, 1) if shape[0] == 1 else (1, 3)
        clean[cv2.dilate(rules, np.ones(across, np.uint8)) > 0] = 255
    return binary, clean


#: How far apart a pixel's colour channels have to be for it to be pen rather
#: than print. Scanned black and grey stay within about 25 of each other.
_PEN_SATURATION = 50
#: ... and how bright its brightest channel has to be. Where a pen stroke
#: crosses printed text the pixel is dark with a colour cast; it is the print
#: showing through, and blanking it cuts the letter ("Alprazolam" circled in
#: blue read as "4lprazolam").
_PEN_BRIGHTNESS = 120


def _pen(image):
    """Pixels coloured enough to be a pen or highlighter, not printed ink."""
    channels = image.astype(np.int16)
    brightest = channels.max(axis=2)
    return ((brightest - channels.min(axis=2)) > _PEN_SATURATION) & (
        brightest > _PEN_BRIGHTNESS
    )


def _line_bands(mask) -> list[tuple[int, int]]:
    """Vertical extents of the text lines in ``mask``."""
    profile = (mask > 0).sum(axis=1)
    inked = profile > 0
    bands: list[list[int]] = []
    start = None
    blank = 0
    for y, on in enumerate(inked):
        if on:
            if start is None:
                start = y
            blank = 0
            end = y + 1
        elif start is not None:
            blank += 1
            if blank > _LINE_GAP:
                bands.append([start, end])
                start = None
    if start is not None:
        bands.append([start, end])
    bands = [band for band in bands if (mask[band[0] : band[1]] > 0).sum() >= _SPECK]
    if not bands:
        return []

    # Fold rows of marks into the line they belong to.
    tallest = max(b - a for a, b in bands)
    merged = True
    while merged and len(bands) > 1:
        merged = False
        for index, (a, b) in enumerate(bands):
            if b - a >= tallest * _MARK_BAND:
                continue
            neighbours = []
            if index > 0:
                neighbours.append((a - bands[index - 1][1], index - 1))
            if index + 1 < len(bands):
                neighbours.append((bands[index + 1][0] - b, index + 1))
            _, other = min(neighbours)
            lo, hi = sorted((index, other))
            bands[lo] = [min(bands[lo][0], bands[hi][0]), max(bands[lo][1], bands[hi][1])]
            del bands[hi]
            merged = True
            break
    return [(a, b) for a, b in bands]


def _word_spans(line, line_height: int) -> list[tuple[int, int]]:
    """Horizontal extents of the words on one text line."""
    columns = (line > 0).sum(axis=0) > 0
    gap_limit = max(int(line_height * _WORD_GAP), 4)
    spans: list[list[int]] = []
    start = None
    blank = 0
    for x, on in enumerate(columns):
        if on:
            if start is None:
                start = x
            elif blank >= gap_limit:
                spans.append([start, end])
                start = x
            blank = 0
            end = x + 1
        elif start is not None:
            blank += 1
    if start is not None:
        spans.append([start, end])
    return [
        (a, b) for a, b in spans if (line[:, a:b] > 0).sum() >= _SPECK
    ]


def _erase_leaders(mask):
    """Remove the dotted leaders a printed form puts where a value is typed.

    A leader is a row of small, separate dots sitting on the baseline. Single
    full stops ("พ.ศ.", "พญ.") are left alone: a run has to be at least three
    dots long, evenly placed, to count.
    """
    mask = mask.copy()
    count, labels, stats, _ = cv2.connectedComponentsWithStats((mask > 0).astype(np.uint8), 8)
    dots = [
        index
        for index in range(1, count)
        if stats[index, cv2.CC_STAT_WIDTH] <= 8 and stats[index, cv2.CC_STAT_HEIGHT] <= 8
        and stats[index, cv2.CC_STAT_AREA] >= 3
    ]
    centres = {
        index: (
            stats[index, cv2.CC_STAT_LEFT] + stats[index, cv2.CC_STAT_WIDTH] / 2,
            stats[index, cv2.CC_STAT_TOP] + stats[index, cv2.CC_STAT_HEIGHT] / 2,
        )
        for index in dots
    }
    ordered = sorted(dots, key=lambda i: (round(centres[i][1] / 4), centres[i][0]))
    run: list[int] = []

    def flush():
        if len(run) >= 3:
            for index in run:
                mask[labels == index] = 0

    for index in ordered:
        if run:
            px, py = centres[run[-1]]
            x, y = centres[index]
            if abs(y - py) <= 3 and 0 < x - px <= 16:
                run.append(index)
                continue
            flush()
        run = [index]
    flush()
    return mask


def _as_crop(clean, box):
    """A word cut from the cleaned grey page, padded with white."""
    x0, y0, x1, y1 = box
    word = clean[y0:y1, x0:x1]
    padded = cv2.copyMakeBorder(
        word, _PAD, _PAD, _PAD, _PAD, cv2.BORDER_CONSTANT, value=255
    )
    return cv2.cvtColor(padded, cv2.COLOR_GRAY2BGR)


def _merge_words(groups: dict[tuple, list[TextLine]]) -> list[TextLine]:
    """Join the words of one line of one cell back into a single box.

    The words were recognised apart only so the spaces between them would come
    from the page; the parser expects a line of a cell as one box.
    """
    merged: list[TextLine] = []
    for words in groups.values():
        words.sort(key=lambda w: w.bbox[0])
        confidences = [w.confidence for w in words if w.confidence is not None]
        merged.append(
            TextLine(
                text=" ".join(w.text for w in words),
                bbox=(
                    words[0].bbox[0],
                    min(w.bbox[1] for w in words),
                    words[-1].bbox[2],
                    max(w.bbox[3] for w in words),
                ),
                confidence=min(confidences) if confidences else None,
            )
        )
    merged.sort(key=lambda line: (round(line.bbox[1], 1), line.bbox[0]))
    for index, line in enumerate(merged):
        line.order = index
    return merged
