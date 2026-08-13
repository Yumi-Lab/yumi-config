"""
QC Wizard Panel — KlipperScreen panel for factory quality control.
Provides a step-by-step wizard with automated tests and visual confirmations.
"""
import gi
import json
import logging
import os
import re
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime

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
# YMS12 = BANC QC YMS (une C235 + 2 HyperDrive, 12 boîtiers testés un par un).
QC_SIZES = ["C235", "C335", "C435", "YMS12"]
QC_CFG_LEGACY = os.path.join(CONFIG_DIR, "qc_printer.cfg")

# Compteur QC central (qc.yumi-lab.com). Le rapport JSON est posté ici en fin
# de QC. Le token (= /opt/yumi-qc/secret_token côté serveur) est placé
# MANUELLEMENT sur le pad usine dans QC_TOKEN_FILE — il n'est pas dans le repo.
QC_COUNTER_URL = "https://qc.yumi-lab.com/api/qc/report"
QC_TOKEN_FILE = os.path.join(CONFIG_DIR, "qc_token")

# ── Banc YMS (contrat docs/FORMAT-YMS.md v1.4 du repo yumi-qc-counter) ──
# Les boîtiers YMS n'ont pas de numéro de série : le serveur ALLOUE le code
# EN FIN de test de chaque boîtier (jamais en amont, zéro code perdu) :
# PASS -> numéro de série YMSL-/YMSP-, FAIL -> code famille QCFL- (taux de
# défectueux). Rapport par boîtier sur le POST /api/qc/report standard avec
# measures{} calculées par le pad (source primaire). Étiquette SYSTÉMATIQUE :
# PASS = numéro de série + QR ; FAIL = étiquette de rejet (position + raison).
QC_YMS_ALLOCATE_URL = "https://qc.yumi-lab.com/api/qc/yms/allocate"
QC_REPORT_URL_BASE = "https://qc.yumi-lab.com/report/"
YMS_BENCH_TOTAL = 12
# Position banc (1..12) -> slot physique
YMS_BENCH_SLOTS = (["main:E0", "main:E1"]
                   + ["hyperdrive_uart:%d" % i for i in range(1, 6)]
                   + ["hyperdrive_usb:%d" % i for i in range(1, 6)])
# Valeurs normées de measures.fail_reason (traduites côté serveur)
# sensor_mute | sensor_lost_feed | sensor_lost_stress | head_not_reached |
# tmc_error | already_at_head | timeout

# Import QC engine from ks_includes (symlinked there by install.sh)
try:
    from ks_includes.qc_engine import QCEngine, QCState, QCResult, QC_TESTS
    from ks_includes.qc_yms import (
        allocate_yms_codes,
        build_box_report,
        build_label_tspl,
        build_retest_sequence,
        build_yms_tests,
        enabled_positions,
        extract_measures,
        load_bench_config,
        load_disabled_positions,
        position_from_test_id,
        test_id_for_position,
        YMS_BENCH_SLOTS,
        YMS_BENCH_TOTAL,
        MODELS,
        DEFAULT_MODEL,
    )
except ImportError:
    # Fallback: try relative import for development
    import sys
    import os
    sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))
    from qc_engine import QCEngine, QCState, QCResult, QC_TESTS
    from qc_yms import (
        allocate_yms_codes,
        build_box_report,
        build_label_tspl,
        build_retest_sequence,
        build_yms_tests,
        enabled_positions,
        extract_measures,
        load_bench_config,
        load_disabled_positions,
        position_from_test_id,
        test_id_for_position,
        YMS_BENCH_SLOTS,
        YMS_BENCH_TOTAL,
        MODELS,
        DEFAULT_MODEL,
    )


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
        self._restart_retries = 0
        self._selected_size = QC_SIZES[0]
        self._yms_model = DEFAULT_MODEL  # light ou pro (sélectionné au lancement)
        self._bench_config = {}     # v1.5 : yms_version + composants montés
        self._disabled_positions = []  # positions 1..12 hors service
        self._bench_session = ""    # pad_mac-YYYYMMDD-HHMM du début de séquence
        self._box_started = {}      # test_id -> datetime de début (durée/boîtier)

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

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        box.set_valign(Gtk.Align.CENTER)
        box.set_halign(Gtk.Align.CENTER)
        box.set_vexpand(True)

        # Title (compact pour tenir sur l'écran 480px)
        title_label = Gtk.Label()
        title_label.set_markup("<span size='large' weight='bold'>YUMI Quality Control</span>")
        box.pack_start(title_label, False, False, 2)

        # Printer ID (YUMI ID = MAC ETH0) — entrée cachée + petite ligne
        try:
            with open("/sys/class/net/end0/address") as f:
                yumi_id = f.read().strip().replace(":", "").upper()
        except Exception:
            yumi_id = "UNKNOWN"
        self.labels["printer_id"] = Gtk.Entry()
        self.labels["printer_id"].set_text(yumi_id)
        self.labels["printer_id"].set_visible(False)
        # On AFFICHE l'UID STM32 (= N° de série gravé sur l'étiquette) pour pouvoir
        # confronter l'étiquette imprimée à l'imprimante branchée. Repli sur la MAC end0.
        self.labels["id_display"] = Gtk.Label()
        self.labels["id_display"].set_markup(f"<span size='small' foreground='#9E9E9E'>ID: {yumi_id}</span>")
        box.pack_start(self.labels["id_display"], False, False, 2)
        self._load_mcu_uid()

        # QC mode status + actions
        qc_mode = self._is_qc_mode()
        active_model = self._active_qc_model() if qc_mode else None
        # En mode QC, le modèle chargé (gravé dans la cfg) fait foi.
        if qc_mode and active_model:
            self._selected_size = active_model

        mode_label = Gtk.Label()
        if qc_mode:
            mode_label.set_markup(
                f"<span size='large' foreground='#4CAF50'>QC模式 已激活 — {active_model or '?'} / "
                f"QC mode active</span>")
        else:
            mode_label.set_markup(
                "<span size='large' foreground='#FF9800'>生产配置 — 触摸机型加载QC / "
                "Production cfg — touch a model to load QC</span>")
        mode_label.set_justify(Gtk.Justification.CENTER)
        mode_label.set_line_wrap(True)
        box.pack_start(mode_label, False, False, 5)

        # Sélecteur de taille machine — TOUJOURS visible et ACTIF : un appui sur
        # un modèle est l'action (charge sa cfg, ou lance le QC si déjà chargé).
        size_title = Gtk.Label()
        size_title.set_markup("<span size='large' weight='bold'>机型 / Model</span>")
        box.pack_start(size_title, False, False, 5)

        size_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        size_row.set_halign(Gtk.Align.CENTER)
        for size in QC_SIZES:
            avail = os.path.exists(self._qc_cfg_path(size))
            selected = (size == self._selected_size)
            label = size if avail else f"{size}\n待生成/TBD"
            style = "color3" if selected else ("color1" if avail else "color2")
            sbtn = self._gtk.Button(None, label, style)
            sbtn.set_size_request(120, 70)
            sbtn.set_sensitive(avail)
            sbtn.connect("clicked", self._on_size_selected, size)
            size_row.pack_start(sbtn, False, False, 0)
        box.pack_start(size_row, False, False, 5)

        # Aide : un APPUI sur le modèle LANCE directement (entre en QC / START).
        hint = Gtk.Label()
        if qc_mode:
            hint.set_markup(f"<span size='large' weight='bold' foreground='#4CAF50'>"
                            f"▶ 触摸 {active_model or 'C235'} 开始检测 / "
                            f"touch {active_model or 'C235'} to START QC</span>")
        else:
            # Générique : les trois modèles ont leur cfg, plus de raison de
            # nommer C235 en dur.
            hint.set_markup("<span size='large' weight='bold' foreground='#4CAF50'>"
                            "▶ 触摸机型 进入QC模式 / touch a model to enter QC mode</span>")
        hint.set_justify(Gtk.Justification.CENTER)
        hint.set_line_wrap(True)
        box.pack_start(hint, False, False, 5)

        # Bouton Calibration Z TAP — juste la séquence G28 -> Z max -> tap.
        # Z TAP + Imprimer etiquette M3 cote a cote (tenir sur 800x480)
        action_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        action_row.set_halign(Gtk.Align.CENTER)
        ztap_btn = self._gtk.Button("refresh", "Z TAP", "color1")
        ztap_btn.connect("clicked", self._on_ztap_calibrate)
        ztap_btn.set_size_request(180, 60)
        action_row.pack_start(ztap_btn, False, False, 0)
        plaque_btn = self._gtk.Button("print", "标签 / Etiquette M3", "color1")
        plaque_btn.connect("clicked", self._on_print_plaque)
        plaque_btn.set_size_request(280, 60)
        action_row.pack_start(plaque_btn, False, False, 0)
        box.pack_start(action_row, False, False, 5)

        if qc_mode:
            exit_btn = self._gtk.Button("cancel", "退出QC模式 / Exit QC mode", "color2")
            exit_btn.connect("clicked", self._on_exit_qc_mode)
            box.pack_start(exit_btn, False, False, 5)

        self.content.add(box)
        self.content.show_all()

    # ─── UID STM32 affiché (confrontation étiquette ↔ imprimante) ──────────

    def _load_mcu_uid(self):
        """Affiche l'UID STM32 (identique au N° de série de l'étiquette) à la place
        de la MAC. Essai immédiat via l'objet KlipperScreen, sinon lecture Moonraker
        en tâche de fond (QUERY_MCU_UID puis query) pour ne pas bloquer l'UI."""
        try:
            uid = (self._screen.printer.get_stat("mcu_uid", "uid") or "").strip().upper()
        except Exception:
            uid = ""
        if uid:
            self._set_mcu_uid(uid)
            return
        import threading
        threading.Thread(target=self._worker_mcu_uid, daemon=True).start()

    def _worker_mcu_uid(self):
        import urllib.request, urllib.parse, json
        uid = ""
        try:
            urllib.request.urlopen(
                "http://localhost:7125/printer/gcode/script?script=QUERY_MCU_UID", timeout=4).read()
            raw = urllib.request.urlopen(
                "http://localhost:7125/printer/objects/query?mcu_uid", timeout=4).read()
            uid = (json.loads(raw)["result"]["status"]["mcu_uid"]["uid"] or "").strip().upper()
        except Exception as e:
            logging.warning("qc_wizard: lecture UID MCU echouee: %s", e)
        if uid:
            GLib.idle_add(self._set_mcu_uid, uid)

    def _set_mcu_uid(self, uid):
        try:
            self.labels["id_display"].set_markup(
                f"<span size='small' foreground='#9E9E9E'>MCU UID: {uid}</span>")
        except Exception:
            pass
        return False

    # ─── QC MODE (swap printer.cfg <-> qc_printer.cfg) ─────────

    def _is_qc_mode(self):
        """True if the currently loaded Klipper config is the QC one
        (detected via the [gcode_macro _QC_MODE] marker)."""
        try:
            return bool(self._screen.printer.get_config_section("gcode_macro _QC_MODE"))
        except Exception:
            return False

    def _active_qc_model(self):
        """Taille machine gravée dans la cfg QC active (variable_model du marqueur
        _QC_MODE), ou None si absente/illisible."""
        try:
            sec = self._screen.printer.get_config_section("gcode_macro _QC_MODE") or {}
            val = str(sec.get("variable_model", "")).strip().strip('"').strip("'")
            return val or None
        except Exception:
            return None

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
        """Un APPUI sur le modèle = L'ACTION directe : entre en mode QC (depuis
        prod) ou LANCE le QC (si déjà en mode QC pour ce modèle)."""
        if not os.path.exists(self._qc_cfg_path(size)):
            return
        self._selected_size = size
        if self._is_qc_mode() and self._active_qc_model() == size:
            self._on_start_clicked(widget)
        else:
            self._on_enter_qc_mode(widget)

    def _on_ztap_calibrate(self, widget):
        """Bouton Calibration Z TAP : envoie juste la séquence
        (G28 -> Z max -> tap), sans capture de log ni rapport."""
        self._screen._ws.klippy.gcode_script("QC_ZTAP_CALIBRATE")

    def _on_enter_qc_mode(self, widget):
        """Backup printer.cfg, install the QC config of the selected size,
        restart Klipper."""
        qc_cfg = self._qc_cfg_path(self._selected_size)
        if not os.path.exists(qc_cfg):
            self._screen.show_popup_message(
                f"{self._selected_size} 配置未生成 / cfg not generated yet "
                f"({os.path.basename(qc_cfg)})",
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
            self._screen.show_popup_message(f"配置切换失败 / cfg swap failed: {e}", level=3)
            return
        self._screen.show_popup_message(
            f"加载 {self._selected_size} QC配置，重启中… / Loading QC cfg, restarting…",
            level=1)
        self._screen._ws.klippy.restart_firmware()

    def _on_exit_qc_mode(self, widget):
        """Restore the production printer.cfg and restart Klipper."""
        if not os.path.exists(BACKUP_CFG):
            self._screen.show_popup_message(
                "无备份 / No backup found (printer.cfg.qc-backup)", level=3)
            return
        try:
            self._copy_cfg_content(BACKUP_CFG, PROD_CFG)
            os.remove(BACKUP_CFG)
        except Exception as e:
            logger.error(f"QC: cfg restore failed: {e}")
            self._screen.show_popup_message(f"恢复失败 / Restore failed: {e}", level=3)
            return
        self._screen.show_popup_message(
            "恢复生产配置，重启中… / Production cfg restored, restarting…", level=1)
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
        self.labels["progress"].set_markup("<span size='large'>0 / {}</span>".format(len(self.engine.tests)))
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
            tid = test.get("id", "")
            result = test.get("result", "pending")
            is_yms_fail = (
                tid.startswith("e") and tid.endswith("_head")
                and result == "fail"
            )

            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
            row.set_margin_start(10)
            row.set_margin_end(10)

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

            # Re-test unitaire : une ligne YMS en FAIL est cliquable.
            if is_yms_fail:
                event_box = Gtk.EventBox()
                event_box.add(row)
                event_box.connect("button-press-event", self._on_yms_fail_row_clicked, tid)
                event_box.get_style_context().add_class("button")
                results_box.pack_start(event_box, False, False, 0)
            else:
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

    def _on_print_plaque(self, widget):
        try:
            self._screen._ws.klippy.gcode_script("QC_PRINT_PLAQUE")
            self._screen.show_popup_message(_("打印标签中… / Impression etiquette…"), level=1)
        except Exception as e:
            self._screen.show_popup_message("Print label failed: %s" % e, level=3)

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
                "请先激活QC模式 / Enable QC mode first", level=2)
            return
        printer_id = self.labels["printer_id"].get_text().strip()
        if not printer_id:
            self._screen.show_popup_message(
                "请输入打印机ID / Enter a Printer ID", level=2
            )
            return

        if self._selected_size.upper().startswith("YMS"):
            # Banc YMS : choix du modèle AVANT l'allocation groupée.
            self._show_yms_model_selector(printer_id)
            return

        self._build_running_screen()
        test = self.engine.start(printer_id, model=self._selected_size)
        self._start_gcode_poller()
        if test:
            self._run_test(test)

    # ─── FILET ANTI-PERTE DE SIGNAUX (gcode_store poller) ──────────────
    # Vécu au gate banc : après un shutdown Klipper (erreur TMC) + restart,
    # KlipperScreen masque ce panel et process_update ne lui livre plus les
    # réponses gcode -> un PASS réel a été raté et le test est parti en faux
    # FAIL par timeout. Ce thread relit le gcode_store Moonraker et REJOUE
    # chaque réponse dans l'engine : la voie GTK reste en place, l'engine
    # déduplique (logs identiques ignorés, signaux d'un test non courant
    # ignorés), donc la double livraison est sans effet.

    def _start_gcode_poller(self):
        self._stop_gcode_poller()
        self._poller_stop = threading.Event()
        threading.Thread(target=self._gcode_poller_worker,
                         args=(self._poller_stop,), daemon=True).start()

    def _stop_gcode_poller(self):
        if getattr(self, "_poller_stop", None):
            self._poller_stop.set()
            self._poller_stop = None

    def _gcode_poller_worker(self, stop):
        # Ne rejoue que les lignes POSTERIEURES au demarrage du run : le
        # buffer Moonraker garde les signaux des runs precedents, qui
        # completeraient faussement le test courant.
        last = time.time()
        while not stop.wait(2.0):
            try:
                with urllib.request.urlopen(
                        "http://127.0.0.1:7125/server/gcode_store?count=60",
                        timeout=3) as r:
                    entries = json.loads(
                        r.read().decode())["result"]["gcode_store"]
            except Exception:
                continue
            for g in entries:
                if g.get("type") == "response" and g.get("time", 0) > last:
                    last = g["time"]
                    GLib.idle_add(self.engine.process_gcode_response,
                                  g.get("message", ""))

    def _show_yms_model_selector(self, printer_id):
        """Dialogue 2 gros boutons LIGHT / PRO avant allocation YMS."""
        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=20)
        content.set_valign(Gtk.Align.CENTER)

        title = Gtk.Label()
        title.set_markup("<span size='x-large' weight='bold'>选择型号 / Select model</span>")
        content.pack_start(title, False, False, 10)

        hint = Gtk.Label()
        hint.set_markup("<span size='large'>YMS 型号 / YMS model</span>")
        content.pack_start(hint, False, False, 5)

        btn_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=20)
        btn_box.set_halign(Gtk.Align.CENTER)

        light_btn = self._gtk.Button(None, "LIGHT", "color3")
        light_btn.set_size_request(160, 120)
        light_btn.connect("clicked", self._on_yms_model_selected, printer_id, "light")
        btn_box.pack_start(light_btn, False, False, 0)

        pro_btn = self._gtk.Button(None, "PRO", "color1")
        pro_btn.set_size_request(160, 120)
        pro_btn.connect("clicked", self._on_yms_model_selected, printer_id, "pro")
        btn_box.pack_start(pro_btn, False, False, 0)

        content.pack_start(btn_box, False, False, 10)

        self._gtk.Dialog(
            _("YMS Model"),
            [],
            content,
            lambda *args: None,
        )

    def _on_yms_model_selected(self, widget, printer_id, model):
        """Le modèle est choisi : démarre DIRECTEMENT la séquence. v1.4 : plus
        aucune allocation en amont — le code de chaque boîtier est demandé en
        FIN de son test (PASS -> numéro de série, FAIL -> code QCFL-)."""
        self._yms_model = model
        self._yms_start_sequence(printer_id)

    # ─── BANC YMS : démarrage de séquence (v1.4, sans allocation) ──────

    def _allocate_yms_codes(self, count, model, result="pass"):
        """Adaptateur autour du client d'allocation pur qc_yms.allocate_yms_codes."""
        token = self._qc_token()
        return allocate_yms_codes(
            QC_YMS_ALLOCATE_URL, token, count, model, result=result,
            yms_version=(self._bench_config or {}).get("yms_version", "1.0"))

    def _yms_start_sequence(self, printer_id):
        """Main thread : démarre la séquence 12 positions (slots HS sautés)."""
        slots_path = os.path.join(CONFIG_DIR, "qc_bench_slots.json")
        self._disabled_positions = load_disabled_positions(slots_path)
        # v1.5 : version produit + composants montés (fichier éditable opérateur)
        self._bench_config = load_bench_config(
            os.path.join(CONFIG_DIR, "qc_bench_config.json"))
        self._box_started = {}
        self._bench_session = "%s-%s" % (printer_id,
                                         datetime.now().strftime("%Y%m%d-%H%M"))
        logger.info("QC YMS: séquence %s (modèle %s), slots hors service %s",
                    self._bench_session, self._yms_model,
                    self._disabled_positions)
        self._build_running_screen()
        # Démarre l'engine, puis remplace la séquence par la vraie liste YMS12
        # incluant les positions désactivées (marquées skipped).
        self.engine.start(printer_id, model=self._selected_size)
        self.engine.tests = build_yms_tests(self._disabled_positions)
        self.engine.results = {}
        self.engine._test_log = {}
        for test in self.engine.tests:
            self.engine.results[test["id"]] = {
                "result": QCResult.PENDING,
                "timestamp": None,
                "details": "",
            }
        self.engine.current_test_index = -1
        self._start_gcode_poller()
        test = self.engine.next_test()
        if test:
            self._run_test(test)
        return False

    # ─── RE-TEST UNITAIRE D'UN BOÎTIER YMS EN FAIL ─────────────────

    def _on_yms_fail_row_clicked(self, widget, event, test_id):
        """Ligne YMS FAIL touchée sur l'écran résumé : propose le re-test."""
        pos = position_from_test_id(test_id)
        label = Gtk.Label()
        label.set_markup(
            "<span size='large'>重新测试 YMS-%d ?\nRe-test YMS-%d ?</span>" % (pos, pos))
        buttons = [
            {"name": _("是 / YES"), "response": Gtk.ResponseType.YES,
             "style": "color3"},
            {"name": _("否 / NO"), "response": Gtk.ResponseType.NO,
             "style": "color2"},
        ]
        self._gtk.Dialog(
            _("Re-test YMS-%d") % pos,
            buttons,
            label,
            lambda dialog, resp: self._on_yms_retest_confirmed(dialog, resp, test_id),
        )

    def _on_yms_retest_confirmed(self, dialog, response_id, test_id):
        self._gtk.remove_dialog(dialog)
        if response_id != Gtk.ResponseType.YES:
            return
        printer_id = self.labels["printer_id"].get_text().strip()
        self._yms_retest_test_id = test_id
        # v1.4 : pas d'allocation en amont — le nouveau code (re-test = code
        # NEUF, jamais réutilisé) est demandé en fin de test comme en séquence.
        logger.info("QC YMS: re-test %s", test_id)
        if not self._bench_config:
            self._bench_config = load_bench_config(
                os.path.join(CONFIG_DIR, "qc_bench_config.json"))
        self._build_running_screen()
        self.engine.start(printer_id, model=self._selected_size)
        pos = position_from_test_id(self._yms_retest_test_id)
        self.engine.tests = build_retest_sequence(pos)
        self.engine.results = {}
        self.engine._test_log = {}
        for test in self.engine.tests:
            self.engine.results[test["id"]] = {
                "result": QCResult.PENDING,
                "timestamp": None,
                "details": "",
            }
        self.engine.current_test_index = -1
        self._start_gcode_poller()
        test = self.engine.next_test()
        if test:
            self._run_test(test)
        return False

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
        # Banc YMS : rapport PAR BOÎTIER envoyé au fil de la séquence (l'envoi
        # et l'étiquette partent en thread, le test suivant démarre sans
        # attendre). Un boîtier SKIPPÉ = code brûlé, pas de rapport.
        is_yms_head = re.fullmatch(r"e\d+_head", test_id)
        if (self._selected_size.upper().startswith("YMS")
                and is_yms_head
                and result in (QCResult.PASS, QCResult.FAIL)):
            # v1.4 : l'allocation du code se fait DANS le thread de dispatch,
            # en fin de test (PASS -> YMSL-/YMSP-, FAIL -> QCFL-). Séquence et
            # re-test passent par le même chemin.
            threading.Thread(target=self._dispatch_box,
                             args=(test_id, result), daemon=True).start()
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
        self._stop_gcode_poller()
        # Modèle machine choisi à l'écran (fiable même si YUMI_CONFIG vide).
        report["qc_model"] = self._selected_size
        # Le YUMI_CONFIG gravé sépare les clés par des ';' ; le compteur ventile
        # par "device=" en découpant sur les espaces -> on normalise les ';'.
        yc = (report.get("yumi_config") or "").replace(";", " ").strip()
        # Si le firmware n'a pas gravé de device (pad non gravé), on injecte le
        # modèle choisi à l'écran. Le firmware gravé reste PRIORITAIRE.
        if "device=" not in yc:
            yc = (yc + " device=" + self._selected_size).strip()
        report["yumi_config"] = yc
        if self._selected_size.upper().startswith("YMS"):
            # Banc YMS (contrat v1.1) : les rapports PAR BOÎTIER sont déjà
            # partis au fil de la séquence. Le résumé de session reste LOCAL
            # (la session n'est pas un boîtier, et le device=C235 gravé de la
            # carte du banc ne doit jamais compter comme une machine).
            report["printer_id"] = self._bench_session or report["printer_id"]
            report["machine_uid"] = ""
            report["yumi_config"] = "device=YMS-BENCH-SESSION"
            self.engine.printer_id = report["printer_id"]
        self._current_report = report
        # Cleanup: stop heaters/fans/motors
        self._screen._ws.klippy.gcode_script("QC_CLEANUP")
        GLib.idle_add(self._build_summary_screen, report)
        if self._selected_size.upper().startswith("YMS"):
            # Résumé de session : sauvegarde locale SEULEMENT, marqué .sent
            # tout de suite pour que le daemon de retry ne l'envoie jamais.
            try:
                path = self.engine.save_report(report)
                open(path + ".sent", "w").close()
            except OSError as e:
                logger.warning("QC YMS: sauvegarde session: %s", e)
        else:
            # Envoi AUTOMATIQUE au compteur en fin de QC — indépendant du
            # bouton Terminer et de la présence d'un backup.
            GLib.idle_add(self._auto_upload)

    def _auto_upload(self):
        """Sauvegarde + envoie le rapport au compteur en fin de QC. Si l'envoi
        échoue (réseau coupé), le rapport reste SANS marqueur .sent -> le daemon
        de retry (timer systemd qc-upload, toutes les 2 min) le renverra jusqu'au
        200. Aucun rapport perdu. One-shot pour GLib.idle_add."""
        if self._current_report:
            path = self.engine.save_report(self._current_report)
            ok, msg = self._upload_report(self._current_report)
            if ok and path:
                try:
                    open(path + ".sent", "w").close()
                except OSError:
                    pass
            elif not ok:
                msg += " — renvoi auto en arrière-plan jusqu'à reprise réseau"
            # Étiquette QC auto sur la POS80L branchée au pad (silencieux si absente)
            lok, lmsg = self._print_qc_label(self._current_report)
            if lok:
                msg += " — 标签已打印 / étiquette imprimée"
            elif "absente" not in lmsg:
                msg += " — " + lmsg
            self._screen.show_popup_message(msg, level=1 if ok else 2)
        return False

    # ─── BANC YMS : rapport par boîtier (contrat FORMAT-YMS.md v1.4) ────

    def _build_box_report(self, test_id, result, yms_id):
        """Rapport individuel d'un boîtier YMS (position banc = e<k>_head ->
        YMS-(k+1)). Adaptateur autour du module pur qc_yms.build_box_report."""
        passed = (result == QCResult.PASS)
        return build_box_report(
            test_id=test_id,
            result="PASS" if passed else "FAIL",
            yms_id=yms_id,
            session=self._bench_session,
            pad_mac=self.labels["printer_id"].get_text().strip(),
            technician=self.engine.technician,
            test_log=self.engine._test_log,
            engine_results=self.engine.results,
            model=self._yms_model,
            bench_total=YMS_BENCH_TOTAL,
            bench_slots=YMS_BENCH_SLOTS,
            started=self._box_started.get(test_id) or datetime.now(),
            now=datetime.now(),
            extruder_model=(self._bench_config or {}).get("extruder_model", ""),
            spring_model=(self._bench_config or {}).get("spring_model", ""),
        )

    @staticmethod
    def _extract_measures(logs, passed):
        """measures{} depuis les logs du test (fail_reason = 7 valeurs normées)."""
        return extract_measures(logs, passed)

    def _dispatch_box(self, test_id, result):
        """Thread de fin de test d'un boîtier (v1.4) :
        1. alloue le code AU RÉSULTAT (PASS -> numéro de série YMSL-/YMSP-,
           FAIL -> code famille QCFL-, retry 3x) ;
        2. construit + sauve le rapport (store-and-forward) et l'envoie
           (.sent sur ACK, sinon le timer qc-upload retentera) ;
        3. imprime l'étiquette SYSTÉMATIQUEMENT (PASS = numéro de série + QR,
           FAIL = étiquette de rejet position + raison) — une étiquette par
           test, aucun décalage possible dans la pile de boîtiers."""
        pos = position_from_test_id(test_id)
        passed = (result == QCResult.PASS)
        ids, err = None, ""
        for _attempt in range(3):
            ids, err = self._allocate_yms_codes(
                1, self._yms_model, "pass" if passed else "fail")
            if ids:
                break
            time.sleep(5)
        if ids:
            yms_id = ids[0]
        else:
            # Pas de code (réseau/serveur HS) : rapport gardé en LOCAL avec
            # identité provisoire, étiquette quand même (pile non décalée),
            # boîtier à repasser une fois le réseau revenu.
            yms_id = "NOCODE-P%02d-%s" % (pos, self._bench_session)
            logger.error("QC YMS: allocation impossible pos %d: %s", pos, err)
        report = self._build_box_report(test_id, result, yms_id)
        no_code = yms_id.startswith("NOCODE-")
        path = ""
        try:
            report_dir = os.path.expanduser("~/printer_data/config/qc_reports")
            os.makedirs(report_dir, exist_ok=True)
            path = os.path.join(report_dir, "QC_%s_%s.json" % (
                yms_id, datetime.now().strftime("%Y%m%d_%H%M%S")))
            with open(path, "w") as f:
                json.dump(report, f, indent=2, ensure_ascii=False)
            if no_code:
                # identité provisoire : ne JAMAIS l'envoyer au compteur
                open(path + ".sent", "w").close()
        except OSError as e:
            logger.error("QC YMS: sauvegarde rapport %s: %s", yms_id, e)
        ok, msg = (False, "sans code (local)") if no_code \
            else self._upload_report(report)
        if ok and path:
            try:
                open(path + ".sent", "w").close()
            except OSError:
                pass
        lok, lmsg = self._print_qc_label(report)
        note = "YMS-%d %s → %s: %s%s" % (
            pos, "PASS" if passed else "FAIL", yms_id,
            "envoyé ✓" if ok else ("local" if no_code else "en attente réseau"),
            " — 标签 ✓" if lok else "")
        logger.info("QC YMS: %s (%s / %s)", note, msg, lmsg)
        GLib.idle_add(self._screen.show_popup_message, note, 1 if ok else 2)

    # ─── ÉTIQUETTE QC (POS80L branchée au pad, TSPL brut) ──────────────
    # v1 locale : texte + QR vers la page rapport. Le format définitif
    # viendra de label.yumi-lab.com (lien avec variables -> document à
    # imprimer) : remplacer le corps de _print_qc_label par le fetch du
    # document + conversion BITMAP (cf. pos80l/bridge du repo POS-Printer).

    POS80L_DEV = "/dev/usb/lp0"

    def _print_qc_label(self, report):
        """Imprime l'étiquette QC (58x37, gap+peel natifs TSPL). Renvoie
        (ok, message). Ne bloque jamais le QC : imprimante absente = no-op."""
        if not os.path.exists(self.POS80L_DEV):
            return False, "POS80L absente"
        tspl = build_label_tspl(report)
        try:
            with open(self.POS80L_DEV, "wb") as f:
                f.write(tspl)
            return True, "étiquette imprimée"
        except OSError as e:
            logger.warning("QC: impression étiquette POS80L: %s", e)
            return False, "étiquette: %s" % e

    # ─── TEST EXECUTION ────────────────────────────────────────

    def _run_test(self, test):
        """Send the macro for the current test."""
        self._update_test_display(test)
        self._cancel_timeout()
        # Position banc hors service : on la marque SKIP sans macro ni rapport.
        if test.get("skipped"):
            self.engine.skip_current_test()
            return
        # Un test peut faire tomber Klipper (ex: phase moteur HS -> erreur TMC
        # -> shutdown). Sans relance, toutes les macros suivantes partiraient
        # dans le vide et chaque test brûlerait son timeout : on relance le
        # firmware et on re-tente CE test 25 s plus tard (2 essais max).
        if getattr(self._screen.printer, "state", "") in ("shutdown", "error"):
            self._restart_retries += 1
            if self._restart_retries > 2:
                self._restart_retries = 0
                self.engine.fail_current_test(
                    "Klipper en shutdown persistant avant ce test")
                return
            logger.warning(
                "QC: klippy en shutdown avant %s — FIRMWARE_RESTART + retry",
                test["id"])
            self._screen._ws.klippy.restart_firmware()
            GLib.timeout_add_seconds(25, self._retry_test_after_restart,
                                     test["id"])
            return
        self._restart_retries = 0
        self._box_started[test["id"]] = datetime.now()
        timeout = test.get("timeout", 0)
        if timeout:
            self._timeout_id = GLib.timeout_add_seconds(
                timeout, self._on_test_timeout, test["id"]
            )
        macro = test.get("macro", "")
        if macro:
            self._screen._ws.klippy.gcode_script(macro)

    def _retry_test_after_restart(self, test_id):
        """Re-tente le test courant après un FIRMWARE_RESTART (one-shot GLib)."""
        test = self.engine.get_current_test()
        if test and test["id"] == test_id and self.engine.state == QCState.RUNNING:
            self._run_test(test)
        return False

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
            # Le budget écoulé EST la valeur configurée du test : on la met dans
            # le rapport et le log pour savoir tout de suite laquelle relever
            # (qc_engine.QC_TESTS) sans avoir à chronométrer à la main.
            budget = test.get("timeout", 0)
            logger.warning(
                f"QC: test {test_id} timed out after {budget}s "
                f"(macro '{test.get('macro', '?')}' n'a pas repondu)")
            # Cut whatever the test turned on (heater/bed/fan) before failing
            cleanup = test.get("cleanup")
            if cleanup:
                self._screen._ws.klippy.gcode_script(cleanup)
            self.engine.fail_current_test(
                "Timeout %ss depasse sur %s : la macro %s n'a pas repondu "
                "(relever le timeout de ce test dans qc_engine.QC_TESTS si la "
                "machine fonctionne)"
                % (budget, test_id, test.get("macro", "?")))
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
        for t in self.engine.tests:
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
