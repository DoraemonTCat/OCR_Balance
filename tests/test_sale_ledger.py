"""The pharmacy's typed sale ledger (บ.ว.จ. ๒-ขพ / บ.ว.จ.-คค 1), end to end
without the OCR engine.

Every property here comes from ``ต.ย. OCR.pdf``: the pages arrive sideways, the
table is ruled under every entry, the date is typed only on the first sale of
each day, the drug is named once in the header, and the last ruled line is the
"รวม" total.
"""
import datetime as dt

import numpy as np
from django.test import SimpleTestCase, override_settings

from workers import balance_normalizer as norm
from workers import balance_parser, cell_ocr, form_layout, grid_detector
from workers.balance_parser import LedgerRow, parse_page

WIDTH, HEIGHT = 2338, 1654
TOP, BOTTOM = 348, 1501
LEFT, RIGHT = 145, 2203
ROW = 36


def _x_rules():
    signature = form_layout.BVJ_SALE.signature
    rules, x = [LEFT], float(LEFT)
    for share in signature:
        x += share * (RIGHT - LEFT)
        rules.append(round(x))
    rules[-1] = RIGHT
    return rules


X_RULES = _x_rules()
Y_RULES = list(range(TOP, BOTTOM + 1, ROW))


def _ledger(header_ink=True):
    """A ruled sale ledger, upright, with a block of 'printed text' above it."""
    import cv2

    page = np.full((HEIGHT, WIDTH, 3), 255, dtype=np.uint8)
    for x in X_RULES:
        cv2.line(page, (x, TOP), (x, Y_RULES[-1]), (0, 0, 0), 2)
    for y in Y_RULES:
        cv2.line(page, (LEFT, y), (RIGHT, y), (0, 0, 0), 2)
    if header_ink:
        for y in range(80, 320, 40):
            for x in range(150, 1600, 60):
                cv2.rectangle(page, (x, y), (x + 40, y + 18), (0, 0, 0), -1)
    return page


class _Line:
    def __init__(self, text, x0, y0, x1, y1, confidence=0.95):
        self.text = text
        self.bbox = (float(x0), float(y0), float(x1), float(y1))
        self.confidence = confidence
        self.order = 0


class _Page:
    def __init__(self, lines):
        for index, line in enumerate(
            sorted(lines, key=lambda b: (round(b.bbox[1], 1), b.bbox[0]))
        ):
            line.order = index
        self.lines = lines
        self.confidence = 0.95


def _cell(text, column, row):
    """A box inside the given column of the given ruled row (0 = header)."""
    x0, x1 = X_RULES[column] + 10, X_RULES[column + 1] - 10
    y0 = Y_RULES[row] + 8
    return _Line(text, x0, y0, x1, y0 + 20)


HEADER = [
    _Line("แบบ บว จ ๒-ขพ", 220, 61, 363, 82),
    _Line("ซื่อวัตถุออกฤทธิ์/วัตถุตำรับและความแรงต่อหน่วย", 143, 269, 534, 300),
    _Line("ซื่อการด้า (ถ้ามี)", 816, 272, 957, 300),
    _Line("Pseudoephedinne ( 60 mg/tab)", 981, 275, 1274, 300),
]

TABLE = [
    _cell("ยอดยกมา", 4, 1), _cell("รับ", 5, 1), _cell("จ่าย", 6, 1), _cell("คงเหลือ", 7, 1),
    # The opening line: brought forward and received, no buyer.
    _cell("15 มกราคม 2567", 0, 2), _cell("0", 4, 2), _cell("2000", 5, 2),
    # The first sale of the 17th, then a second sale the same day, undated.
    _cell("17 มกราคม 2567", 0, 3), _cell("นาย เรืองฤทธิ์ ปัญญาคม", 1, 3),
    _cell("3 3301 01087 14 6", 2, 3), _cell("90", 6, 3),
    _cell("นาย สนอง ทองปลูก", 1, 4), _cell("5 3301 00033 53 7", 2, 4), _cell("60", 6, 4),
    # The total line.
    _cell("รวม", 2, 6), _cell("0", 4, 6), _cell("2000", 5, 6), _cell("150", 6, 6),
    _cell("1850", 7, 6),
]


def _parse(lines=HEADER + TABLE, image=None):
    image = _ledger() if image is None else image

    def cell_reader(straight, grid, **_):
        return _Page(list(lines))

    def page_ocr(straight):  # pragma: no cover - the ruled path must be taken
        raise AssertionError("a ruled table is read cell by cell")

    return parse_page(image, 1, page_ocr, cell_ocr=cell_reader)


@override_settings(OCR={"LANGUAGE": "th"})
class SaleLedgerParseTests(SimpleTestCase):
    def test_identifies_the_layout_and_reads_the_printed_code(self):
        parse = _parse()
        self.assertIs(parse.form, form_layout.BVJ_SALE)
        # "บว จ ๒-ขพ" as recognised, matched to the code as it is spelt.
        self.assertEqual(parse.form_code, "บ.ว.จ. ๒-ขพ")

    def test_one_row_per_entry_and_no_total_line(self):
        rows = _parse().rows
        self.assertEqual(len(rows), 3)
        self.assertEqual(rows[1].cells[form_layout.ISSUED_TO], "นาย เรืองฤทธิ์ ปัญญาคม")
        self.assertEqual(rows[2].quantities[form_layout.ISSUED], 60)

    def test_opening_line_keeps_its_figures(self):
        opening = _parse().rows[0]
        self.assertEqual(opening.quantities[form_layout.BALANCE_BROUGHT], 0)
        self.assertEqual(opening.quantities[form_layout.RECEIVED], 2000)

    def test_undated_sale_takes_the_date_above(self):
        rows = _parse().rows
        self.assertEqual(rows[2].entry_date, dt.date(2024, 1, 17))
        self.assertEqual(rows[2].cells[form_layout.DATE], "17 มกราคม 2567")
        self.assertNotIn("date could not be read", rows[2].review_notes)

    def test_trade_name_comes_from_the_header(self):
        for row in _parse().rows:
            self.assertEqual(row.cells[form_layout.TRADE_NAME], "Pseudoephedinne (60 mg/tab)")

    def test_page_is_turned_upright_first(self):
        sideways = np.ascontiguousarray(np.rot90(_ledger(), -1))  # clockwise
        parse = _parse(image=sideways)
        self.assertEqual(parse.rotation, 270)
        self.assertEqual(len(parse.rows), 3)


class OrientTests(SimpleTestCase):
    def test_upright_page_is_left_alone(self):
        page = _ledger()
        turned, rotation = grid_detector.orient(page)
        self.assertEqual(rotation, 0)
        self.assertEqual(turned.shape, page.shape)

    def test_sideways_page_is_turned_back(self):
        for k in (1, -1):
            turned, rotation = grid_detector.orient(np.rot90(_ledger(), k))
            self.assertEqual(turned.shape[:2], (HEIGHT, WIDTH))
            # Upright means the printed text is back above the table.
            self.assertGreater(turned[:TOP].min(axis=2).size, 0)
            self.assertFalse(grid_detector._upside_down(turned))

    def test_upside_down_page_is_turned_over(self):
        turned, rotation = grid_detector.orient(np.rot90(_ledger(), 2))
        self.assertEqual(rotation, 180)
        self.assertFalse(grid_detector._upside_down(turned))

    def test_page_with_no_header_is_not_turned_over(self):
        _, rotation = grid_detector.orient(_ledger(header_ink=False))
        self.assertEqual(rotation, 0)

    def test_finds_nine_columns_and_every_row_rule(self):
        _, grid = grid_detector.detect(_ledger())
        self.assertEqual(grid.columns, 9)
        self.assertTrue(cell_ocr.is_ruled(grid))
        self.assertIs(form_layout.identify(grid.column_widths), form_layout.BVJ_SALE)


class ThaiMonthTests(SimpleTestCase):
    def test_full_month_names(self):
        self.assertEqual(
            norm.clean_thai_date("15 มกราคม 2567"), (dt.date(2024, 1, 15), True)
        )
        self.assertEqual(
            norm.clean_thai_date("29 กุมภาพันธ์ 2567"), (dt.date(2024, 2, 29), True)
        )

    def test_one_mark_out_of_place_still_names_the_month(self):
        value, certain = norm.clean_thai_date("17 กุมภาพันธุ์ 2567")
        self.assertEqual(value, dt.date(2024, 2, 17))
        self.assertTrue(certain)

    def test_loosely_misread_month_is_matched_but_marked_uncertain(self):
        value, certain = norm.clean_thai_date("17 กุมภพัน 2567")
        self.assertEqual(value, dt.date(2024, 2, 17))
        self.assertFalse(certain)

    def test_date_text_is_rewritten_from_the_reading(self):
        self.assertEqual(
            norm.thai_date_text("1 1 กุมภาพันธุ์ 2567", dt.date(2024, 2, 11)),
            "11 กุมภาพันธ์ 2567",
        )

    def test_abbreviations_still_work(self):
        self.assertEqual(norm.clean_thai_date("3 ม.ค.68"), (dt.date(2025, 1, 3), True))


class SettleDatesTests(SimpleTestCase):
    def _row(self, page, date):
        row = LedgerRow(page_number=page, row_index=0, form_code="x")
        row.entry_date, row.date_certain = date, True
        return row

    def test_each_page_keeps_its_own_month(self):
        # One submission bundling January's and February's ledgers: neither
        # month's dates may be discarded for disagreeing with the other.
        rows = [self._row(1, dt.date(2024, 1, d)) for d in (15, 17, 18)]
        rows += [self._row(2, dt.date(2024, 2, d)) for d in (1, 2, 3, 5, 6)]
        balance_parser.settle_dates(rows)
        self.assertTrue(all(row.entry_date is not None for row in rows))


class CellSplittingTests(SimpleTestCase):
    def _line_mask(self, gaps):
        """A text line of blocks separated by the given gaps (px)."""
        mask = np.zeros((30, 600), dtype=np.uint8)
        x = 5
        for gap in gaps + [None]:
            mask[5:25, x : x + 40] = 255
            x += 40 + (gap or 0)
        return mask

    def test_word_gap_splits_but_letter_gap_does_not(self):
        spans = cell_ocr._word_spans(self._line_mask([2, 12, 3]), 30)
        self.assertEqual(len(spans), 2)

    def test_dotted_leader_is_erased_but_a_full_stop_is_kept(self):
        mask = np.zeros((40, 400), dtype=np.uint8)
        for x in range(20, 120, 10):  # a leader
            mask[30:33, x : x + 3] = 255
        mask[30:33, 300:303] = 255  # a lone full stop
        mask[10:33, 200:240] = 255  # a glyph
        cleaned = cell_ocr._erase_leaders(mask)
        self.assertEqual(cleaned[:, 15:125].sum(), 0)
        self.assertGreater(cleaned[:, 295:310].sum(), 0)
        self.assertGreater(cleaned[:, 195:245].sum(), 0)

    def test_words_of_one_cell_come_back_as_one_spaced_box(self):
        import cv2

        page = _ledger(header_ink=False)
        # Two "words" in the name cell of row 3.
        y = Y_RULES[3] + 10
        x = X_RULES[1] + 20
        # Glyphs, not solid bars: a long enough solid bar is a rule.
        cv2.putText(page, "abc", (x, y + 18), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 0, 0), 2)
        cv2.putText(page, "defg", (x + 90, y + 18), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 0, 0), 2)
        _, grid = grid_detector.detect(page)

        seen = []

        def recognizer(crops):
            seen.extend(crops)
            return [(f"w{i}", 0.9) for i in range(len(crops))]

        result = cell_ocr.recognize(page, grid, recognizer)
        self.assertEqual(len(seen), 2)
        self.assertEqual([line.text for line in result.lines], ["w0 w1"])
        x0, _, x1, _ = result.lines[0].bbox
        self.assertGreater(x0, X_RULES[1])
        self.assertLess(x1, X_RULES[2])


class IdAndStrengthFormatTests(SimpleTestCase):
    def test_a_split_id_is_regrouped_the_way_the_card_prints_it(self):
        self.assertEqual(norm.thai_id_text("3 3301 01 1 15 41 7"), "3 3301 01115 41 7")
        self.assertEqual(norm.thai_id_text("3 3301 01087 14 6"), "3 3301 01087 14 6")

    def test_an_id_written_as_one_run_stays_one_run(self):
        self.assertEqual(norm.thai_id_text("3440600130976"), "3440600130976")

    def test_an_id_with_words_or_wrong_length_is_left_alone(self):
        self.assertEqual(
            norm.thai_id_text("เลขที่ ปชช 1112223334440"), "เลขที่ ปชช 1112223334440"
        )
        self.assertEqual(norm.thai_id_text("12345 678"), "12345 678")

    def test_strength_gets_its_space_back(self):
        header = [
            _Line("ชื่อการค้า (ถ้ามี)", 816, 272, 957, 300),
            _Line("Clonaril ......(2mg/tab)", 981, 275, 1274, 300),
        ]
        self.assertEqual(
            balance_parser._header_value(header, balance_parser._TRADE_NAME_LABEL),
            "Clonaril (2 mg/tab)",
        )

    def test_rule_edges_read_as_marks_are_dropped(self):
        self.assertEqual(norm.thai_id_text("1 3399 00259 35 7 ."), "1 3399 00259 35 7")
        self.assertEqual(norm.thai_id_text("1 3399 00190 57 8 |"), "1 3399 00190 57 8")
        self.assertEqual(norm.thai_id_text("|"), "")
