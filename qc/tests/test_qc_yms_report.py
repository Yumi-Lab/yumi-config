import unittest
from datetime import datetime, timedelta

from qc.qc_yms import build_box_report


class TestBuildBoxReport(unittest.TestCase):
    def setUp(self):
        self.base = datetime(2024, 1, 1, 12, 0, 0)

    def test_pass_keys_and_mapping(self):
        r = build_box_report(
            test_id="e5_head",
            result="PASS",
            yms_id="YMSL-TST01-TST01",
            session="PAD-20240101-1200",
            pad_mac="AABBCCDDEEFF",
            technician="op1",
            test_log={"e5_head": [
                "QC E5_HEAD: loaded 625mm, motion sensor OK -> ready for group stress",
                "QC E5_HEAD: stress OK — 6 segments ±100mm (10→40→80mm/s), sensor tracked throughout",
            ]},
            engine_results={
                "mcu_check": {"result": "pass", "timestamp": "ts", "details": ""},
                "e5_head": {"result": "pass", "timestamp": "ts2", "details": ""},
            },
            started=self.base,
            now=self.base + timedelta(seconds=42),
        )
        self.assertEqual(set(r.keys()), {
            "version", "printer_id", "technician", "date", "date_end",
            "duration_seconds", "overall_result", "failed_tests",
            "skipped_tests", "qc_model", "yumi_config", "machine_uid",
            "pad_mac", "bench_position", "bench_slot", "bench_session",
            "bench_total",
            "extruder_model", "spring_model", "yms_version", "bench_mark",
            "measures", "tests",
        })
        # Sans repère de banc configuré : chaîne vide, jamais None (le gabarit
        # substitue {bench_mark} par str(v) -> "None" imprimé sinon).
        self.assertEqual(r["bench_mark"], "")
        self.assertEqual(r["bench_position"], 6)
        self.assertEqual(r["bench_slot"], "hyperdrive_uart:4")
        self.assertEqual(r["printer_id"], "YMSL-TST01-TST01")
        self.assertEqual(r["overall_result"], "PASS")
        self.assertEqual(r["failed_tests"], [])
        self.assertEqual(r["qc_model"], "YMS-LIGHT")
        self.assertEqual(r["yumi_config"], "device=YMS-LIGHT")
        self.assertEqual(r["duration_seconds"], 42)
        self.assertEqual(r["measures"]["feed_mm"], 625)
        self.assertIsNone(r["measures"]["fail_reason"])
        self.assertEqual(len(r["tests"]), 2)
        self.assertEqual(r["tests"][1]["id"], "e5_head")

    def test_fail_reason_propagated(self):
        r = build_box_report(
            test_id="e9_head",
            result="FAIL",
            yms_id="YMSL-TST01-TST01",
            session="S",
            pad_mac="MAC",
            technician="op",
            test_log={"e9_head": [
                "QC E9_HEAD: no motion detected over 900mm (feeder or sensor faulty)",
            ]},
            engine_results={
                "mcu_check": {"result": "pass"},
                "e9_head": {"result": "fail"},
            },
        )
        self.assertEqual(r["bench_position"], 10)
        self.assertEqual(r["bench_slot"], "hyperdrive_usb:3")
        self.assertEqual(r["overall_result"], "FAIL")
        self.assertEqual(r["failed_tests"], ["e9_head"])
        self.assertEqual(r["measures"]["fail_reason"], "no_motion_on_load")
        self.assertEqual(r["tests"][1]["result"], "fail")

    def test_pro_model(self):
        r = build_box_report(
            test_id="e0_head",
            result="PASS",
            yms_id="YMSP-TST01-TST01",
            session="S",
            pad_mac="MAC",
            technician="op",
            test_log={"e0_head": []},
            engine_results={"e0_head": {"result": "pass"}},
            model="pro",
        )
        self.assertEqual(r["qc_model"], "YMS-PRO")
        self.assertEqual(r["yumi_config"], "device=YMS-PRO")
        self.assertEqual(r["printer_id"], "YMSP-TST01-TST01")



class TestBuildBoxReportBenchMark(unittest.TestCase):
    """16/09/2026 : repère visuel du banc (qc_bench_config.json -> rapport ->
    placeholder {bench_mark} de l'étiquette). Deux bancs sur la même POS80L
    réseau sortent des étiquettes identiques : le 2e porte un point."""

    def _report(self, **kw):
        return build_box_report(
            test_id="e0_head", result="PASS", yms_id="YMSP-X", session="S",
            pad_mac="AABBCCDDEEFF", technician="op", test_log={},
            engine_results={}, **kw)

    def test_bench_mark_propagated_verbatim(self):
        self.assertEqual(self._report(bench_mark="●")["bench_mark"], "●")

    def test_bench_mark_defaults_to_empty(self):
        self.assertEqual(self._report()["bench_mark"], "")


if __name__ == "__main__":
    unittest.main()
