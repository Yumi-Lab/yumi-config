import unittest

from qc.qc_machine_measures import (
    HEAT_BED_TARGET_C,
    extract_measures,
)


# ── Fixtures = logs/details RÉELS (docs/AUDIT-MESURES.md, 13/08) ────────────

ZTAP_PASS_DETAILS = (
    "OK: 3 taps convergents spread=0.0000mm (tol=0.0500) sur 15 taps "
    "| taps=486.1075, 486.1025, 486.1075, 486.1100, 486.1075, 486.1075, "
    "486.1075, 486.1075, 486.1075, 486.1075, 486.1075, 486.1075, 486.1075, "
    "486.1075, 486.1075"
)
ZTAP_PASS_LOGS = [
    "Moving to tap point X=117.5 Y=117.5...",
    "Probing with pressure switch...",
    "VALIDATED: trigger_z=486.1075 -> Z=0 pose 0.5500 au-dessus du tap "
    "(compression=0.5500 - z_offset=0.0000)",
]

ZTAP_FAIL_DETAILS = (
    "aucun groupe de 3 taps <= tol: meilleur=0.0700mm > 0.0500 sur 15 taps "
    "| taps=486.1075, 486.1025, 486.2100, 486.1500, 486.1075, 486.3200, "
    "486.1075, 486.1800, 486.1075, 486.2400, 486.1075, 486.1075, 486.3900, "
    "486.1075, 486.1075"
)

ZTAP_TOO_FEW_DETAILS = "Z tap calib: 2 tap(s), il en faut au moins 3"
ZTAP_TOO_FEW_LOGS = [
    "VALIDATED: trigger_z=486.1075 -> Z=0 pose 0.5500 au-dessus du tap "
    "(compression=0.5500 - z_offset=0.0000)",
    "VALIDATED: trigger_z=486.1025 -> Z=0 pose 0.5450 au-dessus du tap "
    "(compression=0.5450 - z_offset=0.0000)",
]

# Repli sans details : 15 lignes VALIDATED dont un groupe de 3 convergents.
ZTAP_FALLBACK_LOGS = [
    "VALIDATED: trigger_z=%.4f -> Z=0 pose 0.5500 au-dessus du tap" % v
    for v in [486.2100, 486.1800, 486.1075, 486.1025, 486.1075, 486.1500,
              486.1075, 486.1075, 486.1075, 486.1075, 486.1075, 486.1075,
              486.1075, 486.1075, 486.1075]
]

HOME_X_PASS_LOGS = [
    "YUMI_SENSORLESS_HOME X: home base... (sgthrs=63)",
    "tap 1: pos=0.0000 gap=4.5210 (1/3)",
    "tap 2: pos=0.0000 gap=4.5180 (2/3)",
    "tap 3: pos=0.0000 gap=4.5130 fenetre=0.0080/0.0500",
    "YUMI_SENSORLESS_HOME X OK: 3 taps valides (0 rejetes) -> moyenne=0.0000 "
    "spread=0.0080mm (tol=0.0500). Zero pose en butee=0.0000",
]

HOME_Y_REJECT_LOGS = [
    "YUMI_SENSORLESS_HOME Y: home base... (sgthrs=58)",
    "tap 1 rejete: gap=0.0120 < min_gap=1.5000 (faux trigger ?)",
    "tap 2: pos=0.0000 gap=4.5210 (1/3)",
    "tap 3: pos=0.0000 gap=4.5180 (2/3)",
    "tap 4: pos=0.0000 gap=4.5130 fenetre=0.0080/0.0500",
    "YUMI_SENSORLESS_HOME Y OK: 3 taps valides (1 rejetes) -> moyenne=0.0000 "
    "spread=0.0080mm (tol=0.0500). Zero pose en butee=0.0000",
]

HOME_X_NO_REPEAT_LOGS = [
    "YUMI_SENSORLESS_HOME X: home base... (sgthrs=63)",
    "tap 1: pos=0.0000 gap=4.5210 (1/3)",
    "tap 2 rejete: gap=0.0100 < min_gap=1.5000 (faux trigger ?)",
    "tap 3 rejete: gap=0.0110 < min_gap=1.5000 (faux trigger ?)",
    "YUMI_SENSORLESS_HOME X: repetabilite NON etablie (1 taps valides / "
    "3 requis, 2 rejetes) -> referentiel non fiable, home avorte",
]

HOME_X_SPREAD_LOGS = [
    "YUMI_SENSORLESS_HOME X: spread=0.2100mm sur 3 taps (tol=0.0500) -> "
    "butee non repetable, home avorte",
]

HOME_X_NO_CONTACT_LOGS = [
    "YUMI_SENSORLESS_HOME X: aucun contact apres 2 re-home(s) -> position "
    "de depart ou butee non fiable, home avorte",
]

TMC_LOGS = [
    "TMC 'stepper_x' reports error: DRV_STATUS: 00150050 s2vsa=1"
    "(ShortToSupply_A!) cs_actual=21",
]

HEAT_BED_PASS_LOGS = []  # la macro n'émet aucun log de température
HEAT_BED_RUNAWAY_LOGS = [
    "Heater heater_bed not heating at expected rate. See the 'verify_heater' "
    "section in docs/Config_Reference.md for the parameters that control "
    "this check.",
]
HEAT_BED_INSTRUMENTED_LOGS = [  # instrumentation additive (pas encore émise)
    "HEAT_OK bed t=60.2 apres 183s",
]


class TestExtractDispatch(unittest.TestCase):
    def test_unknown_test_returns_none(self):
        self.assertIsNone(extract_measures("cutter", [], True))
        self.assertIsNone(extract_measures("fan_part", [], False))

    def test_pass_clears_fail_reason(self):
        m = extract_measures("z_tap_calib", [], True, details=ZTAP_PASS_DETAILS)
        self.assertIsNone(m["fail_reason"])


class TestZTapCalib(unittest.TestCase):
    def test_pass_from_engine_details(self):
        m = extract_measures("z_tap_calib", ZTAP_PASS_LOGS, True,
                             details=ZTAP_PASS_DETAILS)
        self.assertEqual(m["n_taps"], 15)
        self.assertEqual(m["spread_mm"], 0.0)
        self.assertEqual(m["tolerance_mm"], 0.05)
        self.assertEqual(m["converged_n"], 3)
        self.assertEqual(m["taps_mm"][0], 486.1075)
        self.assertIsNone(m["fail_reason"])

    def test_fail_not_converging(self):
        m = extract_measures("z_tap_calib", [], False,
                             details=ZTAP_FAIL_DETAILS)
        self.assertEqual(m["n_taps"], 15)
        self.assertEqual(m["spread_mm"], 0.07)
        self.assertEqual(m["tolerance_mm"], 0.05)
        self.assertEqual(m["converged_n"], 0)
        self.assertEqual(m["fail_reason"], "tap_not_converging")

    def test_fail_too_few_taps(self):
        m = extract_measures("z_tap_calib", ZTAP_TOO_FEW_LOGS, False,
                             details=ZTAP_TOO_FEW_DETAILS)
        self.assertEqual(m["n_taps"], 2)
        self.assertEqual(m["converged_n"], 0)
        self.assertEqual(m["fail_reason"], "too_few_taps")

    def test_fallback_recompute_from_logs(self):
        # Pas de details engine -> re-calcul fenêtré depuis les trigger_z.
        m = extract_measures("z_tap_calib", ZTAP_FALLBACK_LOGS, True)
        self.assertEqual(m["n_taps"], 15)
        # fenêtre la plus resserrée sur la liste triée = 3 taps identiques
        self.assertAlmostEqual(m["spread_mm"], 0.0, places=4)
        self.assertEqual(m["tolerance_mm"], 0.05)
        self.assertEqual(m["converged_n"], 3)

    def test_fallback_not_converging(self):
        logs = ["VALIDATED: trigger_z=%.4f" % v
                for v in (486.10, 486.20, 486.30, 486.40)]
        m = extract_measures("z_tap_calib", logs, False)
        self.assertEqual(m["converged_n"], 0)
        self.assertEqual(m["fail_reason"], "tap_not_converging")

    def test_fail_timeout_no_signature(self):
        m = extract_measures("z_tap_calib", [], False)
        self.assertEqual(m["fail_reason"], "timeout")

    def test_fail_no_timeout_unknown(self):
        m = extract_measures("z_tap_calib", [], False, timed_out=False)
        self.assertEqual(m["fail_reason"], "unknown_fail")

    def test_fail_tmc(self):
        m = extract_measures("z_tap_calib", TMC_LOGS, False)
        self.assertIn("DRV_STATUS", m["tmc_error"])
        self.assertEqual(m["fail_reason"], "tmc_error")


class TestHomeAxis(unittest.TestCase):
    def test_home_x_pass(self):
        m = extract_measures("home_x", HOME_X_PASS_LOGS, True, duration_s=21.4)
        self.assertEqual(m["axis"], "X")
        self.assertEqual(m["sg_thrs"], 63)
        self.assertEqual(m["taps_valides"], 3)
        self.assertEqual(m["taps_rejetes"], 0)
        self.assertEqual(m["spread_mm"], 0.008)
        self.assertEqual(m["tolerance_mm"], 0.05)
        self.assertEqual(m["zero_pos_mm"], 0.0)
        self.assertEqual(m["duration_s"], 21.4)
        self.assertIsNone(m["fail_reason"])

    def test_home_y_pass_with_reject(self):
        m = extract_measures("home_y", HOME_Y_REJECT_LOGS, True)
        self.assertEqual(m["axis"], "Y")
        self.assertEqual(m["sg_thrs"], 58)
        self.assertEqual(m["taps_valides"], 3)
        self.assertEqual(m["taps_rejetes"], 1)
        self.assertIsNone(m["fail_reason"])

    def test_home_x_fail_not_repeatable(self):
        m = extract_measures("home_x", HOME_X_NO_REPEAT_LOGS, False)
        self.assertEqual(m["taps_valides"], 1)
        self.assertEqual(m["taps_rejetes"], 2)
        self.assertEqual(m["fail_reason"], "endstop_not_triggered")

    def test_home_x_fail_spread_too_wide(self):
        m = extract_measures("home_x", HOME_X_SPREAD_LOGS, False)
        self.assertEqual(m["spread_mm"], 0.21)
        self.assertEqual(m["taps_valides"], 3)
        self.assertEqual(m["tolerance_mm"], 0.05)
        self.assertEqual(m["fail_reason"], "spread_too_wide")

    def test_home_x_fail_no_contact(self):
        m = extract_measures("home_x", HOME_X_NO_CONTACT_LOGS, False)
        self.assertEqual(m["fail_reason"], "endstop_not_triggered")

    def test_home_x_fail_timeout_no_signature(self):
        m = extract_measures("home_x", [], False)
        self.assertEqual(m["fail_reason"], "timeout")

    def test_home_y_fail_tmc(self):
        m = extract_measures("home_y", TMC_LOGS, False)
        self.assertEqual(m["fail_reason"], "tmc_error")


class TestHeatBed(unittest.TestCase):
    def test_pass_no_temperature_log(self):
        m = extract_measures("heat_bed", HEAT_BED_PASS_LOGS, True,
                             duration_s=183.0)
        self.assertEqual(m["target_c"], HEAT_BED_TARGET_C)
        self.assertIsNone(m["reached_c"])  # non observable sans instrumentation
        self.assertIsNone(m["stable"])
        self.assertEqual(m["ramp_s"], 183.0)
        self.assertIsNone(m["fail_reason"])

    def test_fail_thermal_runaway(self):
        m = extract_measures("heat_bed", HEAT_BED_RUNAWAY_LOGS, False)
        self.assertEqual(m["fail_reason"], "thermal_runaway")

    def test_fail_thermal_timeout_default(self):
        m = extract_measures("heat_bed", [], False)
        self.assertEqual(m["fail_reason"], "thermal_timeout")

    def test_fail_no_timeout_unknown(self):
        m = extract_measures("heat_bed", [], False, timed_out=False)
        self.assertEqual(m["fail_reason"], "unknown_fail")

    def test_instrumented_heat_ok_forward_compat(self):
        # Ligne HEAT_OK (instrumentation additive future) -> reached_c/stable.
        m = extract_measures("heat_bed", HEAT_BED_INSTRUMENTED_LOGS, True)
        self.assertEqual(m["reached_c"], 60.2)
        self.assertTrue(m["stable"])


if __name__ == "__main__":
    unittest.main()
