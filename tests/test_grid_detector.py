"""Table detection on drawn forms.

The grid is drawn here rather than loaded from a fixture so each property is
isolated: a tilted page, a table whose data area carries no horizontal rules
(which is what the real forms look like), and a page with no table at all.
"""
import numpy as np
from django.test import SimpleTestCase

from workers import grid_detector
from workers.grid_detector import GridNotFound

WIDTH, HEIGHT = 2000, 1400
LEFT, RIGHT = 200, 1900
TOP, HEADER, BOTTOM = 300, 420, 1200
#: Twelve columns of unequal width, like the real forms.
X_RULES = [200, 320, 620, 780, 950, 1080, 1250, 1420, 1540, 1650, 1760, 1830, 1900]


def _blank():
    return np.full((HEIGHT, WIDTH, 3), 255, dtype=np.uint8)


def _draw_form(image=None, *, ruled_rows=False):
    """An open table: outer box, header rule, and full-height column rules."""
    import cv2

    page = _blank() if image is None else image
    for x in X_RULES:
        cv2.line(page, (x, TOP), (x, BOTTOM), (0, 0, 0), 3)
    for y in (TOP, HEADER, BOTTOM):
        cv2.line(page, (LEFT, y), (RIGHT, y), (0, 0, 0), 3)
    if ruled_rows:
        for y in range(HEADER + 130, BOTTOM, 130):
            cv2.line(page, (LEFT, y), (RIGHT, y), (0, 0, 0), 3)
    return page


class DetectTests(SimpleTestCase):
    def test_finds_every_column_rule(self):
        _, grid = grid_detector.detect(_draw_form())
        self.assertEqual(grid.columns, len(X_RULES) - 1)
        for expected, found in zip(X_RULES, grid.x_rules):
            self.assertAlmostEqual(expected, found, delta=6)

    def test_table_extent_is_the_outer_rules(self):
        _, grid = grid_detector.detect(_draw_form())
        self.assertAlmostEqual(grid.top, TOP, delta=6)
        self.assertAlmostEqual(grid.bottom, BOTTOM, delta=6)

    def test_an_open_data_area_is_still_a_table(self):
        # The real forms rule no lines between entries; requiring rows would
        # reject every page.
        _, grid = grid_detector.detect(_draw_form(ruled_rows=False))
        self.assertEqual(grid.columns, len(X_RULES) - 1)

    def test_a_ruled_data_area_works_too(self):
        _, grid = grid_detector.detect(_draw_form(ruled_rows=True))
        self.assertEqual(grid.columns, len(X_RULES) - 1)

    def test_a_tilted_scan_is_straightened_first(self):
        import cv2

        page = _draw_form()
        matrix = cv2.getRotationMatrix2D((WIDTH / 2, HEIGHT / 2), 1.2, 1.0)
        tilted = cv2.warpAffine(
            page, matrix, (WIDTH, HEIGHT), borderMode=cv2.BORDER_REPLICATE
        )
        _, grid = grid_detector.detect(tilted)
        self.assertEqual(grid.columns, len(X_RULES) - 1)
        self.assertLess(abs(grid.skew_angle), 2.0)
        self.assertGreater(abs(grid.skew_angle), 0.5)

    def test_a_page_without_a_table_is_refused(self):
        with self.assertRaises(GridNotFound):
            grid_detector.detect(_blank())

    def test_too_few_columns_is_refused(self):
        import cv2

        page = _blank()
        for x in (400, 800, 1200):
            cv2.line(page, (x, TOP), (x, BOTTOM), (0, 0, 0), 3)
        cv2.line(page, (LEFT, TOP), (RIGHT, TOP), (0, 0, 0), 3)
        with self.assertRaises(GridNotFound):
            grid_detector.detect(page)


class ColumnLookupTests(SimpleTestCase):
    def setUp(self):
        _, self.grid = grid_detector.detect(_draw_form())

    def test_maps_x_to_its_column(self):
        self.assertEqual(self.grid.column_for(250), 0)
        self.assertEqual(self.grid.column_for(500), 1)

    def test_outside_the_table_is_no_column(self):
        self.assertIsNone(self.grid.column_for(10))
        self.assertIsNone(self.grid.column_for(WIDTH - 10))

    def test_widths_sum_to_the_table_width(self):
        self.assertAlmostEqual(
            sum(self.grid.column_widths),
            self.grid.x_rules[-1] - self.grid.x_rules[0],
            delta=1,
        )


class _Box:
    def __init__(self, y, height=40):
        self.bbox = (0.0, float(y), 10.0, float(y + height))


class ClusterTests(SimpleTestCase):
    def test_groups_boxes_on_one_line(self):
        groups = grid_detector.cluster_by_y([_Box(100), _Box(108), _Box(300)])
        self.assertEqual([len(group) for group in groups], [2, 1])

    def test_empty_input(self):
        self.assertEqual(grid_detector.cluster_by_y([]), [])

    def test_tolerance_scales_with_box_height(self):
        # The same 30 px offset is one line for tall boxes and two for short.
        self.assertEqual(len(grid_detector.cluster_by_y([_Box(0, 80), _Box(30, 80)])), 1)
        self.assertEqual(len(grid_detector.cluster_by_y([_Box(0, 10), _Box(30, 10)])), 2)
