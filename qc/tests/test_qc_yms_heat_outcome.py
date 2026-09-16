import unittest

from qc.qc_yms import (HEAT_FAIL, HEAT_MISSING, HEAT_OK, HEAT_WAIT_TEST_TIMEOUT_S,
                       HEAT_WAIT_TIMEOUT_S, build_retest_sequence, build_yms_tests,
                       heat_outcome)


class TestHeatOutcome(unittest.TestCase):
    """16/09/2026 : six boitiers a 35 C etiquetes PASS parce que leurs lignes de
    verdict de chauffe sont arrivees apres la cloture du test -- sans verdict,
    jamais de PASS."""

    def test_ok(self):
        self.assertEqual(heat_outcome(["QC E2_HEAD: heat 350s 84.2C",
                                       "QC E2_HEAD: heat OK, 85.3C reached (target 85C)"]), HEAT_OK)

    def test_timeout_is_fail(self):
        self.assertEqual(heat_outcome(["QC E2_HEAD: heat timeout, 82.2C after 360s (target 85C)"]), HEAT_FAIL)

    def test_samples_without_verdict_are_missing(self):
        logs = ["QC E2_HEAD: loaded 80mm, motion sensor OK -> ready for group stress",
                "QC E2_HEAD: stress OK — 8 segments"] + ["QC E2_HEAD: heat %ds 35.1C" % t for t in range(0, 360, 10)]
        self.assertEqual(heat_outcome(logs), HEAT_MISSING)

    def test_empty_is_missing(self):
        self.assertEqual(heat_outcome([]), HEAT_MISSING)


class TestHeatWaitTimeouts(unittest.TestCase):
    """Une seule source pour le timeout de la macro (passe en TIMEOUT=) et le
    budget du wizard, qui garde 120 s de marge sur le rejeu des reponses."""

    def _heat_wait(self, tests):
        return next(t for t in tests if t["id"] == "heat_wait")

    def test_sequence_passes_macro_timeout_and_keeps_margin(self):
        hw = self._heat_wait(build_yms_tests([], model="pro"))
        self.assertIn("TIMEOUT=%d" % HEAT_WAIT_TIMEOUT_S, hw["macro"])
        self.assertEqual(hw["timeout"], HEAT_WAIT_TEST_TIMEOUT_S)
        self.assertGreaterEqual(HEAT_WAIT_TEST_TIMEOUT_S - HEAT_WAIT_TIMEOUT_S, 120)

    def test_retest_sequence_same_budget(self):
        hw = self._heat_wait(build_retest_sequence(3, model="pro"))
        self.assertIn("TIMEOUT=%d" % HEAT_WAIT_TIMEOUT_S, hw["macro"])
        self.assertEqual(hw["timeout"], HEAT_WAIT_TEST_TIMEOUT_S)


if __name__ == "__main__":
    unittest.main()
