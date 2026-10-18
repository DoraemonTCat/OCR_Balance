"""A scan with a text layer added by someone else's OCR - Sample (2).pdf.

The file's text is not the truth there: it is another engine's reading. Three
things it does that a typed PDF does not, each reproduced here:

* the space between two words is stored inside a span (" HCl"), or not at all,
  with every word in a block of its own and the boxes overlapping;
* a figure the scan plainly shows is missing from the text;
* (on the report form) a line under "3-31 ม.ค. 68" leaves its date blank.
"""
from django.test import SimpleTestCase

from workers import balance_parser, form_layout, pdf_extractor
from workers.balance_parser import LedgerRow


class _Page:
    """Stands in for a PyMuPDF page: only get_text("dict") is used."""

    def __init__(self, blocks):
        self._blocks = blocks

    def get_text(self, kind):
        assert kind == "dict"
        return {"blocks": self._blocks}


def _block(*lines):
    return {"type": 0, "lines": [{"spans": list(spans)} for spans in lines]}


def _span(text, x0, x1, y0=100.0, y1=110.0):
    return {"text": text, "bbox": (x0, y0, x1, y1), "size": 10, "flags": 0}


class TextBoxSpacingTests(SimpleTestCase):
    def test_space_stored_at_the_start_of_a_span_is_kept(self):
        page = _Page([_block([_span("Methylphenidate", 0, 50), _span(" HCl", 50, 62)])])
        boxes = pdf_extractor.text_boxes(page, 72)
        self.assertEqual([b.text for b in boxes], ["Methylphenidate", "HCl"])
        self.assertTrue(boxes[1].space_before)
        self.assertEqual(balance_parser._join(boxes), "Methylphenidate HCl")

    def test_a_word_in_a_block_of_its_own_is_a_new_word(self):
        # Overlapping boxes: no gap could tell these two words apart.
        page = _Page([_block([_span("พิมพ์", 0, 34)]), _block([_span("เก่งการดี", 32, 69)])])
        boxes = pdf_extractor.text_boxes(page, 72)
        self.assertEqual(balance_parser._join(boxes), "พิมพ์ เก่งการดี")

    def test_fragments_of_one_word_in_one_line_stay_joined(self):
        page = _Page([_block([_span("ม", 0, 4), _span(".", 4, 5), _span("ค.68", 5, 20)])])
        boxes = pdf_extractor.text_boxes(page, 72)
        self.assertEqual(balance_parser._join(boxes), "ม.ค.68")


def _row(index, *, date="", generic="", received=None, issued=None, balance=None):
    row = LedgerRow(page_number=1, row_index=index, form_code="x")
    row.cells = {form_layout.DATE: date, form_layout.GENERIC_NAME: generic}
    row.quantities = {
        form_layout.BALANCE_BROUGHT: None,
        form_layout.RECEIVED: received,
        form_layout.ISSUED: issued,
        form_layout.BALANCE: balance,
    }
    return row


class RunningBalanceTests(SimpleTestCase):
    def test_a_lost_issue_is_recovered_from_the_balances(self):
        rows = [
            _row(0, generic="Methylphenidate", received=2400, balance=2400),
            _row(1, balance=2370),  # the scan shows 30 under จ่าย; the text has none
            _row(2, issued=30, balance=2340),
        ]
        balance_parser.reconcile(rows)
        self.assertEqual(rows[1].quantities[form_layout.ISSUED], 30)
        self.assertTrue(rows[1].needs_review)
        self.assertFalse(rows[2].needs_review)

    def test_the_balance_runs_on_from_a_receipt_with_no_balance(self):
        rows = [
            _row(0, generic="Pseudoephedrine", received=100),
            _row(1, balance=98),
        ]
        balance_parser.reconcile(rows)
        self.assertEqual(rows[1].quantities[form_layout.ISSUED], 2)

    def test_a_new_drug_starts_a_new_account(self):
        rows = [
            _row(0, generic="Methylphenidate", received=2400, balance=2280),
            # A different drug: 98 says nothing about the 2280 above it.
            _row(1, generic="Pseudoephedrine", balance=98),
        ]
        balance_parser.reconcile(rows)
        self.assertIsNone(rows[1].quantities[form_layout.ISSUED])


class DateRangeCarryTests(SimpleTestCase):
    def test_a_blank_date_under_a_range_takes_the_range(self):
        above = _row(0, date="3-31 ม.ค. 68")
        below = _row(1)
        below.flag("date could not be read")
        balance_parser._carry_dates_down([above, below])
        self.assertEqual(below.cells[form_layout.DATE], "3-31 ม.ค. 68")
        self.assertFalse(below.needs_review)
