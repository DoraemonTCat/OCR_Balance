"""REST API for reading psychotropic ledgers.

The contract the caller sees:

* ``POST /api/v1/balance/documents``  - attach a PDF, get the rows back.
* ``GET  /api/v1/balance/documents``  - what has been submitted.
* ``GET  /api/v1/balance/documents/{id}``            - one document with its rows.
* ``GET  /api/v1/balance/documents/{id}/export.xlsx``- the agreed Excel output.
* ``POST /api/v1/balance/documents/{id}/reprocess``  - read the stored file again.

The POST reads the document before it answers, so the caller holds the request
open for roughly a few seconds per page. ``services.MAX_PAGES`` is the cut-off.
"""
from __future__ import annotations

import logging

from django.http import HttpResponse
from rest_framework import status
from rest_framework.decorators import api_view, parser_classes
from rest_framework.parsers import MultiPartParser
from rest_framework.response import Response

from apps.balance import services
from apps.balance.models import BalanceDocument, DocumentStatus
from apps.balance.serializers import (
    BalanceDocumentSerializer,
    BalanceResultSerializer,
    BalanceUploadSerializer,
)
from workers import balance_export

log = logging.getLogger(__name__)


def _get(document_id) -> BalanceDocument | None:
    return BalanceDocument.objects.filter(document_uuid=document_id).first()


def _not_found() -> Response:
    return Response(
        {"error": {"code": "NOT_FOUND", "message": "document not found"}},
        status=status.HTTP_404_NOT_FOUND,
    )


def _failed(document: BalanceDocument) -> Response:
    return Response(
        {
            "error": {
                "code": document.error_code or "UNKNOWN",
                "message": document.error_message,
            },
            "document_id": str(document.document_uuid),
        },
        status=status.HTTP_422_UNPROCESSABLE_ENTITY,
    )


@api_view(["GET", "POST"])
@parser_classes([MultiPartParser])
def document_collection(request) -> Response:
    if request.method == "POST":
        return _submit(request)
    return _list(request)


def _submit(request) -> Response:
    serializer = BalanceUploadSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)

    document = services.store_upload(
        serializer.validated_data["file"],
        uploaded_by=serializer.validated_data.get("uploaded_by", ""),
    )
    log.info(
        "balance document submitted",
        extra={"step": "balance_submit", "file_name": document.file_name},
    )

    document = services.process(document)
    if document.status == DocumentStatus.FAILED:
        return _failed(document)

    return Response(
        BalanceResultSerializer(document).data, status=status.HTTP_201_CREATED
    )


def _list(request) -> Response:
    queryset = BalanceDocument.objects.all()

    wanted = (request.query_params.get("status") or "").strip().upper()
    if wanted:
        queryset = queryset.filter(status=wanted)

    search = (request.query_params.get("search") or "").strip()
    if search:
        queryset = queryset.filter(file_name__icontains=search)

    limit = min(int(request.query_params.get("limit") or 50), 200)
    return Response(
        {"items": BalanceDocumentSerializer(queryset[:limit], many=True).data}
    )


@api_view(["GET"])
def document_detail(request, document_id) -> Response:
    document = _get(document_id)
    if document is None:
        return _not_found()
    return Response(BalanceResultSerializer(document).data)


@api_view(["POST"])
def reprocess_document(request, document_id) -> Response:
    """Read the stored file again - after a parser fix, without a re-upload."""
    document = _get(document_id)
    if document is None:
        return _not_found()

    document = services.process(document)
    if document.status == DocumentStatus.FAILED:
        return _failed(document)
    return Response(BalanceResultSerializer(document).data)


@api_view(["GET"])
def export_document(request, document_id):
    document = _get(document_id)
    if document is None:
        return _not_found()

    entries = list(document.entries.all())
    workbook = balance_export.build(entries, document.page_summary or [])

    response = HttpResponse(
        workbook,
        content_type=(
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        ),
    )
    stem = document.file_name.rsplit(".", 1)[0]
    response["Content-Disposition"] = f'attachment; filename="{stem}_output.xlsx"'
    return response
