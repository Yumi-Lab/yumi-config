import math
import os
import random
import tempfile
import unittest

from qc.qc_adxl import GRAVITY_MM_S2, adxl_verdict, read_measure_csv

REST = (120.0, -80.0, 9790.0)  # flat on the bench: gravity on Z


def shake(n=2000, amplitude=8000.0, dead_axis=None):
    rnd = random.Random(1)
    return [tuple(0.0 if axis == dead_axis else amplitude * math.sin(i / 7.0 + axis) + rnd.uniform(-500, 500)
                  for axis in range(3)) for i in range(n)]


class TestAdxlVerdict(unittest.TestCase):
    def test_good_board(self):
        self.assertEqual(adxl_verdict(REST, shake()), (True, ""))

    def test_dead_chip_reads_no_gravity(self):
        ok, why = adxl_verdict((0.0, 0.0, 0.0), shake())
        self.assertFalse(ok)
        self.assertIn("gravity", why)

    def test_gravity_on_any_axis(self):
        self.assertTrue(adxl_verdict((GRAVITY_MM_S2, 0.0, 0.0), shake())[0])

    def test_stuck_axis_named(self):
        ok, why = adxl_verdict(REST, shake(dead_axis=1))
        self.assertFalse(ok)
        self.assertEqual(why, "Axis Y does not move")

    def test_board_left_on_the_bench(self):
        ok, why = adxl_verdict(REST, [REST] * 2000)
        self.assertEqual(why, "Axis X/Y/Z does not move")

    def test_no_samples(self):
        self.assertFalse(adxl_verdict(REST, [])[0])


class TestReadMeasureCsv(unittest.TestCase):
    def test_klipper_file(self):
        path = os.path.join(tempfile.mkdtemp(), "adxl345-qc.csv")
        with open(path, "w") as f:
            f.write("#time,accel_x,accel_y,accel_z\n12.000100,-196.0,98.5,9810.2\n12.000412,10,-20,9790\n")
        self.assertEqual(read_measure_csv(path), [(-196.0, 98.5, 9810.2), (10.0, -20.0, 9790.0)])


if __name__ == "__main__":
    unittest.main()
