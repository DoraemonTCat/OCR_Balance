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
    """Stands in for ``pdf_extractor.TextLine``.

    ``order`` is reading order, which the parser uses to put a cell's fragments
    back together. A recognised page numbers its boxes top-to-bottom and
    left-to-right, so ``_Page`` assigns it the same way rather than making every
    test spell it out.
    """

    def __init__(self, text, x, y, width=120, height=45, confidence=0.9):
        self.text = text
        self.bbox = (float(x), float(y), float(x + width), float(y + height))
        self.confidence = confidence
        self.order = 0


class _Page:
    def __init__(self, lines):
        # Number the boxes the way the OCR engine does: top to bottom, left to
        # right. Nothing in a test has to think about it unless it is the thing
        # being tested.
        for index, line in enumerate(
            sorted(lines, key=lambda b: (round(b.bbox[1], 1), b.bbox[0]))
        ):
            line.order = index
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


class RowSeparationTests(SimpleTestCase):
    """Entries written close together must not chain into one row.

    Grouping measures each box against the group's running centre, so every box
    added drags that centre down and the next entry stays within reach. On
    page 1 of the sample the entries are 60 px apart under a 45 px tolerance,
    and with nothing to stop it they collapse into one row. What stops it is
    that one entry cannot put two values in the same column.
    """

    def _tight_rows(self):
        lines = []
        for index, (brought, issued, balance) in enumerate(
            [("450", "40", "410"), ("410", "20", "390"), ("390", "10", "380")]
        ):
            y = 700 + index * 60
            lines.append(_Line(brought, _center(7), y, height=50))
            lines.append(_Line(issued, _center(9), y + 4, height=50))
            lines.append(_Line(balance, _center(10), y + 2, height=50))
        return lines

    def test_three_tight_entries_stay_three_rows(self):
        rows = _parse(self._tight_rows()).rows
        self.assertEqual(len(rows), 3)
        self.assertEqual(
            [row.quantities[form_layout.BALANCE] for row in rows], [410, 390, 380]
        )

    def test_a_numeral_split_into_two_boxes_is_one_value(self):
        # "45" and "0" side by side on one baseline are 450, not two entries.
        lines = [
            _Line("45", _center(7), 700, width=60),
            _Line("0", _center(7) + 70, 700, width=30),
            _Line("40", _center(9), 700),
            _Line("410", _center(10), 700),
        ]
        rows = _parse(lines).rows
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].quantities[form_layout.BALANCE_BROUGHT], 450)


class AcceptanceTests(SimpleTestCase):
    def test_an_entry_with_only_its_figures_is_kept(self):
        # Neither date nor name could be read; the quantities alone make it an
        # entry. Judging by the identifying text instead threw away five of the
        # eleven rows on page 1 of the sample.
        lines = [
            _Line("410", _center(7), 900),
            _Line("20", _center(9), 900),
            _Line("390", _center(10), 900),
        ]
        rows = _parse(lines).rows
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].quantities[form_layout.BALANCE], 390)

    def test_a_single_figure_is_not_an_entry(self):
        # The "รวม" total line: one closing figure, dashes beside it.
        self.assertEqual(_parse([_Line("390", _center(10), 1180)]).rows, [])

    def test_printed_column_titles_do_not_become_an_entry(self):
        # The Thai headings read as Latin noise and pick up a stray digit, so
        # only their position above the header rule keeps them out.
        header = [
            _Line("gubuecisuen", _center(1), TOP + 20),
            _Line("Mausinn c (nuad.", _center(7), TOP + 30),
            _Line("1", _center(9), TOP + 40),
        ]
        self.assertEqual(len(_parse(header + ENTRY_LINES).rows), 2)


class CorrectedCellTests(SimpleTestCase):
    """A figure struck through and rewritten is two figures, not one number.

    Joining them produced values that were never written - 390410 from a "390"
    corrected to "410", 11000 from a stricken "1,000" - and those were reported
    as if read off the page.
    """

    def test_two_figures_in_one_cell_are_not_concatenated(self):
        # The recogniser returns both in a single box: "39 o 410" for a 390
        # struck through and corrected to 410.
        lines = [
            _Line("39 o 410", _center(7), 700),
            _Line("20", _center(9), 700),
        ]
        row = _parse(lines).rows[0]
        self.assertIsNone(row.quantities[form_layout.BALANCE_BROUGHT])
        self.assertTrue(
            any("corrected by hand" in note for note in row.review_notes),
            row.review_notes,
        )

    def test_a_numeral_boxed_in_pieces_is_still_one_number(self):
        lines = [
            _Line("45", _center(7), 700, width=60),
            _Line("0", _center(7) + 70, 700, width=30),
            _Line("40", _center(9), 700),
            _Line("410", _center(10), 700),
        ]
        row = _parse(lines).rows[0]
        self.assertEqual(row.quantities[form_layout.BALANCE_BROUGHT], 450)

    def test_the_reconciliation_can_still_recover_the_cell(self):
        # The corrected cell is a gap, so the arithmetic may fill it back in.
        lines = [
            _Line("450", _center(7), 700),
            _Line("40", _center(9), 700),
            _Line("390 410", _center(10), 700),
        ]
        row = _parse(lines).rows[0]
        self.assertEqual(row.quantities[form_layout.BALANCE], 410)


class NameTests(SimpleTestCase):
    def test_a_wrapped_fragment_is_not_a_drug_name(self):
        # "1 MX" is the tail of a wrapped "1 mg" that drifted down into the
        # total line; counting alphanumerics let it keep that line as an entry.
        self.assertFalse(balance_parser._looks_like_a_name("1 MX"))

    def test_a_misread_drug_name_still_counts(self):
        for name in ("Lorazepam", "Loraztpqm", "Lorsztpam L"):
            self.assertTrue(balance_parser._looks_like_a_name(name), name)


class RuledRowsTests(SimpleTestCase):
    """Where the table rules its rows, the rules decide where they end.

    A drug name runs onto a second line well below its own figures. The halfway
    point between two rows of figures falls between those two lines, so with
    nothing but the figures to go on the continuation is handed to the entry
    below: one row loses half its name and the next gains a name it never had.
    """

    #: A row rule under each entry, as the typed ประเภท ๒ forms have.
    ROW_RULES = (800, 900, 1000)

    def _ruled_image(self):
        import cv2

        page = _form_image()
        for y in self.ROW_RULES:
            cv2.line(page, (X_RULES[0], y), (X_RULES[-1], y), (0, 0, 0), 3)
        return page

    def _lines(self):
        # Two entries. The first one's name wraps to a second line that sits
        # below the midpoint between the two rows of figures.
        return [
            _Line("Methylphenidate HCl", _center(1), 815, height=40),
            _Line("tablets 10 mg", _center(1), 865, height=40),
            _Line("2,400", _center(7), 820, height=40),
            _Line("2,400", _center(10), 820, height=40),
            _Line("30", _center(9), 920, height=40),
            _Line("2,370", _center(10), 920, height=40),
        ]

    def _parse_ruled(self, lines):
        page = _Page(lines)
        return parse_page(self._ruled_image(), 2, lambda image: page)

    def test_a_wrapped_name_stays_with_its_own_entry(self):
        rows = self._parse_ruled(self._lines()).rows
        self.assertEqual(len(rows), 2)
        self.assertEqual(
            rows[0].get(form_layout.GENERIC_NAME), "Methylphenidate HCl tablets 10 mg"
        )
        self.assertEqual(rows[1].get(form_layout.GENERIC_NAME), "")

    def test_the_figures_still_land_on_their_own_rows(self):
        rows = self._parse_ruled(self._lines()).rows
        self.assertEqual(rows[0].quantities[form_layout.BALANCE], 2400)
        self.assertEqual(rows[1].quantities[form_layout.BALANCE], 2370)


class DateColumnAcceptanceTests(SimpleTestCase):
    def test_a_line_dated_by_a_period_is_still_an_entry(self):
        # "3-31 ม.ค. 68" summarises a month of dispensing. It is not a date, its
        # name belongs to the entry above, and it carries one figure - so only
        # the date column says it is a row at all.
        lines = [
            _Line("3-31 ม.ค. 68", _center(0), 900),
            _Line("98", _center(10), 900),
        ]
        rows = _parse(lines).rows
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].quantities[form_layout.BALANCE], 98)
        self.assertIsNone(rows[0].entry_date)

    def test_the_total_line_is_still_refused(self):
        # No date, no name, one closing figure.
        self.assertEqual(_parse([_Line("390", _center(10), 1180)]).rows, [])
