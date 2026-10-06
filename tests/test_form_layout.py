"""Form identification from column geometry.

The signatures used here are the ones measured on ``ข้อมูล OCR.PDF`` at 200 dpi
by ``grid_detector.detect``; the absolute widths are what the detector really
returned, so the tests exercise the normalisation too.
"""
from django.test import SimpleTestCase

from workers import form_layout
from workers.form_layout import FormNotRecognised

#: Absolute column widths as detected, page by page.
PAGE_1_RVJ = [169, 329, 167, 210, 147, 169, 167, 126, 148, 167, 147, 146]
PAGE_2_BVJ = [94, 246, 172, 206, 168, 233, 360, 159, 140, 140, 140, 100]
PAGE_3_BVJ = [87, 227, 159, 192, 156, 216, 334, 148, 130, 129, 131, 92]
PAGE_4_RK = [154, 300, 196, 173, 191, 192, 153, 115, 115, 134, 153]


class IdentifyTests(SimpleTestCase):
    def test_identifies_the_monthly_report(self):
        self.assertIs(form_layout.identify(PAGE_1_RVJ), form_layout.RVJ_7_4)

    def test_identifies_the_dispensing_ledger(self):
        self.assertIs(form_layout.identify(PAGE_2_BVJ), form_layout.BVJ_7_4_KP)

    def test_scale_does_not_matter(self):
        # Page 3 is the same form at a smaller scan; only the proportions count.
        self.assertIs(form_layout.identify(PAGE_3_BVJ), form_layout.BVJ_7_4_KP)

    def test_identifies_the_possession_report(self):
        self.assertIs(form_layout.identify(PAGE_4_RK), form_layout.RK_4)

    def test_unknown_column_count_is_rejected(self):
        with self.assertRaises(FormNotRecognised):
            form_layout.identify([100] * 5)

    def test_wrong_proportions_are_rejected(self):
        # Eleven equal columns: the right count for ร.ค.-๔, the wrong shape.
        with self.assertRaises(FormNotRecognised):
            form_layout.identify([100] * 11)


class LayoutTests(SimpleTestCase):
    def test_every_form_lists_one_role_per_signature_column(self):
        for form in form_layout.FORMS:
            self.assertEqual(
                len(form.columns), len(form.signature), f"{form.code} is inconsistent"
            )

    def test_every_form_has_the_four_quantities(self):
        for form in form_layout.FORMS:
            self.assertTrue(
                form_layout.NUMERIC_ROLES.issubset(set(form.columns)), form.code
            )

    def test_no_form_repeats_a_role(self):
        for form in form_layout.FORMS:
            self.assertEqual(len(set(form.columns)), len(form.columns), form.code)

    def test_role_at_tolerates_a_missing_column(self):
        self.assertIsNone(form_layout.RK_4.role_at(None))
        self.assertIsNone(form_layout.RK_4.role_at(99))

    def test_index_of_finds_a_role(self):
        self.assertEqual(form_layout.RK_4.index_of(form_layout.DATE), 0)
        self.assertIsNone(form_layout.RK_4.index_of(form_layout.RECIPIENT_ID))
