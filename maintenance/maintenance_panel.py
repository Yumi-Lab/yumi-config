"""
Maintenance Panel — KlipperScreen panel for maintenance counters and alerts.

Displays print hours, extrusion distance, heater hours, cuts count per YMS.
Shows warnings/critical alerts based on thresholds.
Allows reset of individual counters.

Symlinked to ~/KlipperScreen/panels/maintenance_panel.py by install.sh
"""
import gi
import logging
import json
import os

gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, GLib, Pango

from ks_includes.screen_panel import ScreenPanel

logger = logging.getLogger("KlipperScreen.maintenance_panel")

# Thresholds config path
THRESHOLDS_PATH = os.path.expanduser("~/printer_data/config/maintenance_thresholds.json")

# Default thresholds (used if file not found)
DEFAULT_THRESHOLDS = {
    "print_minutes": {"warning": 30000, "critical": 60000, "label": "Print Time", "unit": "min"},
    "extrusion_total_mm": {"warning": 50000000, "critical": 100000000, "label": "Extrusion Total", "unit": "mm"},
    "bed_heat_minutes": {"warning": 120000, "critical": 300000, "label": "Bed Heat Time", "unit": "min"},
    "hotend_heat_minutes": {"warning": 60000, "critical": 120000, "label": "Hotend Heat Time", "unit": "min"},
    "cuts_count": {"warning": 5000, "critical": 10000, "label": "Cuts", "unit": ""},
    "homing_count": {"warning": 50000, "critical": 100000, "label": "Homing Cycles", "unit": ""},
}


def format_minutes(minutes):
    """Convert minutes to human-readable: 1d 2h 30m"""
    if not isinstance(minutes, (int, float)) or minutes < 0:
        return "0m"
    minutes = int(minutes)
    if minutes < 60:
        return f"{minutes}m"
    hours = minutes // 60
    mins = minutes % 60
    if hours < 24:
        return f"{hours}h {mins}m" if mins else f"{hours}h"
    days = hours // 24
    hours = hours % 24
    parts = []
    if days:
        parts.append(f"{days}d")
    if hours:
        parts.append(f"{hours}h")
    if mins:
        parts.append(f"{mins}m")
    return " ".join(parts)


def format_distance(mm):
    """Convert mm to human-readable: m or km"""
    if not isinstance(mm, (int, float)) or mm < 0:
        return "0m"
    if mm < 1000:
        return f"{mm:.0f}mm"
    meters = mm / 1000
    if meters < 1000:
        return f"{meters:.1f}m"
    km = meters / 1000
    return f"{km:.2f}km"


def format_value(value, unit):
    """Format a counter value based on its unit"""
    if unit == "min":
        return format_minutes(value)
    if unit == "mm":
        return format_distance(value)
    return f"{int(value):,}" if isinstance(value, (int, float)) else str(value)


class Panel(ScreenPanel):
    def __init__(self, screen, title):
        title = title or "Maintenance"
        super().__init__(screen, title)

        self.thresholds = self._load_thresholds()
        self._refresh_timer = None

        self._build_main_screen()
        self._start_refresh()

    def _load_thresholds(self):
        """Load thresholds from config or use defaults"""
        try:
            with open(THRESHOLDS_PATH, "r") as f:
                data = json.load(f)
                # Merge with defaults for missing keys
                merged = DEFAULT_THRESHOLDS.copy()
                for key, val in data.items():
                    if key in merged and isinstance(val, dict):
                        merged[key].update(val)
                return merged
        except (FileNotFoundError, json.JSONDecodeError):
            return DEFAULT_THRESHOLDS.copy()

    def _get_counters(self):
        """Read counters from Klipper save_variables"""
        counters = {}
        try:
            data = self._printer.data.get("save_variables", {}).get("variables", {})
            for key, value in data.items():
                if key.startswith("maint_"):
                    counter_name = key[6:]  # strip "maint_"
                    counters[counter_name] = value
        except Exception:
            pass
        return counters

    def _get_status(self, value, threshold_info):
        """Return status: 'ok', 'warning', 'critical'"""
        if not isinstance(value, (int, float)):
            return "ok"
        if value >= threshold_info.get("critical", float("inf")):
            return "critical"
        if value >= threshold_info.get("warning", float("inf")):
            return "warning"
        return "ok"

    def _get_progress(self, value, threshold_info):
        """Return progress fraction 0.0-1.0 based on critical threshold"""
        critical = threshold_info.get("critical", 1)
        if critical <= 0:
            return 0
        return min(1.0, max(0.0, value / critical))

    # ─── UI ─────────────────────────────────────────────────

    def _build_main_screen(self):
        self._clear_content()

        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)

        main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        main_box.set_margin_start(15)
        main_box.set_margin_end(15)
        main_box.set_margin_top(10)

        # Title
        title = Gtk.Label()
        title.set_markup("<span size='x-large' weight='bold'>Maintenance Status</span>")
        title.set_halign(Gtk.Align.START)
        main_box.pack_start(title, False, False, 5)

        # Counter rows
        self.counter_widgets = {}
        counters = self._get_counters()

        for key, thresh in self.thresholds.items():
            value = counters.get(key, 0)
            row = self._build_counter_row(key, value, thresh)
            main_box.pack_start(row, False, False, 2)

        # YMS-specific counters
        yms_box = self._build_yms_section(counters)
        if yms_box:
            main_box.pack_start(yms_box, False, False, 5)

        # Alerts section
        alerts_box = self._build_alerts_section(counters)
        if alerts_box:
            main_box.pack_start(alerts_box, False, False, 5)

        # Buttons
        btn_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=15)
        btn_box.set_halign(Gtk.Align.CENTER)
        btn_box.set_margin_top(10)

        btn_refresh = self._gtk.Button("refresh", "Refresh", "color1")
        btn_refresh.connect("clicked", self._on_refresh)
        btn_refresh.set_size_request(180, 50)
        btn_box.pack_start(btn_refresh, False, False, 0)

        btn_reset = self._gtk.Button("delete", "Reset Counter", "color3")
        btn_reset.connect("clicked", self._on_reset_menu)
        btn_reset.set_size_request(180, 50)
        btn_box.pack_start(btn_reset, False, False, 0)

        main_box.pack_start(btn_box, False, False, 10)

        scroll.add(main_box)
        self.content.add(scroll)
        self.content.show_all()

    def _build_counter_row(self, key, value, thresh):
        """Build a single counter row with label, value, progress bar, status"""
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        row.set_margin_top(2)
        row.set_margin_bottom(2)

        label_text = thresh.get("label", key)
        unit = thresh.get("unit", "")
        status = self._get_status(value, thresh)
        progress = self._get_progress(value, thresh)

        # Status indicator
        status_colors = {"ok": "#4caf50", "warning": "#ff9800", "critical": "#f44336"}
        status_dot = Gtk.Label()
        status_dot.set_markup(f"<span foreground='{status_colors[status]}'>●</span>")
        status_dot.set_size_request(20, -1)
        row.pack_start(status_dot, False, False, 0)

        # Label
        lbl = Gtk.Label(label=label_text)
        lbl.set_halign(Gtk.Align.START)
        lbl.set_size_request(140, -1)
        row.pack_start(lbl, False, False, 0)

        # Progress bar
        pbar = Gtk.ProgressBar()
        pbar.set_fraction(progress)
        pbar.set_size_request(200, 20)
        pbar.set_valign(Gtk.Align.CENTER)
        if status == "critical":
            pbar.get_style_context().add_class("destructive-action")
        row.pack_start(pbar, True, True, 0)

        # Value
        formatted = format_value(value, unit)
        val_label = Gtk.Label()
        val_label.set_markup(f"<span font='monospace 11'>{formatted}</span>")
        val_label.set_halign(Gtk.Align.END)
        val_label.set_size_request(100, -1)
        row.pack_start(val_label, False, False, 0)

        # Store for refresh
        self.counter_widgets[key] = {
            "status_dot": status_dot,
            "progress": pbar,
            "value_label": val_label,
        }

        return row

    def _build_yms_section(self, counters):
        """Build per-YMS extrusion counters"""
        yms_keys = sorted([k for k in counters if k.startswith("extrusion_yms_")])
        if not yms_keys:
            return None

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)

        sep = Gtk.Separator()
        box.pack_start(sep, False, False, 5)

        title = Gtk.Label()
        title.set_markup("<span weight='bold'>Per-YMS Extrusion</span>")
        title.set_halign(Gtk.Align.START)
        box.pack_start(title, False, False, 3)

        for key in yms_keys:
            idx = key.replace("extrusion_yms_", "")
            value = counters[key]
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)

            lbl = Gtk.Label(label=f"  YMS-{int(idx)+1}")
            lbl.set_halign(Gtk.Align.START)
            lbl.set_size_request(100, -1)
            row.pack_start(lbl, False, False, 0)

            pbar = Gtk.ProgressBar()
            critical = self.thresholds.get("extrusion_total_mm", {}).get("critical", 100000000)
            pbar.set_fraction(min(1.0, value / critical))
            pbar.set_size_request(200, 15)
            pbar.set_valign(Gtk.Align.CENTER)
            row.pack_start(pbar, True, True, 0)

            val = Gtk.Label()
            val.set_markup(f"<span font='monospace 10'>{format_distance(value)}</span>")
            val.set_halign(Gtk.Align.END)
            val.set_size_request(80, -1)
            row.pack_start(val, False, False, 0)

            box.pack_start(row, False, False, 1)

        return box

    def _build_alerts_section(self, counters):
        """Build alerts box if any threshold exceeded"""
        alerts = []
        for key, thresh in self.thresholds.items():
            value = counters.get(key, 0)
            status = self._get_status(value, thresh)
            if status == "critical":
                alerts.append(f"<span foreground='#f44336'>CRITICAL: {thresh.get('label', key)}</span>")
            elif status == "warning":
                alerts.append(f"<span foreground='#ff9800'>WARNING: {thresh.get('label', key)}</span>")

        if not alerts:
            return None

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3)
        sep = Gtk.Separator()
        box.pack_start(sep, False, False, 5)

        title = Gtk.Label()
        title.set_markup("<span weight='bold' foreground='#ff9800'>Alerts</span>")
        title.set_halign(Gtk.Align.START)
        box.pack_start(title, False, False, 3)

        for alert in alerts:
            lbl = Gtk.Label()
            lbl.set_markup(alert)
            lbl.set_halign(Gtk.Align.START)
            box.pack_start(lbl, False, False, 1)

        return box

    # ─── CALLBACKS ──────────────────────────────────────────

    def _on_refresh(self, widget=None):
        self._build_main_screen()

    def _on_reset_menu(self, widget):
        """Show reset counter selection dialog"""
        dialog = Gtk.Dialog(
            title="Reset Counter",
            parent=self._screen.get_toplevel(),
            flags=Gtk.DialogFlags.MODAL,
        )
        dialog.set_default_size(400, 300)

        box = dialog.get_content_area()
        box.set_spacing(5)
        box.set_margin_start(10)
        box.set_margin_end(10)

        lbl = Gtk.Label()
        lbl.set_markup("<span weight='bold'>Select counter to reset:</span>")
        box.pack_start(lbl, False, False, 10)

        counters = self._get_counters()
        buttons = {}

        for key, thresh in self.thresholds.items():
            value = counters.get(key, 0)
            unit = thresh.get("unit", "")
            formatted = format_value(value, unit)
            btn = Gtk.Button(label=f"{thresh.get('label', key)}: {formatted}")
            btn.connect("clicked", self._on_reset_counter, key, dialog)
            btn.set_size_request(-1, 40)
            box.pack_start(btn, False, False, 2)

        # YMS counters
        yms_keys = sorted([k for k in counters if k.startswith("extrusion_yms_")])
        for key in yms_keys:
            idx = key.replace("extrusion_yms_", "")
            value = counters[key]
            btn = Gtk.Button(label=f"YMS-{int(idx)+1}: {format_distance(value)}")
            btn.connect("clicked", self._on_reset_counter, f"yms_{int(idx)+1}", dialog)
            btn.set_size_request(-1, 40)
            box.pack_start(btn, False, False, 2)

        cancel = Gtk.Button(label="Cancel")
        cancel.connect("clicked", lambda w: dialog.destroy())
        box.pack_start(cancel, False, False, 10)

        dialog.show_all()

    def _on_reset_counter(self, widget, counter_key, dialog):
        """Reset a specific counter via GCode"""
        dialog.destroy()
        self._screen._ws.klippy.gcode_script(f"MAINTENANCE_RESET COUNTER={counter_key}")
        GLib.timeout_add(1000, self._on_refresh)

    def _on_restart_klipper(self, widget):
        self._screen._ws.klippy.restart()

    # ─── REFRESH TIMER ──────────────────────────────────────

    def _start_refresh(self):
        """Auto-refresh every 30 seconds"""
        self._refresh_timer = GLib.timeout_add_seconds(30, self._auto_refresh)

    def _auto_refresh(self):
        """Periodic refresh callback"""
        if self.content.get_mapped():
            self._build_main_screen()
            return True  # Continue timer
        return False  # Stop timer if panel not visible

    # ─── HELPERS ────────────────────────────────────────────

    def _clear_content(self):
        for child in self.content.get_children():
            self.content.remove(child)

    def deactivate(self):
        """Called when panel is hidden — stop refresh timer"""
        if self._refresh_timer:
            GLib.source_remove(self._refresh_timer)
            self._refresh_timer = None
