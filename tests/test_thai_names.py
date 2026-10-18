"""Combining several readings of a Thai name - workers/thai_names.py.

The readers are stubs returning the readings the real engines gave on
ต.ย. OCR.pdf, so these run without PaddleOCR or Tesseract.
"""
from unittest import mock

from django.test import SimpleTestCase, override_settings

from workers import tesseract_engine, thai_names


def _reader(*texts):
    return lambda crops: list(texts)


class CleanTests(SimpleTestCase):
    def test_misread_titles_are_put_right(self):
        self.assertEqual(thai_names.clean("น.ส, สุบิน ทองปลูก"), "น.ส. สุบิน ทองปลูก")
        self.assertEqual(thai_names.clean("เด็กขายวีระ ชัยเชิดชู"), "เด็กชายวีระ ชัยเชิดชู")
        self.assertEqual(thai_names.clean("นจิงสาวปรียานันท์"), "นางสาวปรียานันท์")

    def test_abbreviated_title_that_lost_its_stops_gets_them_back(self):
        self.assertEqual(thai_names.clean("นส วาริณี นามมนตรี"), "น.ส. วาริณี นามมนตรี")
        self.assertEqual(thai_names.clean("ดช. สมชาย"), "ด.ช. สมชาย")
        # A name that happens to start with those letters is not a title.
        self.assertEqual(thai_names.clean("นสรี ดีใจ"), "นสรี ดีใจ")

    def test_stray_punctuation_goes_but_abbreviations_stay(self):
        self.assertEqual(thai_names.clean("นาย โนน . ปาระมัด"), "นาย โนน ปาระมัด")
        self.assertEqual(thai_names.clean("นาย คำเขียน- สิงห์สวัสดิ"), "นาย คำเขียน สิงห์สวัสดิ")
        self.assertEqual(thai_names.clean("น.ส.. วาริณี นามมนตรี"), "น.ส. วาริณี นามมนตรี")
        self.assertEqual(thai_names.clean("ส.ต.อ. พงศ์ธร ใบชา"), "ส.ต.อ. พงศ์ธร ใบชา")

    def test_trailing_noise_is_dropped(self):
        self.assertEqual(thai_names.clean("นาย บุญถัน บุญส่ง '"), "นาย บุญถัน บุญส่ง")
        self.assertEqual(thai_names.clean("น.ส. ลำไย อ้วนกลม 0"), "น.ส. ลำไย อ้วนกลม")


class RoverTests(SimpleTestCase):
    def test_majority_wins_per_character(self):
        voted = thai_names.rover(
            ["นาง ประยงค์ ซิตชม", "นาง ประยงค์ ชิดชม", "นาง ประยงค์ ชิดชม"],
            [1.1, 1.0, 0.9],
        )
        self.assertEqual(voted, "นาง ประยงค์ ชิดชม")

    def test_first_reading_breaks_a_tie(self):
        voted = thai_names.rover(["นาย ณัฐพล", "นาย ญัฐพล"], [1.1, 1.0])
        self.assertEqual(voted, "นาย ณัฐพล")


class ReadTests(SimpleTestCase):
    @mock.patch.object(thai_names, "_model", return_value=None)
    def test_agreement_is_reported_per_name(self, _):
        names = thai_names.read(
            [object(), object()],
            [
                (_reader("นาย สนอง ทองปลูก", "นาง วิลา บุญโต"), 1.1),
                (_reader("นาย สนอง ทองปลูก", "นาง วิลา บุญเต"), 1.0),
                (_reader("นาย สนอง ทองปลูก", "นาง วิลา บุญโต"), 0.9),
            ],
        )
        self.assertEqual(names[0], ("นาย สนอง ทองปลูก", 1.0))
        self.assertEqual(names[1][0], "นาง วิลา บุญโต")
        self.assertAlmostEqual(names[1][1], 2 / 3)

    @mock.patch.object(thai_names, "_model", return_value=None)
    def test_a_failing_reader_is_skipped(self, _):
        def broken(crops):
            raise RuntimeError("tesseract crashed")

        names = thai_names.read([object()], [(_reader("นาย กอน บุญทาทอง"), 1.1), (broken, 1.0)])
        self.assertEqual(names, [("นาย กอน บุญทาทอง", 1.0)])


class RespellTests(SimpleTestCase):
    def test_look_alike_letters_follow_thai_spelling(self):
        model = thai_names._CharModel({"ชิดชม", "ธิดา", "ชลธิดา"}, {"ธิดา"})
        with mock.patch.object(thai_names, "_model", return_value=model):
            self.assertEqual(
                thai_names.respell("น.ส. ชลธิตา", ["น.ส. ชลธิตา"]), "น.ส. ชลธิดา"
            )

    def test_other_letters_are_never_changed(self):
        model = thai_names._CharModel({"สมชาย"}, set())
        with mock.patch.object(thai_names, "_model", return_value=model):
            # ก is in no look-alike set, so "สมกาย" cannot become "สมชาย".
            self.assertEqual(thai_names.respell("นาย สมกาย", ["นาย สมกาย"]), "นาย สมกาย")


class TesseractTests(SimpleTestCase):
    @override_settings(OCR={"TESSERACT_CMD": "", "TESSDATA_DIR": "/nowhere"})
    def test_missing_models_mean_unavailable(self):
        self.assertFalse(tesseract_engine.available("tha"))

    @override_settings(OCR={"TESSERACT_CMD": "", "TESSDATA_DIR": "/nowhere"})
    def test_no_program_raises_instead_of_guessing(self):
        with mock.patch.object(tesseract_engine, "command", return_value=None):
            with self.assertRaises(tesseract_engine.TesseractUnavailable):
                tesseract_engine.recognize_lines([object()], "tha")
