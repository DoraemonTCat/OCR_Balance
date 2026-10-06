"""The workbook handed back to the caller.

The first sheet's header row is the contract - it has to equal
``ตัวอย่าง Colume.xlsx`` exactly, including the customer's spelling of
"ประเภทวัตุถเสพติด" - so it is asserted literally rather than derived from the
module under test.
"""
import datetime as dt
import io

from django.test import SimpleTestCase
from openpyxl import load_workbook

from workers import balance_export, form_layout
from workers.balance_parser import LedgerRow
from workers.balance_pipeline import PageOutcome

EXPECTED_HEADER = [
    "เลขที่คำขอซื้อ",
    "ProductGenericName",
    "ProductTradeName",
    "ประเภทวัตุถเสพติด",
    "เลขที่ใบอนุญาต",
    "จำนวนที่ขอซื้อ",
    "จำนวนที่อนุมัติ",
    "ยอดเงินที่อนุมัติ",
    "วันที่สร้าง",
    "วันที่อนุมัติ",
    "ชื่อสถานพยาบาล",
    "ที่ตั้ง",
    "จังหวัด",
    "ผู้ดำเนินกิจการ",
    "เลขที่ใบแจ้งหนี้/ใบเสร็จ",
    "วันที่ออกใบแจ้งหนี้/ใบเสร็จ",
]


def _row():
    row = LedgerRow(
        page_number=4,
        row_index=0,
        form_code="ร.ค.-๔",
        cells={
            form_layout.GENERIC_NAME: "Lorazepam 1 mg",
            form_layout.TRADE_NAME: "Lorazep",
            form_layout.BATCH_NO: "T25275",
            form_layout.ISSUED_TO: "t@on SunScQ",
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
    row.flag("issued_to is written in Thai, which this engine cannot read")
    return row


def _workbook(rows=None, pages=None):
    payload = balance_export.build(
        rows if rows is not None else [_row()],
        pages if pages is not None else [PageOutcome(page_number=4, form_code="ร.ค.-๔", rows=1)],
    )
    return load_workbook(io.BytesIO(payload))


class OutputSheetTests(SimpleTestCase):
    def test_header_matches_the_agreed_columns_exactly(self):
        sheet = _workbook()["Output"]
        header = [cell.value for cell in next(sheet.iter_rows(max_row=1))]
        self.assertEqual(header, EXPECTED_HEADER)

    def test_one_row_per_entry(self):
        self.assertEqual(_workbook()["Output"].max_row, 2)

    def test_fills_the_four_columns_the_forms_supply(self):
        values = list(next(_workbook()["Output"].iter_rows(min_row=2, values_only=True)))
        self.assertEqual(values[1], "Lorazepam 1 mg")
        self.assertEqual(values[2], "Lorazep")
        self.assertEqual(values[3], balance_export.SUBSTANCE_TYPE)
        # openpyxl reads any date cell back as a datetime.
        self.assertEqual(values[8].date(), dt.date(2026, 3, 1))

    def test_leaves_the_purchase_approval_columns_empty(self):
        # These twelve belong to a different record and are not guessed at.
        values = list(next(_workbook()["Output"].iter_rows(min_row=2, values_only=True)))
        for index in (0, 4, 5, 6, 7, 9, 10, 11, 12, 13, 14, 15):
            self.assertIn(values[index], ("", None), f"column {index + 1} was filled")


class LedgerSheetTests(SimpleTestCase):
    def test_carries_the_quantities_the_output_sheet_has_no_room_for(self):
        sheet = _workbook()["ข้อมูลจากตาราง"]
        values = list(next(sheet.iter_rows(min_row=2, values_only=True)))
        self.assertEqual(values[0], 4)             # page
        self.assertEqual(values[11], 450)          # ยอดยกมา
        self.assertIsNone(values[12])              # รับ, a dash on the form
        self.assertEqual(values[13], 40)           # จ่าย
        self.assertEqual(values[14], 410)          # คงเหลือ

    def test_records_why_a_row_needs_review(self):
        sheet = _workbook()["ข้อมูลจากตาราง"]
        values = list(next(sheet.iter_rows(min_row=2, values_only=True)))
        self.assertEqual(values[18], "ใช่")
        self.assertIn("written in Thai", values[19])


class PageSheetTests(SimpleTestCase):
    def test_lists_every_page_including_the_unreadable_ones(self):
        pages = [
            PageOutcome(page_number=1, form_code="ร.ค.-๔", rows=6, confidence=0.78),
            PageOutcome(page_number=2, error="no table grid: found 0 columns"),
        ]
        sheet = _workbook(pages=pages)["สรุปรายหน้า"]
        self.assertEqual(sheet.max_row, 3)
        self.assertIn("no table grid", sheet.cell(row=3, column=6).value)

    def test_accepts_the_stored_page_summary_as_plain_dicts(self):
        # The export runs from BalanceDocument.page_summary, which is JSON.
        sheet = _workbook(pages=[{"page": 1, "form": "ร.ค.-๔", "rows": 6}])["สรุปรายหน้า"]
        self.assertEqual(sheet.cell(row=2, column=2).value, "ร.ค.-๔")


class EmptyDocumentTests(SimpleTestCase):
    def test_a_document_with_no_rows_still_produces_the_header(self):
        sheet = _workbook(rows=[], pages=[])["Output"]
        header = [cell.value for cell in next(sheet.iter_rows(max_row=1))]
        self.assertEqual(header, EXPECTED_HEADER)
        self.assertEqual(sheet.max_row, 1)
