import hashlib
import os
import tempfile
import unittest

from qc.qc_machine_measures import (
    HEAT_BED_TARGET_C,
    extract_measures,
    firmware_versions_from_log,
    image_version_from_files,
    klipper_version_from_log,
    qc_cfg_hash,
    software_versions,
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
        # bed_mesh / e0_head : définis dans l'engine mais hors séquence
        # machine (_QC_ORDER) -> sans extracteur, rapport inchangé.
        self.assertIsNone(extract_measures("bed_mesh", [], True))
        self.assertIsNone(extract_measures("e0_head", [], False))
        self.assertIsNone(extract_measures("test_inconnu", [], False))

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
        m = extract_measures("z_tap_calib", [], False, timed_out=True)
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
        m = extract_measures("home_x", [], False, timed_out=True)
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
        m = extract_measures("heat_bed", [], False, timed_out=True)
        self.assertEqual(m["fail_reason"], "thermal_timeout")

    def test_fail_no_timeout_unknown(self):
        m = extract_measures("heat_bed", [], False, timed_out=False)
        self.assertEqual(m["fail_reason"], "unknown_fail")

    def test_instrumented_heat_ok_forward_compat(self):
        # Ligne HEAT_OK (instrumentation additive future) -> reached_c/stable.
        m = extract_measures("heat_bed", HEAT_BED_INSTRUMENTED_LOGS, True)
        self.assertEqual(m["reached_c"], 60.2)
        self.assertTrue(m["stable"])


# ── L5 : fixtures logs RÉELS (docs/AUDIT-MESURES.md, qc_macros.cfg) ──────────

MCU_PASS_LOGS = [
    "[mcu] version: v0.12.0-159-gabcd1234",
    "[mcu] board=CR-FDM-v2.5.s1 device=YUMI-C235 lot=2026-08 uid=ABC123",
    "[mcu] comment: batch 42",
    "[mcu SmartPiOne] version: v0.12.0-159 (host SmartPi One)",
    "MCU_UID=2D0046000D51353234323830",
]
MCU_NO_YUMI_LOGS = [
    "[mcu] version: v0.12.0-159-gabcd1234",
    "QC: aucune identite YUMI gravee sur les MCU (firmware non grave "
    "par le builder firmware Yumi)",
]
MCU_UID_ERROR_LOGS = ["MCU_UID_ERROR: lecture impossible"]

CUTTER_PASS_LOGS = [
    "QC CUTTER: motion sensor YMS-1 a change d'etat (mouvement detecte)",
    "QC CUTTER: extrude 60 + refroidit poop 5s + coupe + retracte 120",
]
CUTTER_HEAD_NOT_REACHED_LOGS = [
    "QC CUTTER: filament pas a la tete apres 800mm (chemin bouche / "
    "moteur / capteur HS)",
]
CUTTER_SENSOR_MUTE_LOGS = [
    "QC CUTTER: filament a la tete mais motion sensor YMS-1 n'a PAS "
    "change d'etat (cablage / capteur HS)",
]

E1_PASS_LOGS = [
    "QC E1_HEAD: motion sensor YMS-2 a change d'etat (mouvement detecte)",
    "QC E1_HEAD: filament a la tete apres 425mm + motion sensor YMS-2 OK",
]
E1_HEAD_NOT_REACHED_LOGS = [
    "QC E1_HEAD: filament pas a la tete apres 800mm (chemin bouche / "
    "moteur / capteur HS)",
]
E1_SENSOR_MUTE_LOGS = [
    "QC E1_HEAD: filament a la tete mais motion sensor YMS-2 n'a PAS "
    "change d'etat (cablage / capteur HS)",
]
E1_ALREADY_LOGS = [
    "QC E1_HEAD: filament deja a la tete avant feed (pas retire ?)",
]

Z_TAP_HOME_PASS_LOGS = [
    "Moving to tap point X=117.5 Y=117.5...",
    "VALIDATED: trigger_z=486.1075 -> Z=0 pose 0.5500 au-dessus du tap "
    "(compression=0.5500 - z_offset=0.0000)",
]
Z_TAP_HOME_PROBE_FAIL_LOGS = [
    "Pressure probe failed: 1/3 stable after 10 taps (2 rejets vibration)",
]

SCREWS_PASS_LOGS = [
    "01:20 means 1 full turn and 20 minutes, CW=clockwise, CCW=counter-clockwise",
    "front left screw (base) : x=49.5, y=175.5, z=0.00000",
    "front right screw : x=224.0, y=175.5, z=0.02000 : adjust CW 00:02",
    "rear right screw : x=224.0, y=2.0, z=0.04123 : adjust CCW 00:19",
    "rear left screw : x=49.5, y=2.0, z=-0.01000 : adjust CCW 00:01",
]
SCREWS_NOT_HOMED_LOGS = ["QC: machine non homee, screws tilt impossible"]
SCREWS_ABORTED_LOGS = [
    "bed level exceeds configured limits (0.1mm)! Adjust screws and "
    "restart print.",
]


class TestMcuCheck(unittest.TestCase):
    def test_pass_full(self):
        m = extract_measures("mcu_check", MCU_PASS_LOGS, True)
        self.assertEqual(m["mcu_uid"], "2D0046000D51353234323830")
        self.assertEqual(m["mcu_count"], 2)
        self.assertEqual(m["firmware_versions"]["mcu"], "v0.12.0-159-gabcd1234")
        self.assertEqual(m["firmware_versions"]["mcu SmartPiOne"],
                         "v0.12.0-159")
        self.assertTrue(m["yumi_config_found"])
        self.assertIsNone(m["fail_reason"])

    def test_fail_no_yumi_config(self):
        m = extract_measures("mcu_check", MCU_NO_YUMI_LOGS, False)
        self.assertEqual(m["mcu_count"], 1)
        self.assertFalse(m["yumi_config_found"])
        self.assertEqual(m["fail_reason"], "no_yumi_config")

    def test_fail_mcu_uid_error(self):
        m = extract_measures("mcu_check", MCU_UID_ERROR_LOGS, False)
        self.assertEqual(m["fail_reason"], "mcu_uid_error")

    def test_fail_timeout_no_signature(self):
        m = extract_measures("mcu_check", [], False, timed_out=True)
        self.assertEqual(m["fail_reason"], "timeout")


class TestFansVisual(unittest.TestCase):
    def test_pass(self):
        for tid in ("fan_motherboard", "fan_part", "fan_hotend"):
            m = extract_measures(tid, [], True)
            self.assertTrue(m["visual_ack"])
            self.assertIsNone(m["fail_reason"])

    def test_fail_operator_reject(self):
        # Verdict opérateur (pas de timeout engine) -> visual_reject.
        m = extract_measures("fan_part", [], False, timed_out=False)
        self.assertFalse(m["visual_ack"])
        self.assertEqual(m["fail_reason"], "visual_reject")

    def test_fail_timeout(self):
        m = extract_measures("fan_hotend", [], False, timed_out=True)
        self.assertEqual(m["fail_reason"], "timeout")


class TestHeatExtruder(unittest.TestCase):
    def test_pass(self):
        m = extract_measures("heat_extruder", [], True, duration_s=95.0)
        self.assertEqual(m["target_c"], 220)
        self.assertEqual(m["ramp_s"], 95.0)
        self.assertIsNone(m["reached_c"])  # instrumentation HEAT_OK absente
        self.assertIsNone(m["fail_reason"])

    def test_fail_runaway(self):
        logs = ["Heater extruder not heating at expected rate."]
        m = extract_measures("heat_extruder", logs, False)
        self.assertEqual(m["fail_reason"], "thermal_runaway")

    def test_fail_thermal_timeout(self):
        m = extract_measures("heat_extruder", [], False, timed_out=True)
        self.assertEqual(m["fail_reason"], "thermal_timeout")

    def test_instrumented_heat_ok_forward_compat(self):
        m = extract_measures("heat_extruder",
                             ["HEAT_OK extruder t=219.8 apres 95s"], True)
        self.assertEqual(m["reached_c"], 219.8)
        self.assertTrue(m["stable"])


class TestCutter(unittest.TestCase):
    def test_pass(self):
        m = extract_measures("cutter", CUTTER_PASS_LOGS, True)
        self.assertTrue(m["motion_first_detect"])
        self.assertTrue(m["cut_ok"])
        self.assertIsNone(m["feed_mm"])  # non loggé en mode cutter
        self.assertIsNone(m["fail_reason"])

    def test_fail_head_not_reached(self):
        m = extract_measures("cutter", CUTTER_HEAD_NOT_REACHED_LOGS, False)
        self.assertEqual(m["feed_mm"], 800)
        self.assertEqual(m["fail_reason"], "head_not_reached")

    def test_fail_sensor_mute(self):
        m = extract_measures("cutter", CUTTER_SENSOR_MUTE_LOGS, False)
        self.assertEqual(m["fail_reason"], "sensor_mute")

    def test_fail_operator_reject(self):
        # Coupe ratée au visuel (pas de timeout, aucune signature feed).
        m = extract_measures("cutter", CUTTER_PASS_LOGS, False, timed_out=False)
        self.assertFalse(m["cut_ok"])
        self.assertEqual(m["fail_reason"], "visual_reject")

    def test_fail_timeout(self):
        m = extract_measures("cutter", [], False, timed_out=True)
        self.assertEqual(m["fail_reason"], "timeout")


class TestE1Head(unittest.TestCase):
    def test_pass_yms_keys_pruned(self):
        m = extract_measures("e1_head", E1_PASS_LOGS, True)
        self.assertEqual(m["feed_mm"], 425)
        self.assertEqual(m["feed_budget_mm"], 800)  # machine, pas 900 (banc)
        self.assertTrue(m["head_reached"])
        self.assertTrue(m["motion_first_detect"])
        self.assertIsNone(m["fail_reason"])
        # Clés propres au banc YMS (stress/dropouts) absentes de la machine.
        for key in ("dropouts_e", "dropout_count", "feed_dropout",
                    "stress_segments_ok", "stress_segments_total",
                    "stress_speeds_mms", "retract_mm"):
            self.assertNotIn(key, m)

    def test_fail_head_not_reached(self):
        m = extract_measures("e1_head", E1_HEAD_NOT_REACHED_LOGS, False)
        self.assertEqual(m["feed_mm"], 800)
        self.assertFalse(m["head_reached"])
        self.assertEqual(m["fail_reason"], "head_not_reached")

    def test_fail_sensor_mute(self):
        m = extract_measures("e1_head", E1_SENSOR_MUTE_LOGS, False)
        self.assertEqual(m["fail_reason"], "sensor_mute")

    def test_fail_already_at_head(self):
        m = extract_measures("e1_head", E1_ALREADY_LOGS, False)
        self.assertEqual(m["fail_reason"], "already_at_head")

    def test_fail_tmc(self):
        m = extract_measures("e1_head", TMC_LOGS, False)
        self.assertEqual(m["fail_reason"], "tmc_error")

    def test_fail_timeout_no_signature(self):
        m = extract_measures("e1_head", [], False, timed_out=True)
        self.assertEqual(m["fail_reason"], "timeout")

    def test_fail_no_timeout_unknown(self):
        m = extract_measures("e1_head", [], False, timed_out=False)
        self.assertEqual(m["fail_reason"], "unknown_fail")


class TestZTapHome(unittest.TestCase):
    def test_pass(self):
        m = extract_measures("z_tap_home", Z_TAP_HOME_PASS_LOGS, True)
        self.assertEqual(m["tap_z_mm"], 486.1075)
        self.assertIsNone(m["z_max_mm"])  # instrumentation ZMAX absente
        self.assertTrue(m["visual_ack"])
        self.assertIsNone(m["fail_reason"])

    def test_fail_probe_not_converging(self):
        m = extract_measures("z_tap_home", Z_TAP_HOME_PROBE_FAIL_LOGS, False)
        self.assertEqual(m["fail_reason"], "tap_not_converging")

    def test_fail_operator_reject(self):
        m = extract_measures("z_tap_home", Z_TAP_HOME_PASS_LOGS, False,
                             timed_out=False)
        self.assertFalse(m["visual_ack"])
        self.assertEqual(m["fail_reason"], "visual_reject")

    def test_instrumented_zmax_forward_compat(self):
        logs = Z_TAP_HOME_PASS_LOGS + ["ZMAX=495.0"]
        m = extract_measures("z_tap_home", logs, True)
        self.assertEqual(m["z_max_mm"], 495.0)


class TestScrewsTilt(unittest.TestCase):
    def test_pass(self):
        m = extract_measures("screws_tilt", SCREWS_PASS_LOGS, True)
        self.assertAlmostEqual(m["corrections"]["front right screw"], 0.0333,
                               places=4)
        self.assertAlmostEqual(m["corrections"]["rear right screw"], -0.3167,
                               places=4)
        self.assertAlmostEqual(m["corrections"]["rear left screw"], -0.0167,
                               places=4)
        self.assertNotIn("front left screw", m["corrections"])  # vis base
        self.assertAlmostEqual(m["max_deviation_mm"], 0.05123, places=5)
        self.assertEqual(m["n_retries"], 0)
        self.assertIsNone(m["fail_reason"])

    def test_fail_not_homed(self):
        m = extract_measures("screws_tilt", SCREWS_NOT_HOMED_LOGS, False)
        self.assertEqual(m["fail_reason"], "not_homed")

    def test_fail_aborted(self):
        m = extract_measures("screws_tilt", SCREWS_ABORTED_LOGS, False)
        self.assertEqual(m["fail_reason"], "screws_tilt_aborted")

    def test_fail_timeout_no_signature(self):
        m = extract_measures("screws_tilt", [], False, timed_out=True)
        self.assertEqual(m["fail_reason"], "timeout")


# ── L6 : versions logicielles (bloc racine software_versions) ───────────────
# logs réels mcu_check (qc_macros.cfg QC_MCU_CHECK) : une ligne version par
# MCU + la ligne hôte (process linux = version Klipper, suffixe "(host ...)").
MCU_VERSION_LOGS = [
    "[mcu] version: v0.12.0-159-gabcd1234",
    "[mcu SmartPiOne] version: v0.12.0-159-gabcd1234 (host SmartPi One)",
    "[mcu] board=CR-FDM-v2.5.s1 device=YUMI-C235 lot=2026-08 uid=ABC123",
    "MCU_UID=2D0046000D51353234323830",
]


class TestSoftwareVersions(unittest.TestCase):
    def test_firmware_versions_from_log(self):
        versions = firmware_versions_from_log(MCU_VERSION_LOGS)
        self.assertEqual(versions, {
            "mcu": "v0.12.0-159-gabcd1234",
            "mcu SmartPiOne": "v0.12.0-159-gabcd1234",  # suffixe host élagué
        })

    def test_klipper_version_from_host_line(self):
        self.assertEqual(klipper_version_from_log(MCU_VERSION_LOGS),
                         "v0.12.0-159-gabcd1234")

    def test_klipper_version_mcu_rpi_fallback(self):
        logs = ["[mcu rpi] version: v0.11.0-53"]
        self.assertEqual(klipper_version_from_log(logs), "v0.11.0-53")

    def test_klipper_version_absent(self):
        self.assertIsNone(klipper_version_from_log(
            ["[mcu] version: v0.12.0-159-gabcd1234"]))

    def test_image_version_first_present_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            absent = os.path.join(tmp, "absent")
            release = os.path.join(tmp, "yumi-release")
            with open(release, "w") as f:
                f.write("2.1.0-20260801\n")
            self.assertEqual(image_version_from_files([absent, release]),
                             "2.1.0-20260801")

    def test_image_version_empty_file_skipped(self):
        with tempfile.TemporaryDirectory() as tmp:
            empty = os.path.join(tmp, "empty")
            with open(empty, "w") as f:
                f.write("\n")
            self.assertIsNone(image_version_from_files([empty]))
            self.assertIsNone(image_version_from_files(
                [os.path.join(tmp, "absent")]))

    def test_qc_cfg_hash_short_sha256(self):
        with tempfile.NamedTemporaryFile("w", suffix=".cfg", delete=False) as f:
            f.write("[gcode_macro _QC_MODE]\nvariable_active: 1\n")
            path = f.name
        try:
            with open(path, "rb") as f:
                expected = hashlib.sha256(f.read()).hexdigest()[:12]
            self.assertEqual(qc_cfg_hash(path), expected)
        finally:
            os.unlink(path)

    def test_qc_cfg_hash_absent(self):
        self.assertIsNone(qc_cfg_hash("/nonexistent/qc_printer_X.cfg"))

    def test_software_versions_full(self):
        sv = software_versions(MCU_VERSION_LOGS, image_version="2.1.0",
                               qc_cfg_version="sha256:0123456789ab")
        self.assertEqual(sv, {
            "klipper_version": "v0.12.0-159-gabcd1234",
            # hôte exclu : firmware_version = MCU réels flashés uniquement
            "firmware_version": {"mcu": "v0.12.0-159-gabcd1234"},
            "image_version": "2.1.0",
            "qc_cfg_version": "sha256:0123456789ab",
        })

    def test_software_versions_tolerant_partial(self):
        sv = software_versions(["[mcu] version: v0.12.0-159-gabcd1234"])
        self.assertEqual(sv, {
            "firmware_version": {"mcu": "v0.12.0-159-gabcd1234"}})

    def test_software_versions_empty(self):
        self.assertEqual(software_versions([]), {})


if __name__ == "__main__":
    unittest.main()
