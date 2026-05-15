"""
QC Wizard Panel — KlipperScreen panel for factory quality control.
Provides a step-by-step wizard with automated tests and visual confirmations.
"""
import gi
import logging

gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, GLib, Pango

from ks_includes.screen_panel import ScreenPanel

logger = logging.getLogger("KlipperScreen.qc_wizard")

# Import QC engine from ks_includes (symlinked there by install.sh)
try:
    from ks_includes.qc_engine import QCEngine, QCState, QCResult, QC_TESTS
except ImportError:
    # Fallback: try relative import for development
    import sys
    import os
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from qc_engine import QCEngine, QCState, QCResult, QC_TESTS


class Panel(ScreenPanel):
    def __init__(self, screen, title):
        title = title or _("Quality Control")
        super().__init__(screen, title)

        self.engine = QCEngine()
        self.engine.set_callbacks(
            on_state_change=self._on_state_change,
            on_test_complete=self._on_test_complete,
            on_visual_prompt=self._on_visual_prompt,
            on_qc_complete=self._on_qc_complete,
        )
        self._visual_dialog = None
        self._current_report = None

        # Build the UI
        self._build_start_screen()

    # ─── UI BUILDERS ────────────────────────────────────────────

    def _build_start_screen(self):
        self._clear_content()

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=20)
        box.set_valign(Gtk.Align.CENTER)
        box.set_halign(Gtk.Align.CENTER)
        box.set_vexpand(True)

        # Title
        title_label = Gtk.Label()
        title_label.set_markup("<span size='xx-large' weight='bold'>YUMI Quality Control</span>")
        box.pack_start(title_label, False, False, 10)

        # Info
        info_label = Gtk.Label()
        info_label.set_markup(
            f"<span size='large'>{len(QC_TESTS)} tests — Homing, Motors, Fans, "
            f"Temperature, PID, Bed Mesh</span>"
        )
        info_label.set_line_wrap(True)
        info_label.set_max_width_chars(50)
        info_label.set_justify(Gtk.Justification.CENTER)
        box.pack_start(info_label, False, False, 5)

        # Printer ID — auto-filled from YUMI ID (ETH0 MAC)
        try:
            with open("/sys/class/net/end0/address") as f:
                yumi_id = f.read().strip().replace(":", "").upper()
        except Exception:
            yumi_id = "UNKNOWN"
        id_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        id_box.set_halign(Gtk.Align.CENTER)
        id_label = Gtk.Label()
        id_label.set_markup(f"<span size='large'>Printer ID: {yumi_id}</span>")
        self.labels["printer_id"] = Gtk.Entry()
        self.labels["printer_id"].set_text(yumi_id)
        self.labels["printer_id"].set_editable(False)
        self.labels["printer_id"].set_visible(False)
        id_box.pack_start(id_label, False, False, 0)
        id_box.pack_start(self.labels["printer_id"], False, False, 0)
        box.pack_start(id_box, False, False, 10)

        # Start button
        start_btn = self._gtk.Button("resume", _("START QC"), "color3",
                                     scale=self.bts * 1.5)
        start_btn.connect("clicked", self._on_start_clicked)
        start_btn.set_size_request(300, 80)
        box.pack_start(start_btn, False, False, 20)

        self.content.add(box)
        self.content.show_all()

    def _build_running_screen(self):
        self._clear_content()

        main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=5)

        # Header with progress
        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        self.labels["test_name"] = Gtk.Label()
        self.labels["test_name"].set_markup("<span size='large' weight='bold'>Initializing...</span>")
        self.labels["test_name"].set_halign(Gtk.Align.START)
        self.labels["test_name"].set_hexpand(True)

        self.labels["progress"] = Gtk.Label()
        self.labels["progress"].set_markup("<span size='large'>0 / {}</span>".format(len(QC_TESTS)))
        self.labels["progress"].set_halign(Gtk.Align.END)

        header.pack_start(self.labels["test_name"], True, True, 5)
        header.pack_end(self.labels["progress"], False, False, 5)
        main_box.pack_start(header, False, False, 5)

        # Progress bar
        self.labels["progress_bar"] = Gtk.ProgressBar()
        self.labels["progress_bar"].set_fraction(0)
        self.labels["progress_bar"].set_show_text(True)
        main_box.pack_start(self.labels["progress_bar"], False, False, 5)

        # Status indicator
        self.labels["status"] = Gtk.Label()
        self.labels["status"].set_markup("<span size='large'>Running...</span>")
        self.labels["status"].set_halign(Gtk.Align.START)
        main_box.pack_start(self.labels["status"], False, False, 5)

        # Scrollable test log
        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.set_vexpand(True)

        self.labels["log_box"] = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        scroll.add(self.labels["log_box"])
        main_box.pack_start(scroll, True, True, 5)

        # Bottom buttons
        btn_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)

        abort_btn = self._gtk.Button("stop", _("Abort"), "color2")
        abort_btn.connect("clicked", self._on_abort_clicked)
        btn_box.pack_start(abort_btn, True, True, 0)

        skip_btn = self._gtk.Button("arrow-right", _("Skip Test"), "color1")
        skip_btn.connect("clicked", self._on_skip_clicked)
        btn_box.pack_end(skip_btn, True, True, 0)

        main_box.pack_end(btn_box, False, False, 5)

        self.content.add(main_box)
        self.content.show_all()

    def _build_summary_screen(self, report):
        self._clear_content()

        main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=5)

        # Overall result header
        overall = report.get("overall_result", "UNKNOWN")
        if overall == "PASS":
            color = "#4CAF50"
            icon = "complete"
        elif overall == "FAIL":
            color = "#F44336"
            icon = "cancel"
        else:
            color = "#FF9800"
            icon = "warning"

        result_label = Gtk.Label()
        result_label.set_markup(
            f"<span size='xx-large' weight='bold' foreground='{color}'>"
            f"QC {overall}</span>"
        )
        main_box.pack_start(result_label, False, False, 10)

        # Info line
        duration = report.get("duration_seconds", 0)
        mins = duration // 60
        secs = duration % 60
        info_label = Gtk.Label()
        info_label.set_markup(
            f"<span size='large'>Printer: {report.get('printer_id', '?')} — "
            f"Duration: {mins}m {secs}s</span>"
        )
        main_box.pack_start(info_label, False, False, 5)

        # Test results grid (scrollable)
        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.set_vexpand(True)

        results_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3)
        for test in report.get("tests", []):
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
            row.set_margin_start(10)
            row.set_margin_end(10)

            result = test.get("result", "pending")
            if result == "pass":
                mark = "<span foreground='#4CAF50' size='large' weight='bold'>  PASS</span>"
            elif result == "fail":
                mark = "<span foreground='#F44336' size='large' weight='bold'>  FAIL</span>"
            elif result == "skipped":
                mark = "<span foreground='#FF9800' size='large' weight='bold'>  SKIP</span>"
            else:
                mark = "<span foreground='#9E9E9E' size='large'>  ---</span>"

            name_label = Gtk.Label()
            name_label.set_markup(f"<span size='large'>{test['name']}</span>")
            name_label.set_halign(Gtk.Align.START)
            name_label.set_hexpand(True)

            result_lbl = Gtk.Label()
            result_lbl.set_markup(mark)
            result_lbl.set_halign(Gtk.Align.END)

            row.pack_start(name_label, True, True, 0)
            row.pack_end(result_lbl, False, False, 0)
            results_box.pack_start(row, False, False, 0)

        scroll.add(results_box)
        main_box.pack_start(scroll, True, True, 5)

        # Bottom buttons
        btn_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)

        # Show "Validate & Save Config" only if QC passed
        overall = report.get("overall_result", "FAIL")
        if overall == "PASS":
            validate_btn = self._gtk.Button("complete", "Validate & Save Config", "color3")
            validate_btn.connect("clicked", self._on_validate_save)
            btn_box.pack_start(validate_btn, True, True, 0)

        save_btn = self._gtk.Button("sd", _("Save Report"), "color2")
        save_btn.connect("clicked", self._on_save_report)
        btn_box.pack_start(save_btn, True, True, 0)

        new_btn = self._gtk.Button("refresh", _("New QC"), "color1")
        new_btn.connect("clicked", self._on_new_qc)
        btn_box.pack_start(new_btn, True, True, 0)

        main_box.pack_end(btn_box, False, False, 5)

        self.content.add(main_box)
        self.content.show_all()

    # ─── VISUAL CONFIRMATION DIALOG ────────────────────────────

    def _show_visual_dialog(self, test):
        """Show a full-screen Yes/No dialog for visual confirmation."""
        if self._visual_dialog:
            self._gtk.remove_dialog(self._visual_dialog)
            self._visual_dialog = None

        prompt = test.get("prompt", f"Test {test['name']} OK ?")

        # Build dialog content
        content_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=20)
        content_box.set_valign(Gtk.Align.CENTER)

        # Test name
        name_label = Gtk.Label()
        name_label.set_markup(
            f"<span size='x-large' weight='bold'>{test['name']}</span>"
        )
        content_box.pack_start(name_label, False, False, 10)

        # Question
        question_label = Gtk.Label()
        question_label.set_markup(f"<span size='xx-large'>{prompt}</span>")
        question_label.set_line_wrap(True)
        question_label.set_max_width_chars(40)
        question_label.set_justify(Gtk.Justification.CENTER)
        content_box.pack_start(question_label, False, False, 20)

        buttons = [
            {"name": _("YES"), "response": Gtk.ResponseType.YES,
             "style": "color3"},
            {"name": _("NO"), "response": Gtk.ResponseType.NO,
             "style": "color2"},
        ]

        self._visual_dialog = self._gtk.Dialog(
            _("Visual Check"),
            buttons,
            content_box,
            self._on_visual_dialog_response,
        )

    def _on_visual_dialog_response(self, dialog, response_id):
        self._gtk.remove_dialog(dialog)
        self._visual_dialog = None

        passed = (response_id == Gtk.ResponseType.YES)
        self.engine.record_visual_result(passed)

    # ─── EVENT HANDLERS ────────────────────────────────────────

    def _on_start_clicked(self, widget):
        printer_id = self.labels["printer_id"].get_text().strip()
        if not printer_id:
            self._screen.show_popup_message(
                "Please enter a Printer ID", level=2
            )
            return

        self._build_running_screen()
        test = self.engine.start(printer_id)
        if test:
            self._run_test(test)

    def _on_abort_clicked(self, widget):
        # Confirm abort
        label = Gtk.Label()
        label.set_markup("<span size='large'>Abort QC protocol?</span>")
        buttons = [
            {"name": _("Yes, Abort"), "response": Gtk.ResponseType.YES,
             "style": "color2"},
            {"name": _("Continue"), "response": Gtk.ResponseType.NO},
        ]
        self._gtk.Dialog(_("Confirm Abort"), buttons, label,
                         self._on_abort_confirmed)

    def _on_abort_confirmed(self, dialog, response_id):
        self._gtk.remove_dialog(dialog)
        if response_id == Gtk.ResponseType.YES:
            # Stop fans/heaters before aborting
            self._screen._ws.klippy.gcode_script("QC_CLEANUP")
            self.engine.abort()

    def _on_skip_clicked(self, widget):
        if self._visual_dialog:
            self._gtk.remove_dialog(self._visual_dialog)
            self._visual_dialog = None
        self.engine.skip_current_test()

    def _on_validate_save(self, widget):
        """Save report + SAVE_CONFIG (applies PID/mesh values, restarts Klipper)."""
        if self._current_report:
            self.engine.save_report(self._current_report)
        self._screen.show_popup_message("Saving config & restarting Klipper...", level=1)
        self._screen._ws.klippy.gcode_script("SAVE_CONFIG")

    def _on_save_report(self, widget):
        if self._current_report:
            path = self.engine.save_report(self._current_report)
            self._screen.show_popup_message(
                f"Report saved: {path}", level=1
            )

    def _on_upload_report(self, widget):
        if not self._current_report:
            return
        # Save first, then upload
        path = self.engine.save_report(self._current_report)
        try:
            import requests
            r = requests.post(
                "https://app.yumi-lab.com/api/qc/report",
                json=self._current_report,
                timeout=15,
            )
            if r.status_code == 200:
                self._screen.show_popup_message("Report uploaded!", level=1)
            else:
                self._screen.show_popup_message(
                    f"Upload failed: HTTP {r.status_code}", level=3
                )
        except Exception as e:
            self._screen.show_popup_message(f"Upload error: {e}", level=3)

    def _on_new_qc(self, widget):
        self._current_report = None
        self._build_start_screen()

    # ─── ENGINE CALLBACKS ──────────────────────────────────────

    def _on_state_change(self, old_state, new_state):
        GLib.idle_add(self._update_status_label, new_state)

    def _on_test_complete(self, test_id, result):
        GLib.idle_add(self._add_log_entry, test_id, result)
        # Run next test if available
        test = self.engine.get_current_test()
        if test and self.engine.state == QCState.RUNNING:
            GLib.idle_add(self._run_test, test)

    def _on_visual_prompt(self, test):
        # Stop part fan after visual check (it was turned on by the macro)
        GLib.idle_add(self._show_visual_dialog, test)

    def _on_qc_complete(self, report):
        self._current_report = report
        # Cleanup: stop heaters/fans/motors
        self._screen._ws.klippy.gcode_script("QC_CLEANUP")
        GLib.idle_add(self._build_summary_screen, report)

    # ─── TEST EXECUTION ────────────────────────────────────────

    def _run_test(self, test):
        """Send the macro for the current test."""
        self._update_test_display(test)
        macro = test.get("macro", "")
        if macro:
            self._screen._ws.klippy.gcode_script(macro)

    def _update_test_display(self, test):
        current, total = self.engine.get_progress()
        if "test_name" in self.labels:
            self.labels["test_name"].set_markup(
                f"<span size='large' weight='bold'>{test['name']}</span>"
            )
        if "progress" in self.labels:
            self.labels["progress"].set_markup(
                f"<span size='large'>{current} / {total}</span>"
            )
        if "progress_bar" in self.labels:
            self.labels["progress_bar"].set_fraction(current / total)
            self.labels["progress_bar"].set_text(f"{current}/{total}")

    def _update_status_label(self, state):
        status_map = {
            QCState.RUNNING: "Running...",
            QCState.WAITING_GCODE: "Waiting for test result...",
            QCState.WAITING_VISUAL: "Waiting for visual confirmation...",
            QCState.COMPLETED: "QC Complete!",
            QCState.ABORTED: "QC Aborted",
        }
        text = status_map.get(state, str(state.value))
        if "status" in self.labels:
            self.labels["status"].set_markup(f"<span size='large'>{text}</span>")

    def _add_log_entry(self, test_id, result):
        """Add a result line to the scrollable log."""
        if "log_box" not in self.labels:
            return

        # Find test name
        test_name = test_id
        for t in QC_TESTS:
            if t["id"] == test_id:
                test_name = t["name"]
                break

        if result == QCResult.PASS:
            mark = "<span foreground='#4CAF50' weight='bold'>PASS</span>"
        elif result == QCResult.FAIL:
            mark = "<span foreground='#F44336' weight='bold'>FAIL</span>"
        elif result == QCResult.SKIPPED:
            mark = "<span foreground='#FF9800' weight='bold'>SKIP</span>"
        else:
            mark = "<span foreground='#9E9E9E'>---</span>"

        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        row.set_margin_start(5)
        row.set_margin_end(5)

        name_lbl = Gtk.Label()
        name_lbl.set_markup(f"<span size='medium'>{test_name}</span>")
        name_lbl.set_halign(Gtk.Align.START)
        name_lbl.set_hexpand(True)

        result_lbl = Gtk.Label()
        result_lbl.set_markup(mark)
        result_lbl.set_halign(Gtk.Align.END)

        row.pack_start(name_lbl, True, True, 0)
        row.pack_end(result_lbl, False, False, 0)
        row.show_all()

        self.labels["log_box"].pack_start(row, False, False, 0)

        # Auto-scroll to bottom
        parent = self.labels["log_box"].get_parent()
        if parent and hasattr(parent, "get_vadjustment"):
            adj = parent.get_vadjustment()
            GLib.idle_add(lambda: adj.set_value(adj.get_upper()))

    # ─── KLIPPERSCREEN LIFECYCLE ───────────────────────────────

    def activate(self):
        """Called when panel becomes visible."""
        pass

    def process_update(self, action, data):
        """Process printer state updates from KlipperScreen."""
        if action == "notify_gcode_response":
            if isinstance(data, str):
                self.engine.process_gcode_response(data)
            elif isinstance(data, list):
                for msg in data:
                    if isinstance(msg, str):
                        self.engine.process_gcode_response(msg)
        return False

    # ─── HELPERS ───────────────────────────────────────────────

    def _clear_content(self):
        """Remove all children from the content container."""
        for child in self.content.get_children():
            self.content.remove(child)
