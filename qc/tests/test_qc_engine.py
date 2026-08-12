import unittest

from qc import qc_engine


class TestQCEngineYMS12(unittest.TestCase):
    def test_yms12_sequence_has_13_tests(self):
        self.assertEqual(len(qc_engine.QC_TESTS_YMS12), 13)

    def test_yms12_macros_cover_tools_1_to_12(self):
        macros = [t["macro"] for t in qc_engine.QC_TESTS_YMS12 if t["id"].startswith("e")]
        self.assertEqual(len(macros), 12)
        for i, macro in enumerate(macros, start=1):
            self.assertIn(f"TOOL={i}", macro)


if __name__ == "__main__":
    unittest.main()
