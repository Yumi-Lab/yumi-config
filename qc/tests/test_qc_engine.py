import importlib.util
import json
import os
import unittest
from datetime import datetime

from qc import qc_engine

_SANDBOX_SCRIPT = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "scripts", "sandbox_machine_test.py")


def _load_sandbox_module():
    spec = importlib.util.spec_from_file_location("sandbox_machine_test",
                                                  _SANDBOX_SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class TestQCEngineYMS12(unittest.TestCase):
    def test_yms12_sequence_has_13_tests(self):
        self.assertEqual(len(qc_engine.QC_TESTS_YMS12), 13)

    def test_yms12_macros_cover_tools_1_to_12(self):
        macros = [t["macro"] for t in qc_engine.QC_TESTS_YMS12 if t["id"].startswith("e")]
        self.assertEqual(len(macros), 12)
        for i, macro in enumerate(macros, start=1):
            self.assertIn(f"TOOL={i}", macro)


class TestMachineReport(unittest.TestCase):
    """Rapport MACHINE (session C-series simulée via le vrai QCEngine) :
    technician absent (abandonné serveur), invariants du contrat intacts."""

    @classmethod
    def setUpClass(cls):
        cls.sandbox = _load_sandbox_module()
        cls.report = cls.sandbox.build_report()

    def test_technician_absent(self):
        self.assertNotIn("technician", self.report)

    def test_sandbox_flag(self):
        self.assertIs(self.report["sandbox"], True)

    def test_root_keys_intact(self):
        expected = {"version", "printer_id", "date", "date_end",
                    "duration_seconds", "tests", "overall_result",
                    "failed_tests", "skipped_tests", "yumi_config",
                    "machine_uid", "pad_mac"}
        self.assertTrue(expected.issubset(self.report.keys()),
                        "clés manquantes: %s" % (expected - self.report.keys()))

    def test_overall_pass_and_uid_identity(self):
        self.assertEqual(self.report["overall_result"], "PASS")
        self.assertEqual(self.report["failed_tests"], [])
        # machine_uid = UID STM32, printer_id recalé dessus (jamais la MAC).
        self.assertEqual(self.report["machine_uid"], "2D0046000D51353234323830")
        self.assertEqual(self.report["printer_id"],
                         self.report["machine_uid"].upper())
        self.assertEqual(self.report["pad_mac"], "AABBCCDDEEFF")
        self.assertIn("device=C235", self.report["yumi_config"])

    def test_tests_entries_shape(self):
        self.assertEqual(len(self.report["tests"]), len(qc_engine.QC_TESTS))
        for entry in self.report["tests"]:
            self.assertEqual(entry["result"], "pass")
            for key in ("id", "name", "type", "result", "timestamp",
                        "details", "log"):
                self.assertIn(key, entry)
            self.assertIsInstance(entry["log"], list)
            self.assertNotIn("technician", entry)


class TestMachineReportMeasures(unittest.TestCase):
    """L4 : generate_report attache measures + fail_reason (style YMS) à
    chaque entrée tests[] qui a un extracteur et a été exécutée. Additif :
    pas de clé measures sans extracteur, jamais sur skipped/pending."""

    def _report(self, outcomes):
        """Session C235 simulée via le vrai QCEngine.
        outcomes = {test_id: (QCResult, details, logs, timed_out)}."""
        eng = qc_engine.QCEngine()
        eng.start(printer_id="AABBCCDDEEFF", model="C235")
        for test in eng.tests:
            tid = test["id"]
            result, details, logs, timed_out = outcomes.get(
                tid, (qc_engine.QCResult.PASS, "OK", [], False))
            eng._test_log[tid] = list(logs)
            eng.results[tid] = {
                "result": result,
                "timestamp": datetime.now().isoformat(),
                "details": details,
                "duration_s": 12.3,
                "timed_out": timed_out,
            }
        return {e["id"]: e for e in eng.generate_report()["tests"]}

    def test_record_result_captures_duration_and_timed_out(self):
        eng = qc_engine.QCEngine()
        eng.start(printer_id="AABBCCDDEEFF", model="C235")
        eng._record_result("mcu_check", qc_engine.QCResult.PASS)
        r = eng.results["mcu_check"]
        self.assertIsInstance(r["duration_s"], float)
        self.assertGreaterEqual(r["duration_s"], 0.0)
        self.assertIs(r["timed_out"], False)

    def test_z_tap_calib_pass_measures(self):
        entries = self._report({
            "z_tap_calib": (
                qc_engine.QCResult.PASS,
                "OK: 3 taps convergents spread=0.0312mm (tol=0.0500) "
                "sur 15 taps | taps=486.1075, 486.1025, 486.1100",
                [], False),
        })
        m = entries["z_tap_calib"]["measures"]
        self.assertEqual(
            set(m), {"taps_mm", "spread_mm", "tolerance_mm", "n_taps",
                     "converged_n", "fail_reason"})
        self.assertEqual(m["taps_mm"], [486.1075, 486.1025, 486.1100])
        self.assertEqual(m["spread_mm"], 0.0312)
        self.assertEqual(m["tolerance_mm"], 0.05)
        self.assertEqual(m["n_taps"], 3)
        self.assertEqual(m["converged_n"], 3)
        self.assertIsNone(m["fail_reason"])

    def test_heat_bed_pass_measures_ramp_from_duration(self):
        entries = self._report({})
        m = entries["heat_bed"]["measures"]
        self.assertEqual(
            set(m), {"target_c", "reached_c", "ramp_s", "stable",
                     "fail_reason"})
        self.assertEqual(m["target_c"], 60)
        self.assertEqual(m["ramp_s"], 12.3)  # duration_s mesurée par l'engine
        self.assertIsNone(m["reached_c"])    # instrumentation HEAT_OK absente
        self.assertIsNone(m["fail_reason"])

    def test_home_x_fail_timeout_fallback(self):
        entries = self._report({
            "home_x": (qc_engine.QCResult.FAIL,
                       "Timeout 240s depasse sur home_x", [], True),
        })
        m = entries["home_x"]["measures"]
        self.assertEqual(m["axis"], "X")
        self.assertEqual(m["duration_s"], 12.3)
        self.assertEqual(m["fail_reason"], "timeout")

    def test_home_x_fail_signature_beats_fallback(self):
        logs = ["YUMI_SENSORLESS_HOME X: repetabilite NON etablie "
                "(1 taps valides / 3 requis, 2 rejetes)"]
        entries = self._report({
            "home_x": (qc_engine.QCResult.FAIL, "Automated check failed",
                       logs, False),
        })
        m = entries["home_x"]["measures"]
        self.assertEqual(m["fail_reason"], "endstop_not_triggered")
        self.assertEqual(m["taps_valides"], 1)
        self.assertEqual(m["taps_rejetes"], 2)

    def test_fail_without_timeout_is_unknown_fail(self):
        entries = self._report({
            "home_y": (qc_engine.QCResult.FAIL, "Automated check failed",
                       [], False),
        })
        self.assertEqual(entries["home_y"]["measures"]["fail_reason"],
                         "unknown_fail")

    def test_all_executed_tests_have_measures(self):
        """L5 : les 13 tests de la séquence machine ont TOUS un extracteur
        -> toute entrée exécutée (pass/fail) porte measures. Les ids hors
        séquence (bed_mesh, e0_head) restent sans extracteur (test dispatch
        de test_qc_machine_measures)."""
        entries = self._report({})
        for tid, entry in entries.items():
            self.assertIn("measures", entry,
                          "%s doit avoir measures (L5)" % tid)

    def test_skipped_never_has_measures(self):
        entries = self._report({
            "home_x": (qc_engine.QCResult.SKIPPED, "Skipped by operator",
                       [], False),
        })
        self.assertNotIn("measures", entries["home_x"])

    def test_entry_stays_additive(self):
        """Une entrée avec measures garde intactes toutes les clés du
        contrat existant ; measures est la SEULE clé ajoutée."""
        entries = self._report({})
        entry = entries["heat_bed"]
        self.assertEqual(
            set(entry), {"id", "name", "type", "result", "timestamp",
                         "details", "log", "measures"})

    def test_sandbox_report_measures_json_serializable(self):
        """Gate charge réelle : rapport sandbox complet (vrai QCEngine,
        13 tests) — measures présentes sur CHAQUE test exécuté (L5),
        sérialisables."""
        report = _load_sandbox_module().build_report()
        with_measures = [e for e in report["tests"] if "measures" in e]
        self.assertEqual({e["id"] for e in with_measures},
                         {e["id"] for e in report["tests"]})
        self.assertEqual(len(with_measures), 13)
        m = next(e for e in report["tests"]
                 if e["id"] == "z_tap_calib")["measures"]
        self.assertEqual(m["spread_mm"], 0.0312)
        self.assertIsNone(m["fail_reason"])
        m = next(e for e in report["tests"]
                 if e["id"] == "mcu_check")["measures"]
        self.assertEqual(m["mcu_uid"], "2D0046000D51353234323830")
        self.assertTrue(m["yumi_config_found"])
        json.dumps(report)  # lève si une mesure n'est pas sérialisable


if __name__ == "__main__":
    unittest.main()
