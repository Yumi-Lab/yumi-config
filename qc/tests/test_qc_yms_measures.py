import unittest

from qc.qc_yms import extract_measures


PASS_LOGS = [
    "QC E5_HEAD: motion sensor YMS-6 state changed (motion detected)",
    "QC E5_HEAD: loaded 300mm, motion sensor OK -> ready for group stress",
    "QC: YMS-6 encoder dropout E=370.5",
    "QC E5_HEAD: stress 8/16 speed=80mm/s detected=True counted=True",
    "QC E5_HEAD: stress OK — 8 segments ±70mm (10→30→50→80→80→50→30→10mm/s), sensor tracked throughout",
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
    "QC E5_HEAD: motion sensor YMS-6 LOST tracking at segment 7/16",
]

STRESS_LOST_IN_RAMP_LOGS = [
    # Decroche pendant la rampe (segment 2, non compte) -- ignore, jamais de
    # fail_reason (demande du 26/08 : seul le plateau 50-80mm/s compte).
    "QC E5_HEAD: motion sensor YMS-6 lost tracking during ramp segment 2/16 (ignored, not counted)",
]

# Rampe complete 16 segments : 1-4 et 13-16 = rampe (non comptes),
# 5-12 = plateau 50-80mm/s (comptes) -- cf. generate_yms12_cfg.py
# _qc_stress_all_step, "counted = seg > 4 and seg <= (nseg - 4)".
STRESS_POINTS_LOGS = [
    "QC E5_HEAD: stress 1/16 speed=10mm/s detected=True counted=False",
    "QC E5_HEAD: stress 2/16 speed=10mm/s detected=True counted=False",
    "QC E5_HEAD: stress 3/16 speed=30mm/s detected=True counted=False",
    "QC E5_HEAD: stress 4/16 speed=30mm/s detected=True counted=False",
    "QC E5_HEAD: stress 5/16 speed=50mm/s detected=True counted=True",
    "QC E5_HEAD: stress 6/16 speed=50mm/s detected=True counted=True",
    "QC E5_HEAD: stress 7/16 speed=80mm/s detected=True counted=True",
    "QC E5_HEAD: stress 8/16 speed=80mm/s detected=True counted=True",
    "QC E5_HEAD: stress 9/16 speed=80mm/s detected=True counted=True",
    "QC E5_HEAD: stress 10/16 speed=80mm/s detected=True counted=True",
    "QC E5_HEAD: stress 11/16 speed=50mm/s detected=True counted=True",
    "QC E5_HEAD: stress 12/16 speed=50mm/s detected=True counted=True",
    "QC E5_HEAD: stress 13/16 speed=30mm/s detected=True counted=False",
    "QC E5_HEAD: stress 14/16 speed=30mm/s detected=True counted=False",
    "QC E5_HEAD: stress 15/16 speed=10mm/s detected=True counted=False",
    "QC E5_HEAD: stress 16/16 speed=10mm/s detected=True counted=False",
    "QC E5_HEAD: stress OK — 8 segments ±70mm (10→30→50→80→80→50→30→10mm/s), sensor tracked throughout",
]

STRESS_POINTS_LOST_LOGS = [
    "QC E5_HEAD: stress 1/16 speed=10mm/s detected=True counted=False",
    "QC E5_HEAD: motion sensor YMS-6 LOST tracking at segment 5/16",
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
        # La ligne recap "stress OK — 8 segments" (fin de sweep) prime sur le
        # comptage par segment (une seule ligne par-segment ici, non
        # exhaustive) -- comportement voulu, cf. extract_measures.
        self.assertEqual(m["stress_segments_ok"], 8)
        self.assertEqual(m["stress_segments_total"], 8)
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

    def test_stress_lost_in_ramp_is_ignored(self):
        # Un decrochage pendant la rampe (non compte) ne doit JAMAIS
        # produire de fail_reason -- seul le plateau compte pour le verdict.
        m = extract_measures(STRESS_LOST_IN_RAMP_LOGS, passed=True)
        self.assertIsNone(m["fail_reason"])

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

    def test_stress_points_full_ramp(self):
        m = extract_measures(STRESS_POINTS_LOGS, passed=True)
        self.assertEqual(len(m["stress_points"]), 16)
        # Segments 1-4 et 13-16 : rampe, non comptes.
        for sp in m["stress_points"][:4] + m["stress_points"][12:]:
            self.assertFalse(sp["counted"])
        # Segments 5-12 : plateau 50-80mm/s, comptes.
        counted = m["stress_points"][4:12]
        self.assertTrue(all(sp["counted"] for sp in counted))
        self.assertEqual([sp["speed_mms"] for sp in counted],
                         [50, 50, 80, 80, 80, 80, 50, 50])
        # Seuls les 8 segments comptes entrent dans le verdict/total affiche
        # ("mesure propre", hors rampe accel/decel).
        self.assertEqual(m["stress_segments_ok"], 8)
        self.assertEqual(m["stress_segments_total"], 8)

    def test_stress_points_stop_at_loss(self):
        # Le decrochage au segment 5 (premier segment COMPTE) arrete la
        # position -> aucune ligne "stress" n'est plus emise pour elle
        # ensuite, seul le segment 1 (rampe) laisse un point.
        m = extract_measures(STRESS_POINTS_LOST_LOGS, passed=False)
        self.assertEqual(len(m["stress_points"]), 1)
        self.assertFalse(m["stress_points"][0]["counted"])
        self.assertEqual(m["fail_reason"], "sensor_lost_stress")
        # Aucun segment compte n'a reussi -> stress_segments_ok reste a 0.
        self.assertEqual(m["stress_segments_ok"], 0)

    def test_stress_pitches_parsed_with_edges_flagged(self):
        logs = [
            "QC E5_HEAD: pitch seg=5/16 sub=1/4 E=87.50 detected=True edge=True",
            "QC E5_HEAD: pitch seg=5/16 sub=2/4 E=105.00 detected=True edge=False",
            "QC E5_HEAD: pitch seg=5/16 sub=3/4 E=122.50 detected=True edge=False",
            "QC E5_HEAD: pitch seg=5/16 sub=4/4 E=140.00 detected=True edge=True",
        ]
        m = extract_measures(logs, passed=True)
        self.assertEqual(len(m["stress_pitches"]), 4)
        self.assertEqual(m["stress_pitches"][0],
                         {"seg": 5, "sub": 1, "e_mm": 87.5,
                          "detected": True, "edge": True})
        # Seuls le 1er et le dernier point du segment sont "edge" -- les 2
        # du milieu ne le sont pas (demande du 27/08 : sortir les extremes,
        # possible decalage mecanique en debut/fin de segment).
        self.assertEqual([p["edge"] for p in m["stress_pitches"]],
                         [True, False, False, True])

    def test_stress_pitches_absent_on_ramp_segments(self):
        # La rampe (segments non comptes) reste un seul G1 -- aucune ligne
        # "pitch" n'est jamais emise pour elle (seulement le plateau compte).
        logs = ["QC E5_HEAD: stress 2/16 speed=10mm/s detected=True counted=False"]
        m = extract_measures(logs, passed=True)
        self.assertEqual(m["stress_pitches"], [])


if __name__ == "__main__":
    unittest.main()
