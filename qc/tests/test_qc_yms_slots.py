import os
import tempfile
import unittest

from qc.qc_yms import (
    build_yms_tests,
    enabled_positions,
    load_disabled_positions,
    yms_code_for_position,
)


class TestDisabledSlots(unittest.TestCase):
    def test_load_valid(self):
        with tempfile.NamedTemporaryFile("w", delete=False) as f:
            f.write('{"disabled": [2, 11]}')
            path = f.name
        try:
            self.assertEqual(load_disabled_positions(path), [2, 11])
        finally:
            os.unlink(path)

    def test_load_absent_returns_empty(self):
        self.assertEqual(load_disabled_positions("/nonexistent/qc_bench_slots.json"), [])

    def test_load_invalid_returns_empty(self):
        with tempfile.NamedTemporaryFile("w", delete=False) as f:
            f.write("not json")
            path = f.name
        try:
            self.assertEqual(load_disabled_positions(path), [])
        finally:
            os.unlink(path)

    def test_enabled_positions(self):
        self.assertEqual(enabled_positions([2, 11]),
                         [1, 3, 4, 5, 6, 7, 8, 9, 10, 12])
        self.assertEqual(enabled_positions([]), list(range(1, 13)))

    def test_build_yms_tests_includes_skipped(self):
        tests = build_yms_tests([2, 11])
        # v3 (23/08) : mcu_check + load_all (groupé) + stress_all (groupé) --
        # plus de test individuel par position (plus de capteur tête à
        # atteindre séquentiellement). TOOLS= porte les positions ACTIVES
        # seulement (2 et 11 désactivées -> absentes), source de vérité
        # indépendante de tout état firmware résiduel d'un run précédent.
        self.assertEqual(len(tests), 3)
        self.assertEqual(tests[0]["id"], "mcu_check")
        self.assertEqual(tests[1]["id"], "load_all")
        self.assertEqual(tests[1]["macro"],
                         "QC_LOAD_ALL TOOLS=1,3,4,5,6,7,8,9,10,12 DIST=300")
        self.assertEqual(tests[2]["id"], "stress_all")
        self.assertEqual(tests[2]["macro"],
                         "QC_STRESS_ALL TOOLS=1,3,4,5,6,7,8,9,10,12")

    def test_yms_code_for_position_skips_disabled(self):
        yms_ids = ["YMSL-001", "YMSL-003", "YMSL-004"]
        # positions 1,3,4 enabled (2 disabled)
        self.assertEqual(yms_code_for_position(1, yms_ids, [2]), "YMSL-001")
        self.assertEqual(yms_code_for_position(3, yms_ids, [2]), "YMSL-003")
        self.assertEqual(yms_code_for_position(4, yms_ids, [2]), "YMSL-004")


if __name__ == "__main__":
    unittest.main()
