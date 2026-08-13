import unittest

from qc.qc_yms import extract_measures


PASS_LOGS = [
    "QC E5_HEAD: motion sensor YMS-6 a change d'etat (mouvement detecte)",
    "QC E5_HEAD: filament a la tete apres 625mm + motion sensor YMS-6 OK -> stress aller-retour",
    "QC: YMS-6 decrochage encodeur E=370.5",
    "QC E5_HEAD: stress 16/16 detected=True",
    "QC E5_HEAD: stress OK — 16 segments ±100mm (10→100→10mm/s), suivi capteur permanent",
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

HEAD_NOT_REACHED_LOGS = [
    "QC E5_HEAD: filament pas a la tete apres 900mm (chemin bouche / moteur / capteur HS)",
]

STRESS_LOST_LOGS = [
    "QC E5_HEAD: motion sensor YMS-6 a PERDU le suivi au segment 3/16",
]


class TestExtractMeasures(unittest.TestCase):
    def test_pass_yms6(self):
        m = extract_measures(PASS_LOGS, passed=True)
        self.assertEqual(m["feed_mm"], 625)
        self.assertTrue(m["head_reached"])
        self.assertTrue(m["motion_first_detect"])
        self.assertEqual(m["dropouts_e"], [370.5])
        self.assertEqual(m["dropout_count"], 1)
        self.assertEqual(m["stress_segments_ok"], 16)
        self.assertEqual(m["stress_segments_total"], 16)
        self.assertEqual(m["retract_mm"], 625)
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

    def test_fail_head_not_reached(self):
        m = extract_measures(HEAD_NOT_REACHED_LOGS, passed=False)
        self.assertEqual(m["feed_mm"], 900)
        self.assertFalse(m["head_reached"])
        self.assertEqual(m["fail_reason"], "head_not_reached")

    def test_fail_timeout_no_signature(self):
        m = extract_measures([], passed=False)
        self.assertEqual(m["fail_reason"], "timeout")

    def test_fail_stress_lost(self):
        m = extract_measures(STRESS_LOST_LOGS, passed=False)
        self.assertEqual(m["fail_reason"], "sensor_lost_stress")


if __name__ == "__main__":
    unittest.main()
