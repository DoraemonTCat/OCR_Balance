"""The HTTP contract callers depend on.

Reading a document is stubbed out: these tests are about the endpoints - what
they accept, what they return, and what happens when a document cannot be read -
not about OCR, which ``test_balance_parser`` covers without an engine.
"""
from __future__ import annotations

import datetime as dt
import io
from unittest import mock

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client
from openpyxl import load_workbook
from rest_framework import status

from apps.balance.models import DocumentStatus
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
        form_code="บ.ย.ส. ๒/ว.จ. ๒-จ๑",
        cells={
            form_layout.DATE: "5 มค 68",
            form_layout.GENERIC_NAME: "Methylphenidate HCl tablets 10 mg",
            form_layout.TRADE_NAME: "Ritalin tablets 10 mg",
            form_layout.BATCH_NO: "BE210",
            form_layout.RECIPIENT_ID: "1112223334440",
            **cells,
        },
        entry_date=dt.date(2025, 1, 5),
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
        pages=[
            PageOutcome(
                page_number=1,
                form_code="บ.ย.ส. ๒/ว.จ. ๒-จ๑",
                rows=2,
                confidence=1.0,
                source="TEXT_LAYER",
            )
        ],
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
        name = body["entries"][0]["generic_name"]
        assert name == "Methylphenidate HCl tablets 10 mg"


class TestAgreedColumns:
    """``entries`` is the twelve columns of ฟอแมทตาราง OCR.xlsx, in order.

    Asserted literally rather than against the module that produces them, so a
    change to the agreed shape has to be made deliberately in two places. The
    keys carry the line breaks the format's headings are set with.
    """

    EXPECTED = [
        "sequence",
        "date",
        "generic_name",
        "trade_name",
        "batch_no",
        "received_from",
        "issued_to",
        "recipient_id",
        "balance_brought",
        "received",
        "issued",
        "balance",
    ]

    def test_every_entry_has_exactly_those_keys_in_that_order(self, client):
        for entry in _upload(client).json()["entries"]:
            assert list(entry) == self.EXPECTED

    def test_carries_what_the_form_says(self, client):
        entry = _upload(client).json()["entries"][0]
        assert entry["sequence"] == 1
        assert entry["date"] == "5 มค 68"
        assert entry["generic_name"] == (
            "Methylphenidate HCl tablets 10 mg"
        )
        assert entry["trade_name"] == "Ritalin tablets 10 mg"
        assert entry["batch_no"] == "BE210"

    def test_carries_the_four_quantities(self, client):
        entry = _upload(client).json()["entries"][0]
        assert entry["balance_brought"] == 450
        assert entry["received"] is None  # a dash on the form
        assert entry["issued"] == 40
        assert entry["balance"] == 410

    def test_the_sequence_restarts_at_each_new_form(self, client):
        second = _row(index=1)
        second.form_code = "ร.ย.ส. ๒/ว.จ. ๒-จ๑"
        response = _upload(client, result=_result([_row(), second]))
        assert [e["sequence"] for e in response.json()["entries"]] == [1, 1]

    def test_the_api_and_the_workbook_line_up(self, client):
        # The workbook keeps the format's Thai headings - it is the deliverable
        # the customer prints - while the JSON uses role names a program can
        # read. They are not the same strings, so what has to hold is that they
        # describe the same twelve columns in the same order.
        document_id = _upload(client).json()["document_id"]
        sheet = load_workbook(
            io.BytesIO(client.get(f"{URL}/{document_id}/export.xlsx").content)
        )["Output"]
        top = [c.value for c in next(sheet.iter_rows(max_row=1))]
        sub = [c.value for c in next(sheet.iter_rows(min_row=2, max_row=2))]
        headings = [s or t for t, s in zip(top, sub)]

        assert len(headings) == len(self.EXPECTED)
        # The sheet still carries the format's headings, line breaks included.
        assert headings[0] == "ลำดับ"
        assert headings[1] == "วัน\nเดือน\nปี"
        assert headings[-1] == "คงเหลือ"


class TestResponseShape:
    """``entries`` is the whole of the per-row answer."""

    def test_nothing_else_per_row_is_sent(self, client):
        body = _upload(client).json()
        assert "ledger" not in body
        assert set(body["entries"][0]) == set(TestAgreedColumns.EXPECTED)

    def test_the_document_still_says_how_many_rows_need_checking(self, client):
        # The per-row verdict is not in the response; this is what is left of
        # it, and it is the number that decides whether anyone has to look.
        body = _upload(client).json()
        assert body["entry_count"] == 2
        assert "review_count" in body


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


class TestPrettyOutput:
    """``?pretty`` indents the answer so a person can read it.

    The default is one line with no spaces, which is right for a caller and
    useless the moment somebody opens the file to check what came back.
    """

    def test_the_default_is_compact(self, client):
        body = _upload(client).content.decode("utf-8")
        assert "\n" not in body

    def test_pretty_indents_and_keeps_thai_readable(self, client):
        with mock.patch(
            "apps.balance.services._extract", return_value=_result()
        ):
            response = client.post(
                f"{URL}?pretty",
                {"file": SimpleUploadedFile("ledger.pdf", PDF_BYTES, "application/pdf")},
            )
        body = response.content.decode("utf-8")
        assert body.count("\n") > 20
        # Thai as itself, not as \uXXXX escapes.
        assert "บ.ย.ส. ๒/ว.จ. ๒-จ๑" in body

    def test_pretty_changes_nothing_but_the_spacing(self, client):
        plain = _upload(client).json()
        with mock.patch(
            "apps.balance.services._extract", return_value=_result()
        ):
            pretty = client.post(
                f"{URL}?pretty",
                {"file": SimpleUploadedFile("ledger.pdf", PDF_BYTES, "application/pdf")},
            ).json()
        assert plain["entries"] == pretty["entries"]

    def test_any_value_turns_it_on(self, client):
        with mock.patch(
            "apps.balance.services._extract", return_value=_result()
        ):
            response = client.post(
                f"{URL}?pretty=0",
                {"file": SimpleUploadedFile("ledger.pdf", PDF_BYTES, "application/pdf")},
            )
        assert "\n" in response.content.decode("utf-8")
