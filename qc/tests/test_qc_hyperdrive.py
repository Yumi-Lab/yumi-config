"""Banc QC HyperDrive (14/10/2026) : une carte HyperDrive à la fois sur le banc YMS
PRO, mêmes macros et même verdict par position, un rapport par carte (UID STM32)."""
import os
import re
import sys
import tempfile
import unittest
from unittest import mock

from qc.qc_engine import QC_TESTS, QCEngine, QCResult
from qc.qc_yms import (
    HD_CAMERA_ERR, HD_DEVICE, HD_FIRMWARE_DESCRIPTOR, HD_FIRMWARE_VERSION, HD_FW_BUILD_ERR,
    HD_FW_VERSION_ERR, HD_HEAT_POSITIONS, HD_LED, HD_MCU, HD_MODEL,
    HD_POSITIONS, HD_USB_TEST_ID, box_verdict, build_hyperdrive_report,
    build_hyperdrive_tests, build_label_png_job, find_usb_device, led_cam_verdict,
    test_id_for_position)
from qc import render_qc_tspl

QC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_NO_LIVE_TEMPLATE = mock.patch("qc.render_qc_tspl.load_template", return_value=None)

STRESS_OK = ["QC E2_HEAD: stress 5/16 speed=50mm/s detected=True counted=True",
             "QC E2_HEAD: stress OK — 8 segments ±70mm"]
HEAT_OK = ["QC E2_HEAD: heat OK, 85.2C reached (target 85C)"]
UID = "2D0046000D51353234323830"


def _generator():
    if QC_DIR not in sys.path:
        sys.path.insert(0, QC_DIR)
    import generate_yms12_cfg
    return generate_yms12_cfg


class TestBoxVerdict(unittest.TestCase):
    """Verdict par position partagé YMS / HyperDrive (extrait du panel YMS)."""

    def test_pass_needs_stress_proof(self):
        self.assertEqual(box_verdict(STRESS_OK, False), ("PASS", ""))
        self.assertEqual(box_verdict([], False)[0], "FAIL")
        # "ready for group stress" n'est PAS une preuve de stress (27/08).
        self.assertEqual(box_verdict(["QC E2_HEAD: loaded 80mm, motion sensor OK -> ready for group stress"], False)[0], "FAIL")

    def test_load_and_stress_failures(self):
        self.assertEqual(box_verdict(["QC E2_HEAD: no motion detected over 80mm"] + STRESS_OK, False),
                         ("FAIL", "Load: no motion detected"))
        self.assertEqual(box_verdict(STRESS_OK + ["QC E2_HEAD: motion sensor YMS-3 LOST tracking at segment 7/16"], False),
                         ("FAIL", "Stress sweep (group): tracking lost"))

    def test_heat_verdict_required_when_heating(self):
        self.assertEqual(box_verdict(STRESS_OK + HEAT_OK, True), ("PASS", ""))
        self.assertEqual(box_verdict(STRESS_OK, True), ("FAIL", "Heat (group): no heat verdict received"))
        self.assertEqual(box_verdict(STRESS_OK + ["QC E2_HEAD: heat timeout, 60.1C after 360s (target 85C)"], True),
                         ("FAIL", "Heat (group): target temperature not reached"))
        self.assertEqual(box_verdict(STRESS_OK, False), ("PASS", ""))


class TestHyperdriveSequence(unittest.TestCase):
    def test_sequence(self):
        tests = build_hyperdrive_tests()
        self.assertEqual([t["id"] for t in tests],
                         ["mcu_check", "heat_start", HD_USB_TEST_ID, "hd_led", "load_all", "stress_all", "heat_wait"])
        by_id = {t["id"]: t for t in tests}
        self.assertEqual(by_id["mcu_check"]["macro"], "QC_HD_FIRMWARE")
        self.assertIn("TOOLS=3,4,5,6,7 ", by_id["load_all"]["macro"])
        self.assertTrue(by_id["stress_all"]["macro"].endswith("TOOLS=3,4,5,6,7"))
        self.assertTrue(by_id["heat_start"]["macro"].endswith("TOOLS=3,4,5 TARGET=42"))
        self.assertIn("TOOLS=3,4,5 TARGET=42 ", by_id["heat_wait"]["macro"])
        # Le YMS PRO garde ses 85 °C.
        from qc.qc_yms import build_yms_tests
        self.assertIn("TARGET=85", [t for t in build_yms_tests(model="pro") if t["id"] == "heat_start"][0]["macro"])
        self.assertEqual(by_id[HD_USB_TEST_ID].get("host_check"), HD_USB_TEST_ID)
        self.assertNotIn("macro", by_id[HD_USB_TEST_ID])
        self.assertEqual(by_id["hd_led"].get("host_check"), "hd_led")
        self.assertNotIn("macro", by_id["hd_led"])

    def test_engine_runs_the_given_sequence(self):
        e = QCEngine()
        first = e.start("PADMAC", model=HD_MODEL, tests=build_hyperdrive_tests())
        self.assertEqual(first["id"], "mcu_check")
        e.process_gcode_response("QC:MCU_CHECK:PASS")
        e.process_gcode_response("QC:HEAT_START:PASS")
        self.assertEqual(e.get_current_test()["id"], HD_USB_TEST_ID)
        e.record_check(False, "clé absente")
        self.assertEqual(e.results[HD_USB_TEST_ID]["result"], QCResult.FAIL)
        self.assertEqual(e.get_current_test()["id"], "hd_led")
        e.record_check(True)
        self.assertEqual(e.get_current_test()["id"], "load_all")
        # Les autres modèles gardent leur séquence.
        machine = QCEngine()
        machine.start("X", model="C235")
        self.assertIs(machine.tests, QC_TESTS)


class TestLedCamVerdict(unittest.TestCase):
    """Mesures réelles du banc le 14/10 (LED collée sur l'objectif, caméra saturée)."""
    REAL = {"off": (0, 0, 0), "red": (255, 254, 255), "green": (77, 249, 216), "blue": (75, 237, 255)}

    def test_real_bench_capture_passes(self):
        self.assertEqual(led_cam_verdict(self.REAL), (True, ""))

    def test_dead_led_stays_dark(self):
        dead = {n: (0, 0, 0) for n in self.REAL}
        ok, why = led_cam_verdict(dead)
        self.assertFalse(ok)
        self.assertIn("red/green/blue", why)

    def test_led_off_from_the_lens(self):
        ok, why = led_cam_verdict(dict(self.REAL, off=(180, 170, 150)))
        self.assertFalse(ok)
        self.assertIn("lens", why)

    def test_led_stuck_on_one_colour(self):
        stuck = dict(self.REAL, red=(200, 200, 200), green=(205, 200, 198), blue=(201, 203, 200))
        self.assertFalse(led_cam_verdict(stuck)[0])


class TestFindUsbDevice(unittest.TestCase):
    def _sysfs(self, devices):
        root = tempfile.mkdtemp()
        for name, attrs in devices.items():
            os.makedirs(os.path.join(root, name))
            for k, v in attrs.items():
                with open(os.path.join(root, name, k), "w") as f:
                    f.write(v + "\n")
        return root

    def test_match_by_id_or_serial(self):
        root = self._sysfs({
            "1-1": {"idVendor": "0781", "idProduct": "5567", "serial": "4C530001"},
            "1-1:1.0": {"bInterfaceClass": "08"},
            "usb1": {"idVendor": "1d6b", "idProduct": "0002"},
        })
        self.assertTrue(find_usb_device("0781:5567", root)[0])
        self.assertTrue(find_usb_device("0781:5567".upper(), root)[0])
        self.assertTrue(find_usb_device("4C530001", root)[0])
        found, seen = find_usb_device("0951:1666", root)
        self.assertFalse(found)
        self.assertEqual(seen, ["0781:5567 4C530001", "1d6b:0002"])

    def test_no_reference_never_passes(self):
        root = self._sysfs({"1-1": {"idVendor": "0781", "idProduct": "5567", "serial": ""}})
        self.assertFalse(find_usb_device("", root)[0])
        self.assertFalse(find_usb_device(None, "/nonexistent")[0])


class TestHyperdriveReport(unittest.TestCase):
    def _engine_report(self, overall="PASS"):
        return {
            "printer_id": UID, "machine_uid": UID, "overall_result": overall,
            "yumi_config": HD_FIRMWARE_DESCRIPTOR, "failed_tests": [],
            "tests": [{"id": "mcu_check", "name": "HyperDrive 固件 + UID / firmware + UID", "result": "pass"}],
        }

    def _log(self, broken=None):
        log = {}
        for pos in HD_POSITIONS:
            lines = list(STRESS_OK) + (list(HEAT_OK) if pos in HD_HEAT_POSITIONS else [])
            if pos == broken:
                lines = ["QC E%d_HEAD: no motion detected over 80mm" % (pos - 1)]
            log[test_id_for_position(pos)] = lines
        return log

    def test_all_slots_pass(self):
        rep = build_hyperdrive_report(self._engine_report(), self._log())
        self.assertEqual(rep["overall_result"], "PASS")
        self.assertEqual(rep["qc_model"], HD_MODEL)
        self.assertEqual(rep["yumi_config"], "device=%s" % HD_DEVICE)
        self.assertEqual(rep["firmware"], HD_FIRMWARE_DESCRIPTOR)
        slots = [t for t in rep["tests"] if t["id"].startswith("slot")]
        self.assertEqual([t["result"] for t in slots], ["pass"] * 5)
        self.assertEqual(rep["fail_reason"], "")

    def test_one_slot_fails_the_board(self):
        rep = build_hyperdrive_report(self._engine_report(), self._log(broken=6))
        self.assertEqual(rep["overall_result"], "FAIL")
        self.assertEqual(rep["failed_tests"], ["slot4"])
        self.assertEqual(rep["fail_reason"], "S4 no motion")

    def test_failed_engine_test_named_in_reason(self):
        er = self._engine_report("FAIL")
        er["failed_tests"] = ["mcu_check"]
        rep = build_hyperdrive_report(er, self._log())
        self.assertEqual(rep["overall_result"], "FAIL")
        self.assertEqual(rep["fail_reason"], "Firmware")

    def _failed(self, tid, log=(), details="Automated check failed"):
        er = self._engine_report("FAIL")
        er["failed_tests"] = [tid]
        er["tests"] = [{"id": tid, "name": tid, "result": "fail", "details": details, "log": list(log)}]
        return build_hyperdrive_report(er, self._log())

    def test_firmware_build_or_version_is_named(self):
        rep = self._failed("mcu_check", ["QC: %s (descriptor above)" % HD_FW_BUILD_ERR])
        self.assertEqual(rep["fail_reasons"], ["Firmware build"])
        self.assertIn(HD_FW_BUILD_ERR, rep["tests"][0]["details"])  # écran résumé
        rep = self._failed("mcu_check", ["QC: %s v0.13.0-1, expected x" % HD_FW_VERSION_ERR])
        self.assertEqual(rep["fail_reasons"], ["Firmware version"])

    def test_bench_camera_is_not_the_board(self):
        self.assertEqual(self._failed("hd_led", details="%s: timed out" % HD_CAMERA_ERR)["fail_reasons"],
                         ["Camera (bench)"])
        self.assertEqual(self._failed("hd_led", details="LED stays dark on red/green/blue")["fail_reasons"],
                         ["LED"])
        self.assertEqual(self._failed(HD_USB_TEST_ID)["fail_reasons"], ["USB"])

    def test_slot_lost_at_the_ramp_is_a_lost_sensor(self):
        log = self._log()
        log["e3_head"] = ["QC E3_HEAD: motion sensor YMS-4 lost tracking during ramp segment 1/16 (ignored, not counted)"]
        rep = build_hyperdrive_report(self._engine_report(), log)
        self.assertEqual(rep["fail_reasons"], ["S2 sensor lost"])

    def test_partial_never_becomes_pass(self):
        self.assertEqual(build_hyperdrive_report(self._engine_report("PARTIAL"), self._log())["overall_result"], "PARTIAL")

    def test_device_imposed_whatever_the_firmware(self):
        er = self._engine_report()
        er["yumi_config"] = "board=SMART_MAKER_1_X;device=C235;uid=772238"
        rep = build_hyperdrive_report(er, self._log())
        self.assertEqual(rep["yumi_config"], "device=%s" % HD_DEVICE)
        self.assertIn("device=C235", rep["firmware"])


@_NO_LIVE_TEMPLATE
class TestHyperdriveLabel(unittest.TestCase):
    def test_label_kind_and_media(self, _m):
        rep = {"overall_result": "PASS", "printer_id": UID, "qc_model": HD_MODEL,
               "date_end": "2026-10-14T10:00:00"}
        job = build_label_png_job(rep)
        self.assertEqual((job["width_mm"], job["height_mm"]), (50, 30))
        rep.update(overall_result="FAIL", fail_reasons=["S2 sensor_lost_stress", "NeoPixel LED"])
        build_label_png_job(rep)
        from qc.qc_yms import _label_kind_section_data
        kind, section, data = _label_kind_section_data(rep)
        self.assertEqual((kind, section), ("hyperdrive", "fail"))
        self.assertEqual(data["fail_reason"], "S2 sensor_lost_stress +1")
        self.assertEqual(data["bench_position"], "")

    def test_no_bench_position_box(self, _m):
        for sec, els in render_qc_tspl.DEFAULTS["hyperdrive"].items():
            self.assertNotIn("{bench_position}", [e.get("c") for e in els])
            self.assertEqual(len(els), len(render_qc_tspl.DEFAULTS["yms"][sec]) - 2)
        self.assertIn("hyperdrive", render_qc_tspl.TEMPLATE_URL)


class TestGeneratedCfg(unittest.TestCase):
    """La cfg HyperDrive vient du même générateur que YMS12 ; YMS12 ne bouge pas."""

    @classmethod
    def setUpClass(cls):
        cls.gen = _generator()
        with open(os.path.join(QC_DIR, "qc_printer_%s.cfg" % HD_MODEL), encoding="utf-8") as f:
            cls.cfg = f.read()

    def test_committed_cfgs_are_generated(self):
        with open(os.path.join(QC_DIR, "qc_printer_YMS12.cfg"), encoding="utf-8") as f:
            self.assertEqual(f.read(), self.gen.build())
        self.assertEqual(self.cfg, self.gen.build_hyperdrive())

    def test_one_hyperdrive_under_test(self):
        self.assertEqual(re.findall(r"^\[mcu (hyperdrive\w*)\]", self.cfg, re.M), [HD_MCU])
        self.assertIn('variable_model: "%s"' % HD_MODEL, self.cfg)
        self.assertEqual(sorted(int(n) for n in re.findall(r"^\[heater_generic YMS-(\d+)-heater\]", self.cfg, re.M)),
                         list(HD_HEAT_POSITIONS))
        sensors = [int(n) for n in re.findall(r"^\[filament_yumi_smart_motion_sensor YMS-(\d+)\]", self.cfg, re.M)]
        self.assertEqual(sensors, [1, 2] + list(HD_POSITIONS))

    def test_filament_returns_to_start(self):
        # Banc HyperDrive : on recule de la charge ; le banc YMS garde son ejection.
        self.assertIn("G1 E-{ns.maxpush} F1200", self.cfg)
        self.assertNotIn("G1 E-300 F1200", self.cfg)
        with open(os.path.join(QC_DIR, "qc_printer_YMS12.cfg"), encoding="utf-8") as f:
            self.assertIn("G1 E-300 F1200", f.read())

    def test_serial_number_is_the_board_under_test(self):
        self.assertRegex(self.cfg, r"\[mcu_uid\]\nmcu: %s\b" % HD_MCU)

    def test_firmware_and_led(self):
        self.assertIn('{% if got != "' + HD_FIRMWARE_DESCRIPTOR + '" %}', self.cfg)
        self.assertIn('{% elif m.mcu_version != "' + HD_FIRMWARE_VERSION + '" %}', self.cfg)
        self.assertIn(HD_FW_BUILD_ERR, self.cfg)
        self.assertIn(HD_FW_VERSION_ERR, self.cfg)
        self.assertIn("[neopixel %s]\npin: %s:PC12" % (HD_LED, HD_MCU), self.cfg)
        self.assertIn("color_order: BRG", self.cfg)
        # ";" ouvre un commentaire dans une ligne gcode : jamais de descripteur dans un RESPOND.
        for line in self.cfg.splitlines():
            if line.strip().startswith("RESPOND"):
                self.assertNotIn(";", line)

    def test_led_pin_free_on_the_board(self):
        used = re.findall(r"%s:!?(P[A-C]\d+)" % HD_MCU, self.cfg)
        self.assertEqual(used.count("PC12"), 1)
        self.assertEqual(len(used), len(set(used)))


if __name__ == "__main__":
    unittest.main()
