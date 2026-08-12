import unittest
from datetime import datetime, timedelta

from qc.qc_yms import build_box_report, build_retest_sequence


class TestRetest(unittest.TestCase):
    def test_retest_sequence(self):
        tests = build_retest_sequence(6)
        self.assertEqual(len(tests), 2)
        self.assertEqual(tests[0]["id"], "mcu_check")
        self.assertTrue(tests[0].get("skipped"))
        self.assertEqual(tests[1]["id"], "e5_head")
        self.assertEqual(tests[1]["macro"], "QC_HEAD_FEED TOOL=6")

    def test_retest_report_single_code(self):
        r = build_box_report(
            test_id="e5_head",
            result="PASS",
            yms_ids=["YMSL-RET-01"],
            session="S",
            pad_mac="MAC",
            technician="op",
            test_log={"e5_head": [], "mcu_check": []},
            engine_results={
                "mcu_check": {"result": "skipped"},
                "e5_head": {"result": "pass"},
            },
            retest=True,
        )
        self.assertEqual(r["printer_id"], "YMSL-RET-01")
        self.assertEqual(r["bench_position"], 6)
        self.assertEqual(r["overall_result"], "PASS")
        self.assertEqual(r["tests"][0]["result"], "skipped")


if __name__ == "__main__":
    unittest.main()
