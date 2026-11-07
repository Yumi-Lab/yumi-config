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

    def test_bbox_center_has_equal_margins(self):
        mx, my, box = scan.bbox_center([(120, 210), (124, 210), (122, 214), (120, 212)])
        self.assertEqual((mx, my), (122.0, 212.0))
        self.assertEqual(box, (120, 124, 210, 214))


if __name__ == "__main__":
    unittest.main()
