"""
Printer configuration wizard — KlipperScreen panel.

Three situations, read from the last hardware scan (detection.state_file):
  yumi     the main board names a Yumi machine: only the print head is left to choose — a
           direct drive and a CHROMAX X12 cannot be told from the boards — plus hotend type
           and nozzle;
  unknown  boards answer but none names a machine: choose the machine, then the head;
  none     no board answered: check the cables, scan again.
Every choice is a component of the catalog (selection layers); it is saved to the preferences
file and autoconfig.py regenerates printer.cfg from the boards (Klipper stopped and started
through Moonraker). The logic lives in prefs.py, this file is the screen only.

Symlinked to ~/KlipperScreen/panels/cfg_wizard.py by install.sh.
"""
import logging
import os
import sys
import threading

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, GLib  # noqa: E402

from ks_includes.screen_panel import ScreenPanel  # noqa: E402

GENERATOR_DIR = os.path.dirname(os.path.realpath(__file__))
if GENERATOR_DIR not in sys.path:
    sys.path.insert(0, GENERATOR_DIR)
import prefs as wizard  # noqa: E402

logger = logging.getLogger("KlipperScreen.cfg_wizard")


class Panel(ScreenPanel):
    def __init__(self, screen, title):
        super().__init__(screen, title or "Printer configuration")
        self.catalog = wizard.generator.load_catalog()
        self.config_dir = str(wizard.compose.DEFAULT_CONFIG_DIR)
        self.prefs = wizard.load_prefs(self.catalog, self.config_dir)
        self.busy = False
        self._build()

    # ─── screens ────────────────────────────────────────────────

    def _clear(self):
        for child in self.content.get_children():
            self.content.remove(child)

    def _label(self, text, bold=False, small=False):
        label = Gtk.Label()
        text = GLib.markup_escape_text(text)
        if bold:
            text = "<b>%s</b>" % text
        if small:
            text = "<small>%s</small>" % text
        label.set_markup(text)
        label.set_halign(Gtk.Align.START)
        label.set_line_wrap(True)
        label.set_xalign(0)
        return label

    def _build(self):
        self._clear()
        state = wizard.load_state(self.catalog, self.config_dir)
        sit = wizard.situation(state)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        box.set_margin_start(12)
        box.set_margin_end(12)
        box.set_margin_top(6)
        box.set_vexpand(True)

        for i, line in enumerate(wizard.describe(self.catalog, state)):
            box.pack_start(self._label(line, bold=(i == 0)), False, False, 0)

        if sit != wizard.SITUATION_NONE:
            sel = wizard.selection(self.catalog, state, self.prefs)
            layers = ([wizard.MACHINE_LAYER] if wizard.MACHINE_LAYER in sel else []) + list(wizard.HEAD_LAYERS)
            for layer in layers:
                box.pack_start(self._row(layer, sel[layer]), False, False, 0)

        head = wizard.head_sensor_state()
        if head is not None:
            box.pack_start(self._head_sensor_row(head), False, False, 0)

        footer = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        footer.set_valign(Gtk.Align.END)
        footer.set_vexpand(True)
        back = self._gtk.Button("back", "Back", None)
        back.connect("clicked", lambda w: self._screen._menu_go_back())
        footer.pack_start(back, True, True, 0)
        scan = self._gtk.Button("refresh", "Scan", None)
        scan.connect("clicked", self._on_scan)
        footer.pack_start(scan, True, True, 0)
        if sit != wizard.SITUATION_NONE:
            apply_btn = self._gtk.Button("complete", "Apply", "color1")
            apply_btn.connect("clicked", self._on_apply)
            footer.pack_start(apply_btn, True, True, 0)
        box.pack_end(footer, False, False, 0)

        self.content.add(box)
        self.content.show_all()

    def _row(self, layer, info):
        """One line per layer: its label, then one button per option, the current one lit."""
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        name = self._label(wizard.layer_label(self.catalog, layer))
        name.set_size_request(150, -1)
        row.pack_start(name, False, False, 0)
        for cid, label in info["options"]:
            btn = self._gtk.Button(None, label, "color1" if cid == info["value"] else None)
            btn.set_sensitive(not info["forced"])
            btn.connect("clicked", self._on_pick, layer, cid)
            row.pack_start(btn, True, True, 0)
        if info["forced"]:
            row.pack_start(self._label("set by the smartbox", small=True), False, False, 0)
        return row

    def _head_sensor_row(self, head):
        """Broken head sensor: the load still runs, blind; only the detection is switched off."""
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        name = self._label("Head sensor")
        name.set_size_request(150, -1)
        row.pack_start(name, False, False, 0)
        for enable, label in ((False, "Enabled"), (True, "Bypassed")):
            btn = self._gtk.Button(None, label, "color1" if head["bypass"] == enable else None)
            btn.connect("clicked", self._on_head_sensor, enable)
            row.pack_start(btn, True, True, 0)
        state = "filament present" if head["present"] else ("no filament" if head["present"] is not None else "")
        if state:
            row.pack_start(self._label(state, small=True), False, False, 0)
        return row

    def _on_head_sensor(self, widget, enable):
        try:
            wizard.set_head_sensor_bypass(enable)
        except Exception as e:
            logger.error("cfg_wizard: head sensor bypass: %s", e)
            self._screen.show_popup_message("Cannot change the head sensor setting: %s" % e, level=3)
        self._build()

    def _wait_screen(self, text):
        self._clear()
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        box.set_valign(Gtk.Align.CENTER)
        box.set_vexpand(True)
        spinner = Gtk.Spinner()
        spinner.set_size_request(48, 48)
        spinner.start()
        box.pack_start(spinner, False, False, 0)
        label = self._label(text)
        label.set_halign(Gtk.Align.CENTER)
        box.pack_start(label, False, False, 0)
        self.content.add(box)
        self.content.show_all()

    def _result_screen(self, code, summary, log):
        self.busy = False
        self._clear()
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        box.set_margin_start(12)
        box.set_margin_end(12)
        box.set_margin_top(6)
        box.set_vexpand(True)
        for i, line in enumerate(wizard.result_lines(code, summary, log)):
            box.pack_start(self._label(line, bold=(i == 0), small=(i > 0)), False, False, 0)
        footer = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        footer.set_valign(Gtk.Align.END)
        footer.set_vexpand(True)
        again = self._gtk.Button("settings", "Configuration", None)
        again.connect("clicked", lambda w: self._build())
        footer.pack_start(again, True, True, 0)
        home = self._gtk.Button("home", "Home", "color1")
        home.connect("clicked", lambda w: self._screen._menu_go_back())
        footer.pack_start(home, True, True, 0)
        box.pack_end(footer, False, False, 0)
        self.content.add(box)
        self.content.show_all()

    # ─── actions ────────────────────────────────────────────────

    def _on_pick(self, widget, layer, cid):
        self.prefs[layer] = cid
        try:
            wizard.save_prefs(self.catalog, self.config_dir, self.prefs)
        except OSError as e:
            logger.error("cfg_wizard: cannot save preferences: %s", e)
            self._screen.show_popup_message("Cannot save the preferences: %s" % e, level=3)
        self._build()

    def _on_scan(self, widget):
        self._run("Scanning the boards — Klipper restarts")

    def _on_apply(self, widget):
        self._run("Scanning the boards and writing printer.cfg — Klipper restarts")

    def _run(self, text):
        if self.busy:
            return
        self.busy = True
        self._wait_screen(text)
        threading.Thread(target=self._worker, daemon=True).start()

    def _worker(self):
        try:
            code, summary, log = wizard.run_autoconfig()
        except Exception as e:  # subprocess timeout, missing python...
            logger.exception("cfg_wizard: autoconfig failed")
            code, summary, log = 1, None, str(e)
        GLib.idle_add(self._result_screen, code, summary, log)
