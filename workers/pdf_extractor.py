"""Pre-checks on a submitted PDF, and reading the text a page already carries.

Everything that opens a PDF is behind ``inspect``: it is the one place that
decides a file is unusable, and it does so before any page is rendered or any
model is loaded, so a corrupt or password-protected upload costs nothing.

``text_boxes`` is the other half. Submissions arrive in two kinds:

* A **searchable** PDF - typed, or scanned and already run through an OCR that
  embedded its result. The text is in the file, correct, Thai included, with the
  position of every fragment. Nothing this service can do will read it better.
* A **scan** of a form filled in by hand, which carries no text at all and has
  to go through ``ocr_engine``.

Both end up as the same ``TextLine`` boxes in the same pixel coordinates, so
``balance_parser`` never learns which kind it was given.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

try:
    import fitz  # PyMuPDF
except ImportError:  # pragma: no cover - only the parser is importable without it
    # The dataclasses must import cleanly where PyMuPDF is absent (unit tests);
    # only the function that actually opens a PDF needs it.
    fitz = None

log = logging.getLogger(__name__)

#: A page with fewer extractable characters than this is treated as scanned.
MIN_TEXT_CHARS_PER_PAGE = 60


class PdfError(Exception):
    """Base class for pre-check failures."""

    error_code = "INVALID_PDF"


class PdfNotFound(PdfError):
    error_code = "FILE_NOT_FOUND"


class PdfPasswordRequired(PdfError):
    error_code = "PDF_PASSWORD_REQUIRED"


class PdfCorrupted(PdfError):
    error_code = "INVALID_PDF"


def _require_fitz():
    if fitz is None:  # pragma: no cover
        raise PdfError("PyMuPDF (fitz) is not installed")
    return fitz


@dataclass(slots=True)
class TextLine:
    """One recognised box: its text, where it is, and how sure the engine is.

    The position is what the whole extraction rests on - ``balance_parser``
    assigns a box to a column and a row by where its centre falls - so every
    engine adapter must fill ``bbox`` in the page's own pixel coordinates.
    """

    text: str
    bbox: tuple[float, float, float, float]
    confidence: float | None = None
    size: float | None = None
    bold: bool = False
    #: Reading order, set by whoever produced the box. Within one cell the
    #: fragments are put back together in this order rather than by position:
    #: a searchable PDF stores them in the order they are meant to be read, and
    #: that is more reliable than their coordinates - the Thai prefix of a name
    #: comes back with its glyphs at descending y, which sorts into "พิมพ์.เก่ง
    #: การดี ญด." rather than "ด.ญ.พิมพ์ เก่งการดี".
    order: int = 0

    @property
    def x0(self) -> float:
        return self.bbox[0]

    @property
    def top(self) -> float:
        return self.bbox[1]


@dataclass(slots=True)
class PdfMetadata:
    filename: str
    pages: int
    text_layer: bool
    encrypted: bool
    file_size: int


def inspect(path: Path) -> PdfMetadata:
    """Run the pre-checks before any OCR work starts.

    Raises a ``PdfError`` subclass - each carrying the ``error_code`` the API
    reports - rather than returning a verdict, so a caller cannot forget to
    check one.
    """
    if not path.exists() or not path.is_file():
        raise PdfNotFound(f"file not found: {path}")
    if path.suffix.lower() != ".pdf":
        raise PdfError(f"unsupported extension: {path.suffix}")

    size = path.stat().st_size
    if size == 0:
        raise PdfCorrupted(f"file is empty: {path}")

    try:
        document = _require_fitz().open(path)
    except Exception as exc:  # PyMuPDF raises bare exceptions for bad files
        raise PdfCorrupted(f"cannot open PDF: {exc}") from exc

    try:
        if document.needs_pass:
            raise PdfPasswordRequired(f"password protected: {path.name}")
        page_count = document.page_count
        if page_count == 0:
            raise PdfCorrupted(f"PDF has no pages: {path.name}")
        # Sample the front of the document rather than every page.
        sample = min(page_count, 5)
        text_layer = any(
            len((document.load_page(i).get_text("text") or "").strip())
            >= MIN_TEXT_CHARS_PER_PAGE
            for i in range(sample)
        )
        return PdfMetadata(
            filename=path.name,
            pages=page_count,
            text_layer=text_layer,
            encrypted=bool(document.needs_pass),
            file_size=size,
        )
    finally:
        document.close()


# --- the text a page already carries ---------------------------------------

#: PDF user space is 72 units to the inch; a page rendered at ``dpi`` is that
#: many pixels to the inch. Boxes are scaled by the ratio so they land on the
#: same grid the rendered image was measured on.
_PDF_UNITS_PER_INCH = 72.0

def has_usable_text(page) -> bool:
    """Whether this page's own text is worth reading instead of OCR-ing it."""
    return len(page.get_text("text").strip()) >= MIN_TEXT_CHARS_PER_PAGE


def text_boxes(page, dpi: int) -> list[TextLine]:
    """The page's embedded text as boxes, in the pixels of a ``dpi`` render.

    One box per span, not per line: a span is already the unit the PDF breaks
    text at, and the parser places each box in a column by where it sits, so
    splitting further is unnecessary and joining first would merge cells.

    The boxes keep the order the file stores them in, which for a searchable
    PDF is reading order. They are deliberately *not* sorted by position: the
    coordinates of a span can be wrong where its text is not, and on these
    files they are - see ``TextLine.order``.

    Confidence is 1.0 throughout. The text is not a reading of the page, it is
    the page, and the review flags downstream are about what could not be read.
    """
    scale = dpi / _PDF_UNITS_PER_INCH
    boxes: list[TextLine] = []
    for block in page.get_text("dict").get("blocks", []):
        if block.get("type") != 0:  # 0 = text
            continue
        for line in block.get("lines", []):
            for span in line.get("spans", []):
                text = (span.get("text") or "").strip()
                if not text:
                    continue
                x0, y0, x1, y1 = span["bbox"]
                boxes.append(
                    TextLine(
                        text=text,
                        bbox=(x0 * scale, y0 * scale, x1 * scale, y1 * scale),
                        confidence=1.0,
                        size=span.get("size"),
                        bold=bool(span.get("flags", 0) & 16),
                        order=len(boxes),
                    )
                )
    return boxes
