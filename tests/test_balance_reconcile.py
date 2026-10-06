"""Filling the recogniser's gaps from the ledger's own arithmetic.

A page of these ledgers is one running account, which gives two relations the
extraction can lean on without consulting anything outside the document:
``ยอดยกมา + รับ - จ่าย = คงเหลือ`` within a line, and this line's ยอดยกมา is the
previous line's คงเหลือ.

The rule these tests hold to is that both are used only to fill a cell that
could not be read. A figure the recogniser did produce is never replaced,
however unlikely it looks - a wrong value the caller can see disagreeing with
the arithmetic is worth more than a plausible one this code invented.
"""
import datetime as dt

from django.test import SimpleTestCase

from workers import balance_parser, form_layout

BROUGHT = form_layout.BALANCE_BROUGHT
RECEIVED = form_layout.RECEIVED
ISSUED = form_layout.ISSUED
BALANCE = form_layout.BALANCE


def _row(brought=None, received=None, issued=None, balance=None, unread=()):
    row = balance_parser.LedgerRow(page_number=1, row_index=0, form_code="ร.ค.-๔")
    row.quantities = {
        BROUGHT: brought,
        RECEIVED: received,
        ISSUED: issued,
        BALANCE: balance,
    }
    row.unread = set(unread)
    return row


class DeriveTests(SimpleTestCase):
    def test_derives_a_closing_balance_that_could_not_be_read(self):
        row = _row(brought=450, issued=40, unread=[BALANCE])
        balance_parser.reconcile([row])
        self.assertEqual(row.quantities[BALANCE], 410)

    def test_derives_an_opening_balance_from_the_closing_one(self):
        row = _row(issued=40, balance=410, unread=[BROUGHT])
        balance_parser.reconcile([row])
        self.assertEqual(row.quantities[BROUGHT], 450)

    def test_derives_the_quantity_issued(self):
        row = _row(brought=450, balance=410, unread=[ISSUED])
        balance_parser.reconcile([row])
        self.assertEqual(row.quantities[ISSUED], 40)

    def test_says_so_whenever_it_fills_a_cell(self):
        row = _row(brought=450, issued=40, unread=[BALANCE])
        balance_parser.reconcile([row])
        self.assertTrue(row.needs_review)
        self.assertTrue(any("derived" in note for note in row.review_notes))

    def test_two_gaps_are_left_alone(self):
        row = _row(issued=40, unread=[BROUGHT, BALANCE])
        balance_parser.reconcile([row])
        self.assertIsNone(row.quantities[BALANCE])
        self.assertIsNone(row.quantities[BROUGHT])

    def test_does_not_invent_a_negative_stock(self):
        row = _row(brought=10, issued=40, unread=[BALANCE])
        balance_parser.reconcile([row])
        self.assertIsNone(row.quantities[BALANCE])


class CarryForwardTests(SimpleTestCase):
    def test_carries_the_previous_closing_balance_forward(self):
        first = _row(brought=450, issued=40, balance=410)
        second = _row(issued=20, balance=390, unread=[BROUGHT])
        balance_parser.reconcile([first, second])
        self.assertEqual(second.quantities[BROUGHT], 410)

    def test_a_broken_line_does_not_carry_a_stale_balance(self):
        # The middle line's closing balance could not be read, so the third
        # line has nothing to inherit and must stay empty rather than take the
        # first line's figure.
        first = _row(brought=450, issued=40, balance=410)
        second = _row(brought=410, issued=20, unread=[BALANCE, RECEIVED])
        third = _row(issued=10, unread=[BROUGHT, BALANCE])
        balance_parser.reconcile([first, second, third])
        self.assertIsNone(third.quantities[BROUGHT])


class NeverOverwriteTests(SimpleTestCase):
    def test_keeps_a_figure_that_was_read_even_when_it_does_not_add_up(self):
        row = _row(brought=450, issued=40, balance=999)
        balance_parser.reconcile([row])
        self.assertEqual(row.quantities[BALANCE], 999)
        self.assertTrue(any("does not add up" in n for n in row.review_notes))

    def test_a_dash_is_a_zero_and_not_a_gap(self):
        # received is None because the form has a dash there, which means no
        # movement. Nothing is derived for it and nothing is flagged.
        row = _row(brought=450, received=None, issued=40, balance=410)
        balance_parser.reconcile([row])
        self.assertIsNone(row.quantities[RECEIVED])
        self.assertEqual(row.review_notes, [])

    def test_a_line_that_adds_up_is_left_untouched(self):
        row = _row(brought=450, issued=40, balance=410)
        balance_parser.reconcile([row])
        self.assertFalse(row.needs_review)


def _dated(page, cell, date=None, certain=False):
    row = balance_parser.LedgerRow(page_number=page, row_index=0, form_code="ร.ค.-๔")
    row.cells = {form_layout.DATE: cell}
    row.entry_date = date
    row.date_certain = certain
    return row


class SettleDatesTests(SimpleTestCase):
    """The month comes from the whole document, the day from the cell.

    The slashes of a date are the thinnest marks on these forms and the first
    thing the recogniser loses, so "16/3/69" comes back as "16369" or "163769"
    and splitting the digits on their own is guesswork. These are monthly
    returns, which removes it: the month is fixed, and the entries run in date
    order down each page.
    """

    CLEAN = ("16/3/69", dt.date(2026, 3, 16))

    def test_discards_a_date_from_another_month(self):
        clean = _dated(1, *self.CLEAN, certain=True)
        stray = _dated(1, "/3769", dt.date(2026, 7, 3))
        balance_parser.settle_dates([clean, stray])
        self.assertIsNone(stray.entry_date)
        self.assertTrue(any("not in the month" in n for n in stray.review_notes))

    def test_recovers_a_day_that_can_only_be_read_one_way(self):
        clean = _dated(1, *self.CLEAN, certain=True)
        # "287376": 28 is a day and falls after the 16th; 2 falls before it.
        damaged = _dated(1, "287376")
        balance_parser.settle_dates([clean, damaged])
        self.assertEqual(damaged.entry_date, dt.date(2026, 3, 28))
        self.assertTrue(any("recovered" in n for n in damaged.review_notes))

    def test_leaves_an_ambiguous_day_empty(self):
        # "3169" is the 3rd or the 31st; neither leaves the month's digit where
        # it would have to be, so nothing chooses between them. It comes first
        # on the page, so the running day rules nothing out either.
        damaged = _dated(1, "3169")
        clean = _dated(1, *self.CLEAN, certain=True)
        balance_parser.settle_dates([damaged, clean])
        self.assertIsNone(damaged.entry_date)

    def test_a_page_with_no_clean_date_is_settled_by_the_others(self):
        # Page 4 of the sample contains no cleanly read date at all; without
        # looking across the document nothing on it could be dated.
        clean = _dated(1, *self.CLEAN, certain=True)
        elsewhere = _dated(4, "287376")
        balance_parser.settle_dates([clean, elsewhere])
        self.assertEqual(elsewhere.entry_date, dt.date(2026, 3, 28))

    def test_each_page_restarts_the_running_day(self):
        clean = _dated(1, "28/3/69", dt.date(2026, 3, 28), certain=True)
        fresh = _dated(4, "373169")  # the 3rd, not "earlier than" anything yet
        balance_parser.settle_dates([clean, fresh])
        self.assertEqual(fresh.entry_date, dt.date(2026, 3, 3))

    def test_does_nothing_without_a_single_clean_reading(self):
        only = _dated(1, "287376")
        balance_parser.settle_dates([only])
        self.assertIsNone(only.entry_date)

    def test_an_uncertain_date_does_not_decide_the_month(self):
        # A substituted glyph is not evidence of which month these returns are
        # for; only a clean reading is.
        guess = _dated(1, "1%/7/69", dt.date(2026, 7, 18))
        balance_parser.settle_dates([guess])
        self.assertEqual(guess.entry_date, dt.date(2026, 7, 18))
