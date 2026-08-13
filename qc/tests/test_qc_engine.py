import importlib.util
import os
import unittest

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


if __name__ == "__main__":
    unittest.main()
