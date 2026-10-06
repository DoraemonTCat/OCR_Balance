"""Excel output for a psychotropic ledger extraction.

Sheet 1 carries the sixteen columns of ``ตัวอย่าง Colume.xlsx`` exactly, in that
order, because that is the agreed output shape.

Only three of them exist on the ledger forms:

===============================  ====================================
Column                           Source
===============================  ====================================
ProductGenericName               ชื่อและความแรงของวัตถุออกฤทธิ์
ProductTradeName                 ชื่อการค้า (ร.ค.-๔ and บ.ว.จ ๗/๔-ขพ)
ประเภทวัตุถเสพติด                 the form's own subject line
วันที่สร้าง                        วัน เดือน ปี of the entry
===============================  ====================================

The other twelve - เลขที่คำขอซื้อ, เลขที่ใบอนุญาต, จำนวนที่ขอซื้อ, จำนวนที่อนุมัติ,
ยอดเงินที่อนุมัติ, วันที่อนุมัติ, ชื่อสถานพยาบาล, ที่ตั้ง, จังหวัด, ผู้ดำเนินกิจการ and the
two invoice fields - belong to a purchase-approval record. The ledgers do not
contain them, so those cells are left empty rather than filled with a guess.

Sheet 2 therefore carries what the forms *do* say: the full ledger line,
including the ยอดยกมา / รับ / จ่าย / คงเหลือ figures that the sixteen columns have
no place for. Sheet 3 is the per-page outcome, which is where a page that could
not be read is visible.
"""
from __future__ import annotations

import io
from typing import Sequence

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.worksheet import Worksheet

from workers import form_layout

#: Sheet 1, verbatim from ตัวอย่าง Colume.xlsx. Order and spelling are the
#: customer's, including "ประเภทวัตุถเสพติด", and are not corrected here.
OUTPUT_COLUMNS: Sequence[tuple[str, int]] = (
    ("เลขที่คำขอซื้อ", 18),
    ("ProductGenericName", 34),
    ("ProductTradeName", 30),
    ("ประเภทวัตุถเสพติด", 34),
    ("เลขที่ใบอนุญาต", 18),
    ("จำนวนที่ขอซื้อ", 14),
    ("จำนวนที่อนุมัติ", 14),
    ("ยอดเงินที่อนุมัติ", 16),
    ("วันที่สร้าง", 14),
    ("วันที่อนุมัติ", 14),
    ("ชื่อสถานพยาบาล", 28),
    ("ที่ตั้ง", 40),
    ("จังหวัด", 14),
    ("ผู้ดำเนินกิจการ", 24),
    ("เลขที่ใบแจ้งหนี้/ใบเสร็จ", 20),
    ("วันที่ออกใบแจ้งหนี้/ใบเสร็จ", 22),
)

#: Sheet 2: the ledger line as the form sets it out.
LEDGER_COLUMNS: Sequence[tuple[str, int]] = (
    ("หน้า", 7),
    ("แบบฟอร์ม", 14),
    ("วัน เดือน ปี", 13),
    ("ชื่อและความแรง", 30),
    ("ชื่อการค้า", 16),
    ("เลขที่/รุ่นที่ผลิต", 16),
    ("ชื่อผู้ผลิต", 18),
    ("ได้มาจาก", 18),
    ("จ่ายไปให้ / ผู้รับยา", 24),
    ("เลขบัตรประชาชน", 20),
    ("เลขที่ใบสั่งยา", 14),
    ("ยอดยกมา", 11),
    ("รับ", 9),
    ("จ่าย", 9),
    ("คงเหลือ", 11),
    ("หน่วย", 9),
    ("หมายเหตุ", 18),
    ("Confidence", 12),
    ("ต้องตรวจทาน", 13),
    ("เหตุผล", 56),
)

PAGE_COLUMNS: Sequence[tuple[str, int]] = (
    ("หน้า", 8),
    ("แบบฟอร์ม", 16),
    ("จำนวนแถว", 11),
    ("เอียง (องศา)", 13),
    ("Confidence", 12),
    ("ปัญหา", 60),
)

#: The subject of all three forms, used for ประเภทวัตุถเสพติด. It is printed on
#: the form, not written in, so it is the one document-level value that is known
#: without reading any Thai.
SUBSTANCE_TYPE = "วัตถุออกฤทธิ์ในประเภท 3 หรือประเภท 4"

_HEADER_FILL = PatternFill("solid", fgColor="1F3864")
_HEADER_FONT = Font(color="FFFFFF", bold=True)
_REVIEW_FILL = PatternFill("solid", fgColor="FFF2CC")
_EMPTY_FILL = PatternFill("solid", fgColor="F2F2F2")
_DATE_FORMAT = "yyyy-mm-dd"


def build(rows, pages) -> bytes:
    """Return the workbook for one extraction.

    ``rows`` are ``balance_parser.LedgerRow`` or ``BalanceEntry`` instances -
    anything exposing the same attribute names - and ``pages`` are
    ``balance_pipeline.PageOutcome``.
    """
    workbook = Workbook()

    output = workbook.active
    output.title = "Output"
    _write_output(output, rows)

    _write_ledger(workbook.create_sheet("ข้อมูลจากตาราง"), rows)
    _write_pages(workbook.create_sheet("สรุปรายหน้า"), pages)

    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


def _write_output(sheet: Worksheet, rows) -> None:
    _header(sheet, OUTPUT_COLUMNS)
    # The twelve columns with no source are shaded so a reader can see at a
    # glance that they are empty by nature, not because extraction missed them.
    sourced = {2, 3, 4, 9}

    for row in rows:
        sheet.append(
            [
                "",                                   # เลขที่คำขอซื้อ
                _value(row, "generic_name"),
                _value(row, "trade_name"),
                SUBSTANCE_TYPE,
                "",                                   # เลขที่ใบอนุญาต
                None,                                 # จำนวนที่ขอซื้อ
                None,                                 # จำนวนที่อนุมัติ
                None,                                 # ยอดเงินที่อนุมัติ
                _date(row),
                None,                                 # วันที่อนุมัติ
                "",                                   # ชื่อสถานพยาบาล
                "",                                   # ที่ตั้ง
                "",                                   # จังหวัด
                "",                                   # ผู้ดำเนินกิจการ
                "",                                   # เลขที่ใบแจ้งหนี้/ใบเสร็จ
                None,                                 # วันที่ออกใบแจ้งหนี้/ใบเสร็จ
            ]
        )
        written = sheet[sheet.max_row]
        written[8].number_format = _DATE_FORMAT
        for index, cell in enumerate(written, start=1):
            if index not in sourced:
                cell.fill = _EMPTY_FILL
        if _value(row, "needs_review"):
            for index in sourced:
                written[index - 1].fill = _REVIEW_FILL

    _finish(sheet, OUTPUT_COLUMNS)


def _write_ledger(sheet: Worksheet, rows) -> None:
    _header(sheet, LEDGER_COLUMNS)
    for row in rows:
        notes = _value(row, "review_notes") or []
        sheet.append(
            [
                _value(row, "page_number"),
                _value(row, "form_code"),
                _date(row),
                _value(row, "generic_name"),
                _value(row, "trade_name"),
                _value(row, "batch_no"),
                _value(row, "manufacturer"),
                _value(row, "received_from"),
                _value(row, "issued_to"),
                _value(row, "recipient_id"),
                _value(row, "prescription_no"),
                _quantity(row, form_layout.BALANCE_BROUGHT),
                _quantity(row, form_layout.RECEIVED),
                _quantity(row, form_layout.ISSUED),
                _quantity(row, form_layout.BALANCE),
                _value(row, "unit"),
                _value(row, "remark"),
                _confidence(row),
                "ใช่" if _value(row, "needs_review") else "",
                "; ".join(notes),
            ]
        )
        written = sheet[sheet.max_row]
        written[2].number_format = _DATE_FORMAT
        if _value(row, "needs_review"):
            for cell in written:
                cell.fill = _REVIEW_FILL

    _finish(sheet, LEDGER_COLUMNS)


def _write_pages(sheet: Worksheet, pages) -> None:
    _header(sheet, PAGE_COLUMNS)
    for page in pages:
        data = page.as_dict() if hasattr(page, "as_dict") else dict(page)
        sheet.append(
            [
                data.get("page"),
                data.get("form"),
                data.get("rows"),
                data.get("skew"),
                data.get("confidence"),
                data.get("error"),
            ]
        )
        if data.get("error"):
            for cell in sheet[sheet.max_row]:
                cell.fill = _REVIEW_FILL
    _finish(sheet, PAGE_COLUMNS)


# --- value access ----------------------------------------------------------
#
# The same workbook is built from the parser's LedgerRow (cells keyed by role)
# and from the stored BalanceEntry (one attribute per column), so every read
# goes through these.


def _value(row, name: str):
    if hasattr(row, name):
        return getattr(row, name) or ("" if name != "needs_review" else False)
    cells = getattr(row, "cells", None)
    return (cells or {}).get(name, "")


def _quantity(row, role: str):
    quantities = getattr(row, "quantities", None)
    if quantities is not None:
        return quantities.get(role)
    return getattr(row, role, None)


def _date(row):
    return getattr(row, "entry_date", None)


def _confidence(row):
    value = getattr(row, "confidence", None)
    return float(value) if value is not None else None


# --- sheet furniture -------------------------------------------------------


def _header(sheet: Worksheet, columns: Sequence[tuple[str, int]]) -> None:
    sheet.append([title for title, _ in columns])
    for index, (_, width) in enumerate(columns, start=1):
        cell = sheet.cell(row=1, column=index)
        cell.fill = _HEADER_FILL
        cell.font = _HEADER_FONT
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        sheet.column_dimensions[get_column_letter(index)].width = width
    sheet.row_dimensions[1].height = 30


def _finish(sheet: Worksheet, columns: Sequence[tuple[str, int]]) -> None:
    sheet.freeze_panes = "A2"
    sheet.auto_filter.ref = (
        f"A1:{get_column_letter(len(columns))}{max(sheet.max_row, 1)}"
    )
