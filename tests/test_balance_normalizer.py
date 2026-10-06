"""Value cleaning for the handwritten ledgers.

The inputs here are real OCR output from ``ข้อมูล OCR.PDF``, not invented
examples: the point of these functions is to survive what the recogniser
actually produces on handwriting.
"""
import datetime as dt

from django.test import SimpleTestCase

from workers import balance_normalizer as norm


class QuantityTests(SimpleTestCase):
    def test_reads_a_clean_number(self):
        self.assertEqual(norm.clean_quantity("450"), (450, True))

    def test_joins_a_number_the_recogniser_split(self):
        # "45 0" is one numeral whose glyphs were boxed apart, never 45 and 0.
        self.assertEqual(norm.clean_quantity("45 0"), (450, True))

    def test_dash_is_a_certain_absence(self):
        for dash in ("-", "—", "~", ""):
            self.assertEqual(norm.clean_quantity(dash), (None, True))

    def test_substituted_glyphs_are_read_but_not_certain(self):
        value, certain = norm.clean_quantity("3$0")
        self.assertEqual(value, 380)
        self.assertFalse(certain)

    def test_unreadable_cell_is_rejected(self):
        self.assertEqual(norm.clean_quantity("0b E"), (None, False))


class DateTests(SimpleTestCase):
    def test_reads_a_buddhist_era_date(self):
        self.assertEqual(norm.clean_date("16/3/69"), (dt.date(2026, 3, 16), True))

    def test_single_digit_day(self):
        self.assertEqual(norm.clean_date("1/3/69"), (dt.date(2026, 3, 1), True))

    def test_empty_cell_is_not_an_error(self):
        self.assertEqual(norm.clean_date(""), (None, True))

    def test_recovers_a_date_whose_separators_were_lost(self):
        # "1369" can only be 1/3/69, so it is accepted - but not as certain.
        value, certain = norm.clean_date("1369")
        self.assertEqual(value, dt.date(2026, 3, 1))
        self.assertFalse(certain)

    def test_rejects_an_ambiguous_run_of_digits(self):
        # 173169 splits only as 17/31/69, whose month does not exist.
        self.assertEqual(norm.clean_date("173169"), (None, False))

    def test_rejects_an_impossible_date(self):
        self.assertEqual(norm.clean_date("31/2/69"), (None, True))


class BatchTests(SimpleTestCase):
    def test_closes_the_gaps_in_a_batch_number(self):
        self.assertEqual(norm.clean_batch_no("T 25 275"), ("T25275", True))

    def test_uppercases_the_prefix(self):
        self.assertEqual(norm.clean_batch_no("t25275"), ("T25275", True))

    def test_unexpected_shape_is_kept_and_flagged(self):
        value, certain = norm.clean_batch_no("T L5273 T15225")
        self.assertEqual(value, "T L5273 T15225")
        self.assertFalse(certain)


class CorrectedCellTests(SimpleTestCase):
    """A figure struck through and rewritten is two figures, not one number."""

    def test_detects_a_corrected_cell(self):
        # "39 o 410": a 390 struck through, 410 written beside it. Stripping the
        # spaces made 390410, a number nobody wrote.
        self.assertTrue(norm.holds_two_figures("39 o 410"))
        self.assertTrue(norm.holds_two_figures("390 410"))

    def test_a_numeral_boxed_in_pieces_is_one_figure(self):
        for text in ("45 0", "3 80", "9 9 6", "1,000", "390"):
            self.assertFalse(norm.holds_two_figures(text), text)

    def test_an_empty_cell_holds_nothing(self):
        self.assertFalse(norm.holds_two_figures(""))
        self.assertFalse(norm.holds_two_figures("-"))


class ThaiDateTests(SimpleTestCase):
    """The ประเภท ๒ forms write the month in Thai, and sometimes a range."""

    def test_reads_a_thai_month(self):
        self.assertEqual(
            norm.clean_thai_date("3 ม.ค.68"), (dt.date(2025, 1, 3), True)
        )

    def test_the_full_stops_are_optional(self):
        # One form writes "3 มค 68", the other "3 ม.ค.68".
        self.assertEqual(norm.clean_thai_date("3 มค 68")[0], dt.date(2025, 1, 3))

    def test_a_two_digit_day(self):
        self.assertEqual(norm.clean_thai_date("20มค 68")[0], dt.date(2025, 1, 20))

    def test_a_range_is_not_a_failure(self):
        # "3-31 ม.ค. 68" is a month of dispensing on one line. There is no date
        # to give, but nothing failed to be read either.
        self.assertEqual(norm.clean_thai_date("3-31 ม.ค. 68"), (None, True))
        self.assertTrue(norm.is_date_range("3-31 ม.ค. 68"))

    def test_a_cell_with_no_thai_month_is_refused(self):
        # Left for the numeric parser the handwritten forms need.
        self.assertEqual(norm.clean_thai_date("16/3/69"), (None, False))

    def test_an_empty_cell_is_not_an_error(self):
        self.assertEqual(norm.clean_thai_date(""), (None, True))

    def test_a_mangled_cell_is_refused_rather_than_guessed(self):
        self.assertEqual(norm.clean_thai_date("3คม.68.")[0], None)


class UnitInTheQuantityCellTests(SimpleTestCase):
    """The ประเภท ๒ forms write the unit under the figure, same column."""

    def test_the_unit_does_not_make_the_cell_unreadable(self):
        self.assertEqual(norm.clean_quantity("2,400 เม็ด"), (2400, True))
        self.assertEqual(norm.clean_quantity("1,000 ขวด"), (1000, True))

    def test_a_cell_with_only_a_unit_has_no_figure(self):
        self.assertEqual(norm.clean_quantity("เม็ด"), (None, False))

    def test_a_correction_is_still_two_figures(self):
        # Dropping the word must not also drop the second number.
        self.assertTrue(norm.holds_two_figures("39 o 410"))
