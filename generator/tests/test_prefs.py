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

    def test_heads_incompatible_with_the_board_are_not_offered(self):
        state = {"situation": "yumi", "main": MAIN, "smartbox": None}
        heads = [o[0] for o in prefs.selection(CATALOG, state, {})["hotend"]["options"]]
        self.assertEqual(heads, ["DIRECT_DRIVE", "CHROMAX_X12"])
        self.assertNotIn("HYPER_DRIVE_UART", heads)

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


class HeadSensor(unittest.TestCase):
    def test_state_and_bypass_go_through_moonraker(self):
        import io, json as _json
        from unittest import mock
        payload = _json.dumps({"result": {"status": {"yumi_filament_head": {"bypass": True, "present": False}}}}).encode()
        with mock.patch("urllib.request.urlopen") as uo:
            uo.return_value.__enter__.return_value = io.BytesIO(payload)
            self.assertEqual(prefs.head_sensor_state(), {"bypass": True, "present": False})
        with mock.patch("urllib.request.urlopen") as uo:
            uo.return_value.__enter__.return_value = io.BytesIO(b'{"result": "ok"}')
            self.assertTrue(prefs.set_head_sensor_bypass(False))
            self.assertIn("SET_HEAD_SENSOR_BYPASS%20ENABLE%3D0", uo.call_args[0][0].full_url)
        with mock.patch("urllib.request.urlopen", side_effect=OSError("down")):
            self.assertIsNone(prefs.head_sensor_state())


class Setup(unittest.TestCase):
    """YUMI_SETUP / prefs.py --set: one line, ids or slicer labels, same prefs file as the panel."""

    def test_resolve_by_id_name_or_slicer_label(self):
        self.assertEqual(prefs.resolve_option(CATALOG, "hotend", "CHROMAX_X12"), "CHROMAX_X12")
        self.assertEqual(prefs.resolve_option(CATALOG, "hotend", "ChromaX12"), "CHROMAX_X12")
        self.assertEqual(prefs.resolve_option(CATALOG, "hotend", "direct drive"), "DIRECT_DRIVE")
        self.assertEqual(prefs.resolve_option(CATALOG, "hotend_type", "Low waste"), "LOW_WASTE")
        self.assertEqual(prefs.resolve_option(CATALOG, "machine", "c335"), "C335")
        self.assertIsNone(prefs.resolve_option(CATALOG, "hotend", "Hyper"))

    def test_apply_settings_writes_the_prefs(self):
        from unittest import mock
        with tempfile.TemporaryDirectory() as d:
            with mock.patch.object(prefs, "set_head_sensor_bypass") as bypass:
                changed = prefs.apply_settings(CATALOG, d, {"HEAD": "ChromaX12", "nozzle": "NOZZLE_06", "HEAD_SENSOR": "0"})
            self.assertEqual(changed, {"hotend": "CHROMAX_X12", "nozzle": "NOZZLE_06", "head_sensor": "bypassed"})
            bypass.assert_called_once_with(True)
            self.assertEqual(prefs.load_prefs(CATALOG, d), {"hotend": "CHROMAX_X12", "nozzle": "NOZZLE_06"})
            with self.assertRaises(ValueError):
                prefs.apply_settings(CATALOG, d, {"HEAD": "Titan"})
            with self.assertRaises(ValueError):
                prefs.apply_settings(CATALOG, d, {"COLOR": "red"})


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
