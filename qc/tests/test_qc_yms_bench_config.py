import json
import os
import tempfile
import unittest

from qc.qc_yms import BENCH_CONFIG_DEFAULTS, load_bench_config


class TestLoadBenchConfig(unittest.TestCase):
    """qc_bench_config.json = config PAR BANC (version produit, composants montés,
    repère visuel du banc sur l'étiquette)."""

    def _load(self, payload):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "qc_bench_config.json")
            with open(path, "w", encoding="utf-8") as f:
                f.write(payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False))
            return load_bench_config(path)

    def test_missing_file_gives_defaults(self):
        cfg = load_bench_config("/nonexistent/qc_bench_config.json")
        self.assertEqual(cfg, BENCH_CONFIG_DEFAULTS)
        self.assertEqual(cfg["bench_mark"], "")

    def test_bench_mark_read_and_stripped(self):
        cfg = self._load({"yms_version": "1.0", "bench_mark": " ● "})
        self.assertEqual(cfg["bench_mark"], "●")

    def test_bench_mark_ignored_unless_non_empty_string(self):
        self.assertEqual(self._load({"bench_mark": ""})["bench_mark"], "")
        self.assertEqual(self._load({"bench_mark": 2})["bench_mark"], "")
        self.assertEqual(self._load({"bench_mark": None})["bench_mark"], "")

    def test_existing_keys_untouched_by_the_new_one(self):
        cfg = self._load({"yms_version": "1.1", "extruder_model": "MK12",
                          "spring_model": "1.0*8*20", "bench_mark": "●"})
        self.assertEqual(cfg["yms_version"], "1.1")
        self.assertEqual(cfg["extruder_model"], "MK12")
        self.assertEqual(cfg["spring_model"], "1.0*8*20")

    def test_invalid_json_gives_defaults(self):
        self.assertEqual(self._load("{not json"), BENCH_CONFIG_DEFAULTS)


if __name__ == "__main__":
    unittest.main()
