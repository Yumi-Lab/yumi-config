"""prefs.py — the wizard's logic (no GTK): situations, choices, result text."""
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
import compose    # noqa: E402
import generator  # noqa: E402
import prefs      # noqa: E402

CATALOG = generator.load_catalog()
MAIN = {"port": "/dev/ttyS1", "uid": "aaaaaa", "board": "SMART_MAKER_1_X", "device": "C235"}


class Situations(unittest.TestCase):
    def test_never_scanned(self):
        self.assertEqual(prefs.situation({}), prefs.SITUATION_NONE)
        self.assertIn("not scanned", prefs.describe(CATALOG, {})[0])

    def test_yumi_machine_only_asks_for_the_head(self):
        state = {"situation": "yumi", "main": MAIN, "smartbox": None, "composition": {"boards": [MAIN]}}
        sel = prefs.selection(CATALOG, state, {})
        self.assertEqual(list(sel), list(prefs.HEAD_LAYERS))
        self.assertEqual(sel["hotend"]["value"], CATALOG["detection"]["defaults"]["hotend"])
        self.assertFalse(sel["hotend"]["forced"])
        self.assertIn("YUMI C235", prefs.describe(CATALOG, state)[0])

    def test_smartbox_forces_the_head(self):
        sb = {"port": "/dev/ttyS2", "uid": "bbbbbb", "board": "SMART_MAKER_1_X", "device": "HYPERDRIVE_3P2L"}
        state = {"situation": "yumi", "main": MAIN, "smartbox": sb}
        sel = prefs.selection(CATALOG, state, {"hotend": "DIRECT_DRIVE"})
        self.assertTrue(sel["hotend"]["forced"])
        self.assertEqual(sel["hotend"]["value"], CATALOG["detection"]["with_smartbox"]["hotend"])

    def test_unknown_machine_adds_the_machine_layer_first(self):
        b = dict(MAIN, device="C999")
        state = {"situation": "unknown", "main": None, "smartbox": None, "composition": {"boards": [b]}}
        sel = prefs.selection(CATALOG, state, {"machine": "C335"})
        self.assertEqual(list(sel)[0], prefs.MACHINE_LAYER)
        self.assertEqual(sel["machine"]["value"], "C335")
        self.assertEqual([o[0] for o in sel["machine"]["options"]], ["C235", "C335", "C435"])
        self.assertIn("names no known machine", prefs.describe(CATALOG, state)[0])

    def test_options_come_from_the_catalog_layers(self):
        self.assertEqual([o[0] for o in prefs.layer_options(CATALOG, "hotend")],
                         [cid for cid, c in CATALOG["components"].items() if c.get("layer") == "hotend"])
        self.assertEqual(prefs.layer_label(CATALOG, "hotend"), "Print Head")


class Prefs(unittest.TestCase):
    def test_round_trip(self):
        with tempfile.TemporaryDirectory() as d:
            prefs.save_prefs(CATALOG, d, {"hotend": "CHROMAX_X12", "nozzle": "NOZZLE_06"})
            self.assertEqual(prefs.load_prefs(CATALOG, d), {"hotend": "CHROMAX_X12", "nozzle": "NOZZLE_06"})
            # what compose reads is the same file
            self.assertEqual(prefs.prefs_path(CATALOG, d), Path(d) / CATALOG["detection"]["prefs_file"])


class Results(unittest.TestCase):
    def test_written(self):
        lines = prefs.result_lines(compose.EXIT_APPLIED, {"product": "C235_DD_LW_04", "mode": "preserve",
                                                          "backup": "/x/printer-1-autoconfig.cfg", "reasons": ["r1"]}, "")
        self.assertEqual(lines[0], "printer.cfg written: C235_DD_LW_04 (preserve)")
        self.assertIn("printer-1-autoconfig.cfg", lines[1])
        self.assertEqual(lines[-1], "r1")

    def test_alert_and_failure(self):
        self.assertIn("no MCU answered", prefs.result_lines(compose.EXIT_ALERT, {"alert": "no MCU answered", "reasons": []}, "")[0])
        self.assertIn("exit 1", prefs.result_lines(1, None, "boom")[0])
        self.assertEqual(prefs.result_lines(compose.EXIT_UNCHANGED, {"reasons": []}, "")[0],
                         "printer.cfg already matches the boards: nothing changed.")


if __name__ == "__main__":
    unittest.main()
