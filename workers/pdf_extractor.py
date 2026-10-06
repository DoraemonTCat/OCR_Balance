"""Pre-checks on a submitted PDF, and the box type the OCR engine returns.

Everything that opens a PDF is behind ``inspect``: it is the one place that
decides a file is unusable, and it does so before any page is rendered or any
model is loaded, so a corrupt or password-protected upload costs nothing.

Rendering the pages is ``balance_pipeline``'s job, not this module's. These
documents are scans with no text layer, so there is no text-extraction path
here - ``inspect`` only *reports* whether a text layer exists, because a
document that has one is not the kind of document this service is for and the
operator should know.
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
