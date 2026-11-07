"""yumi_bed_scan — the geometry of the scan, without Klipper."""
import importlib.util
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
MODULE = HERE.parent.parent / "klipper" / "klippy" / "extras" / "yumi_bed_scan.py"
spec = importlib.util.spec_from_file_location("yumi_bed_scan", MODULE)
scan = importlib.util.module_from_spec(spec)
spec.loader.exec_module(scan)


class Grid(unittest.TestCase):
    def test_serpentine_covers_the_window_row_by_row(self):
        pts = scan.serpentine(117.5, 218, 10, 5, 2, 2)
        self.assertEqual(len(pts), 11 * 6)
        self.assertEqual(pts[0], (107.5, 223.0))           # far row, left to right
        self.assertEqual(pts[10], (127.5, 223.0))
        self.assertEqual(pts[11], (127.5, 221.0))          # next row starts where the last ended
        self.assertEqual(pts[-1][1], 213.0)
        xs = {p[0] for p in pts}
        self.assertEqual(min(xs), 107.5)
        self.assertEqual(max(xs), 127.5)

    def test_planes_go_down_to_the_floor_included(self):
        self.assertEqual(scan.planes(0.6, 0.1, 0.1), [0.6, 0.5, 0.4, 0.3, 0.2, 0.1])
        self.assertEqual(scan.planes(0.5, 0.2, 0.2), [0.5, 0.3])

    def test_interior_point_ignores_where_the_window_cuts_a_long_strip(self):
        """C335 07/11: the reference is a strip wider than the window; the box centre followed
        the window (172.5, 169.5, 160). The interior point stays on the strip's mid-line,
        as close to the formula point as the clearance allows."""
        for cx in (160.0, 169.5, 172.5):
            grid = scan.serpentine(cx, 306, 14, 14, 2, 2)
            hits = [p for p in grid if 305 <= p[1] <= 309]          # strip Y 305..309, endless in X
            (x, y), margin = scan.interior_point(hits, grid, (cx, 306), 2, 2)
            self.assertIn(y, (306.0, 308.0))
            self.assertEqual(margin, 2.0)
            self.assertLessEqual(abs(x - cx), 1.0)

    def test_interior_point_keeps_away_from_a_hole(self):
        grid = scan.serpentine(0, 0, 6, 6, 1, 1)
        hits = [p for p in grid if p != (0.0, 0.0)]                  # one perforation at the target
        (x, y), margin = scan.interior_point(hits, grid, (0, 0), 1, 1)
        self.assertGreater(margin, 1.0)
        self.assertNotEqual((x, y), (0.0, 0.0))

    def test_bbox_center_has_equal_margins(self):
        mx, my, box = scan.bbox_center([(120, 210), (124, 210), (122, 214), (120, 212)])
        self.assertEqual((mx, my), (122.0, 212.0))
        self.assertEqual(box, (120, 124, 210, 214))


if __name__ == "__main__":
    unittest.main()
