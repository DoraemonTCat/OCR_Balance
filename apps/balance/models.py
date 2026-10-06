"""Storage for the psychotropic ledger extraction.

One ``BalanceDocument`` per PDF submitted, one ``BalanceEntry`` per handwritten
line read off it. The entries keep every column the forms carry, not only the
ones the requested Excel output has a place for: the ledger's own figures -
ยอดยกมา / รับ / จ่าย / คงเหลือ - are what make the extraction checkable, and
discarding them at the database would make the check impossible to repeat.

Nothing here is compared against master data. This service reads documents and
hands back what they say; deciding what to do with that is the caller's.
"""
from __future__ import annotations

import uuid

from django.db import models


class DocumentStatus(models.TextChoices):
    PENDING = "PENDING", "PENDING"
    PROCESSING = "PROCESSING", "PROCESSING"
    COMPLETED = "COMPLETED", "COMPLETED"
    FAILED = "FAILED", "FAILED"


class BalanceDocument(models.Model):
    """One submitted PDF and the outcome of reading it."""

    id = models.BigAutoField(primary_key=True)
    document_uuid = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)

    file_name = models.CharField(max_length=255)
    #: Relative to DOCUMENT_ROOT, like the rest of the service.
    file_path = models.CharField(max_length=500)
    file_hash = models.CharField(max_length=64, blank=True, db_index=True)
    file_size = models.BigIntegerField(null=True, blank=True)
    page_count = models.IntegerField(null=True, blank=True)

    status = models.CharField(
        max_length=20, choices=DocumentStatus.choices, default=DocumentStatus.PENDING
    )
    error_code = models.CharField(max_length=50, blank=True)
    error_message = models.TextField(blank=True)

    #: The PaddleOCR language actually used, recorded per document because the
    #: readable fields depend on it (see docs/OCR_THAI.md).
    ocr_language = models.CharField(max_length=20, blank=True)
    #: Per-page outcome: form code, row count, skew and confidence. Enough to
    #: tell a page that was read badly from one that was not read at all.
    page_summary = models.JSONField(null=True, blank=True)

    uploaded_by = models.CharField(max_length=150, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    processing_ms = models.IntegerField(null=True, blank=True)

    class Meta:
        db_table = "balance_document"
        indexes = [
            models.Index(fields=["status", "created_at"]),
        ]
        ordering = ["-created_at", "-id"]

    def __str__(self) -> str:
        return f"{self.file_name} ({self.status})"

    @property
    def review_count(self) -> int:
        return self.entries.filter(needs_review=True).count()


class BalanceEntry(models.Model):
    """One line of a ledger table.

    The column set is the union of the three forms. A field a form does not have
    stays empty - ``recipient_id`` only exists on บ.ว.จ ๗/๔-ขพ, ``unit`` only on
    ร.ว.จ ๗/๔ - and ``form_code`` says which form the row came from.
    """

    id = models.BigAutoField(primary_key=True)
    document = models.ForeignKey(
        BalanceDocument,
        on_delete=models.CASCADE,
        related_name="entries",
        db_column="document_id",
    )

    page_number = models.IntegerField()
    row_index = models.IntegerField()
    #: ร.ว.จ ๗/๔ | บ.ว.จ ๗/๔-ขพ | ร.ค.-๔
    form_code = models.CharField(max_length=30)

    entry_date = models.DateField(null=True, blank=True)
    #: The date cell as the form writes it. Kept because that is what the
    #: agreed output carries: these forms use Thai month abbreviations, and the
    #: report form puts a range on a line - "3-31 ม.ค. 68" is a month of
    #: dispensing summarised, which is not a date and must not be forced into
    #: one. ``entry_date`` is the parse of it, where there is one to make.
    entry_date_text = models.CharField(max_length=60, blank=True)
    generic_name = models.TextField(blank=True)
    trade_name = models.TextField(blank=True)
    batch_no = models.CharField(max_length=100, blank=True)
    manufacturer = models.TextField(blank=True)
    received_from = models.TextField(blank=True)
    issued_to = models.TextField(blank=True)
    recipient_id = models.CharField(max_length=100, blank=True)
    prescription_no = models.CharField(max_length=100, blank=True)
    unit = models.CharField(max_length=50, blank=True)
    remark = models.TextField(blank=True)

    # Null is a dash on the form, which these ledgers use for "no movement".
    balance_brought = models.IntegerField(null=True, blank=True)
    received = models.IntegerField(null=True, blank=True)
    issued = models.IntegerField(null=True, blank=True)
    balance = models.IntegerField(null=True, blank=True)

    confidence = models.DecimalField(
        max_digits=5, decimal_places=4, null=True, blank=True
    )
    needs_review = models.BooleanField(default=False)
    #: Why the row needs review, one note per reason, in English.
    review_notes = models.JSONField(null=True, blank=True)
    #: The cells as they were read, before cleaning. Kept so a disputed value
    #: can be traced back without re-running OCR.
    raw_text = models.TextField(blank=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "balance_entry"
        ordering = ["page_number", "row_index"]
        constraints = [
            models.UniqueConstraint(
                fields=["document", "page_number", "row_index"],
                name="uq_balance_entry_doc_page_row",
            )
        ]
        indexes = [
            models.Index(fields=["document", "page_number"]),
            models.Index(fields=["document", "needs_review"]),
            models.Index(fields=["entry_date"]),
        ]

    def __str__(self) -> str:
        return f"p{self.page_number}r{self.row_index} {self.generic_name}"
