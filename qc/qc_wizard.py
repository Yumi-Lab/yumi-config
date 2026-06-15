"""
QC Wizard Panel — KlipperScreen panel for factory quality control.
Provides a step-by-step wizard with automated tests and visual confirmations.
"""
import gi
import logging
import os

gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, GLib, Pango

from ks_includes.screen_panel import ScreenPanel

logger = logging.getLogger("KlipperScreen.qc_wizard")

# QC mode = printer.cfg swapped with the dedicated QC config.
# The production cfg is kept in BACKUP_CFG until the QC is finished.
CONFIG_DIR = os.path.expanduser("~/printer_data/config")
PROD_CFG = os.path.join(CONFIG_DIR, "printer.cfg")
BACKUP_CFG = os.path.join(CONFIG_DIR, "printer.cfg.qc-backup")

# Tailles machine sélectionnables AVANT le QC. Chaque taille a sa propre cfg
# qc_printer_<TAILLE>.cfg (géométrie/course différentes), déployée par install.sh.
# C335/C435 sont à générer sur une vraie machine (le sélecteur les marque "à
# générer" tant que leur cfg n'existe pas). C235 retombe sur le legacy
# qc_printer.cfg si qc_printer_C235.cfg n'est pas encore déployé.
QC_SIZES = ["C235", "C335", "C435"]
QC_CFG_LEGACY = os.path.join(CONFIG_DIR, "qc_printer.cfg")

# Compteur QC central (qc.yumi-lab.com). Le rapport JSON est posté ici en fin
# de QC. Le token (= /opt/yumi-qc/secret_token côté serveur) est placé
# MANUELLEMENT sur le pad usine dans QC_TOKEN_FILE — il n'est pas dans le repo.
QC_COUNTER_URL = "https://qc.yumi-lab.com/api/qc/report"
QC_TOKEN_FILE = os.path.join(CONFIG_DIR, "qc_token")

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
        title = title or _("质量检测 / Quality Control")
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
        self._timeout_id = None
        self._selected_size = QC_SIZES[0]

        # Build the UI
        self._build_start_screen()

    def _qc_cfg_path(self, size):
        """Chemin de la cfg QC pour une taille machine. Fallback sur le legacy
        qc_printer.cfg pour C235 si la cfg suffixée n'est pas encore déployée."""
        p = os.path.join(CONFIG_DIR, f"qc_printer_{size}.cfg")
        if not os.path.exists(p) and size == "C235" and os.path.exists(QC_CFG_LEGACY):
            return QC_CFG_LEGACY
        return p

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
            f"<span size='large'>{len(QC_TESTS)} tests — Ventilateurs, Cutter, "
            f"Chauffe 220°C, Homing, Screws Tilt, Bed Mesh, Rotation E0/E1</span>"
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

        # QC mode status + actions
        qc_mode = self._is_qc_mode()
        mode_label = Gtk.Label()
        if qc_mode:
            mode_label.set_markup(
                "<span size='large' foreground='#4CAF50'>Mode QC actif "
                "(cfg production sauvegardée)</span>"
            )
        else:
            mode_label.set_markup(
                "<span size='large' foreground='#FF9800'>Cfg de production active — "
                "le mode QC sauvegarde printer.cfg, installe la cfg QC\n"
                "et redémarre Klipper. Tout est restauré à la fin du QC.</span>"
            )
        mode_label.set_justify(Gtk.Justification.CENTER)
        box.pack_start(mode_label, False, False, 5)

        if qc_mode:
            start_btn = self._gtk.Button("resume", _("开始检测 / START QC"), "color3",
                                         scale=self.bts * 1.5)
            start_btn.connect("clicked", self._on_start_clicked)
            start_btn.set_size_request(300, 80)
            box.pack_start(start_btn, False, False, 10)

            exit_btn = self._gtk.Button("cancel", "Quitter le mode QC (cfg prod)", "color2")
            exit_btn.connect("clicked", self._on_exit_qc_mode)
            box.pack_start(exit_btn, False, False, 5)
        else:
            # Sélecteur de taille machine (AVANT d'activer le mode QC) — chaque
            # taille swappe sa propre cfg qc_printer_<TAILLE>.cfg.
            size_title = Gtk.Label()
            size_title.set_markup(
                "<span size='large' weight='bold'>机型 / Modèle machine</span>")
            box.pack_start(size_title, False, False, 5)

            size_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
            size_row.set_halign(Gtk.Align.CENTER)
            for size in QC_SIZES:
                avail = os.path.exists(self._qc_cfg_path(size))
                selected = (size == self._selected_size)
                label = size if avail else f"{size}\n(à générer)"
                style = "color3" if selected else ("color1" if avail else "color2")
                sbtn = self._gtk.Button(None, label, style)
                sbtn.set_size_request(120, 70)
                sbtn.set_sensitive(avail)
                sbtn.connect("clicked", self._on_size_selected, size)
                size_row.pack_start(sbtn, False, False, 0)
            box.pack_start(size_row, False, False, 5)

            enter_btn = self._gtk.Button(
                "refresh", f"启用QC模式 / Enable QC mode ({self._selected_size})",
                "color3", scale=self.bts * 1.5)
            enter_btn.connect("clicked", self._on_enter_qc_mode)
            enter_btn.set_size_request(300, 80)
            box.pack_start(enter_btn, False, False, 10)

        self.content.add(box)
        self.content.show_all()

    # ─── QC MODE (swap printer.cfg <-> qc_printer.cfg) ─────────

    def _is_qc_mode(self):
        """True if the currently loaded Klipper config is the QC one
        (detected via the [gcode_macro _QC_MODE] marker)."""
        try:
            return bool(self._screen.printer.get_config_section("gcode_macro _QC_MODE"))
        except Exception:
            return False

    @staticmethod
    def _copy_cfg_content(src, dst):
        """Copy file content. Writes in place so an existing destination
        keeps its inode/ownership (Moonraker edits must keep working)."""
        with open(src, "rb") as f:
            data = f.read()
        existed = os.path.exists(dst)
        with open(dst, "wb") as f:
            f.write(data)
        if not existed:
            try:
                st = os.stat(src)
                os.chown(dst, st.st_uid, st.st_gid)
            except (PermissionError, OSError):
                pass

    def _on_size_selected(self, widget, size):
        """Sélection de la taille machine : mémorise et rafraîchit l'écran."""
        self._selected_size = size
        self._build_start_screen()

    def _on_enter_qc_mode(self, widget):
        """Backup printer.cfg, install the QC config of the selected size,
        restart Klipper."""
        qc_cfg = self._qc_cfg_path(self._selected_size)
        if not os.path.exists(qc_cfg):
            self._screen.show_popup_message(
                f"Cfg QC {self._selected_size} pas encore générée "
                f"({os.path.basename(qc_cfg)}) — à générer sur une vraie machine",
                level=3)
            return
        try:
            # Never overwrite an existing backup: it is the real prod cfg
            # from a previous QC that was not finished.
            if not os.path.exists(BACKUP_CFG):
                self._copy_cfg_content(PROD_CFG, BACKUP_CFG)
            self._copy_cfg_content(qc_cfg, PROD_CFG)
        except Exception as e:
            logger.error(f"QC: cfg swap failed: {e}")
            self._screen.show_popup_message(f"Echec swap cfg: {e}", level=3)
            return
        self._screen.show_popup_message(
            "Mode QC : redémarrage de Klipper...", level=1)
        self._screen._ws.klippy.restart_firmware()

    def _on_exit_qc_mode(self, widget):
        """Restore the production printer.cfg and restart Klipper."""
        if not os.path.exists(BACKUP_CFG):
            self._screen.show_popup_message(
                "Aucun backup trouvé (printer.cfg.qc-backup)", level=3)
            return
        try:
            self._copy_cfg_content(BACKUP_CFG, PROD_CFG)
            os.remove(BACKUP_CFG)
        except Exception as e:
            logger.error(f"QC: cfg restore failed: {e}")
            self._screen.show_popup_message(f"Echec restauration cfg: {e}", level=3)
            return
        self._screen.show_popup_message(
            "Cfg production restaurée : redémarrage de Klipper...", level=1)
        self._screen._ws.klippy.restart_firmware()

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

        abort_btn = self._gtk.Button("stop", _("中止 / Abort"), "color2")
        abort_btn.connect("clicked", self._on_abort_clicked)
        btn_box.pack_start(abort_btn, True, True, 0)

        skip_btn = self._gtk.Button("arrow-right", _("跳过 / Skip"), "color1")
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
            f"<span size='large'>{report.get('qc_model', '?')} — "
            f"Printer: {report.get('printer_id', '?')} — "
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

            # Sous-ligne détails : la mesure la plus parlante du log capturé
            # (distance feed, spread Z, corrections vis...) ou le champ details.
            detail = test.get("details", "")
            log = test.get("log", [])
            info = detail or (log[-1] if log else "")
            if info:
                d = Gtk.Label()
                d.set_markup(f"<span size='small' foreground='#9E9E9E'>    {GLib.markup_escape_text(info[:80])}</span>")
                d.set_halign(Gtk.Align.START)
                d.set_line_wrap(True)
                results_box.pack_start(d, False, False, 0)

        scroll.add(results_box)
        main_box.pack_start(scroll, True, True, 5)

        # Bottom buttons
        btn_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)

        # Finish: save report + restore production cfg + restart Klipper
        if os.path.exists(BACKUP_CFG):
            finish_btn = self._gtk.Button("complete", "完成 / Finish", "color3")
            finish_btn.connect("clicked", self._on_finish_qc)
            btn_box.pack_start(finish_btn, True, True, 0)

        save_btn = self._gtk.Button("sd", _("保存报告 / Save report"), "color2")
        save_btn.connect("clicked", self._on_save_report)
        btn_box.pack_start(save_btn, True, True, 0)

        new_btn = self._gtk.Button("refresh", _("新检测 / New QC"), "color1")
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
            {"name": _("是 / YES"), "response": Gtk.ResponseType.YES,
             "style": "color3"},
            {"name": _("否 / NO"), "response": Gtk.ResponseType.NO,
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

        # Capture cleanup BEFORE recording (recording advances to next test)
        test = self.engine.get_current_test()
        cleanup = test.get("cleanup") if test else None

        passed = (response_id == Gtk.ResponseType.YES)
        self.engine.record_visual_result(passed)

        if cleanup:
            self._screen._ws.klippy.gcode_script(cleanup)

    # ─── EVENT HANDLERS ────────────────────────────────────────

    def _on_start_clicked(self, widget):
        if not self._is_qc_mode():
            self._screen.show_popup_message(
                "Activez d'abord le mode QC (cfg dédiée)", level=2)
            return
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
        self._cancel_timeout()
        test = self.engine.get_current_test()
        cleanup = test.get("cleanup") if test else None
        self.engine.skip_current_test()
        if cleanup:
            self._screen._ws.klippy.gcode_script(cleanup)

    def _qc_token(self):
        """Token X-QC-Token, posé manuellement sur le pad usine (hors repo)."""
        try:
            with open(QC_TOKEN_FILE) as f:
                return f.read().strip()
        except OSError:
            return ""

    def _upload_report(self, report):
        """POST le rapport JSON sur le compteur central qc.yumi-lab.com.
        Renvoie (ok: bool, message: str). N'utilise que la stdlib (urllib)."""
        token = self._qc_token()
        if not token:
            return False, f"Token QC manquant : {QC_TOKEN_FILE}"
        import json
        import urllib.request
        import urllib.error
        data = json.dumps(report).encode("utf-8")
        req = urllib.request.Request(
            QC_COUNTER_URL, data=data, method="POST",
            headers={"Content-Type": "application/json", "X-QC-Token": token},
        )
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                if resp.status == 200:
                    return True, "Rapport QC envoyé ✓"
                return False, f"Envoi échoué : HTTP {resp.status}"
        except urllib.error.HTTPError as e:
            hint = " (token invalide ?)" if e.code == 401 else ""
            return False, f"Envoi refusé : HTTP {e.code}{hint}"
        except Exception as e:
            return False, f"Erreur réseau : {e}"

    def _on_finish_qc(self, widget):
        """Save report, push au compteur central, restore prod cfg, restart."""
        if self._current_report:
            self.engine.save_report(self._current_report)
            ok, msg = self._upload_report(self._current_report)
            self._screen.show_popup_message(msg, level=1 if ok else 3)
        self._on_exit_qc_mode(widget)

    def _on_save_report(self, widget):
        if self._current_report:
            path = self.engine.save_report(self._current_report)
            self._screen.show_popup_message(
                f"Report saved: {path}", level=1
            )

    def _on_upload_report(self, widget):
        if not self._current_report:
            return
        self.engine.save_report(self._current_report)  # sauvegarde locale d'abord
        ok, msg = self._upload_report(self._current_report)
        self._screen.show_popup_message(msg, level=1 if ok else 3)

    def _on_new_qc(self, widget):
        self._current_report = None
        self._build_start_screen()

    # ─── ENGINE CALLBACKS ──────────────────────────────────────

    def _on_state_change(self, old_state, new_state):
        GLib.idle_add(self._update_status_label, new_state)

    def _on_test_complete(self, test_id, result):
        self._cancel_timeout()
        GLib.idle_add(self._add_log_entry, test_id, result)
        # Run next test if available
        test = self.engine.get_current_test()
        if test and self.engine.state == QCState.RUNNING:
            GLib.idle_add(self._run_test, test)

    def _on_visual_prompt(self, test):
        # Operator response is unbounded: no timeout while the dialog is open
        self._cancel_timeout()
        GLib.idle_add(self._show_visual_dialog, test)

    def _on_qc_complete(self, report):
        self._cancel_timeout()
        # Modèle machine choisi à l'écran (fiable même si YUMI_CONFIG vide) —
        # exploité par le compteur qc.yumi-lab.com (segmentation par modèle).
        report["qc_model"] = self._selected_size
        self._current_report = report
        # Cleanup: stop heaters/fans/motors
        self._screen._ws.klippy.gcode_script("QC_CLEANUP")
        GLib.idle_add(self._build_summary_screen, report)

    # ─── TEST EXECUTION ────────────────────────────────────────

    def _run_test(self, test):
        """Send the macro for the current test."""
        self._update_test_display(test)
        self._cancel_timeout()
        timeout = test.get("timeout", 0)
        if timeout:
            self._timeout_id = GLib.timeout_add_seconds(
                timeout, self._on_test_timeout, test["id"]
            )
        macro = test.get("macro", "")
        if macro:
            self._screen._ws.klippy.gcode_script(macro)

    def _cancel_timeout(self):
        if self._timeout_id:
            GLib.source_remove(self._timeout_id)
            self._timeout_id = None

    def _on_test_timeout(self, test_id):
        """Test exceeded its timeout (macro aborted by a Klipper error,
        MCU stuck...) — send its cleanup (thermal safety) then record a
        FAIL so the QC can move on."""
        self._timeout_id = None
        test = self.engine.get_current_test()
        if (test and test["id"] == test_id
                and self.engine.state in (QCState.RUNNING, QCState.WAITING_GCODE)):
            logger.warning(f"QC: test {test_id} timed out")
            # Cut whatever the test turned on (heater/bed/fan) before failing
            cleanup = test.get("cleanup")
            if cleanup:
                self._screen._ws.klippy.gcode_script(cleanup)
            self.engine.fail_current_test("Timeout: no result from printer")
        return False

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
