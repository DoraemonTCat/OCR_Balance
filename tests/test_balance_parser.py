"""Row assembly, driven by a stub OCR so the engine is not in the loop.

The boxes in these tests are placed at the coordinates the real recogniser
returned for ``ข้อมูล OCR.PDF`` page 2, including the two things that break the
obvious approach: the date written a third of a line above its own quantities,
and a drug name wrapped onto a second line.
"""
import datetime as dt

import numpy as np
from django.test import SimpleTestCase

from workers import balance_parser, form_layout
from workers.balance_parser import parse_page

WIDTH, HEIGHT = 2400, 1500
TOP, HEADER, BOTTOM = 477, 620, 1260
#: The twelve บ.ว.จ ๗/๔-ขพ columns, as detected on page 2.
X_RULES = [114, 208, 454, 626, 832, 1000, 1233, 1593, 1752, 1892, 2032, 2172, 2272]


class _Line:
    """Stands in for ``pdf_extractor.TextLine``."""

    def __init__(self, text, x, y, width=120, height=45, confidence=0.9):
        self.text = text
        self.bbox = (float(x), float(y), float(x + width), float(y + height))
        self.confidence = confidence


class _Page:
    def __init__(self, lines):
        self.lines = lines
        self.confidence = 0.8


def _form_image():
    """A บ.ว.จ ๗/๔-ขพ grid, ruled the way the real scans are."""
    import cv2

    page = np.full((HEIGHT, WIDTH, 3), 255, dtype=np.uint8)
    for x in X_RULES:
        cv2.line(page, (x, TOP), (x, BOTTOM), (0, 0, 0), 3)
    for y in (TOP, HEADER, BOTTOM):
        cv2.line(page, (X_RULES[0], y), (X_RULES[-1], y), (0, 0, 0), 3)
    return page


def _center(column):
    return (X_RULES[column] + X_RULES[column + 1]) / 2 - 60


#: Two entries. The first has its date 35 px above its quantities and its name
#: wrapped onto a second line; the second is written on one baseline.
ENTRY_LINES = [
    _Line("1/3/69", _center(0), 700),
    _Line("Lorazepam", _center(1), 710),
    _Line("1 mg", _center(1), 770),          # the wrap
    _Line("Lorazep", _center(2), 712),
    _Line("T 25 275", _center(3), 714),
    _Line("Asian pharm", _center(4), 714),
    _Line("1wsu6yo1ssp", _center(5), 716),   # Thai, read as Latin noise
    _Line("3440600130976", _center(6), 716),
    _Line("450", _center(7), 735),
    _Line("-", _center(8), 735),
    _Line("40", _center(9), 735),
    _Line("410", _center(10), 735),

    _Line("3/3/69", _center(0), 900),
    _Line("Lorazepam 1 mg", _center(1), 900),
    _Line("Lorazep", _center(2), 900),
    _Line("T25275", _center(3), 900),
    _Line("410", _center(7), 900),
    _Line("-", _center(8), 900),
    _Line("20", _center(9), 900),
    _Line("390", _center(10), 900),
]

#: The printed header: Thai noise in the same columns, but without digits.
HEADER_LINES = [
    _Line("Yu", _center(0), 520),
    _Line("gubuecisuen", _center(1), 520),
    _Line("hnaataumns", _center(6), 530),
    _Line("Mausinn", _center(7), 540),
    _Line("BLO", _center(9), 540),
]

#: The "รวม" total line below the entries: a closing balance and nothing else.
TOTAL_LINES = [_Line("390", _center(10), 1180)]


def _parse(lines):
    page = _Page(lines)
    return parse_page(_form_image(), 2, lambda image: page)


class ParsePageTests(SimpleTestCase):
    def test_identifies_the_form_from_the_grid(self):
        result = _parse(ENTRY_LINES)
        self.assertIs(result.form, form_layout.BVJ_7_4_KP)

    def test_one_row_per_entry(self):
        self.assertEqual(len(_parse(ENTRY_LINES).rows), 2)

    def test_the_printed_header_does_not_become_a_row(self):
        # It sits in the anchor columns but carries no digits there.
        self.assertEqual(len(_parse(HEADER_LINES + ENTRY_LINES).rows), 2)

    def test_the_total_line_does_not_become_a_row(self):
        self.assertEqual(len(_parse(ENTRY_LINES + TOTAL_LINES).rows), 2)

    def test_cells_land_in_their_own_columns(self):
        row = _parse(ENTRY_LINES).rows[0]
        self.assertEqual(row.get(form_layout.TRADE_NAME), "Lorazep")
        self.assertEqual(row.get(form_layout.BATCH_NO), "T25275")
        self.assertEqual(row.get(form_layout.RECIPIENT_ID), "3440600130976")

    def test_a_wrapped_name_joins_its_own_entry(self):
        row = _parse(ENTRY_LINES).rows[0]
        self.assertEqual(row.get(form_layout.GENERIC_NAME), "Lorazepam 1 mg")

    def test_a_date_written_above_its_quantities_stays_on_that_entry(self):
        rows = _parse(ENTRY_LINES).rows
        self.assertEqual(rows[0].entry_date, dt.date(2026, 3, 1))
        self.assertEqual(rows[1].entry_date, dt.date(2026, 3, 3))

    def test_quantities_are_numbers_and_a_dash_is_none(self):
        row = _parse(ENTRY_LINES).rows[0]
        self.assertEqual(row.quantities[form_layout.BALANCE_BROUGHT], 450)
        self.assertEqual(row.quantities[form_layout.ISSUED], 40)
        self.assertEqual(row.quantities[form_layout.BALANCE], 410)
        self.assertIsNone(row.quantities[form_layout.RECEIVED])

    def test_a_row_that_adds_up_is_not_flagged_for_arithmetic(self):
        row = _parse(ENTRY_LINES).rows[0]
        self.assertNotIn(
            "balance does not add up",
            " ".join(row.review_notes),
        )

    def test_a_row_that_does_not_add_up_is_flagged(self):
        lines = [line for line in ENTRY_LINES if line.text != "410"]
        lines.append(_Line("999", _center(10), 735))
        row = _parse(lines).rows[0]
        self.assertTrue(any("does not add up" in note for note in row.review_notes))

    def test_thai_columns_are_flagged_by_column(self):
        row = _parse(ENTRY_LINES).rows[0]
        self.assertTrue(row.needs_review)
        self.assertTrue(
            any("written in Thai" in note for note in row.review_notes),
            row.review_notes,
        )

    def test_header_text_above_the_table_is_kept(self):
        page = _Page(ENTRY_LINES + [_Line("nw1/2569", 300, 200)])
        result = parse_page(_form_image(), 2, lambda image: page)
        self.assertIn("nw1/2569", result.header_text)

    def test_page_number_is_carried_onto_every_row(self):
        for row in _parse(ENTRY_LINES).rows:
            self.assertEqual(row.page_number, 2)
