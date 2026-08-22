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

    def test_build_yms_tests_light_has_no_heat_step(self):
        # model="light" (ou absent) : jamais de heat_all, meme sur des
        # positions cablees chauffe (3,4,5).
        tests = build_yms_tests([], model="light")
        self.assertNotIn("heat_all", [t["id"] for t in tests])
        tests_default = build_yms_tests([])
        self.assertNotIn("heat_all", [t["id"] for t in tests_default])

    def test_build_yms_tests_pro_adds_heat_step_capable_positions_only(self):
        # model="pro" : heat_all ajoute, TOOLS= seulement les positions a la
        # fois ACTIVES et CABLEES chauffe (3,4,5,8,9,10) -- 1,2,6,7,11,12
        # n'ont jamais cette option quel que soit le modele.
        tests = build_yms_tests([], model="pro")
        self.assertEqual(tests[-1]["id"], "heat_all")
        self.assertEqual(tests[-1]["macro"], "QC_HEAT_ALL TOOLS=3,4,5,8,9,10 TARGET=85")

    def test_build_yms_tests_pro_restricts_load_and_stress_too(self):
        # v3 (23/08) : "quand on fait les YMS Pro, on ne fait que le
        # 3,4,5,8,9,10, on ne fait pas les autres" -- en model="pro", TOUTES
        # les etapes (load_all/stress_all/heat_all) sont restreintes aux
        # positions cablees chauffe, pas seulement heat_all. Un boitier Pro
        # ne se place jamais sur 1/2/6/7/11/12.
        tests = build_yms_tests([], model="pro")
        by_id = {t["id"]: t for t in tests}
        self.assertEqual(by_id["load_all"]["macro"],
                         "QC_LOAD_ALL TOOLS=3,4,5,8,9,10 DIST=300")
        self.assertEqual(by_id["stress_all"]["macro"],
                         "QC_STRESS_ALL TOOLS=3,4,5,8,9,10")

    def test_build_yms_tests_pro_no_heat_step_if_no_capable_position_active(self):
        # Lot restreint a des positions NON cablees chauffe (1,2,6,7,11,12
        # actives, tout le reste desactive) -> pas de heat_all du tout, meme
        # en model="pro".
        disabled = [3, 4, 5, 8, 9, 10]
        tests = build_yms_tests(disabled, model="pro")
        self.assertNotIn("heat_all", [t["id"] for t in tests])

    def test_yms_code_for_position_skips_disabled(self):
        yms_ids = ["YMSL-001", "YMSL-003", "YMSL-004"]
        # positions 1,3,4 enabled (2 disabled)
        self.assertEqual(yms_code_for_position(1, yms_ids, [2]), "YMSL-001")
        self.assertEqual(yms_code_for_position(3, yms_ids, [2]), "YMSL-003")
        self.assertEqual(yms_code_for_position(4, yms_ids, [2]), "YMSL-004")


if __name__ == "__main__":
    unittest.main()
