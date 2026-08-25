import unittest

from qc.qc_yms import extract_measures


PASS_LOGS = [
    "QC E5_HEAD: motion sensor YMS-6 a change d'etat (mouvement detecte)",
    "QC E5_HEAD: charge 300mm, motion sensor OK -> pret pour stress groupe",
    "QC: YMS-6 decrochage encodeur E=370.5",
    "QC E5_HEAD: stress 6/6 detected=True",
    "QC E5_HEAD: stress OK — 6 segments ±100mm (10→40→80mm/s), suivi capteur permanent",
]

SENSOR_MUTE_LOGS = [
    "QC E10_HEAD: filament a la tete mais motion sensor YMS-11 n'a PAS change d'etat (cablage / capteur HS)",
]

LOST_FEED_LOGS = [
    "QC: YMS-6 decrochage encodeur E=370.5",
    "QC: YMS-6 decrochage encodeur E=384.8",
    "QC E5_HEAD: motion sensor a CESSE de suivre pendant le feed (a 250mm)",
]

TMC_LOGS = [
    "TMC 'extruder_stepper extruder1' reports error: DRV_STATUS: 00150050 s2vsa=1(ShortToSupply_A!) ola=1(OpenLoad_A!) cs_actual=21",
]

NO_MOTION_ON_LOAD_LOGS = [
    "QC E5_HEAD: aucun mouvement detecte sur 300mm (feeder ou capteur HS)",
]

STRESS_LOST_LOGS = [
    "QC E5_HEAD: motion sensor YMS-6 a PERDU le suivi au segment 3/6",
]

HEAT_OK_LOGS = [
    "QC E2_HEAD: chauffe OK, 85.3C atteint (cible 85C)",
]

HEAT_TIMEOUT_LOGS = [
    "QC E2_HEAD: chauffe timeout, 61.2C apres 300s (cible 85C)",
]

HEAT_CURVE_LOGS = [
    "QC E2_HEAD: heat 0s 21.4C",
    "QC E2_HEAD: heat 10s 34.1C",
    "QC E2_HEAD: heat 20s 48.7C",
    "QC E2_HEAD: chauffe OK, 85.3C atteint (cible 85C)",
]


class TestExtractMeasures(unittest.TestCase):
    def test_pass_yms6(self):
        m = extract_measures(PASS_LOGS, passed=True)
        self.assertEqual(m["feed_mm"], 300)
        self.assertFalse(m["head_reached"])  # v3 : plus jamais verifie
        self.assertTrue(m["motion_first_detect"])
        self.assertEqual(m["dropouts_e"], [370.5])
        self.assertEqual(m["dropout_count"], 1)
        self.assertEqual(m["stress_segments_ok"], 6)
        self.assertEqual(m["stress_segments_total"], 6)
        self.assertEqual(m["retract_mm"], 300)
        self.assertIsNone(m["fail_reason"])

    def test_fail_sensor_mute(self):
        m = extract_measures(SENSOR_MUTE_LOGS, passed=False)
        self.assertFalse(m["motion_first_detect"])
        self.assertEqual(m["fail_reason"], "sensor_mute")

    def test_fail_lost_feed(self):
        m = extract_measures(LOST_FEED_LOGS, passed=False)
        self.assertTrue(m["feed_dropout"])
        self.assertEqual(m["feed_mm"], 250)
        self.assertEqual(m["dropout_count"], 2)
        self.assertEqual(m["fail_reason"], "sensor_lost_feed")

    def test_fail_tmc(self):
        m = extract_measures(TMC_LOGS, passed=False)
        self.assertIn("DRV_STATUS", m["tmc_error"])
        self.assertEqual(m["fail_reason"], "tmc_error")

    def test_fail_no_motion_on_load(self):
        m = extract_measures(NO_MOTION_ON_LOAD_LOGS, passed=False)
        self.assertEqual(m["feed_mm"], 300)
        self.assertFalse(m["head_reached"])
        self.assertEqual(m["fail_reason"], "no_motion_on_load")

    def test_fail_timeout_no_signature(self):
        m = extract_measures([], passed=False)
        self.assertEqual(m["fail_reason"], "timeout")

    def test_fail_stress_lost(self):
        m = extract_measures(STRESS_LOST_LOGS, passed=False)
        self.assertEqual(m["fail_reason"], "sensor_lost_stress")

    def test_pass_heat_ok(self):
        m = extract_measures(HEAT_OK_LOGS, passed=True)
        self.assertEqual(m["heat_target_c"], 85)
        self.assertEqual(m["heat_reached_c"], 85.3)
        self.assertIsNone(m["fail_reason"])

    def test_fail_heat_timeout(self):
        m = extract_measures(HEAT_TIMEOUT_LOGS, passed=False)
        self.assertEqual(m["heat_target_c"], 85)
        self.assertEqual(m["heat_reached_c"], 61.2)
        self.assertEqual(m["fail_reason"], "heat_timeout")

    def test_heat_curve_points(self):
        m = extract_measures(HEAT_CURVE_LOGS, passed=True)
        self.assertEqual(m["heat_curve"],
                         [[0, 21.4], [10, 34.1], [20, 48.7]])
        self.assertEqual(m["heat_reached_c"], 85.3)


if __name__ == "__main__":
    unittest.main()
