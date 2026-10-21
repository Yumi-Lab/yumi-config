import os
import re
import sys
import unittest
from datetime import datetime

from qc.qc_engine import (HOTEND_DEVICES, HOTEND_TEMP, HOTEND_TESTS, QCEngine, hotend_plugged,
                          hotend_report, tests_for_model)

QC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _generator():
    if QC_DIR not in sys.path:
        sys.path.insert(0, QC_DIR)
    import generate_hotend_cfg
    return generate_hotend_cfg


def _body(cfg, section):
    return cfg.split("[%s]\n" % section, 1)[1].split("gcode:\n", 1)[1].split("\n\n", 1)[0]


def _macro_body(cfg, name):
    return _body(cfg, "gcode_macro " + name)


def _render(body, detected, left=0, params=None):
    import jinja2
    tpl = jinja2.Environment("{%", "%}", "{", "}").from_string(body)   # = gcode_macro.py
    return tpl.render(params=params or {}, printer={
        "filament_switch_sensor head_sensor": {"filament_detected": detected},
        "gcode_macro QC_HOTEND_SWITCH": {"left": left}})


class TestSequences(unittest.TestCase):
    def test_x12_heats_and_checks_the_fans(self):
        self.assertEqual([t["id"] for t in tests_for_model("X12")],
                         ["heat_start", "head_switch", "fan_hotend", "fan_part", "hotend_temp"])

    def test_dd_also_extrudes(self):
        self.assertEqual([t["id"] for t in tests_for_model("DD")],
                         ["heat_start", "fan_hotend", "fan_part", "hotend_temp", "hotend_extrude"])

    def test_operator_reads_the_target_temperature(self):
        temp = next(t for t in HOTEND_TESTS["X12"] if t["id"] == "hotend_temp")
        self.assertEqual(temp["type"], "visual")
        self.assertIn("%d" % HOTEND_TEMP, temp["prompt"])
        self.assertTrue(temp["macro"].endswith("TEMP=%d" % HOTEND_TEMP))


class TestPlugged(unittest.TestCase):
    def test_open_thermistor_is_no_head(self):
        self.assertFalse(hotend_plugged(-87.4))
        self.assertTrue(hotend_plugged(23.1))
        self.assertFalse(hotend_plugged({}))   # KlipperScreen get_stat before the first update
        self.assertFalse(hotend_plugged(None))


class TestCfg(unittest.TestCase):
    def test_committed_cfgs_are_generated(self):
        gen = _generator()
        for model in HOTEND_DEVICES:
            with open(os.path.join(QC_DIR, "qc_printer_%s.cfg" % model)) as f:
                self.assertEqual(f.read(), gen.build(model), "run qc/generate_hotend_cfg.py")

    def test_every_test_has_its_macro_and_signals(self):
        for model, tests in HOTEND_TESTS.items():
            cfg = _generator().build(model)
            self.assertIn('variable_model: "%s"' % model, cfg)
            self.assertIn("kinematics: none", cfg)
            for t in tests:
                self.assertIn('"QC:%s:START"' % t["id"].upper(), _macro_body(cfg, t["macro"].split()[0]))
                last = "PASS" if t["type"] == "automated" else "VISUAL"
                self.assertIn('"QC:%s:%s"' % (t["id"].upper(), last), cfg)   # the switch loop sends PASS

    def test_macros_render(self):
        import jinja2
        cfg = _generator().build("DD")
        env = jinja2.Environment("{%", "%}", "{", "}")   # = gcode_macro.py
        params = {"TEMP": "230", "MM": "30", "SPEED": "2.5"}
        out = env.from_string(_macro_body(cfg, "QC_HOTEND_EXTRUDE")).render(params=params)
        self.assertIn("G1 E30 F150.0", out)
        out = env.from_string(_macro_body(cfg, "QC_HOTEND_TEMP")).render(params=params)
        self.assertIn("M109 S230", out)


class TestHeadSwitch(unittest.TestCase):
    """Same check as the machine QC feed: free first, then triggered by the filament."""

    def setUp(self):
        self.cfg = _generator().build("X12")

    def test_stuck_switch_fails_at_once(self):
        out = _render(_macro_body(self.cfg, "QC_HOTEND_SWITCH"), True, params={"WAIT": "60"})
        self.assertIn("QC:HEAD_SWITCH:FAIL", out)
        self.assertNotIn("UPDATE_DELAYED_GCODE", out)

    def test_free_switch_starts_the_wait(self):
        out = _render(_macro_body(self.cfg, "QC_HOTEND_SWITCH"), False, params={"WAIT": "60"})
        self.assertIn("VARIABLE=left VALUE=120", out)
        self.assertIn("UPDATE_DELAYED_GCODE ID=_qc_hotend_switch DURATION=0.5", out)

    def test_loop(self):
        loop = _body(self.cfg, "delayed_gcode _qc_hotend_switch")
        self.assertIn("QC:HEAD_SWITCH:PASS", _render(loop, True, left=40))
        self.assertIn("VALUE=39", _render(loop, False, left=40))
        self.assertIn("QC:HEAD_SWITCH:FAIL", _render(loop, False, left=0))

    def test_cleanup_stops_the_loop(self):
        self.assertIn("UPDATE_DELAYED_GCODE ID=_qc_hotend_switch DURATION=0\n",
                      _macro_body(self.cfg, "QC_CLEANUP"))


class TestRun(unittest.TestCase):
    def test_x12_pass_gives_one_head_report(self):
        engine = QCEngine()
        engine.start("02810E28F273", model="X12")
        for tid in ("HEAT_START", "HEAD_SWITCH", "FAN_HOTEND", "FAN_PART", "HOTEND_TEMP"):
            engine.process_gcode_response("QC:%s:START" % tid)
            if tid in ("HEAT_START", "HEAD_SWITCH"):
                engine.process_gcode_response("QC:%s:PASS" % tid)
            else:
                engine.process_gcode_response("QC:%s:VISUAL" % tid)
                engine.record_visual_result(True)
        report = hotend_report(engine.generate_report(), "X12", datetime(2026, 10, 21, 9, 30, 5))
        self.assertEqual(report["overall_result"], "PASS")
        self.assertEqual(report["printer_id"], "HOTEND-X12-02810E28F273-20261021-093005")
        self.assertEqual((report["yumi_config"], report["machine_uid"]), ("device=HOTEND-X12", ""))
        self.assertNotIn("machine_uid_missing", report)

    def test_wrong_temperature_fails(self):
        engine = QCEngine()
        engine.start("P", model="DD")
        engine.process_gcode_response("QC:HEAT_START:PASS")
        for tid in ("FAN_HOTEND", "FAN_PART", "HOTEND_TEMP", "HOTEND_EXTRUDE"):
            engine.process_gcode_response("QC:%s:VISUAL" % tid)
            engine.record_visual_result(tid != "HOTEND_TEMP")
        report = engine.generate_report()
        self.assertEqual((report["overall_result"], report["failed_tests"]), ("FAIL", ["hotend_temp"]))


if __name__ == "__main__":
    unittest.main()
