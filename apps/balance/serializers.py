"""Request and response shapes for the balance API."""
from __future__ import annotations

from rest_framework import serializers

from apps.balance.models import BalanceDocument
from workers import balance_export


class BalanceUploadSerializer(serializers.Serializer):
    file = serializers.FileField()
    uploaded_by = serializers.CharField(required=False, allow_blank=True, max_length=150)

    def validate_file(self, value):
        if not value.name.lower().endswith(".pdf"):
            raise serializers.ValidationError("only PDF files are accepted")
        return value


class BalanceDocumentSerializer(serializers.ModelSerializer):
    """The document without its rows, for the status call."""

    document_id = serializers.UUIDField(source="document_uuid", read_only=True)
    entry_count = serializers.SerializerMethodField()
    review_count = serializers.SerializerMethodField()

    class Meta:
        model = BalanceDocument
        fields = (
            "document_id",
            "file_name",
            "file_hash",
            "file_size",
            "page_count",
            "status",
            "error_code",
            "error_message",
            "ocr_language",
            "page_summary",
            "entry_count",
            "review_count",
            "uploaded_by",
            "created_at",
            "completed_at",
            "processing_ms",
        )

    def get_entry_count(self, document: BalanceDocument) -> int:
        return document.entries.count()

    def get_review_count(self, document: BalanceDocument) -> int:
        return document.review_count


class BalanceResultSerializer(BalanceDocumentSerializer):
    """The document with every row it produced.

    ``entries`` is the agreed output and the whole of it: the twelve columns of
    ``ฟอแมทตาราง OCR.xlsx``, keyed by their headings and in their order, the
    same values the workbook's first sheet carries. Eleven of them come
    straight off the form; ``ลำดับ`` is counted here - see
    ``workers/balance_export.py``.

    Nothing else per row is sent. The columns a form has but the agreed shape
    does not - the unit, the remark - and the per-row review verdict stay in
    the workbook's second sheet and in the stored rows, where a reader checking
    the extraction can get at them; ``review_count`` here says how many rows
    that is.
    """

    entries = serializers.SerializerMethodField()

    class Meta(BalanceDocumentSerializer.Meta):
        fields = BalanceDocumentSerializer.Meta.fields + ("entries",)

    def get_entries(self, document: BalanceDocument) -> list[dict]:
        rows = list(document.entries.all())
        return [
            balance_export.output_row(row, sequence)
            for row, sequence in zip(rows, balance_export.sequences(rows))
        ]
