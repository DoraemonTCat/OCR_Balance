"""Run a whole PDF of psychotropic ledgers through the parser.

Pages are rendered and handed to ``balance_parser`` one at a time, so a long
document never holds more than one page image in memory. A rendered A4 page at
200 dpi is about 14 MB as an array, and deskewing makes a second copy.

A page that cannot be read does not fail the document. The forms are scanned in
batches and a single bad page is normal; it is recorded in the per-page summary
with its reason and the remaining pages are still extracted.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from pathlib import Path

try:
    import fitz  # PyMuPDF
    import numpy as np
except ImportError:  # pragma: no cover - the API image does not render pages
    fitz = None
    np = None

try:
    import cv2
except ImportError:  # pragma: no cover
    cv2 = None

from django.conf import settings

from workers import balance_parser, ocr_engine
from workers.balance_parser import LedgerRow, PageParse
from workers.form_layout import FormNotRecognised
from workers.grid_detector import GridNotFound
from workers.pdf_extractor import PdfError, inspect

log = logging.getLogger(__name__)


@dataclass(slots=True)
class PageOutcome:
    """What happened to one page, readable or not."""

    page_number: int
    form_code: str = ""
    rows: int = 0
    skew_angle: float = 0.0
    confidence: float | None = None
    error: str = ""

    def as_dict(self) -> dict:
        return {
            "page": self.page_number,
            "form": self.form_code,
            "rows": self.rows,
            "skew": round(self.skew_angle, 2),
            "confidence": self.confidence,
            "error": self.error,
        }


@dataclass(slots=True)
class DocumentResult:
    rows: list[LedgerRow] = field(default_factory=list)
    pages: list[PageOutcome] = field(default_factory=list)
    page_count: int = 0
    processing_ms: int = 0

    @property
    def failed_pages(self) -> list[PageOutcome]:
        return [page for page in self.pages if page.error]

    @property
    def review_count(self) -> int:
        return sum(1 for row in self.rows if row.needs_review)


def extract(path: Path, *, dpi: int | None = None) -> DocumentResult:
    """Read every page of ``path`` and return the rows found."""
    if fitz is None or cv2 is None:  # pragma: no cover - deployment issue
        raise PdfError("PyMuPDF and OpenCV are required to read a document")

    metadata = inspect(path)  # refuses an unusable file before any model loads
    render_dpi = dpi or settings.OCR["RENDER_DPI"]
    started = time.monotonic()

    result = DocumentResult(page_count=metadata.pages)
    document = fitz.open(path)
    try:
        for index in range(document.page_count):
            page_number = index + 1
            outcome = PageOutcome(page_number=page_number)
            try:
                image = _render(document.load_page(index), render_dpi)
                parse = balance_parser.parse_page(
                    image, page_number, ocr_engine.recognize_image
                )
                result.rows.extend(parse.rows)
                _fill(outcome, parse)
            except (GridNotFound, FormNotRecognised) as exc:
                # Expected on a cover sheet, a signature page or a scan too poor
                # to find the rules in: not an error in the document.
                outcome.error = str(exc)
                log.info(
                    "page skipped",
                    extra={"step": "balance_page", "page": page_number, "reason": str(exc)},
                )
            except Exception as exc:  # pragma: no cover - defensive
                outcome.error = f"{type(exc).__name__}: {exc}"
                log.exception(
                    "page failed", extra={"step": "balance_page", "page": page_number}
                )
            result.pages.append(outcome)
    finally:
        document.close()

    result.processing_ms = int((time.monotonic() - started) * 1000)
    log.info(
        "document extracted",
        extra={
            "step": "balance_extract",
            "file_name": path.name,
            "pages": result.page_count,
            "rows": len(result.rows),
            "failed_pages": len(result.failed_pages),
            "duration_ms": result.processing_ms,
        },
    )
    return result


def _fill(outcome: PageOutcome, parse: PageParse) -> None:
    outcome.form_code = parse.form.code
    outcome.rows = len(parse.rows)
    outcome.skew_angle = parse.skew_angle
    outcome.confidence = parse.confidence


def _render(page, dpi: int):
    """Render one PDF page to a BGR array."""
    pixmap = page.get_pixmap(dpi=dpi)
    image = np.frombuffer(pixmap.samples, dtype=np.uint8).reshape(
        pixmap.height, pixmap.width, pixmap.n
    )
    if pixmap.n == 4:
        return cv2.cvtColor(image, cv2.COLOR_RGBA2BGR)
    if pixmap.n == 3:
        return cv2.cvtColor(image, cv2.COLOR_RGB2BGR)
    return cv2.cvtColor(image, cv2.COLOR_GRAY2BGR)
