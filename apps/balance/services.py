"""Store a submitted PDF, read it, and keep what was read.

This is the whole job of the service: it does not compare the result against
anything or decide what it means. The caller asks for a document to be read and
gets back what the forms say, with every uncertain value marked.

Processing is synchronous: these submissions are a handful of pages and the
caller wants the rows back in the same exchange, which is simpler to operate and
to reason about than a queue. ``MAX_PAGES`` keeps a large document from being
read inside a request thread by mistake; a document that big needs a background
worker, which this service does not have.
"""
from __future__ import annotations

import hashlib
import logging
import uuid
from pathlib import Path

from django.conf import settings
from django.db import transaction as db_transaction
from django.utils import timezone

from apps.balance.models import BalanceDocument, BalanceEntry, DocumentStatus
from workers import balance_pipeline, form_layout
from workers.pdf_extractor import PdfError

log = logging.getLogger(__name__)

#: Beyond this, the caller should be using the worker queue rather than holding
#: an HTTP request open. A page costs a few seconds of OCR.
MAX_PAGES = 50

#: Where submitted files land, under DOCUMENT_ROOT.
INPUT_DIR = "input"


class SubmissionError(Exception):
    def __init__(self, error_code: str, message: str) -> None:
        super().__init__(message)
        self.error_code = error_code


def store_upload(uploaded_file, *, uploaded_by: str = "") -> BalanceDocument:
    """Write an uploaded PDF under DOCUMENT_ROOT and register it."""
    name = Path(uploaded_file.name).name
    if not name.lower().endswith(".pdf"):
        raise SubmissionError("INVALID_PDF", f"not a PDF: {name}")

    root = Path(settings.DOCUMENT_ROOT)
    target_dir = root / INPUT_DIR
    target_dir.mkdir(parents=True, exist_ok=True)

    # The caller's filename is kept visible but never trusted as a path, and a
    # second submission of the same name must not overwrite the first.
    stored_name = f"{uuid.uuid4().hex}_{name}"
    target = target_dir / stored_name

    digest = hashlib.sha256()
    with target.open("wb") as handle:
        for chunk in uploaded_file.chunks():
            digest.update(chunk)
            handle.write(chunk)

    return BalanceDocument.objects.create(
        file_name=name,
        file_path=f"{INPUT_DIR}/{stored_name}",
        file_hash=digest.hexdigest(),
        file_size=target.stat().st_size,
        uploaded_by=uploaded_by,
        status=DocumentStatus.PENDING,
    )


def process(document: BalanceDocument) -> BalanceDocument:
    """Read the document and persist its entries.

    Re-processing the same document replaces its entries rather than adding to
    them, so a retry after a parser fix leaves one set of rows.
    """
    path = Path(settings.DOCUMENT_ROOT) / document.file_path
    document.status = DocumentStatus.PROCESSING
    document.ocr_language = settings.OCR["LANGUAGE"]
    document.save(update_fields=["status", "ocr_language", "updated_at"])

    try:
        result = _extract(path)
    except (PdfError, SubmissionError) as exc:
        return _fail(document, getattr(exc, "error_code", "INVALID_PDF"), str(exc))
    except Exception as exc:  # pragma: no cover - defensive
        log.exception("document failed", extra={"file_name": document.file_name})
        return _fail(document, "UNKNOWN", f"{type(exc).__name__}: {exc}")

    with db_transaction.atomic():
        BalanceEntry.objects.filter(document=document).delete()
        BalanceEntry.objects.bulk_create(
            [_entry(document, row) for row in result.rows],
            batch_size=settings.BULK_INSERT_BATCH_SIZE,
        )
        document.status = DocumentStatus.COMPLETED
        document.page_count = result.page_count
        document.page_summary = [page.as_dict() for page in result.pages]
        document.processing_ms = result.processing_ms
        document.completed_at = timezone.now()
        document.error_code = ""
        document.error_message = ""
        document.save()

    log.info(
        "document processed",
        extra={
            "step": "balance_process",
            "file_name": document.file_name,
            "rows": len(result.rows),
            "review": result.review_count,
            "duration_ms": result.processing_ms,
        },
    )
    return document


def _extract(path: Path):
    from workers.pdf_extractor import inspect

    metadata = inspect(path)
    if metadata.pages > MAX_PAGES:
        raise SubmissionError(
            "TOO_MANY_PAGES",
            f"{metadata.pages} pages exceeds the synchronous limit of {MAX_PAGES}",
        )
    return balance_pipeline.extract(path)


def _fail(document: BalanceDocument, code: str, message: str) -> BalanceDocument:
    document.status = DocumentStatus.FAILED
    document.error_code = code
    document.error_message = message
    document.completed_at = timezone.now()
    document.save()
    return document


def _entry(document: BalanceDocument, row) -> BalanceEntry:
    """Map a parsed ``LedgerRow`` onto the stored row.

    The parser keys its cells by column role, and the roles are named after the
    model's fields, so the mapping is a lookup rather than a table of
    correspondences that could drift out of step.
    """
    cells = row.cells
    return BalanceEntry(
        document=document,
        page_number=row.page_number,
        row_index=row.row_index,
        form_code=row.form_code,
        entry_date=row.entry_date,
        generic_name=cells.get(form_layout.GENERIC_NAME, ""),
        trade_name=cells.get(form_layout.TRADE_NAME, ""),
        batch_no=cells.get(form_layout.BATCH_NO, "")[:100],
        manufacturer=cells.get(form_layout.MANUFACTURER, ""),
        received_from=cells.get(form_layout.RECEIVED_FROM, ""),
        issued_to=cells.get(form_layout.ISSUED_TO, ""),
        recipient_id=cells.get(form_layout.RECIPIENT_ID, "")[:100],
        prescription_no=cells.get(form_layout.PRESCRIPTION_NO, "")[:100],
        unit=cells.get(form_layout.UNIT, "")[:50],
        remark=cells.get(form_layout.REMARK, ""),
        balance_brought=row.quantities.get(form_layout.BALANCE_BROUGHT),
        received=row.quantities.get(form_layout.RECEIVED),
        issued=row.quantities.get(form_layout.ISSUED),
        balance=row.quantities.get(form_layout.BALANCE),
        confidence=row.confidence,
        needs_review=row.needs_review,
        review_notes=row.review_notes or None,
        raw_text=row.raw_text,
    )
