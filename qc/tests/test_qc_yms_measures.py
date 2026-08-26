import unittest

from qc.qc_yms import extract_measures


PASS_LOGS = [
    "QC E5_HEAD: motion sensor YMS-6 state changed (motion detected)",
    "QC E5_HEAD: loaded 300mm, motion sensor OK -> ready for group stress",
    "QC: YMS-6 encoder dropout E=370.5",
    "QC E5_HEAD: stress 6/6 speed=80mm/s detected=True",
    "QC E5_HEAD: stress OK — 6 segments ±100mm (10→40→80mm/s), sensor tracked throughout",
]

SENSOR_MUTE_LOGS = [
    "QC E10_HEAD: filament at head but motion sensor YMS-11 did NOT change state (wiring / sensor faulty)",
]

LOST_FEED_LOGS = [
    "QC: YMS-6 encoder dropout E=370.5",
    "QC: YMS-6 encoder dropout E=384.8",
    "QC E5_HEAD: motion sensor STOPPED tracking during feed (at 250mm)",
]

TMC_LOGS = [
    "TMC 'extruder_stepper extruder1' reports error: DRV_STATUS: 00150050 s2vsa=1(ShortToSupply_A!) ola=1(OpenLoad_A!) cs_actual=21",
]

NO_MOTION_ON_LOAD_LOGS = [
    "QC E5_HEAD: no motion detected over 300mm (feeder or sensor faulty)",
]

STRESS_LOST_LOGS = [
    "QC E5_HEAD: motion sensor YMS-6 LOST tracking at segment 3/6",
]

STRESS_POINTS_LOGS = [
    "QC E5_HEAD: stress 1/6 speed=10mm/s detected=True",
    "QC E5_HEAD: stress 2/6 speed=10mm/s detected=True",
    "QC E5_HEAD: stress 3/6 speed=40mm/s detected=True",
    "QC E5_HEAD: stress 4/6 speed=40mm/s detected=True",
    "QC E5_HEAD: stress 5/6 speed=80mm/s detected=True",
    "QC E5_HEAD: stress 6/6 speed=80mm/s detected=True",
    "QC E5_HEAD: stress OK — 6 segments ±20mm (10→40→80mm/s), sensor tracked throughout",
]

STRESS_POINTS_LOST_LOGS = [
    "QC E5_HEAD: stress 1/6 speed=10mm/s detected=True",
    "QC E5_HEAD: motion sensor YMS-6 LOST tracking at segment 2/6",
]

HEAT_OK_LOGS = [
    "QC E2_HEAD: heat OK, 85.3C reached (target 85C)",
]

HEAT_TIMEOUT_LOGS = [
    "QC E2_HEAD: heat timeout, 61.2C after 300s (target 85C)",
]

HEAT_CURVE_LOGS = [
    "QC E2_HEAD: heat 0s 21.4C",
    "QC E2_HEAD: heat 10s 34.1C",
    "QC E2_HEAD: heat 20s 48.7C",
    "QC E2_HEAD: heat OK, 85.3C reached (target 85C)",
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

    def test_stress_points_full_sweep(self):
        m = extract_measures(STRESS_POINTS_LOGS, passed=True)
        self.assertEqual(m["stress_points"], [
            {"seg": 1, "speed_mms": 10, "detected": True},
            {"seg": 2, "speed_mms": 10, "detected": True},
            {"seg": 3, "speed_mms": 40, "detected": True},
            {"seg": 4, "speed_mms": 40, "detected": True},
            {"seg": 5, "speed_mms": 80, "detected": True},
            {"seg": 6, "speed_mms": 80, "detected": True},
        ])
        self.assertEqual(m["stress_segments_ok"], 6)
        self.assertEqual(m["stress_segments_total"], 6)

    def test_stress_points_stop_at_loss(self):
        # Segment 2 PERD le suivi -> aucune ligne "stress 2/6 ... detected="
        # n'est jamais emise pour ce segment (le gcode bascule direct sur le
        # marqueur d'echec) -- seul le segment 1 laisse un point.
        m = extract_measures(STRESS_POINTS_LOST_LOGS, passed=False)
        self.assertEqual(m["stress_points"], [
            {"seg": 1, "speed_mms": 10, "detected": True},
        ])
        self.assertEqual(m["fail_reason"], "sensor_lost_stress")


if __name__ == "__main__":
    unittest.main()
