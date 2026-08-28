import unittest

from qc.qc_engine import QCEngine


class TestPerPositionLogRouting(unittest.TestCase):
    """v5 (25/08) : une ligne "QC E<n>_HEAD: ..." va TOUJOURS dans le buffer
    de SA position (test_id "e<n>_head"), quel que soit le test GROUPE en
    cours -- isole chaque position des 11 autres, plus de plafond partage a
    deviner (regression du bug YMS-7/YMS-10 : stress_segments_ok fige a 0)."""

    def setUp(self):
        self.engine = QCEngine()
        self.engine.tests = [{"id": "stress_all"}]
        self.engine.current_test_index = 0

    def test_tagged_line_routes_to_its_own_position_bucket(self):
        self.engine.process_gcode_response("// QC E6_HEAD: stress 1/6 detected=True")
        self.engine.process_gcode_response("// QC E9_HEAD: stress 1/6 detected=True")
        self.assertEqual(self.engine._test_log["e6_head"],
                         ["QC E6_HEAD: stress 1/6 detected=True"])
        self.assertEqual(self.engine._test_log["e9_head"],
                         ["QC E9_HEAD: stress 1/6 detected=True"])
        self.assertNotIn("stress_all", self.engine._test_log)

    def test_untagged_line_falls_back_to_current_test_id(self):
        self.engine.process_gcode_response("// QC:STRESS_ALL:START")
        self.assertEqual(self.engine._test_log["stress_all"], ["QC:STRESS_ALL:START"])

    def test_one_position_saturating_its_cap_does_not_starve_another(self):
        # Avant le fix : les 12 positions partageaient UN SEUL buffer plafonne
        # -> une position en tete de boucle pouvait affamer les suivantes.
        # Desormais chaque position a son PROPRE plafond (900, cf. 28/08 --
        # le vrai capteur pitch_view logge un tick par bascule reelle, de
        # l'ordre de 500-600 sur un run complet) : en saturer une ne doit
        # rien retirer a une autre.
        for i in range(1000):
            self.engine.process_gcode_response(
                "// QC E0_HEAD: stress %d/6 detected=True" % i)
        self.engine.process_gcode_response("// QC E9_HEAD: stress 1/6 detected=True")
        self.assertEqual(len(self.engine._test_log["e0_head"]), 900)
        self.assertEqual(self.engine._test_log["e9_head"],
                         ["QC E9_HEAD: stress 1/6 detected=True"])


class TestPitchLineRouting(unittest.TestCase):
    """v12 (28/08) : filament_yumi_smart_motion_sensor (mode=hold,
    pitch_view) logge "YMS-<n> - Pitch: ..." (PAS le prefixe "QC E<n>_HEAD:")
    -- doit quand meme router vers le buffer de SA position (YMS-n ->
    e(n-1)_head), et UNIQUEMENT pendant le sweep stress (sinon le volume
    d'un tick par bascule, present sur TOUT mouvement, noierait le plafond
    par position avant meme que le sweep ne commence)."""

    def setUp(self):
        self.engine = QCEngine()

    def test_pitch_line_routes_to_its_position_during_stress_all(self):
        self.engine.tests = [{"id": "stress_all"}]
        self.engine.current_test_index = 0
        self.engine.process_gcode_response("// YMS-6 - Pitch: 1.980 mm [NORMAL]")
        self.assertEqual(self.engine._test_log["e5_head"],
                         ["YMS-6 - Pitch: 1.980 mm [NORMAL]"])
        self.assertNotIn("stress_all", self.engine._test_log)

    def test_pitch_line_outside_stress_all_is_dropped_not_rerouted(self):
        # Un tick pendant load_all (chargement) ne doit atterrir NULLE PART
        # -- ni dans e5_head (pas la mesure voulue), ni dans le buffer
        # partage load_all (noierait ses lignes feed_mm utiles).
        self.engine.tests = [{"id": "load_all"}]
        self.engine.current_test_index = 0
        self.engine.process_gcode_response("// YMS-6 - Pitch: 1.980 mm [NORMAL]")
        self.assertNotIn("e5_head", self.engine._test_log)
        self.assertNotIn("load_all", self.engine._test_log)


if __name__ == "__main__":
    unittest.main()
