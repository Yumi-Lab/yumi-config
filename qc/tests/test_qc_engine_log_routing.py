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
        # Desormais chaque position a son PROPRE plafond (80, cf. 27/08 --
        # les lignes "pitch" du sous-echantillonnage font monter le volume
        # par position au-dela des 40 d'origine) : en sature une ne doit
        # rien retirer a une autre.
        for i in range(100):
            self.engine.process_gcode_response(
                "// QC E0_HEAD: stress %d/6 detected=True" % i)
        self.engine.process_gcode_response("// QC E9_HEAD: stress 1/6 detected=True")
        self.assertEqual(len(self.engine._test_log["e0_head"]), 80)
        self.assertEqual(self.engine._test_log["e9_head"],
                         ["QC E9_HEAD: stress 1/6 detected=True"])


if __name__ == "__main__":
    unittest.main()
