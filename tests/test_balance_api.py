"""The HTTP contract callers depend on.

Reading a document is stubbed out: these tests are about the endpoints - what
they accept, what they return, and what happens when a document cannot be read -
not about OCR, which ``test_balance_parser`` covers without an engine.
"""
from __future__ import annotations

import datetime as dt
from unittest import mock

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client
from rest_framework import status

from apps.balance.models import BalanceDocument, DocumentStatus
from workers import form_layout
from workers.balance_parser import LedgerRow
from workers.balance_pipeline import DocumentResult, PageOutcome

pytestmark = pytest.mark.django_db

URL = "/api/v1/balance/documents"
#: Enough of a PDF for the stub; the real file never reaches PyMuPDF here.
PDF_BYTES = b"%PDF-1.4\n%%EOF\n"


def _row(page=1, index=0, **cells):
    row = LedgerRow(
        page_number=page,
        row_index=index,
        form_code="ร.ค.-๔",
        cells={
            form_layout.GENERIC_NAME: "Lorazepam 1 mg",
            form_layout.TRADE_NAME: "Lorazep",
            form_layout.BATCH_NO: "T25275",
            **cells,
        },
        entry_date=dt.date(2026, 3, 1),
        confidence=0.88,
    )
    row.quantities = {
        form_layout.BALANCE_BROUGHT: 450,
        form_layout.RECEIVED: None,
        form_layout.ISSUED: 40,
        form_layout.BALANCE: 410,
    }
    return row


def _result(rows=None):
    return DocumentResult(
        rows=rows if rows is not None else [_row(), _row(index=1)],
        pages=[PageOutcome(page_number=1, form_code="ร.ค.-๔", rows=2, confidence=0.8)],
        page_count=1,
        processing_ms=1234,
    )


def _upload(client, result=None, **extra):
    with mock.patch(
        "apps.balance.services._extract", return_value=result or _result()
    ):
        return client.post(
            URL,
            {"file": SimpleUploadedFile("ledger.pdf", PDF_BYTES, "application/pdf"), **extra},
        )


@pytest.fixture
def client():
    return Client()


class TestSubmit:
    def test_returns_the_rows_it_read(self, client):
        response = _upload(client)
        assert response.status_code == status.HTTP_201_CREATED
        body = response.json()
        assert body["status"] == DocumentStatus.COMPLETED
        assert body["entry_count"] == 2
        assert len(body["entries"]) == 2
        assert body["entries"][0]["generic_name"] == "Lorazepam 1 mg"

    def test_records_the_engine_language_on_the_document(self, client):
        # Which fields are trustworthy depends on it, so it is part of the answer.
        assert _upload(client).json()["ocr_language"] == "latin"

    def test_keeps_the_per_page_outcome(self, client):
        summary = _upload(client).json()["page_summary"]
        assert summary == [
            {"page": 1, "form": "ร.ค.-๔", "rows": 2, "skew": 0.0,
             "confidence": 0.8, "error": ""}
        ]

    def test_stores_the_uploader(self, client):
        response = _upload(client, uploaded_by="go-backend")
        assert response.json()["uploaded_by"] == "go-backend"

    def test_rejects_anything_that_is_not_a_pdf(self, client):
        response = client.post(
            URL, {"file": SimpleUploadedFile("rows.xlsx", b"PK\x03\x04", "application/zip")}
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_requires_a_file(self, client):
        assert client.post(URL, {}).status_code == status.HTTP_400_BAD_REQUEST

    def test_an_unreadable_document_answers_422_not_500(self, client):
        from workers.pdf_extractor import PdfCorrupted

        with mock.patch(
            "apps.balance.services._extract", side_effect=PdfCorrupted("cannot open PDF")
        ):
            response = client.post(
                URL,
                {"file": SimpleUploadedFile("bad.pdf", PDF_BYTES, "application/pdf")},
            )
        assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY
        assert response.json()["error"]["code"] == "INVALID_PDF"
        assert BalanceDocument.objects.get().status == DocumentStatus.FAILED

    def test_two_submissions_of_one_name_do_not_collide(self, client):
        first = _upload(client).json()["document_id"]
        second = _upload(client).json()["document_id"]
        assert first != second
        paths = set(BalanceDocument.objects.values_list("file_path", flat=True))
        assert len(paths) == 2


class TestRetrieve:
    def test_detail_returns_the_rows(self, client):
        document_id = _upload(client).json()["document_id"]
        response = client.get(f"{URL}/{document_id}")
        assert response.status_code == status.HTTP_200_OK
        assert len(response.json()["entries"]) == 2

    def test_unknown_document_is_404(self, client):
        response = client.get(f"{URL}/00000000-0000-0000-0000-000000000000")
        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_list_filters_by_status(self, client):
        _upload(client)
        assert len(client.get(URL, {"status": "COMPLETED"}).json()["items"]) == 1
        assert client.get(URL, {"status": "FAILED"}).json()["items"] == []

    def test_list_searches_by_file_name(self, client):
        _upload(client)
        assert len(client.get(URL, {"search": "ledger"}).json()["items"]) == 1
        assert client.get(URL, {"search": "nothing"}).json()["items"] == []


class TestExport:
    def test_returns_a_workbook(self, client):
        document_id = _upload(client).json()["document_id"]
        response = client.get(f"{URL}/{document_id}/export.xlsx")
        assert response.status_code == status.HTTP_200_OK
        assert response["Content-Type"].endswith("spreadsheetml.sheet")
        assert "ledger_output.xlsx" in response["Content-Disposition"]
        assert response.content[:2] == b"PK"  # a real zip container

    def test_unknown_document_is_404(self, client):
        response = client.get(
            f"{URL}/00000000-0000-0000-0000-000000000000/export.xlsx"
        )
        assert response.status_code == status.HTTP_404_NOT_FOUND


class TestReprocess:
    def test_replaces_the_rows_rather_than_adding_to_them(self, client):
        document_id = _upload(client).json()["document_id"]
        with mock.patch(
            "apps.balance.services._extract", return_value=_result([_row()])
        ):
            response = client.post(f"{URL}/{document_id}/reprocess")
        assert response.status_code == status.HTTP_200_OK
        assert response.json()["entry_count"] == 1

    def test_unknown_document_is_404(self, client):
        response = client.post(f"{URL}/00000000-0000-0000-0000-000000000000/reprocess")
        assert response.status_code == status.HTTP_404_NOT_FOUND
