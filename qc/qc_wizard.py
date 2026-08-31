"""
QC Wizard Panel — KlipperScreen panel for factory quality control.
Provides a step-by-step wizard with automated tests and visual confirmations.
"""
import gi
import json
import logging
import os
import re
import shutil
import subprocess
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

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

# Mode d'impression étiquette QC MACHINE (C235/C335/C435 — pas le banc YMS12,
# qui garde sa POS80L locale sans bascule possible). Les pads QC machine
# n'ont plus d'imprimante branchée en local : "network" (serveur usine
# smartpi-printer-factory) est le seul mode réel et le défaut. "local" est un
# secours manuel gardé par sécurité (ex. une POS80L rebranchée en direct sur
# le pad en dépannage) — persisté pour ne pas avoir à rebasculer à chaque QC.
QC_PRINT_MODE_FILE = os.path.join(CONFIG_DIR, "qc_print_mode")
QC_PRINT_MODE_NETWORK = "network"
QC_PRINT_MODE_LOCAL = "local"

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
# Relais impression réseau (26/08) : quand le pad n'a pas d'accès LAN/VPN
# direct à smartpi-printer-factory, on pousse le job via qc.yumi-lab.com
# (même token que /api/qc/report) -> gs1-proxy -> file que le boîtier
# imprimante va lui-même chercher en HTTPS normal. Voir _print_qc_label_relay.
QC_PRINT_RELAY_URL = "https://qc.yumi-lab.com/api/qc/print/factory"
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
        build_label_png_job,
        build_label_tspl,
        build_retest_sequence,
        build_yms_tests,
        enabled_positions,
        extract_measures,
        load_bench_config,
        load_disabled_positions,
        find_unready_heat_positions,
        heat_positions_for_run,
        position_from_test_id,
        test_id_for_position,
        LOAD_ALL_TEST_ID,
        STRESS_ALL_TEST_ID,
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
        build_label_png_job,
        build_label_tspl,
        build_retest_sequence,
        build_yms_tests,
        enabled_positions,
        extract_measures,
        load_bench_config,
        load_disabled_positions,
        find_unready_heat_positions,
        heat_positions_for_run,
        position_from_test_id,
        test_id_for_position,
        LOAD_ALL_TEST_ID,
        STRESS_ALL_TEST_ID,
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
        self._poller_lock = threading.Lock()
        self._poller_stop = None
        self._dispatch_lock = threading.Lock()
        # POS80L locale (31/08) : /dev/usb/lp0 est un device caractere brut,
        # sans file d'attente -- deux write() concurrents (ex. un reimprimer
        # manuel pendant qu'un dispatch auto imprime une AUTRE position)
        # s'entrelacent sur le fil USB (constate en reel : images decalees/
        # superposees, "comme si la trame s'arrete pour etre remplacee par
        # un bout d'une autre etiquette"). _dispatch_lock ne protege PAS ce
        # cas (le reimprimer manuel ne le prend jamais) -- verrou dedie.
        self._pos80l_lock = threading.Lock()
        self._selected_size = QC_SIZES[0]
        self._yms_model = DEFAULT_MODEL  # light ou pro (sélectionné au lancement)
        self._bench_config = {}     # v1.5 : yms_version + composants montés
        self._disabled_positions = []  # positions 1..12 hors service
        self._bench_session = ""    # pad_mac-YYYYMMDD-HHMM du début de séquence
        self._box_started = {}      # test_id -> datetime de début (durée/boîtier)
        self._batch_ended = None    # v8 (26/08) : datetime de fin du lot (durée/boîtier)
        self._box_reports = {}      # position -> rapport envoyé (carte + réimpression)

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

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3)
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
                f"<span size='medium' foreground='#4CAF50'>QC模式 已激活 — {active_model or '?'} / "
                f"QC mode active</span>")
        else:
            mode_label.set_markup(
                "<span size='medium' foreground='#FF9800'>生产配置 — 触摸机型加载QC / "
                "Production cfg — touch a model to load QC</span>")
        mode_label.set_justify(Gtk.Justification.CENTER)
        mode_label.set_line_wrap(True)
        box.pack_start(mode_label, False, False, 2)

        # Sélecteur de taille machine — TOUJOURS visible et ACTIF : un appui sur
        # un modèle est l'action (charge sa cfg, ou lance le QC si déjà chargé).
        size_title = Gtk.Label()
        size_title.set_markup("<span size='medium' weight='bold'>机型 / Model</span>")
        box.pack_start(size_title, False, False, 2)

        size_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        size_row.set_halign(Gtk.Align.CENTER)
        for size in QC_SIZES:
            avail = os.path.exists(self._qc_cfg_path(size))
            selected = (size == self._selected_size)
            label = size if avail else f"{size}\n待生成/TBD"
            style = "color3" if selected else ("color1" if avail else "color2")
            sbtn = self._gtk.Button(None, label, style)
            sbtn.set_size_request(120, 58)
            sbtn.set_sensitive(avail)
            sbtn.connect("clicked", self._on_size_selected, size)
            size_row.pack_start(sbtn, False, False, 0)
        box.pack_start(size_row, False, False, 2)

        # Aide : un APPUI sur le modèle LANCE directement (entre en QC / START).
        hint = Gtk.Label()
        if qc_mode:
            hint.set_markup(f"<span size='medium' weight='bold' foreground='#4CAF50'>"
                            f"▶ 触摸 {active_model or 'C235'} 开始检测 / "
                            f"touch {active_model or 'C235'} to START QC</span>")
        else:
            # Générique : les trois modèles ont leur cfg, plus de raison de
            # nommer C235 en dur.
            hint.set_markup("<span size='medium' weight='bold' foreground='#4CAF50'>"
                            "▶ 触摸机型 进入QC模式 / touch a model to enter QC mode</span>")
        hint.set_justify(Gtk.Justification.CENTER)
        hint.set_line_wrap(True)
        box.pack_start(hint, False, False, 2)

        # Bouton Calibration Z TAP — juste la séquence G28 -> Z max -> tap.
        # Z TAP + Imprimer etiquette M3 cote a cote (tenir sur 800x480)
        action_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        action_row.set_halign(Gtk.Align.CENTER)
        ztap_btn = self._gtk.Button("refresh", "Z TAP", "color1")
        ztap_btn.connect("clicked", self._on_ztap_calibrate)
        ztap_btn.set_size_request(180, 52)
        action_row.pack_start(ztap_btn, False, False, 0)
        plaque_btn = self._gtk.Button("print", "标签 / Etiquette M3", "color1")
        plaque_btn.connect("clicked", self._on_print_plaque)
        plaque_btn.set_size_request(280, 52)
        action_row.pack_start(plaque_btn, False, False, 0)
        zup_btn = self._gtk.Button("z-farther", "Z ↑ 200mm", "color2")
        zup_btn.connect("clicked", self._on_z_up)
        zup_btn.set_size_request(160, 52)
        action_row.pack_start(zup_btn, False, False, 0)
        box.pack_start(action_row, False, False, 2)

        # Bascule mode impression étiquette QC machine — réseau par défaut
        # (plus d'imprimante locale sur les pads), local = secours manuel
        # rare, + sortie QC. Toujours dans le flux normal (pas de scroll :
        # glissé tactile peu fiable constaté sur banc, bouton invisible).
        is_network_mode = (self._get_print_mode() == QC_PRINT_MODE_NETWORK)
        mode_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        mode_row.set_halign(Gtk.Align.CENTER)
        mode_btn = self._gtk.Button(
            "network",
            "标签打印: 网络 / Étiquette: Réseau" if is_network_mode
            else "标签打印: 本地 / Étiquette: Local (secours)",
            "color1" if is_network_mode else "color2")
        mode_btn.connect("clicked", self._on_toggle_print_mode)
        mode_btn.set_size_request(320, 44)
        mode_row.pack_start(mode_btn, False, False, 0)

        if qc_mode:
            exit_btn = self._gtk.Button("cancel", "退出QC模式 / Exit QC mode", "color2")
            exit_btn.connect("clicked", self._on_exit_qc_mode)
            exit_btn.set_size_request(280, 44)
            mode_row.pack_start(exit_btn, False, False, 0)

        box.pack_start(mode_row, False, False, 2)

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

    def _query_heat_temperatures(self, positions):
        """Lecture Moonraker EN DIRECT (pas le cache self._screen.printer --
        les objets heater_generic YMS-N-heater ne sont pas garantis souscrits)
        des températures de positions chauffantes, même pattern que
        _worker_mcu_uid. Renvoie {position: température°C|None} ; appel
        synchrone volontairement (localhost, ~ms) pour bloquer le démarrage
        tant que le résultat n'est pas connu -- cf. _yms_start_sequence."""
        import urllib.parse
        if not positions:
            return {}
        names = ["heater_generic YMS-%d-heater" % p for p in positions]
        qs = "&".join("%s=temperature" % urllib.parse.quote(n) for n in names)
        temps = {}
        try:
            raw = urllib.request.urlopen(
                "http://localhost:7125/printer/objects/query?%s" % qs,
                timeout=4).read()
            status = json.loads(raw)["result"]["status"]
            for p, n in zip(positions, names):
                temps[p] = (status.get(n) or {}).get("temperature")
        except Exception as e:
            logger.warning("QC YMS: lecture temperatures chauffe echouee: %s", e)
        return temps

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

    def _on_z_up(self, widget):
        """Bouton Z ↑ 200mm : monte Z de 200mm SANS homing (QC_Z_UP -> FORCE_MOVE),
        pour degager largement la buse/le plateau a la main (acces tactile seul, pas
        de console dispo sur ces pads). ⚠️ FORCE_MOVE ne detecte ni fin de course ni
        collision -- accepte en connaissance de cause (29/08), pas un depart par
        defaut prudent : si la marge reelle est plus faible que prevu en haut, ca
        peut forcer sur la butee mecanique."""
        self._screen._ws.klippy.gcode_script("QC_Z_UP DIST=200")

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
        # Ligne sans limite de largeur ni retour -> le printer_id (session
        # banc YMS = "0281D1AF910C-20260822-1423", ~26 car.) pousse la ligne
        # au-delà de 800px et sort de l'écran (signalé 22/08 -- persiste
        # malgré la taille fixe posée sur les boutons, cause différente).
        info_label = Gtk.Label()
        info_label.set_markup(
            f"<span size='large'>{report.get('qc_model', '?')} — "
            f"Printer: {report.get('printer_id', '?')} — "
            f"Duration: {mins}m {secs}s</span>"
        )
        info_label.set_line_wrap(True)
        info_label.set_max_width_chars(50)
        info_label.set_justify(Gtk.Justification.CENTER)
        main_box.pack_start(info_label, False, False, 5)

        # ── BANC YMS : carte des positions (vert = PASS, rouge = FAIL,
        # gris = non testé) — un appui sur un carré propose re-tester OU
        # réimprimer l'étiquette du boîtier (cf. _on_pos_square_clicked). Les
        # 12 tiennent sur UNE ligne (demande du 22/08 : plus lisible que 2
        # rangées de 6, et l'écran est en 800px de large). ──
        self._pos_buttons = {}
        # v7 (26/08) : la grille doit TOUJOURS se construire pour un run YMS,
        # même si self._box_reports est encore vide -- ce qui est désormais
        # TOUJOURS le cas ici : _on_qc_complete affiche ce résumé puis lance
        # _dispatch_all_boxes_ordered dans un thread d'arrière-plan (v3,
        # 23/08), qui remplit self._box_reports et déclenche
        # _refresh_pos_button progressivement APRÈS. Avec le "and
        # self._box_reports" d'origine (hérité du modèle séquentiel où le
        # dispatch était déjà terminé à ce stade), la grille entière ne se
        # construisait plus jamais -- aucun carré, donc aucun moyen de
        # réimprimer une étiquette manquante (constaté en réel 26/08 :
        # YMS-7 jamais sortie, YMS-2 imprimée avec un chiffre manquant, et
        # plus de panneau pour les rattraper). Les carrés démarrent gris/
        # désactivés (rep is None ci-dessous) et se colorent au fil de l'eau.
        if self._selected_size.upper().startswith("YMS"):
            pos_grid = Gtk.Grid(column_spacing=4, row_spacing=6)
            pos_grid.set_halign(Gtk.Align.CENTER)
            for pos in range(1, YMS_BENCH_TOTAL + 1):
                rep = self._box_reports.get(pos)
                if rep is None:
                    sq = self._gtk.Button(None, str(pos), None)
                    sq.set_sensitive(False)
                else:
                    passed = rep.get("overall_result") == "PASS"
                    sq = self._gtk.Button(None, str(pos),
                                          "color3" if passed else "color2")
                    sq.connect("clicked", self._on_pos_square_clicked, pos)
                # 52px (pas 56+) : la zone de contenu KlipperScreen n'est PAS
                # les 800px de l'écran -- la sidebar de gauche (action_bar,
                # ks_includes/KlippyGtk.py) prend 10% en mode paysage, donc
                # 720px reels de large ici. 12*52+11*4=668px, marge confortable.
                sq.set_size_request(52, 50)
                pos_grid.attach(sq, pos - 1, 0, 1, 1)
                self._pos_buttons[pos] = sq
            main_box.pack_start(pos_grid, False, False, 4)
            reprint_hint = Gtk.Label()
            reprint_hint.set_markup(
                "<span size='small' foreground='#9E9E9E'>"
                "触摸方块：重测或重印 / toucher un carré : re-tester ou réimprimer</span>")
            main_box.pack_start(reprint_hint, False, False, 2)

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

        # Bottom buttons — jusqu'à 4 dessus (YMS + backup cfg) : taille FIXE +
        # pack sans expand, sinon le 4e sort de l'écran (signalé 22/08 — les
        # Button() ks_includes sont pensés pour une grille de menu, pas une
        # rangée compacte, donc leur taille naturelle déborde à 4). 160px, pas
        # 185+ : la zone de contenu réelle fait 720px (écran 800px - sidebar
        # gauche action_bar 10%, cf. ks_includes/KlippyGtk.py), pas 800.
        btn_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        btn_box.set_halign(Gtk.Align.CENTER)

        # Banc YMS : enchaîner un NOUVEAU lot de 12 sans repasser par l'accueil
        # (même modèle que le lot précédent, slots relus du fichier).
        if self._selected_size.upper().startswith("YMS"):
            batch_btn = self._gtk.Button("refresh", "新一批×12 / Nouveau lot ×12",
                                         "color3")
            batch_btn.connect("clicked", self._on_new_batch)
            batch_btn.set_size_request(160, 55)
            btn_box.pack_start(batch_btn, False, False, 0)

        # Finish: save report + restore production cfg + restart Klipper
        if os.path.exists(BACKUP_CFG):
            finish_btn = self._gtk.Button("complete", "完成 / Finish", "color3")
            finish_btn.connect("clicked", self._on_finish_qc)
            finish_btn.set_size_request(160, 55)
            btn_box.pack_start(finish_btn, False, False, 0)

        save_btn = self._gtk.Button("sd", _("保存报告 / Save report"), "color2")
        save_btn.connect("clicked", self._on_save_report)
        save_btn.set_size_request(160, 55)
        btn_box.pack_start(save_btn, False, False, 0)

        new_btn = self._gtk.Button("refresh", _("新检测 / New QC"), "color1")
        new_btn.connect("clicked", self._on_new_qc)
        new_btn.set_size_request(160, 55)
        btn_box.pack_start(new_btn, False, False, 0)

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
        # v4 (23/08) : start/stop DOIVENT être atomiques (self._poller_lock).
        # Sans lock, deux appels concurrents à _start_gcode_poller (déclenché
        # à chaque run/re-test/reconnect) peuvent tous les deux lire le même
        # ancien Event AVANT que l'un des deux écrase self._poller_stop -->
        # le thread créé par le second perd toute référence à son propre
        # stop Event, devient un ZOMBIE qui tourne toutes les 2s pour
        # toujours (jamais arrêtable). Vécu en réel le 23/08 : accumulation
        # de zombies sur une session de plusieurs heures pleine de
        # relances -> des dizaines de threads qui repollent en boucle,
        # rejouent les mêmes vieux messages en rafale (des centaines/s),
        # CPU explosé -> "Timer too close" -> shutdown MCU.
        with self._poller_lock:
            self._stop_gcode_poller_locked()
            self._poller_stop = threading.Event()
            stop = self._poller_stop
            threading.Thread(target=self._gcode_poller_worker,
                             args=(stop,), daemon=True).start()

    def _stop_gcode_poller(self):
        with self._poller_lock:
            self._stop_gcode_poller_locked()

    def _stop_gcode_poller_locked(self):
        """Appelant DOIT déjà tenir self._poller_lock."""
        if self._poller_stop is not None:
            self._poller_stop.set()
            self._poller_stop = None

    def _gcode_poller_worker(self, stop):
        # Ne rejoue que les lignes POSTERIEURES au demarrage du run : le
        # buffer Moonraker garde les signaux des runs precedents, qui
        # completeraient faussement le test courant.
        last = time.time()
        while not stop.wait(2.0):
            # Filet de sécurité supplémentaire : si le QC n'est plus en
            # cours (terminé/abandonné), plus rien à rejouer -- s'arrêter
            # tout seul, même si un appelant a oublié de le faire.
            if self.engine.state not in (QCState.RUNNING, QCState.WAITING_GCODE,
                                         QCState.WAITING_VISUAL):
                return
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
                    GLib.idle_add(self._replay_response, g.get("message", ""))

    def _replay_response(self, msg):
        # process_gcode_response retourne True sur presque tous ses chemins ;
        # passé tel quel à GLib.idle_add, ce True signifie "replanifie-moi" et
        # chaque message rejoué devenait un callback idle éternel (~1000
        # logs/s, /var/log zram saturé). Toujours retirer la source.
        self.engine.process_gcode_response(msg)
        return GLib.SOURCE_REMOVE

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
        # v4 (25/08) : bloque un NOUVEAU lot tant que le PRÉCÉDENT est encore
        # en train d'imprimer ses étiquettes (_dispatch_all_boxes_ordered,
        # sous self._dispatch_lock). self.engine (test_log, results,
        # bench_session, yms_model...) est une INSTANCE UNIQUE PARTAGÉE --
        # démarrer un 2e lot pendant que le 1er dispatch encore en cours la
        # RÉÉCRIT en place, donc le 1er lot finit par imprimer avec les
        # données du 2e. Constaté en réel 25/08 : deux lots entiers
        # imprimaient leurs étiquettes entrelacées (YMS-1..12 x2, mélangés).
        if self._dispatch_lock.locked():
            self._screen.show_popup_message(
                "上一批标签还在打印，请稍等 / Étiquettes du lot précédent "
                "encore en cours d'impression, patiente un instant",
                level=2)
            return
        slots_path = os.path.join(CONFIG_DIR, "qc_bench_slots.json")
        self._disabled_positions = load_disabled_positions(slots_path)
        # v6 (26/08) : PRO seulement -- si une SEULE des positions câblées
        # chauffe du lot ne renvoie pas une température plausible (sonde
        # débranchée/HS), on ne lance PAS le test. Évite de découvrir le
        # problème 30min plus tard alors qu'il était visible avant de
        # démarrer (constaté en réel 26/08 : YMS-9 à -38.1°C, thermistance
        # flottante). Lecture Moonraker directe (pas le cache _screen.printer,
        # ces objets custom ne sont pas garantis souscrits).
        heat_positions = heat_positions_for_run(self._disabled_positions, self._yms_model)
        if heat_positions:
            bad = find_unready_heat_positions(
                heat_positions, self._query_heat_temperatures(heat_positions))
            if bad:
                names = ", ".join("YMS-%d" % p for p in bad)
                self._screen.show_popup_message(
                    "温度传感器异常 %s，测试未启动 / capteur(s) température "
                    "%s non fonctionnel(s) -- test PRO NON lancé" % (names, names),
                    level=3)
                return
        # v1.5 : version produit + composants montés (fichier éditable opérateur)
        self._bench_config = load_bench_config(
            os.path.join(CONFIG_DIR, "qc_bench_config.json"))
        self._box_started = {}
        self._batch_ended = None
        self._box_reports = {}
        self._bench_session = "%s-%s" % (printer_id,
                                         datetime.now().strftime("%Y%m%d-%H%M"))
        logger.info("QC YMS: séquence %s (modèle %s), slots hors service %s",
                    self._bench_session, self._yms_model,
                    self._disabled_positions)
        self._build_running_screen()
        # Démarre l'engine, puis remplace la séquence par la vraie liste YMS12
        # incluant les positions désactivées (marquées skipped).
        self.engine.start(printer_id, model=self._selected_size)
        self.engine.tests = build_yms_tests(self._disabled_positions, model=self._yms_model)
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
        # v4 (25/08) : même garde que _yms_start_sequence -- le re-test
        # réutilise self.engine, ne pas le réécrire pendant qu'un dispatch
        # précédent l'utilise encore.
        if self._dispatch_lock.locked():
            self._screen.show_popup_message(
                "上一批标签还在打印，请稍等 / Étiquettes du lot précédent "
                "encore en cours d'impression, patiente un instant",
                level=2)
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
        self.engine.tests = build_retest_sequence(pos, model=self._yms_model)
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
        # v3 (23/08) : plus aucun dispatch par-test ici. load_all et
        # stress_all sont des étapes GROUPÉES (tous les boîtiers actifs à la
        # fois, plus de capteur tête à atteindre donc plus de test individuel
        # par position) -- même pour le re-test unitaire (build_retest_sequence
        # utilise les mêmes deux étapes, juste TOOLS= réduit à 1 position).
        # Le dispatch (allocation + rapport + étiquette), pour TOUS les cas,
        # part groupé dans l'ordre 1..12 depuis _on_qc_complete une fois la
        # séquence ENTIÈRE terminée (cf. _dispatch_all_boxes_ordered).
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
        # v8 (26/08) : fin RÉELLE du lot -- self._build_box_report l'utilise
        # comme "now" pour CHAQUE position au lieu de re-lire l'horloge à
        # chaque dispatch (cf. _build_box_report). Les positions sont testées
        # en parallèle (load_all/stress_all groupés) : elles finissent toutes
        # au même instant réel, l'impression séquentielle qui suit n'est que
        # de la paperasse, pas une nouvelle mesure.
        self._batch_ended = datetime.now(timezone.utc)
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
            # v3 (22/08) : séquence ENTIÈRE terminée (stress_all inclus) --
            # dispatch (allocation + rapport + étiquette) de TOUS les
            # boîtiers, DANS L'ORDRE des positions 1..12, un par un (pas de
            # threads concurrents ici -> l'ordre de sortie des étiquettes
            # est garanti, plus de course entre boîtiers).
            if any(t.get("id") == STRESS_ALL_TEST_ID for t in self.engine.tests):
                threading.Thread(target=self._dispatch_all_boxes_ordered,
                                 daemon=True).start()
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
            # Étiquette QC auto — réseau par défaut, "local" seulement si
            # basculé manuellement depuis le panel (secours, cf. bouton
            # d'écran d'accueil). Silencieux si imprimante/réseau absents.
            if self._get_print_mode() == QC_PRINT_MODE_LOCAL:
                lok, lmsg = self._print_qc_label(self._current_report)
            else:
                lok, lmsg = self._print_qc_label_network(self._current_report)
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
            # v8 (26/08) : test_id ("e8_head"...) n'est JAMAIS une clé de
            # self._box_started -- ce dict n'est rempli que par _run_test
            # avec l'id du test GROUPÉ en cours ("load_all", "stress_all"...,
            # cf. v3 23/08), donc ce lookup renvoyait toujours None et
            # "started" retombait sur l'heure du DISPATCH (après coup), quasi
            # identique à "now" -> Début/Fin affichaient la même heure à la
            # microseconde près et une durée de 0s (constaté en réel 26/08).
            # LOAD_ALL_TEST_ID est la bonne clé : c'est le début RÉEL du lot,
            # partagé par toutes les positions (elles chargent ensemble).
            started=self._box_started.get(LOAD_ALL_TEST_ID) or datetime.now(timezone.utc),
            now=self._batch_ended or datetime.now(timezone.utc),
            extruder_model=(self._bench_config or {}).get("extruder_model", ""),
            spring_model=(self._bench_config or {}).get("spring_model", ""),
            yms_version=(self._bench_config or {}).get("yms_version", "1.0"),
        )

    @staticmethod
    def _extract_measures(logs, passed):
        """measures{} depuis les logs du test (fail_reason = 7 valeurs normées)."""
        return extract_measures(logs, passed)

    def _dispatch_all_boxes_ordered(self):
        """Séquence ENTIÈRE terminée (stress_all inclus, v3 22/08) : dispatch
        TOUS les boîtiers (allocation + rapport + étiquette). Les étiquettes
        s'IMPRIMENT dans l'ORDRE des positions 1..12 -- garanti, pas dans
        l'ordre où chaque dispatch réseau finit par hasard (signalé 22/08 :
        source de confusion à l'usine).

        v6 (31/08) : les 12 positions tournent EN VRAI PARALLÈLE de bout en
        bout (allocation+rapport+upload+IMPRESSION, un thread par position,
        cf. _process_box) -- plus aucune attente entre positions côté pad.
        L'ordre d'impression reste garanti côté SERVEUR : chaque requête de
        print porte seq=pos, et gs1-proxy (commit 7100120, dépôt
        YUMI-POS-Printer, PAS touché depuis ce dépôt-ci -- scope partagé
        avec la session "yumi-pos-printer-39") trie sa file par ce champ
        au lieu d'un simple ordre d'arrivée réseau. Mesuré en réel avant ce
        changement (v5, 31/08 toujours) : ~15-24s entre 2 étiquettes,
        jusqu'à ~214s/position au pire cas (105s allocation + 93s
        impression, cf. analyse latence du 31/08) -- une position lente
        retardait TOUTES les positions derrière elle pour rien, alors que
        rien n'empêchait leur traitement d'avancer en parallèle.

        v3 (23/08) : load_all, stress_all ET heat_all (YMS Pro seulement) sont
        des étapes GROUPÉES (plus de capteur tête -> plus de test individuel
        par position, cf. qc_yms.build_yms_tests) -- donc PLUS de résultat
        engine.results par position à lire.

        v5 (25/08) : qc_engine.process_gcode_response route déjà CHAQUE ligne
        taguée "QC E<n>_HEAD: ..." dans le buffer de SA position (même id que
        le rapport final, "e<n>_head") au moment de la capture -- quel que
        soit le test groupé qui l'émet. self.engine._test_log[test_id] est
        donc DÉJÀ le log complet et dans l'ordre de cette position (plus
        besoin de re-trier 3 buffers partagés par préfixe après coup, cf.
        ancienne version dans l'historique git si besoin de comparer).
        Absente -> position hors de ce lot (désactivée), jamais de rapport.
        "aucun mouvement detecte" -> FAIL définitif au chargement, jamais
        éligible au stress/chauffe. Sinon, résultat tranché par le stress
        (perdu le suivi, ou aucune ligne stress captée -> FAIL, jamais de
        PASS par défaut faute de preuve) PUIS, si heat_wait a tourné pour
        cette position (YMS Pro, position câblée chauffe), par le chauffage
        (timeout -> FAIL).

        v4 (25/08) : tout le corps sous self._dispatch_lock -- si un 2e lot
        réussissait quand même à démarrer pendant que ce dispatch tourne
        encore (malgré la garde dans _yms_start_sequence), ce lock le fait
        ATTENDRE ici plutôt que d'entrelacer ses impressions avec celles du
        1er lot (constaté en réel : YMS-1..12 de deux lots mélangés)."""
        with self._dispatch_lock:
            pending = []
            for pos in range(1, YMS_BENCH_TOTAL + 1):
                test_id = test_id_for_position(pos)
                logs = self.engine._test_log.get(test_id, [])
                if not logs:
                    continue  # position absente de ce lot (désactivée) -> pas de rapport
                load_failed = any("no motion detected" in l for l in logs)
                if load_failed:
                    final = QCResult.FAIL
                    details = "Load: no motion detected"
                else:
                    stress_lost = any("LOST tracking" in l for l in logs)
                    # Aucune ligne stress captée pour cette position = son
                    # résultat n'a jamais été confirmé -- ne JAMAIS retomber
                    # sur un PASS par défaut faute de preuve (constaté 25/08 :
                    # YMS-7 et YMS-10 validés PASS avec 0 ligne stress).
                    # v10 (27/08) : "stress" en simple sous-chaine matchait
                    # AUSSI la ligne de fin de charge ("...ready for group
                    # stress") -> stress_missing valait TOUJOURS False des
                    # qu'un chargement reussissait, meme sans une seule
                    # ligne de stress reelle (constate en reel : arret moteur
                    # manuel en plein stress -> 12/12 boitiers PASS avec
                    # "0/8 segments"). Motif precis : uniquement les VRAIES
                    # lignes de la phase stress (progression "stress N/M
                    # speed=...", recap "stress OK", ou perte de suivi).
                    stress_missing = not any(
                        re.search(r"stress \d+/\d+ speed=", l) or "stress OK" in l
                        for l in logs)
                    heat_failed = any("heat timeout" in l for l in logs)
                    if stress_lost:
                        final = QCResult.FAIL
                        details = "Stress sweep (group): tracking lost"
                    elif stress_missing:
                        final = QCResult.FAIL
                        details = "Stress sweep (group): no data received"
                    elif heat_failed:
                        final = QCResult.FAIL
                        details = "Heat (group): target temperature not reached"
                    else:
                        final = QCResult.PASS
                        details = ""
                self.engine.results[test_id] = {
                    "result": final,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "details": details,
                }
                pending.append((pos, test_id, final))

            # v6 (31/08) : allocation+rapport+upload+IMPRESSION de TOUTES
            # les positions EN VRAI PARALLÈLE, un thread PAR POSITION du
            # début à la fin (_process_box) -- rendu sûr par gs1-proxy
            # (commit 7100120, côté serveur "yumi-pos-printer-39", pas
            # touché depuis ce dépôt) qui trie désormais sa file par le
            # champ "seq" (= position banc) au lieu d'un simple ordre
            # d'arrivée réseau : l'ordre d'IMPRESSION physique reste
            # garanti même si les 12 requêtes HTTP arrivent au serveur
            # dans le désordre (jitter TLS/scheduling). Remplace le modèle
            # v5 (préparation parallèle + impression séquentielle avec
            # attente d'événement par position, cf. historique git) --
            # plus aucune position n'attend son tour côté pad, la queue
            # serveur fait le travail d'ordonnancement final.
            threads = []
            for pos, test_id, final in pending:
                t = threading.Thread(
                    target=self._process_box, args=(test_id, final, pos),
                    daemon=True)
                threads.append(t)
                t.start()
            for t in threads:
                t.join()

    def _process_box(self, test_id, result, pos):
        """Prépare (allocation+rapport+upload) PUIS imprime CETTE position,
        de bout en bout dans SON PROPRE thread -- l'ordre d'impression est
        garanti côté serveur (seq, cf. _dispatch_all_boxes_ordered), plus
        besoin d'attendre son tour côté pad."""
        report = self._prepare_box(test_id, result, pos)
        if report is None:
            logger.error(
                "QC YMS: pos %d sans rapport préparé (exception ?), "
                "étiquette non imprimée", pos)
            return
        self._print_box(pos, report, result)

    def _prepare_box(self, test_id, result, pos):
        """Alloue le code AU RÉSULTAT (PASS -> numéro de série YMSL-/YMSP-,
        FAIL -> code famille QCFL-, retry 5x/6s) + construit/sauve le
        rapport (store-and-forward) + l'envoie (.sent sur ACK, sinon le
        timer qc-upload retentera). Renvoie le rapport (avec les clés
        internes _upload_ok/_upload_msg/_no_code, consommées par
        _print_box) ou None si une exception imprévue a empêché même sa
        construction -- ne doit JAMAIS laisser _process_box planter (une
        position en échec ne doit jamais empêcher les 11 autres)."""
        try:
            return self._prepare_box_inner(test_id, result, pos)
        except Exception:
            logger.exception("QC YMS: _prepare_box_inner pos %d", pos)
            return None

    def _prepare_box_inner(self, test_id, result, pos):
        passed = (result == QCResult.PASS)
        ids, err = None, ""
        # 3x/5s -> 5x/6s (31/08) : le WiFi de ce pad (cle USB sur le meme hub
        # que la POS80L) met par intermittence jusqu'a ~28s a se retablir
        # completement (rechargement firmware + reassociation, vu en reel
        # dans dmesg) -- un echec rapide type "connexion refusee"/"reset"
        # (quasi instantane, PAS le timeout 15s) pouvait epuiser les 3
        # tentatives en ~15-18s, avant que le WiFi soit vraiment revenu ->
        # NOCODE evitable. Nouvelle fenetre : jusqu'a ~30s de sommeil seul.
        for _attempt in range(5):
            ids, err = self._allocate_yms_codes(
                1, self._yms_model, "pass" if passed else "fail")
            if ids:
                break
            time.sleep(6)
        if ids:
            yms_id = ids[0]
        else:
            # Pas de code (réseau/serveur HS) : rapport gardé en LOCAL avec
            # identité provisoire, étiquette quand même (pile non décalée),
            # boîtier à repasser une fois le réseau revenu.
            yms_id = "NOCODE-P%02d-%s" % (pos, self._bench_session)
            logger.error("QC YMS: allocation impossible pos %d: %s", pos, err)
        report = self._build_box_report(test_id, result, yms_id)
        # UTC d'envoi : permet au serveur de recaler l'heure usine et de
        # mesurer la derive d'horloge du pad (received_at - sent_at_utc).
        report["sent_at_utc"] = datetime.now(timezone.utc).isoformat()
        self._box_reports[pos] = report   # carte des positions + réimpression
        # Ce thread peut finir APRÈS l'affichage de l'écran résumé (allocation
        # réseau + étiquette) -- le carré de CE poste a alors été dessiné gris/
        # non cliquable avant que le rapport existe (signalé 22/08 : carré 12
        # jamais cliquable, il termine presque toujours après le résumé).
        GLib.idle_add(self._refresh_pos_button, pos)
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
        report["_upload_ok"] = ok
        report["_upload_msg"] = msg
        report["_no_code"] = no_code
        return report

    def _print_box(self, pos, report, result):
        """Imprime l'étiquette SYSTÉMATIQUEMENT (PASS = numéro de série + QR,
        FAIL = étiquette de rejet position + raison) — une étiquette par
        test, aucun décalage possible dans la pile de boîtiers ; retry 3x
        (28/08 : un blip réseau ponctuel sur le LAN ET le relais faisait
        sauter une étiquette en silence). Appelé par _process_box, un
        thread PAR POSITION -- l'ordre d'impression est garanti côté
        SERVEUR (seq=pos transmis au relais, cf. _print_qc_label_network),
        plus par une attente côté pad (31/08)."""
        passed = (result == QCResult.PASS)
        ok = report.pop("_upload_ok")
        msg = report.pop("_upload_msg")
        no_code = report.pop("_no_code")
        yms_id = report["printer_id"]
        lok, lmsg = False, ""
        for _attempt in range(3):
            lok, lmsg = self._print_qc_label_network(report, seq=pos)
            if lok:
                break
            time.sleep(3)
        note = "YMS-%d %s → %s: %s%s" % (
            pos, "PASS" if passed else "FAIL", yms_id,
            "envoyé ✓" if ok else ("local" if no_code else "en attente réseau"),
            " — 标签 ✓" if lok else "")
        logger.info("QC YMS: %s (%s / %s)", note, msg, lmsg)
        GLib.idle_add(self._screen.show_popup_message, note, 1 if ok else 2)

    def _on_new_batch(self, widget):
        """Relance directement une séquence complète (même modèle YMS)."""
        printer_id = self.labels["printer_id"].get_text().strip()
        self._yms_start_sequence(printer_id)

    def _refresh_pos_button(self, pos):
        """Rend cliquable + colore un carré de position dont le rapport vient
        d'arriver APRÈS la construction de l'écran résumé (cf. _dispatch_box).
        No-op si l'écran résumé n'est plus affiché (carte reconstruite/quittée
        entre-temps) ou si le carré est déjà à jour."""
        btn = getattr(self, "_pos_buttons", {}).get(pos)
        rep = self._box_reports.get(pos)
        if not btn or not rep or btn.get_sensitive():
            return False
        try:
            passed = rep.get("overall_result") == "PASS"
            btn.get_style_context().add_class("color3" if passed else "color2")
            btn.set_sensitive(True)
            btn.connect("clicked", self._on_pos_square_clicked, pos)
        except Exception:
            pass
        return False

    def _on_pos_square_clicked(self, widget, pos):
        """Carré de position touché sur l'écran résumé : propose re-tester CE
        boîtier ou réimprimer son étiquette (demande du 22/08 — avant, un
        appui réimprimait direct, or c'est souvent le re-test qui est voulu)."""
        rep = self._box_reports.get(pos)
        if not rep:
            return
        label = Gtk.Label()
        label.set_markup("<span size='large'>YMS-%d</span>" % pos)
        buttons = [
            {"name": _("重新测试 / Re-tester"), "response": Gtk.ResponseType.YES,
             "style": "color1"},
            {"name": _("重印标签 / Étiquette"), "response": Gtk.ResponseType.APPLY,
             "style": "color3"},
            {"name": _("取消 / Annuler"), "response": Gtk.ResponseType.CANCEL},
        ]
        test_id = test_id_for_position(pos)
        self._gtk.Dialog(
            "YMS-%d" % pos, buttons, label,
            lambda dialog, resp: self._on_pos_square_response(dialog, resp, pos, test_id),
        )

    def _on_pos_square_response(self, dialog, response_id, pos, test_id):
        if response_id == Gtk.ResponseType.APPLY:
            self._gtk.remove_dialog(dialog)
            self._on_reprint_label(None, pos)
            return
        if response_id == Gtk.ResponseType.YES:
            # _on_yms_retest_confirmed enlève elle-même le dialogue.
            self._on_yms_retest_confirmed(dialog, Gtk.ResponseType.YES, test_id)
            return
        self._gtk.remove_dialog(dialog)

    def _on_reprint_label(self, widget, pos):
        """Réimprime l'étiquette du boîtier testé (depuis l'écran résumé)."""
        rep = self._box_reports.get(pos)
        if not rep:
            return
        ok, msg = self._print_qc_label_network(rep)
        self._screen.show_popup_message(
            "YMS-%d: 标签已重印 / étiquette réimprimée ✓" % pos if ok
            else "YMS-%d: %s" % (pos, msg), level=1 if ok else 3)

    # ─── ÉTIQUETTE QC — POS80L branchée EN DIRECT sur CE pad ───────────
    # Priorité 1 de _print_qc_label_network (31/08, remis en tête -- avait
    # été relégué à la bascule manuelle "local" du panel le 22/08 quand le
    # banc YMS12 était passé au tout-réseau, mais rien n'empêche une POS80L
    # d'être branchée directement sur le pad qui fait le QC : autant s'en
    # servir en premier, aucune dépendance réseau du tout). Reste aussi
    # appelable seul via la bascule manuelle "local" du panel QC machine.

    POS80L_DEV = "/dev/usb/lp0"

    def _print_qc_label(self, report):
        """Imprime l'étiquette QC (58x37, gap+peel natifs TSPL) sur une POS80L
        branchée EN LOCAL sur ce pad. Renvoie (ok, message). Ne bloque jamais
        le QC : imprimante absente = no-op.

        31/08 : sous _pos80l_lock (device caractere brut sans file d'attente
        -- deux write() concurrents s'entrelacent sur le fil USB, constate en
        reel) + retry 1x apres 1.5s (dmesg reel : la liaison USB de cette
        POS80L decroche/se reconnecte par intermittence en <1s -- un write()
        qui tombe pile sur la coupure peut echouer silencieusement sans
        OSError alors que rien n'est sorti a l'impression)."""
        if not os.path.exists(self.POS80L_DEV):
            return False, "POS80L absente"
        tspl = build_label_tspl(report)
        with self._pos80l_lock:
            for _attempt in range(2):
                try:
                    with open(self.POS80L_DEV, "wb") as f:
                        f.write(tspl)
                    return True, "étiquette imprimée"
                except OSError as e:
                    logger.warning("QC: impression étiquette POS80L: %s", e)
                    err = str(e)
                    time.sleep(1.5)
            return False, "étiquette: %s" % err

    # ─── ÉTIQUETTE QC (serveur d'impression réseau usine) ──────────────
    # Chemin par défaut pour YMS ET machine (C235/C335/C435...) depuis le
    # 22/08/2026 — imprime via la POS80L partagée smartpi-printer-factory
    # (queue CUPS raw "POS80L", port 631), jointe en WiFi usine ou VPN FR
    # (10.8.0.x) en secours.

    NETWORK_PRINTER_HOST = "smartpi-printer-factory.local:631"
    NETWORK_PRINTER_QUEUE = "POS80L"

    def _print_qc_label_network(self, report, seq=None):
        """Imprime l'étiquette QC — 3 chemins, essayés DANS L'ORDRE, chacun
        un secours du précédent, jamais un remplacement :
        1. POS80L branchée EN DIRECT sur CE pad (_print_qc_label, USB local
           /dev/usb/lp0) -- le plus rapide/fiable quand elle est présente,
           aucune dépendance réseau du tout (31/08 : remis en priorité
           automatique, n'était plus accessible que via la bascule manuelle
           du panel depuis le 22/08 -- Nicolas : imprimer en USB direct si
           une POS80L est branchée sur le pad qui fait le QC, sinon bascule).
        2. Serveur d'impression réseau usine (smartpi-printer-factory, LAN/
           VPN, queue CUPS raw) -- v2 26/08.
        3. Relais cloud (qc.yumi-lab.com -> gs1-proxy -> file que le boîtier
           va chercher lui-même) -- si le pad n'est pas sur le LAN/VPN de
           smartpi-printer-factory.
        seq (31/08, optionnel) : position banc (1..12), transmis au relais
        SEUL (gs1-proxy trie sa file par seq quand fourni, cf. commit
        7100120 sur ce dépôt) -- permet d'appeler ceci pour les 12 positions
        d'un lot EN VRAI PARALLÈLE (cf. _process_box) sans perdre l'ordre
        d'impression, même si l'ordre d'ARRIVÉE réseau des 12 requêtes est
        mélangé. Sans objet pour les chemins USB local/LAN (synchrones,
        un seul appelant à la fois de toute façon, cf. _pos80l_lock).
        Renvoie (ok, message). Ne bloque jamais le QC : réseau/imprimante
        indisponibles partout = échec rapide."""
        ok, local_msg = self._print_qc_label(report)
        if ok:
            return True, "étiquette imprimée (USB local)"
        if shutil.which("lp"):
            tspl = build_label_tspl(report)
            try:
                # 10 -> 3 (31/08) : sur un pad qui n'est structurellement PAS
                # sur le LAN/VPN de smartpi-printer-factory (mDNS ne resout
                # meme pas .local, cf. diagnostic reel du 31/08), cette
                # tentative echoue a CHAQUE fois -- 10s perdues pour rien sur
                # CHAQUE etiquette avant de retomber sur le relais (mesure
                # en reel : ~15-24s entre 2 etiquettes, l'essentiel venant
                # de ce timeout). 3s reste large pour un LAN qui marche
                # vraiment (resolution+connexion+job raw tient en <1-2s).
                proc = subprocess.run(
                    ["lp", "-h", self.NETWORK_PRINTER_HOST,
                     "-d", self.NETWORK_PRINTER_QUEUE, "-o", "raw"],
                    input=tspl, capture_output=True, timeout=3)
                if proc.returncode == 0:
                    return True, "étiquette imprimée"
                err = proc.stderr.decode("utf-8", "replace").strip()
                lan_msg = "étiquette réseau: %s" % (err or "échec lp")
            except subprocess.TimeoutExpired:
                lan_msg = "étiquette réseau: timeout"
            except OSError as e:
                logger.warning("QC: impression étiquette réseau: %s", e)
                lan_msg = "étiquette réseau: %s" % e
        else:
            lan_msg = "client CUPS (lp) absent"
        ok, relay_msg = self._print_qc_label_relay(report, seq=seq)
        if ok:
            return True, "étiquette imprimée (relais)"
        return False, "USB local: %s -- %s -- relais: %s" % (local_msg, lan_msg, relay_msg)

    def _print_qc_label_relay(self, report, seq=None):
        """Relais réseau (26/08) : pousse l'étiquette (PNG data URL) sur
        qc.yumi-lab.com/api/qc/print/factory, authentifié avec le MÊME token
        que l'upload de rapport -- aucun nouveau secret sur le pad. Le
        serveur relaie ensuite vers gs1-proxy avec SA propre clé GS1 (jamais
        distribuée aux pads). seq (31/08, optionnel) : cf. docstring
        _print_qc_label_network. Renvoie (ok, message)."""
        token = self._qc_token()
        if not token:
            return False, "token QC manquant"
        try:
            job = build_label_png_job(report, seq=seq)
        except Exception as e:
            logger.warning("QC: rendu PNG etiquette (relais): %s", e)
            return False, "rendu PNG: %s" % e
        data = json.dumps(job).encode("utf-8")
        req = urllib.request.Request(
            QC_PRINT_RELAY_URL, data=data, method="POST",
            headers={"Content-Type": "application/json", "X-QC-Token": token})
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                if resp.status == 200:
                    return True, "job envoyé"
                return False, "HTTP %d" % resp.status
        except urllib.error.HTTPError as e:
            return False, "HTTP %d" % e.code
        except Exception as e:
            return False, "réseau: %s" % e

    # ─── BASCULE MODE IMPRESSION QC MACHINE (réseau / local secours) ───

    def _get_print_mode(self):
        try:
            with open(QC_PRINT_MODE_FILE) as f:
                mode = f.read().strip()
            if mode in (QC_PRINT_MODE_NETWORK, QC_PRINT_MODE_LOCAL):
                return mode
        except OSError:
            pass
        return QC_PRINT_MODE_NETWORK

    def _on_toggle_print_mode(self, widget):
        new_mode = (QC_PRINT_MODE_LOCAL
                    if self._get_print_mode() == QC_PRINT_MODE_NETWORK
                    else QC_PRINT_MODE_NETWORK)
        try:
            with open(QC_PRINT_MODE_FILE, "w") as f:
                f.write(new_mode)
        except OSError as e:
            self._screen.show_popup_message(f"Mode impression: {e}", level=3)
            return
        self._build_start_screen()

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
        self._box_started[test["id"]] = datetime.now(timezone.utc)
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
