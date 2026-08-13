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
        self.assertEqual(len(tests), 13)
        self.assertEqual(tests[0]["id"], "mcu_check")
        ids = [t["id"] for t in tests[1:]]
        self.assertEqual(ids, ["e%d_head" % i for i in range(12)])
        self.assertTrue(tests[2]["skipped"])   # e1_head = position 2
        self.assertTrue(tests[11]["skipped"])  # e10_head = position 11
        self.assertFalse(tests[1].get("skipped"))  # e0_head = position 1
        self.assertEqual(tests[2]["macro"], "")
        self.assertIn("TOOL=1", tests[1]["macro"])

    def test_yms_code_for_position_skips_disabled(self):
        yms_ids = ["YMSL-001", "YMSL-003", "YMSL-004"]
        # positions 1,3,4 enabled (2 disabled)
        self.assertEqual(yms_code_for_position(1, yms_ids, [2]), "YMSL-001")
        self.assertEqual(yms_code_for_position(3, yms_ids, [2]), "YMSL-003")
        self.assertEqual(yms_code_for_position(4, yms_ids, [2]), "YMSL-004")


if __name__ == "__main__":
    unittest.main()
