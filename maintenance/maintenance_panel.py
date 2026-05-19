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
import time

gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, GLib, Pango

from ks_includes.screen_panel import ScreenPanel

logger = logging.getLogger("KlipperScreen.maintenance_panel")

# Thresholds config path
THRESHOLDS_PATH = os.path.expanduser("~/printer_data/config/maintenance_thresholds.json")
# Persistent maintenance state (survives reboot)
MAINT_STATE_PATH = os.path.expanduser("~/printer_data/config/.maintenance_state.json")

# Default thresholds — aligned with BambuLab A1 timing
DEFAULT_THRESHOLDS = {
    "print_minutes": {"warning": 30000, "critical": 60000, "label": "Print Time", "unit": "min"},
    "extrusion_total_mm": {"warning": 50000000, "critical": 100000000, "label": "Extrusion Total", "unit": "mm"},
    "bed_heat_minutes": {"warning": 120000, "critical": 300000, "label": "Bed Heat Time", "unit": "min"},
    "hotend_heat_minutes": {"warning": 60000, "critical": 120000, "label": "Hotend Heat Time", "unit": "min"},
    "cuts_count": {"warning": 5000, "critical": 10000, "label": "Cuts", "unit": ""},
    "homing_count": {"warning": 50000, "critical": 100000, "label": "Homing Cycles", "unit": ""},
}

# Periodic maintenance tasks — calendar-based (days since last done)
# Aligned with BambuLab A1 maintenance schedule
PERIODIC_TASKS = {
    "lube_xy_rails": {
        "label": "Lubricate X/Y Rails",
        "interval_days": 30,
        "description": "Apply lubricant oil on X-axis linear rail and Y-axis guide rails",
        "type": "preventive"
    },
    "grease_z_leadscrew": {
        "label": "Grease Z Lead Screw",
        "interval_days": 90,
        "description": "Apply grease on Z lead screw and anti-backlash nut",
        "type": "preventive"
    },
    "clean_nozzle": {
        "label": "Clean Nozzle",
        "interval_days": 14,
        "description": "Cold pull or needle clean of the nozzle (more often with CF filaments)",
        "type": "preventive"
    },
    "inspect_belts": {
        "label": "Inspect Belts",
        "interval_days": 90,
        "description": "Check belt tension and wear on X/Y axes",
        "type": "preventive"
    },
    "clean_bed": {
        "label": "Clean Print Bed",
        "interval_days": 7,
        "description": "IPA wipe of the build plate surface",
        "type": "preventive"
    },
    "check_wiring": {
        "label": "Check Wiring",
        "interval_days": 180,
        "description": "Inspect all cable connections, hotend wires, thermistor",
        "type": "preventive"
    },
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


def load_maint_state():
    """Load persistent maintenance state (survives reboot)"""
    try:
        with open(MAINT_STATE_PATH, "r") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {"tasks": {}, "dismissed_alerts": [], "last_boot_check": 0}


def save_maint_state(state):
    """Save persistent maintenance state"""
    try:
        with open(MAINT_STATE_PATH, "w") as f:
            json.dump(state, f, indent=2)
    except IOError:
        logger.error("Failed to save maintenance state")


def get_overdue_tasks(state):
    """Return list of periodic tasks that are overdue"""
    now = time.time()
    overdue = []
    for task_id, task_info in PERIODIC_TASKS.items():
        last_done = state.get("tasks", {}).get(task_id, {}).get("last_done", 0)
        interval_sec = task_info["interval_days"] * 86400
        if last_done == 0 or (now - last_done) >= interval_sec:
            days_overdue = int((now - last_done) / 86400) if last_done > 0 else -1
            overdue.append({
                "id": task_id,
                "label": task_info["label"],
                "description": task_info["description"],
                "interval_days": task_info["interval_days"],
                "days_overdue": days_overdue,
                "never_done": last_done == 0,
            })
    return overdue


class Panel(ScreenPanel):
    def __init__(self, screen, title):
        title = title or "Maintenance"
        super().__init__(screen, title)

        self.thresholds = self._load_thresholds()
        self.maint_state = load_maint_state()
        self._refresh_timer = None
        self._boot_alert_shown = False

        self._build_main_screen()
        self._start_refresh()

        # Check for overdue tasks on panel open (post-reboot alert)
        GLib.idle_add(self._check_boot_alerts)

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

    def _check_boot_alerts(self):
        """Show popup for overdue maintenance tasks after boot"""
        if self._boot_alert_shown:
            return False

        overdue = get_overdue_tasks(self.maint_state)
        if not overdue:
            return False

        self._boot_alert_shown = True
        self._show_overdue_popup(overdue)
        return False

    def _show_overdue_popup(self, overdue):
        """Show a dialog with overdue maintenance tasks"""
        dialog = Gtk.Dialog(
            title="Maintenance Required",
            parent=self._screen.get_toplevel(),
            flags=Gtk.DialogFlags.MODAL,
        )
        dialog.set_default_size(500, 400)

        box = dialog.get_content_area()
        box.set_spacing(8)
        box.set_margin_start(15)
        box.set_margin_end(15)
        box.set_margin_top(10)

        title = Gtk.Label()
        title.set_markup(
            "<span size='x-large' weight='bold' foreground='#ff9800'>"
            "Maintenance Required</span>"
        )
        box.pack_start(title, False, False, 10)

        for task in overdue:
            task_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)

            # Warning icon + label
            info_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
            lbl = Gtk.Label()
            if task["never_done"]:
                lbl.set_markup(
                    f"<span weight='bold'>{task['label']}</span>"
                    f"\n<span size='small'>Never done — recommended every "
                    f"{task['interval_days']} days</span>"
                )
            else:
                lbl.set_markup(
                    f"<span weight='bold' foreground='#f44336'>{task['label']}</span>"
                    f"\n<span size='small'>Overdue by {task['days_overdue']} days</span>"
                )
            lbl.set_halign(Gtk.Align.START)
            lbl.set_line_wrap(True)
            info_box.pack_start(lbl, False, False, 0)

            desc = Gtk.Label(label=task["description"])
            desc.set_halign(Gtk.Align.START)
            desc.modify_font(Pango.FontDescription("9"))
            info_box.pack_start(desc, False, False, 0)

            task_box.pack_start(info_box, True, True, 0)

            # "Done" button
            btn_done = Gtk.Button(label="Done")
            btn_done.set_size_request(80, 40)
            btn_done.connect("clicked", self._on_task_done, task["id"], dialog)
            task_box.pack_start(btn_done, False, False, 0)

            box.pack_start(task_box, False, False, 5)

        # Dismiss button
        btn_dismiss = Gtk.Button(label="Remind me later")
        btn_dismiss.connect("clicked", lambda w: dialog.destroy())
        btn_dismiss.set_size_request(-1, 45)
        box.pack_start(btn_dismiss, False, False, 15)

        dialog.show_all()

    def _on_task_done(self, widget, task_id, dialog=None):
        """Mark a periodic task as done"""
        if "tasks" not in self.maint_state:
            self.maint_state["tasks"] = {}
        self.maint_state["tasks"][task_id] = {
            "last_done": time.time(),
            "done_count": self.maint_state.get("tasks", {}).get(task_id, {}).get("done_count", 0) + 1,
        }
        save_maint_state(self.maint_state)
        logger.info(f"Maintenance task '{task_id}' marked as done")

        if dialog:
            dialog.destroy()
        self._build_main_screen()

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

        # Periodic maintenance tasks
        periodic_box = self._build_periodic_section()
        if periodic_box:
            main_box.pack_start(periodic_box, False, False, 5)

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

    def _build_periodic_section(self):
        """Build periodic maintenance tasks section with status and QR codes"""
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)

        sep = Gtk.Separator()
        box.pack_start(sep, False, False, 5)

        title = Gtk.Label()
        title.set_markup("<span weight='bold'>Scheduled Maintenance</span>")
        title.set_halign(Gtk.Align.START)
        box.pack_start(title, False, False, 3)

        now = time.time()
        for task_id, task_info in PERIODIC_TASKS.items():
            last_done = self.maint_state.get("tasks", {}).get(task_id, {}).get("last_done", 0)
            interval_sec = task_info["interval_days"] * 86400

            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)

            # Status
            if last_done == 0:
                status_color = "#9e9e9e"
                status_text = "Never done"
            elif (now - last_done) >= interval_sec:
                days_over = int((now - last_done - interval_sec) / 86400)
                status_color = "#f44336"
                status_text = f"Overdue {days_over}d"
            else:
                days_left = int((interval_sec - (now - last_done)) / 86400)
                status_color = "#4caf50"
                status_text = f"OK — {days_left}d left"

            dot = Gtk.Label()
            dot.set_markup(f"<span foreground='{status_color}'>●</span>")
            dot.set_size_request(15, -1)
            row.pack_start(dot, False, False, 0)

            lbl = Gtk.Label()
            lbl.set_markup(f"{task_info['label']}")
            lbl.set_halign(Gtk.Align.START)
            lbl.set_size_request(160, -1)
            row.pack_start(lbl, False, False, 0)

            status_lbl = Gtk.Label()
            status_lbl.set_markup(f"<span font='monospace 9' foreground='{status_color}'>{status_text}</span>")
            status_lbl.set_size_request(120, -1)
            row.pack_start(status_lbl, True, True, 0)

            # "Done" button
            btn = Gtk.Button(label="Done")
            btn.set_size_request(60, 30)
            btn.connect("clicked", self._on_task_done, task_id)
            row.pack_start(btn, False, False, 0)

            # QR code button → wiki guide
            wiki_slug = task_id.replace("_", "-")
            btn_qr = Gtk.Button(label="Guide")
            btn_qr.set_size_request(60, 30)
            btn_qr.connect("clicked", self._on_show_guide_qr, task_id, wiki_slug)
            row.pack_start(btn_qr, False, False, 0)

            box.pack_start(row, False, False, 2)

        return box

    def _on_show_guide_qr(self, widget, task_id, wiki_slug):
        """Show QR code pointing to wiki maintenance guide"""
        wiki_url = f"https://wiki.yumi-lab.com/c-series/maintenance/{wiki_slug}/"

        dialog = Gtk.Dialog(
            title=PERIODIC_TASKS[task_id]["label"],
            parent=self._screen.get_toplevel(),
            flags=Gtk.DialogFlags.MODAL,
        )
        dialog.set_default_size(350, 400)

        box = dialog.get_content_area()
        box.set_spacing(10)
        box.set_margin_start(15)
        box.set_margin_end(15)

        title = Gtk.Label()
        title.set_markup(f"<span size='large' weight='bold'>{PERIODIC_TASKS[task_id]['label']}</span>")
        box.pack_start(title, False, False, 10)

        desc = Gtk.Label(label=PERIODIC_TASKS[task_id]["description"])
        desc.set_line_wrap(True)
        box.pack_start(desc, False, False, 5)

        # Generate QR code image
        qr_path = f"/tmp/maint_qr_{task_id}.png"
        try:
            import qrcode
            qr = qrcode.QRCode(box_size=6, border=2)
            qr.add_data(wiki_url)
            qr.make(fit=True)
            img = qr.make_image(fill_color="black", back_color="white")
            img.save(qr_path)

            qr_image = Gtk.Image.new_from_file(qr_path)
            box.pack_start(qr_image, True, True, 5)
        except ImportError:
            lbl = Gtk.Label()
            lbl.set_markup(f"<span font='monospace 9'>{wiki_url}</span>")
            lbl.set_selectable(True)
            box.pack_start(lbl, False, False, 5)

        url_lbl = Gtk.Label()
        url_lbl.set_markup(f"<span size='small' foreground='#888'>{wiki_url}</span>")
        box.pack_start(url_lbl, False, False, 5)

        btn_close = Gtk.Button(label="Close")
        btn_close.connect("clicked", lambda w: dialog.destroy())
        box.pack_start(btn_close, False, False, 10)

        dialog.show_all()

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
