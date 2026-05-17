"""
CFG Wizard Panel — KlipperScreen panel for printer.cfg configuration.
Provides a step-by-step wizard to select model, YMS count, hotend type,
run hardware scanner, and generate printer.cfg.

Symlinked to ~/KlipperScreen/panels/cfg_wizard.py by install.sh
"""
import gi
import logging
import json
import subprocess
import os
import threading

gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, GLib, Pango

from ks_includes.screen_panel import ScreenPanel

logger = logging.getLogger("KlipperScreen.cfg_wizard")

# Path to generator
GENERATOR_DIR = os.path.expanduser("~/yumi-config/generator")
PRINTER_CFG = os.path.expanduser("~/printer_data/config/printer.cfg")

MODELS = ["C235", "C335", "C435"]
HOTENDS = [
    ("chroma_x12", "Chroma X12"),
    ("direct_drive", "Direct Drive"),
]
YMS_OPTIONS = [2, 3, 4, 5, 6, 7]
NOZZLE_SIZES = ["0.2", "0.4", "0.6", "0.8", "1.0"]


class Panel(ScreenPanel):
    def __init__(self, screen, title):
        title = title or "Printer Configuration"
        super().__init__(screen, title)

        self.selected_model = "C235"
        self.selected_yms = 7
        self.selected_hotend = "chroma_x12"
        self.selected_nozzle = "0.4"
        self.scan_result = None
        self._generating = False

        self._build_start_screen()

    # ─── SCREENS ────────────────────────────────────────────

    def _build_start_screen(self):
        self._clear_content()

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=15)
        box.set_valign(Gtk.Align.CENTER)
        box.set_halign(Gtk.Align.CENTER)
        box.set_vexpand(True)

        # Title
        title = Gtk.Label()
        title.set_markup("<span size='xx-large' weight='bold'>Printer Configuration Wizard</span>")
        box.pack_start(title, False, False, 10)

        # Check if printer.cfg exists
        if os.path.exists(PRINTER_CFG) and os.path.getsize(PRINTER_CFG) > 0:
            warn = Gtk.Label()
            warn.set_markup(
                "<span size='large' foreground='orange'>⚠ printer.cfg exists — "
                "generating will OVERWRITE it</span>"
            )
            warn.set_line_wrap(True)
            box.pack_start(warn, False, False, 5)

        # Buttons
        btn_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=20)
        btn_box.set_halign(Gtk.Align.CENTER)

        btn_scan = self._button("Scan Hardware", "refresh", self._on_scan)
        btn_manual = self._button("Manual Config", "settings", self._on_manual)
        btn_box.pack_start(btn_scan, False, False, 0)
        btn_box.pack_start(btn_manual, False, False, 0)

        box.pack_start(btn_box, False, False, 20)

        self.content.add(box)
        self.content.show_all()

    def _build_scan_screen(self):
        """Show hardware scan results"""
        self._clear_content()

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        box.set_valign(Gtk.Align.START)
        box.set_margin_start(20)
        box.set_margin_top(10)

        title = Gtk.Label()
        title.set_markup("<span size='x-large' weight='bold'>Hardware Detected</span>")
        title.set_halign(Gtk.Align.START)
        box.pack_start(title, False, False, 5)

        if self.scan_result:
            mcu = self.scan_result.get("mcu", {})
            main_serial = mcu.get("mcu_main", {}).get("serial", "Not found") if mcu.get("mcu_main") else "Not found"
            smartbox = mcu.get("mcu_smartbox")
            yms_detected = self.scan_result.get("yms_count_detected", 0)
            connection = self.scan_result.get("smartbox_connection", "none")

            info_lines = [
                f"MCU Main: {main_serial}",
                f"Smartbox: {'Yes (' + connection + ')' if smartbox else 'No'}",
                f"YMS Detected: {yms_detected}",
            ]

            # TMC status
            tmc = self.scan_result.get("tmc_drivers", {})
            for name, status in tmc.items():
                if "extruder" in name:
                    present = "✓" if status.get("present") else "✗" if status.get("present") is False else "?"
                    info_lines.append(f"  {name}: {present}")

            for line in info_lines:
                lbl = Gtk.Label(label=line)
                lbl.set_halign(Gtk.Align.START)
                lbl.modify_font(Pango.FontDescription("monospace 12"))
                box.pack_start(lbl, False, False, 2)

            # Auto-fill from scan
            self.selected_yms = yms_detected

        # Action buttons
        btn_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=15)
        btn_box.set_halign(Gtk.Align.CENTER)
        btn_box.set_margin_top(15)

        btn_accept = self._button("Accept & Configure", "complete", self._on_accept_scan)
        btn_rescan = self._button("Re-scan", "refresh", self._on_scan)
        btn_back = self._button("Back", "back", lambda w: self._build_start_screen())

        btn_box.pack_start(btn_accept, False, False, 0)
        btn_box.pack_start(btn_rescan, False, False, 0)
        btn_box.pack_start(btn_back, False, False, 0)

        box.pack_start(btn_box, False, False, 0)
        self.content.add(box)
        self.content.show_all()

    def _build_config_screen(self):
        """Manual configuration selection"""
        self._clear_content()

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        box.set_valign(Gtk.Align.START)
        box.set_margin_start(30)
        box.set_margin_top(10)

        title = Gtk.Label()
        title.set_markup("<span size='x-large' weight='bold'>Configuration</span>")
        title.set_halign(Gtk.Align.START)
        box.pack_start(title, False, False, 5)

        # Model selection
        model_box = self._make_combo_row("Model:", MODELS, self.selected_model, self._on_model_changed)
        box.pack_start(model_box, False, False, 0)

        # YMS count
        yms_strings = [str(y) for y in YMS_OPTIONS]
        yms_box = self._make_combo_row("YMS Count:", yms_strings, str(self.selected_yms), self._on_yms_changed)
        box.pack_start(yms_box, False, False, 0)

        # Hotend
        hotend_names = [h[1] for h in HOTENDS]
        hotend_active = next((h[1] for h in HOTENDS if h[0] == self.selected_hotend), hotend_names[0])
        hotend_box = self._make_combo_row("Hotend:", hotend_names, hotend_active, self._on_hotend_changed)
        box.pack_start(hotend_box, False, False, 0)

        # Nozzle
        nozzle_box = self._make_combo_row("Nozzle:", NOZZLE_SIZES, self.selected_nozzle, self._on_nozzle_changed)
        box.pack_start(nozzle_box, False, False, 0)

        # Summary
        summary = Gtk.Label()
        summary.set_markup(
            f"<span size='large'>Will generate: {self.selected_model} / "
            f"{self.selected_yms} YMS / {self.selected_hotend} / "
            f"{self.selected_nozzle}mm nozzle</span>"
        )
        summary.set_line_wrap(True)
        summary.set_halign(Gtk.Align.START)
        summary.set_margin_top(10)
        box.pack_start(summary, False, False, 0)
        self.labels["summary"] = summary

        # Generate button
        btn_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=15)
        btn_box.set_halign(Gtk.Align.CENTER)
        btn_box.set_margin_top(20)

        btn_generate = self._button("Generate printer.cfg", "complete", self._on_generate)
        btn_back = self._button("Back", "back", lambda w: self._build_start_screen())

        btn_box.pack_start(btn_generate, False, False, 0)
        btn_box.pack_start(btn_back, False, False, 0)

        box.pack_start(btn_box, False, False, 0)
        self.content.add(box)
        self.content.show_all()

    def _build_result_screen(self, success, message, lines=0):
        """Show generation result"""
        self._clear_content()

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=15)
        box.set_valign(Gtk.Align.CENTER)
        box.set_halign(Gtk.Align.CENTER)
        box.set_vexpand(True)

        icon = "✓" if success else "✗"
        color = "green" if success else "red"
        title = Gtk.Label()
        title.set_markup(
            f"<span size='xx-large' weight='bold' foreground='{color}'>"
            f"{icon} {'Success' if success else 'Error'}</span>"
        )
        box.pack_start(title, False, False, 10)

        msg_label = Gtk.Label(label=message)
        msg_label.set_line_wrap(True)
        msg_label.set_max_width_chars(60)
        box.pack_start(msg_label, False, False, 5)

        if success and lines > 0:
            detail = Gtk.Label()
            detail.set_markup(f"<span size='large'>Generated {lines} lines</span>")
            box.pack_start(detail, False, False, 5)

            restart_label = Gtk.Label()
            restart_label.set_markup(
                "<span foreground='orange'>Klipper will restart to load the new configuration</span>"
            )
            box.pack_start(restart_label, False, False, 5)

        btn_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=15)
        btn_box.set_halign(Gtk.Align.CENTER)

        if success:
            btn_restart = self._button("Restart Klipper", "refresh", self._on_restart_klipper)
            btn_box.pack_start(btn_restart, False, False, 0)

        btn_home = self._button("Home", "home", lambda w: self._screen._menu_go_back())
        btn_box.pack_start(btn_home, False, False, 0)

        box.pack_start(btn_box, False, False, 20)
        self.content.add(box)
        self.content.show_all()

    # ─── CALLBACKS ──────────────────────────────────────────

    def _on_scan(self, widget):
        """Run hardware scanner in background"""
        self._show_loading("Scanning hardware...")
        thread = threading.Thread(target=self._run_scan, daemon=True)
        thread.start()

    def _run_scan(self):
        """Background thread: run scanner"""
        try:
            result = subprocess.run(
                ["python3", os.path.join(GENERATOR_DIR, "scanner.py"), "--json"],
                capture_output=True, text=True, timeout=30
            )
            if result.returncode == 0:
                self.scan_result = json.loads(result.stdout)
                GLib.idle_add(self._build_scan_screen)
            else:
                GLib.idle_add(self._build_result_screen, False, f"Scanner error: {result.stderr}", 0)
        except Exception as e:
            GLib.idle_add(self._build_result_screen, False, f"Scanner exception: {e}", 0)

    def _on_manual(self, widget):
        self._build_config_screen()

    def _on_accept_scan(self, widget):
        """Accept scan results and go to config screen"""
        self._build_config_screen()

    def _on_model_changed(self, combo):
        self.selected_model = combo.get_active_text()
        self._update_summary()

    def _on_yms_changed(self, combo):
        self.selected_yms = int(combo.get_active_text())
        self._update_summary()

    def _on_hotend_changed(self, combo):
        name = combo.get_active_text()
        self.selected_hotend = next((h[0] for h in HOTENDS if h[1] == name), "chroma_x12")
        self._update_summary()

    def _on_nozzle_changed(self, combo):
        self.selected_nozzle = combo.get_active_text()
        self._update_summary()

    def _on_generate(self, widget):
        """Generate printer.cfg"""
        if self._generating:
            return
        self._generating = True
        self._show_loading("Generating printer.cfg...")
        thread = threading.Thread(target=self._run_generate, daemon=True)
        thread.start()

    def _run_generate(self):
        """Background thread: run engine"""
        try:
            cmd = [
                "python3", os.path.join(GENERATOR_DIR, "engine.py"),
                "--model", self.selected_model,
                "--yms", str(self.selected_yms),
                "--hotend", self.selected_hotend,
                "--output", PRINTER_CFG,
            ]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)

            if result.returncode == 0:
                lines = 0
                if os.path.exists(PRINTER_CFG):
                    with open(PRINTER_CFG) as f:
                        lines = sum(1 for _ in f)
                GLib.idle_add(self._build_result_screen, True,
                              f"{self.selected_model} / {self.selected_yms} YMS / {self.selected_hotend}",
                              lines)
            else:
                GLib.idle_add(self._build_result_screen, False, f"Error: {result.stderr}", 0)
        except Exception as e:
            GLib.idle_add(self._build_result_screen, False, f"Exception: {e}", 0)
        finally:
            self._generating = False

    def _on_restart_klipper(self, widget):
        """Restart Klipper after cfg generation"""
        self._screen._ws.klippy.restart()

    # ─── HELPERS ────────────────────────────────────────────

    def _clear_content(self):
        for child in self.content.get_children():
            self.content.remove(child)

    def _button(self, label, icon_name, callback):
        btn = self._gtk.Button(icon_name, label, "color1")
        btn.connect("clicked", callback)
        btn.set_size_request(200, 60)
        return btn

    def _make_combo_row(self, label_text, options, active_value, callback):
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        lbl = Gtk.Label(label=label_text)
        lbl.set_size_request(120, -1)
        lbl.set_halign(Gtk.Align.END)
        row.pack_start(lbl, False, False, 0)

        combo = Gtk.ComboBoxText()
        for opt in options:
            combo.append_text(opt)
        if active_value in options:
            combo.set_active(options.index(active_value))
        combo.connect("changed", callback)
        combo.set_size_request(200, -1)
        row.pack_start(combo, False, False, 0)
        return row

    def _show_loading(self, message):
        self._clear_content()
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        box.set_valign(Gtk.Align.CENTER)
        box.set_halign(Gtk.Align.CENTER)
        box.set_vexpand(True)

        spinner = Gtk.Spinner()
        spinner.start()
        box.pack_start(spinner, False, False, 10)

        lbl = Gtk.Label(label=message)
        lbl.set_markup(f"<span size='large'>{message}</span>")
        box.pack_start(lbl, False, False, 5)

        self.content.add(box)
        self.content.show_all()

    def _update_summary(self):
        if "summary" in self.labels:
            self.labels["summary"].set_markup(
                f"<span size='large'>Will generate: {self.selected_model} / "
                f"{self.selected_yms} YMS / {self.selected_hotend} / "
                f"{self.selected_nozzle}mm nozzle</span>"
            )
