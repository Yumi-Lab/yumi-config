import unittest

from qc.qc_yms import find_unready_heat_positions, heat_positions_for_run


class TestHeatPositionsForRun(unittest.TestCase):
    def test_light_never_returns_positions(self):
        self.assertEqual(heat_positions_for_run([], model="light"), [])
        self.assertEqual(heat_positions_for_run([], model=None), [])

    def test_pro_returns_heat_capable_positions(self):
        self.assertEqual(heat_positions_for_run([], model="pro"),
                         [3, 4, 5, 8, 9, 10])

    def test_pro_excludes_disabled(self):
        self.assertEqual(heat_positions_for_run([4, 9], model="pro"),
                         [3, 5, 8, 10])


class TestFindUnreadyHeatPositions(unittest.TestCase):
    def test_all_ready_returns_empty(self):
        temps = {3: 24.1, 4: 25.0, 5: 23.8, 8: 24.5, 9: 24.9, 10: 25.3}
        self.assertEqual(
            find_unready_heat_positions([3, 4, 5, 8, 9, 10], temps), [])

    def test_negative_temperature_flagged(self):
        # Signature d'une sonde debranchee/HS constatee en reel le 26/08
        # (YMS-9 lisait -38.1C, thermistance flottante).
        temps = {3: 24.1, 4: 25.0, 5: 23.8, 8: 24.5, 9: -38.1, 10: 25.3}
        self.assertEqual(
            find_unready_heat_positions([3, 4, 5, 8, 9, 10], temps), [9])

    def test_zero_temperature_flagged(self):
        temps = {3: 24.1, 4: 0.0}
        self.assertEqual(find_unready_heat_positions([3, 4], temps), [4])

    def test_missing_temperature_flagged(self):
        # Objet absent de la reponse Moonraker (requete echouee, etc.)
        temps = {3: 24.1}
        self.assertEqual(find_unready_heat_positions([3, 4], temps), [4])

    def test_multiple_bad_positions(self):
        temps = {3: -1.0, 4: 25.0, 5: None}
        self.assertEqual(find_unready_heat_positions([3, 4, 5], temps), [3, 5])

    def test_empty_positions_returns_empty(self):
        self.assertEqual(find_unready_heat_positions([], {}), [])


if __name__ == "__main__":
    unittest.main()
