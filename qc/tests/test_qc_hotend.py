import os
import re
import sys
import unittest
from datetime import datetime

from qc.qc_engine import (HOTEND_DEVICES, HOTEND_FEED_MM_S, HOTEND_RETRACT_MM, HOTEND_TEMP, HOTEND_TESTS,
                          MEASURE_DIGITS_MAX, QCEngine, hotend_plugged, hotend_report, measure_value,
                          measure_verdict,
                          tests_for_model)

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


def _render(body, detected, pushed=0, params=None):
    import jinja2
    tpl = jinja2.Environment("{%", "%}", "{", "}").from_string(body)   # = gcode_macro.py
    return tpl.render(params=params or {}, action_respond_info=lambda msg: "// " + msg, printer={
        "filament_switch_sensor head_sensor": {"filament_detected": detected},
        "gcode_macro QC_HOTEND_SWITCH": {"pushed": pushed, "maxd": 800, "speed": 10.0, "chunk": 25,
                                         "feeder": "extruder2"}})


class TestSequences(unittest.TestCase):
    def test_x12_heats_and_checks_the_fans(self):
        self.assertEqual([t["id"] for t in tests_for_model("X12")],
                         ["heat_start", "head_switch", "fan_hotend", "fan_part", "hotend_temp",
                          "hotend_extrude", "filament_cut"])

    def test_dd_also_extrudes(self):
        self.assertEqual([t["id"] for t in tests_for_model("DD")],
                         ["heat_start", "fan_hotend", "fan_part", "hotend_temp", "hotend_extrude"])

    def test_operator_types_the_reading(self):
        temp = next(t for t in HOTEND_TESTS["X12"] if t["id"] == "hotend_temp")
        self.assertEqual((temp["type"], temp["measure"]), ("visual", "hotend_wall_c"))
        self.assertTrue(temp["macro"].endswith("TEMP=%d" % HOTEND_TEMP))

    def test_reading_tolerance(self):
        """Nicolas 21/10: 220 C asked, the wall reads ~210; 210 and 215 pass, 200 fails, more than
        8 C away is not conform."""
        temp = next(t for t in HOTEND_TESTS["X12"] if t["id"] == "hotend_temp")
        verdict = {v: measure_verdict(temp, v)[0] for v in (210, 215, 200, 202, 218, 201, 219)}
        self.assertEqual(verdict, {210: True, 215: True, 200: False, 202: True, 218: True,
                                   201: False, 219: False})
        self.assertEqual(measure_verdict(temp, 200)[1], "hotend_wall_c=200 (expected 210 +-8)")

    def test_keypad_value_in_whole_degrees(self):
        self.assertEqual(measure_value("211"), 211)
        self.assertEqual(MEASURE_DIGITS_MAX, 3)
        self.assertIsNone(measure_value(""))


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

    def test_real_acceleration(self):
        """Klipper derives the extrude-only acceleration from max_accel: the C235 limits, never a
        placeholder (max_accel 1 made each 25 mm feed step last ~20 s, 21/10)."""
        with open(os.path.join(QC_DIR, "qc_printer_C235.cfg")) as f:
            c235 = f.read()
        for model in HOTEND_DEVICES:
            cfg = _generator().build(model)
            for opt in ("max_velocity", "max_accel"):
                self.assertEqual(re.search(r"\n%s: (\d+)" % opt, cfg).group(1),
                                 re.search(r"\n%s: (\d+)" % opt, c235).group(1))

    def test_macros_render(self):
        import jinja2
        cfg = _generator().build("DD")
        env = jinja2.Environment("{%", "%}", "{", "}")   # = gcode_macro.py
        params = {"TEMP": "230", "MM": "30", "SPEED": "2.5"}
        out = env.from_string(_macro_body(cfg, "QC_HOTEND_EXTRUDE")).render(params=params)
        self.assertIn("G1 E30 F150.0", out)
        self.assertNotIn("SYNC_EXTRUDER_MOTION", out)        # DD: the head's own motor
        out = env.from_string(_macro_body(cfg, "QC_HOTEND_TEMP")).render(params=params)
        self.assertIn("M109 S230", out)

    def test_x12_extrudes_with_yms3(self):
        import jinja2
        x12 = next(t for t in HOTEND_TESTS["X12"] if t["id"] == "hotend_extrude")
        params = dict(kv.split("=") for kv in x12["macro"].split()[1:])
        body = _macro_body(_generator().build("X12"), "QC_HOTEND_EXTRUDE")
        out = jinja2.Environment("{%", "%}", "{", "}").from_string(body).render(params=params)
        lines = [l.strip() for l in out.splitlines() if l.strip()]
        self.assertEqual(lines[1:5], ["SYNC_EXTRUDER_MOTION EXTRUDER=extruder2 MOTION_QUEUE=extruder",
                                      "M83", "G1 E60 F150.0", "M400"])
        self.assertEqual(lines[5], "SYNC_EXTRUDER_MOTION EXTRUDER=extruder2 MOTION_QUEUE=")


class TestHeadSwitch(unittest.TestCase):
    """Same check as the machine QC feed: switch free first, then YMS-3 feeds until it triggers.
    No retract (the operator cuts the filament)."""

    def setUp(self):
        self.cfg = _generator().build("X12")

    def test_stuck_switch_fails_at_once(self):
        out = _render(_macro_body(self.cfg, "QC_HOTEND_SWITCH"), True, params={"MAXD": "800", "SPEED": "10"})
        self.assertIn("QC:HEAD_SWITCH:FAIL", out)
        self.assertNotIn("UPDATE_DELAYED_GCODE", out)

    def test_free_switch_starts_the_feed(self):
        out = _render(_macro_body(self.cfg, "QC_HOTEND_SWITCH"), False, params={"MAXD": "800", "SPEED": "10"})
        self.assertIn("VARIABLE=maxd VALUE=800", out)
        self.assertIn("UPDATE_DELAYED_GCODE ID=_qc_hotend_switch DURATION=0.1", out)

    def test_feed_loop(self):
        loop = _body(self.cfg, "delayed_gcode _qc_hotend_switch")
        step = _render(loop, False, pushed=100)
        self.assertIn("SYNC_EXTRUDER_MOTION EXTRUDER=extruder2 MOTION_QUEUE=extruder", step)
        self.assertIn("G1 E25 F600.0", step)
        self.assertIn("VARIABLE=pushed VALUE=125", step)
        done = _render(loop, True, pushed=325)
        self.assertIn("QC:HEAD_SWITCH:PASS", done)
        self.assertIn("MOTION_QUEUE=\n", done)
        self.assertNotIn("E-", done)                      # no retract
        self.assertIn("QC:HEAD_SWITCH:FAIL", _render(loop, False, pushed=800))

    def test_yms3_only_where_the_switch_is_checked(self):
        """X12 drives YMS-3 on the HyperDrive slot 1; DD does not need a HyperDrive on the bench."""
        self.assertIn("[extruder_stepper extruder2]\nextruder:\nstep_pin: hyperdrive_uart:PB12", self.cfg)
        self.assertIn("[mcu hyperdrive_uart]", self.cfg)
        dd = _generator().build("DD")
        self.assertNotIn("hyperdrive_uart", dd)
        self.assertNotIn("QC_HOTEND_SWITCH", dd)

    def test_cut_then_retract(self):
        """The operator's answer to the cut prompt runs the retract (visual test cleanup)."""
        cut = HOTEND_TESTS["X12"][-1]
        self.assertEqual((cut["id"], cut["type"]), ("filament_cut", "visual"))
        macro, mm, speed = re.match(r"(\w+) MM=(\d+) SPEED=(\S+)", cut["cleanup"]).groups()
        out = _render(_macro_body(self.cfg, macro), False, params={"MM": mm, "SPEED": speed})
        self.assertIn("G1 E-%.1f F%.1f" % (HOTEND_RETRACT_MM, HOTEND_FEED_MM_S * 60), out)
        self.assertIn("MOTION_QUEUE=extruder\n", out)
        self.assertTrue(out.rstrip().endswith("MOTION_QUEUE="))

    def test_cleanup_stops_the_loop(self):
        self.assertIn("UPDATE_DELAYED_GCODE ID=_qc_hotend_switch DURATION=0\n",
                      _macro_body(self.cfg, "QC_CLEANUP"))


class TestRun(unittest.TestCase):
    def test_x12_pass_gives_one_head_report(self):
        engine = QCEngine()
        engine.start("02810E28F273", model="X12")
        for tid in ("HEAT_START", "HEAD_SWITCH", "FAN_HOTEND", "FAN_PART", "HOTEND_TEMP",
                    "HOTEND_EXTRUDE", "FILAMENT_CUT"):
            engine.process_gcode_response("QC:%s:START" % tid)
            if tid in ("HEAT_START", "HEAD_SWITCH"):
                engine.process_gcode_response("QC:%s:PASS" % tid)
            else:
                engine.process_gcode_response("QC:%s:VISUAL" % tid)
                if tid == "HOTEND_TEMP":       # what the wizard does with the keypad entry
                    engine.record_measure("hotend_wall_c", measure_value("211"))
                    temp = next(t for t in HOTEND_TESTS["X12"] if t["id"] == "hotend_temp")
                    engine.record_visual_result(*measure_verdict(temp, 211))
                else:
                    engine.record_visual_result(True)
        report = hotend_report(engine.generate_report(), "X12", datetime(2026, 10, 21, 9, 30, 5))
        self.assertEqual(report["overall_result"], "PASS")
        self.assertEqual(report["measures"], {"hotend_wall_c": 211})
        temp = next(t for t in report["tests"] if t["id"] == "hotend_temp")
        self.assertEqual(temp["details"], "hotend_wall_c=211 (expected 210 +-8)")
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


class TestPresentHyperdrive(unittest.TestCase):
    """21/10: X12 stayed down on the UART serial while the USB HyperDrive carried the YMS."""

    def test_usb_hyperdrive_when_plugged(self):
        from qc.qc_yms import HD_MCU, HD_MODEL, HD_SERIAL, HD_USB_MODEL, with_present_hyperdrive
        cfg = _generator().build("X12")
        usb = with_present_hyperdrive(cfg, True)
        self.assertIn("[mcu %s]\nserial: %s " % (HD_MCU, HD_SERIAL[HD_USB_MODEL]), usb)
        self.assertIn("[mcu]\n", usb)
        self.assertEqual(usb.replace(HD_SERIAL[HD_USB_MODEL], HD_SERIAL[HD_MODEL]), cfg)
        self.assertEqual(with_present_hyperdrive(cfg, False), cfg)
        dd = _generator().build("DD")
        self.assertEqual(with_present_hyperdrive(dd, True), dd)
