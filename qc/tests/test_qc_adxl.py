import math
import os
import random
import re
import tempfile
import unittest
from datetime import datetime

from qc.qc_adxl import (ADXL_DEVICE, ADXL_MODEL, GRAVITY_MM_S2, SHAKE_STRIKES_MAX, build_adxl_report,
                        chip_state, read_measure_csv, rest_check, shake_step)

QC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REST = [(-2324.0 + i % 7, 421.0 - i % 5, 10420.0 + i % 11) for i in range(3000)]  # real board, 19/10


def shake(n=3000, amplitude=8000.0, dead_axis=None):
    rnd = random.Random(1)
    return [tuple(0.0 if axis == dead_axis else amplitude * math.sin(i / 7.0 + axis) + rnd.uniform(-500, 500)
                  for axis in range(3)) for i in range(n)]


class TestRest(unittest.TestCase):
    def test_real_board_at_rest(self):
        self.assertEqual(rest_check(REST), (True, ""))

    def test_dead_chip(self):
        ok, why = rest_check([(0.0, 0.0, 0.0)] * 3000)
        self.assertFalse(ok)
        self.assertIn("gravity", why)

    def test_gravity_on_any_axis(self):
        self.assertTrue(rest_check([(GRAVITY_MM_S2, 0.0, 0.0)] * 3000)[0])

    def test_no_samples(self):
        self.assertFalse(rest_check([])[0])


class TestShakeStep(unittest.TestCase):
    def test_full_shake_passes(self):
        self.assertEqual(shake_step(shake(), 0)[0], "pass")

    def test_board_left_alone_keeps_waiting(self):
        self.assertEqual(shake_step(REST, 0), ("wait", 0, ""))

    def test_stuck_axis_fails_after_strikes(self):
        strikes, state, why = 0, "wait", ""
        for _ in range(SHAKE_STRIKES_MAX):
            state, strikes, why = shake_step(shake(dead_axis=1), strikes)
        self.assertEqual((state, why), ("fail", "Axis Y does not move"))

    def test_one_partial_window_is_not_a_fail(self):
        self.assertEqual(shake_step(shake(dead_axis=1), 0)[0], "wait")


class TestChipState(unittest.TestCase):
    def test_moonraker_refusals(self):
        """Bodies as Moonraker returns them on the pad (19/10)."""
        self.assertEqual(chip_state('{"error":{"code":400,"message":"No accelerometer measurements found"}}'),
                         "stuck")
        self.assertEqual(chip_state('{"error":{"code":400,"message":"Invalid adxl345 id (got 0 vs e5)"}}'),
                         "absent")
        self.assertEqual(chip_state('{"error":{"code":503,"message":"Klippy Host not connected"}}'), "absent")


class TestReport(unittest.TestCase):
    def test_each_test_is_a_distinct_board(self):
        now = datetime(2026, 10, 19, 18, 5, 1)
        r = build_adxl_report(True, "", "02810E28F273", now)
        self.assertEqual(r["printer_id"], "ADXL-02810E28F273-20261019-180501")
        self.assertEqual((r["overall_result"], r["qc_model"]), ("PASS", ADXL_MODEL))
        self.assertIn("device=" + ADXL_DEVICE, r["yumi_config"])
        self.assertEqual(build_adxl_report(False, "Axis Y does not move", "P", now)["fail_reasons"],
                         ["Axis Y does not move"])


class TestBenchCfg(unittest.TestCase):
    def test_cfg_marks_the_adxl_qc_mode(self):
        with open(os.path.join(QC_DIR, "qc_printer_ADXL.cfg")) as f:
            cfg = f.read()
        self.assertEqual(re.search(r'variable_model: "(\w+)"', cfg).group(1), ADXL_MODEL)
        self.assertIn("serial: /tmp/klipper_host_mcu", cfg)
        self.assertIn("kinematics: none", cfg)


class TestReadMeasureCsv(unittest.TestCase):
    def test_klipper_file(self):
        path = os.path.join(tempfile.mkdtemp(), "adxl345-qc.csv")
        with open(path, "w") as f:
            f.write("#time,accel_x,accel_y,accel_z\n12.000100,-196.0,98.5,9810.2\n12.000412,10,-20,9790\n")
        self.assertEqual(read_measure_csv(path), [(-196.0, 98.5, 9810.2), (10.0, -20.0, 9790.0)])


if __name__ == "__main__":
    unittest.main()
