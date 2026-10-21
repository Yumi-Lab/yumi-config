import math
import os
import random
import re
import tempfile
import unittest
from datetime import datetime

from qc.qc_adxl import (ADXL_DEVICE, ADXL_MODEL, GRAVITY_MM_S2, SHAKE_STRIKES_MAX, build_adxl_report,
                        ADXL345_DEVID, devid_from_messages, read_measure_csv, rest_check, shake_step)

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


class TestDevid(unittest.TestCase):
    def test_newest_answer_wins(self):
        """gcode_store messages as Moonraker keeps them (oldest first), Klipper's own format."""
        msgs = ["// Accelerometer REG[0x0] = 0x0", "ACCELEROMETER_DEBUG_READ REG=0",
                "// Accelerometer REG[0x0] = 0xe5"]
        self.assertEqual(devid_from_messages(msgs), ADXL345_DEVID)
        self.assertEqual(devid_from_messages(msgs[:2]), 0)
        self.assertIsNone(devid_from_messages(["ok"]))


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



class TestSavedFileName(unittest.TestCase):
    def test_file_named_after_the_report(self):
        """21/10: ADXL reports were saved as QC__<date>.json (the engine id is empty on that bench)."""
        from unittest import mock
        from qc.qc_engine import QCEngine
        home = tempfile.mkdtemp()
        report = build_adxl_report(True, "", "0281E4FB8D93", datetime(2026, 10, 20, 8, 40, 26))
        with mock.patch.dict(os.environ, {"HOME": home}):
            path = QCEngine().save_report(report)
        self.assertTrue(os.path.basename(path).startswith("QC_ADXL-0281E4FB8D93-20261020-084026_"))


if __name__ == "__main__":
    unittest.main()
