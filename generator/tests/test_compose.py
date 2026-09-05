"""compose.py — composition -> product -> printer.cfg (stdlib unittest, no hardware)."""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
import compose      # noqa: E402
import generator    # noqa: E402

CATALOG = generator.load_catalog()


def board(port, device, uid, kind="main"):
    """A board entry as yumi-detect.py reports it."""
    if kind == "main":
        return {"port": port, "uid": uid, "board": "SMART_MAKER_1_X", "cpu": "STM32F401",
                "device": device, "conn": "UART", "lot": "202607",
                "drivers": {"x": "SMART_TMC2209_V2", "y": "SMART_TMC2209_V2", "z": "SMART_TMC2209_V2",
                            "e0": "SMART_TMC2209_V2", "e1": "SMART_TMC2209_V2"},
                "motors": {"x": "42BYGH3023-B-22DH", "y": "42BYGH3023-B-22DH", "z": "42BYGH2216-B-24DH"},
                "piezo": None, "comment": None, "klipper": "v0.13.0", "descriptor": "board=SMART_MAKER_1_X;device=%s;uid=%s" % (device, uid)}
    return {"port": port, "uid": uid, "board": "SMART_MAKER_1_X", "cpu": "STM32F401", "device": device,
            "conn": "UART", "drivers": {}, "motors": {}, "piezo": None, "comment": None, "klipper": "v0.13.0",
            "descriptor": "board=SMART_MAKER_1_X;device=%s;uid=%s" % (device, uid)}


C235 = {"boards": [board("/dev/ttyS1", "C235", "aaaaaa")], "cameras": []}
C235_HD = {"boards": [board("/dev/ttyS1", "C235", "aaaaaa"),
                      board("/dev/ttyS2", "HYPERDRIVE_3P2L", "bbbbbb", kind="smartbox")], "cameras": []}
SAVE_BLOCK = ("#*# <---------------------- SAVE_CONFIG ---------------------->\n"
              "#*# DO NOT EDIT THIS BLOCK OR BELOW. The contents are auto-generated.\n"
              "#*#\n#*# [probe]\n#*# z_offset = 1.234\n")


class Select(unittest.TestCase):
    def test_main_only_uses_defaults(self):
        sel = compose.select(C235, CATALOG)
        self.assertEqual(sel["product"], "C235_DD_LW_04")
        self.assertEqual(sel["overrides"], {"mcu": {"serial": "/dev/ttyS1"}})
        self.assertFalse(sel["minimal"])
        self.assertIsNone(sel["alert"])

    def test_smartbox_means_chromax_7yms(self):
        sel = compose.select(C235_HD, CATALOG)
        self.assertEqual(sel["product"], "C235_CX12_LW_04_7YMS")
        self.assertEqual(sel["overrides"]["smartbox"], {"serial": "/dev/ttyS2"})

    def test_prefs_chromax_without_smartbox_is_2yms(self):
        sel = compose.select(C235, CATALOG, {"hotend": "CHROMAX_X12", "hotend_type": "HIGH_FLOW"})
        self.assertEqual(sel["product"], "C235_CX12_HF_04_2YMS")

    def test_usb_main_board_port_is_injected(self):
        comp = {"boards": [board("/dev/serial/by-id/usb-Klipper_stm32f401xc_1234-if00", "C435", "cccccc")]}
        sel = compose.select(comp, CATALOG)
        self.assertEqual(sel["product"], "C435_DD_LW_04")
        cfg = generator.generate(sel["product"], sel["overrides"])
        self.assertIn("serial: /dev/serial/by-id/usb-Klipper_stm32f401xc_1234-if00", cfg)

    def test_unknown_device_is_minimal(self):
        comp = {"boards": [board("/dev/ttyS1", "C999", "dddddd")]}
        sel = compose.select(comp, CATALOG)
        self.assertTrue(sel["minimal"])
        self.assertIsNone(sel["product"])

    def test_no_boards_is_alert(self):
        self.assertEqual(compose.select({"boards": []}, CATALOG)["alert"], "no MCU answered")

    def test_smartbox_alone_is_alert(self):
        comp = {"boards": [board("/dev/ttyS2", "HYPERDRIVE_3P2L", "bbbbbb", kind="smartbox")]}
        self.assertIn("no main board", compose.select(comp, CATALOG)["alert"])


class Build(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def _state(self, uid):
        (self.dir / CATALOG["detection"]["state_file"]).write_text(json.dumps({"main": {"uid": uid}}))

    def test_fresh_pad_generates_and_keeps_existing_save_config(self):
        (self.dir / "printer.cfg").write_text("[mcu]\nserial: /dev/ttyACM0\n" + SAVE_BLOCK)
        code, summary, cfg = compose.build(C235, CATALOG, self.dir)
        self.assertEqual(code, compose.EXIT_APPLIED)
        self.assertEqual(summary["mode"], "preserve")
        self.assertIn("z_offset = 1.234", cfg)
        self.assertIn("serial: /dev/ttyS1", cfg)
        self.assertEqual(cfg.count(compose.SAVE_CONFIG_MARKER), 1)

    def test_same_board_preserves(self):
        self._state("aaaaaa")
        (self.dir / "printer.cfg").write_text(SAVE_BLOCK)
        code, summary, cfg = compose.build(C235, CATALOG, self.dir)
        self.assertEqual(summary["mode"], "preserve")
        self.assertIn("z_offset = 1.234", cfg)

    def test_other_board_is_factory(self):
        self._state("ffffff")
        (self.dir / "printer.cfg").write_text(SAVE_BLOCK)
        code, summary, cfg = compose.build(C235, CATALOG, self.dir)
        self.assertEqual(summary["mode"], "factory")
        self.assertNotIn("z_offset = 1.234", cfg)
        self.assertTrue(cfg.rstrip().endswith("#*#"))

    def test_same_boards_leave_printer_cfg_alone(self):
        # first boot: cfg generated and state recorded
        code, summary = compose.apply(C235, CATALOG, self.dir)
        self.assertEqual(code, compose.EXIT_APPLIED)
        # the operator edits the cfg by hand
        (self.dir / "printer.cfg").write_text("# hand edited\n" + (self.dir / "printer.cfg").read_text())
        # next boots: same boards -> untouched, nothing generated
        code, summary = compose.apply(C235, CATALOG, self.dir)
        self.assertEqual(code, compose.EXIT_UNCHANGED)
        self.assertEqual(summary["mode"], "unchanged")
        self.assertTrue((self.dir / "printer.cfg").read_text().startswith("# hand edited"))
        # a board appears -> regenerated (hyperdrive => 7 YMS)
        code, summary = compose.apply(C235_HD, CATALOG, self.dir)
        self.assertEqual(code, compose.EXIT_APPLIED)
        self.assertEqual(summary["product"], "C235_CX12_LW_04_7YMS")
        # --factory bypasses the fingerprint (and reports "unchanged" only because
        # the freshly generated cfg is byte-identical to the one just written)
        code, summary = compose.apply(C235_HD, CATALOG, self.dir, factory=True)
        self.assertEqual(summary["mode"], "factory")
        self.assertEqual(code, compose.EXIT_UNCHANGED)
        (self.dir / "printer.cfg").write_text("# hand edited again\n")
        code, summary = compose.apply(C235_HD, CATALOG, self.dir, factory=True)
        self.assertEqual(code, compose.EXIT_APPLIED)

    def test_deleted_printer_cfg_is_regenerated_even_with_same_boards(self):
        code, summary = compose.apply(C235, CATALOG, self.dir)
        self.assertEqual(code, compose.EXIT_APPLIED)
        (self.dir / "printer.cfg").unlink()
        code, summary = compose.apply(C235, CATALOG, self.dir)
        self.assertEqual(code, compose.EXIT_APPLIED)
        self.assertTrue(summary["written"])
        self.assertIn("[mcu]\nserial: /dev/ttyS1", (self.dir / "printer.cfg").read_text())

    def test_fingerprint_ignores_cameras(self):
        a = dict(C235, cameras=[])
        b = dict(C235, cameras=[{"name": "cam"}])
        self.assertEqual(compose.hardware_fingerprint(a), compose.hardware_fingerprint(b))

    def test_identical_cfg_is_unchanged(self):
        code, summary, cfg = compose.build(C235, CATALOG, self.dir)
        (self.dir / "printer.cfg").write_text(cfg)
        code2, _, _ = compose.build(C235, CATALOG, self.dir)
        self.assertEqual(code2, compose.EXIT_UNCHANGED)

    def test_minimal_cfg_connects_only(self):
        comp = {"boards": [board("/dev/ttyS1", "C999", "dddddd"),
                           board("/dev/ttyS2", "HYPERDRIVE_3P2L", "bbbbbb", kind="smartbox")]}
        code, summary, cfg = compose.build(comp, CATALOG, self.dir)
        self.assertEqual(code, compose.EXIT_MINIMAL)
        self.assertIn("[mcu]\nserial: /dev/ttyS1", cfg)
        self.assertIn("[mcu smartbox]\nserial: /dev/ttyS2", cfg)
        self.assertIn("kinematics: none", cfg)
        self.assertNotIn("[stepper_x]", cfg)

    def test_alert_writes_nothing(self):
        (self.dir / "printer.cfg").write_text("keep me")
        code, summary = compose.apply({"boards": []}, CATALOG, self.dir)
        self.assertEqual(code, compose.EXIT_ALERT)
        self.assertEqual((self.dir / "printer.cfg").read_text(), "keep me")
        self.assertFalse(summary["written"])

    def test_apply_backs_up_and_records_state(self):
        (self.dir / "printer.cfg").write_text("old")
        code, summary = compose.apply(C235_HD, CATALOG, self.dir)
        self.assertEqual(code, compose.EXIT_APPLIED)
        self.assertTrue(summary["written"])
        self.assertEqual(Path(summary["backup"]).read_text(), "old")
        state = json.loads((self.dir / CATALOG["detection"]["state_file"]).read_text())
        self.assertEqual(state["product"], "C235_CX12_LW_04_7YMS")
        self.assertEqual(state["main"]["uid"], "aaaaaa")
        new = (self.dir / "printer.cfg").read_text()
        self.assertIn("[mcu smartbox]\nserial: /dev/ttyS2", new)

    def test_dry_run_writes_nothing(self):
        (self.dir / "printer.cfg").write_text("old")
        code, summary = compose.apply(C235, CATALOG, self.dir, dry_run=True)
        self.assertEqual(code, compose.EXIT_APPLIED)
        self.assertEqual((self.dir / "printer.cfg").read_text(), "old")
        self.assertFalse((self.dir / CATALOG["detection"]["state_file"]).exists())

    def test_new_recipe_regenerates_and_keeps_calibrations(self):
        """A catalog/generator update (yumi-config) reaches the machine at its next boot."""
        code, summary = compose.apply(C235, CATALOG, self.dir)
        self.assertEqual(code, compose.EXIT_APPLIED)
        cfg = (self.dir / "printer.cfg").read_text()
        (self.dir / "printer.cfg").write_text(cfg.rstrip("\n") + "\n#*# [probe]\n#*# z_offset = 1.234\n")
        code, summary = compose.apply(C235, CATALOG, self.dir)
        self.assertEqual(code, compose.EXIT_UNCHANGED)
        # a new catalog/generator that renders the same cfg has nothing to write...
        with mock.patch.object(compose, "recipe_hash", return_value="same-output"):
            code, summary = compose.apply(C235, CATALOG, self.dir)
        self.assertEqual(code, compose.EXIT_UNCHANGED)
        # ...one that changes the common trunk rewrites the cfg, calibrations kept
        real_generate = compose.generator.generate
        with mock.patch.object(compose, "recipe_hash", return_value="new-recipe"), \
                mock.patch.object(compose.generator, "generate",
                                  side_effect=lambda *a, **k: "# trunk v2\n" + real_generate(*a, **k)):
            code, summary = compose.apply(C235, CATALOG, self.dir)
        self.assertEqual(code, compose.EXIT_APPLIED)
        self.assertEqual(summary["mode"], "preserve")
        new = (self.dir / "printer.cfg").read_text()
        self.assertTrue(new.startswith("# trunk v2"))
        self.assertIn("z_offset = 1.234", new)
        self.assertTrue(any("regenerated" in r for r in summary["reasons"]))
        state = json.loads((self.dir / CATALOG["detection"]["state_file"]).read_text())
        self.assertEqual(state["recipe"], "new-recipe")


class WizardMachine(unittest.TestCase):
    """The boards name no machine: the wizard does (prefs "machine")."""

    def test_unknown_device_takes_the_chosen_machine(self):
        comp = {"boards": [board("/dev/ttyS1", "C999", "dddddd")]}
        sel = compose.select(comp, CATALOG, {"machine": "C335"})
        self.assertEqual(sel["situation"], "unknown")
        self.assertFalse(sel["minimal"])
        self.assertEqual(sel["product"], "C335_DD_LW_04")
        self.assertEqual(sel["overrides"], {"mcu": {"serial": "/dev/ttyS1"}})

    def test_foreign_board_takes_the_chosen_machine_through_its_parent_board(self):
        b = dict(board("/dev/ttyACM0", None, "eeeeee"), board="MKS_ROBIN_NANO")
        sel = compose.select({"boards": [b]}, CATALOG, {"machine": "C235"})
        self.assertEqual(sel["product"], "C235_DD_LW_04")
        self.assertEqual(sel["chain"][0], "SMART_MAKER_1X")

    def test_detected_machine_wins_over_the_preference(self):
        sel = compose.select(C235, CATALOG, {"machine": "C435"})
        self.assertEqual(sel["situation"], "yumi")
        self.assertEqual(sel["product"], "C235_DD_LW_04")

    def test_unknown_machine_not_in_catalog_stays_minimal(self):
        comp = {"boards": [board("/dev/ttyS1", "C999", "dddddd")]}
        sel = compose.select(comp, CATALOG, {"machine": "D12_300"})
        self.assertTrue(sel["minimal"])


if __name__ == "__main__":
    unittest.main()
