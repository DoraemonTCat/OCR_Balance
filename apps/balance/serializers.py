"""Request and response shapes for the balance API."""
from __future__ import annotations

from rest_framework import serializers

from apps.balance.models import BalanceDocument, BalanceEntry


class BalanceUploadSerializer(serializers.Serializer):
    file = serializers.FileField()
    uploaded_by = serializers.CharField(required=False, allow_blank=True, max_length=150)

    def validate_file(self, value):
        if not value.name.lower().endswith(".pdf"):
            raise serializers.ValidationError("only PDF files are accepted")
        return value


class BalanceEntrySerializer(serializers.ModelSerializer):
    class Meta:
        model = BalanceEntry
        fields = (
            "page_number",
            "row_index",
            "form_code",
            "entry_date",
            "generic_name",
            "trade_name",
            "batch_no",
            "manufacturer",
            "received_from",
            "issued_to",
            "recipient_id",
            "prescription_no",
            "unit",
            "remark",
            "balance_brought",
            "received",
            "issued",
            "balance",
            "confidence",
            "needs_review",
            "review_notes",
        )


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
    """The document with every row it produced."""

    entries = BalanceEntrySerializer(many=True, read_only=True)

    class Meta(BalanceDocumentSerializer.Meta):
        fields = BalanceDocumentSerializer.Meta.fields + ("entries",)
