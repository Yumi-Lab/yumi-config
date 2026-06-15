"""
QC Engine — State machine, test definitions, and report generator
for Yumi Lab factory quality control protocol.
"""
import json
import os
import re
import logging
from datetime import datetime
from enum import Enum

logger = logging.getLogger("KlipperScreen.qc_engine")

# Répétabilité du Z tap plein course. Comme le homing : on retape plusieurs
# fois et on valide dès que Z_TAP_WINDOW taps CONSECUTIFS convergent dans la
# fenêtre Z_TAP_SPREAD_TOL (fenêtre glissante). Ça écarte le tassement/jeu des
# premiers contacts (le Z dérive au 1er contact en arrivant de tout en haut,
# puis se stabilise) — sinon le spread brut des 1ers taps fait échouer à tort.
Z_TAP_SPREAD_TOL = 0.05
Z_TAP_WINDOW = 3


class QCState(Enum):
    IDLE = "idle"
    RUNNING = "running"
    WAITING_GCODE = "waiting_gcode"
    WAITING_VISUAL = "waiting_visual"
    COMPLETED = "completed"
    ABORTED = "aborted"


class QCResult(Enum):
    PENDING = "pending"
    PASS = "pass"
    FAIL = "fail"
    SKIPPED = "skipped"


# Ordered list of all QC tests — protocole usine C235 CHROMAX X12.
# "cleanup" : gcode de sécurité envoyé par le wizard pour couper ce que le
# test a allumé (heater/bed/fan). Appliqué après la réponse opérateur d'un
# test visuel, sur un skip, et sur un timeout. Les tests automatisés qui
# chauffent (screws_tilt/bed_mesh) le portent comme filet anti-surchauffe
# en cas de timeout — en complétion normale leur macro coupe elle-même.
QC_TESTS = [
    # ── BLOC VISUEL AU TOUT DÉBUT : toutes les validations manuelles d'un coup.
    #    Chaque test visuel est auto-contenu (le cutter home X + chauffe + feed,
    #    le Z tap home). L'opérateur fait ses confirmations puis s'en va. ──
    {
        "id": "fan_motherboard",
        "name": "主板风扇 / Motherboard fan",
        "type": "visual",
        "macro": "QC_FAN_MOTHERBOARD",
        "prompt": "主板风扇在转吗？\nIs the motherboard fan spinning?",
        "timeout": 30,
    },
    {
        "id": "fan_part",
        "name": "模型冷却风扇 / Part cooling fan",
        "type": "visual",
        "macro": "QC_FAN_PART",
        "prompt": "模型冷却风扇在转吗？\nIs the part cooling fan spinning?",
        "cleanup": "M106 S0",
        "timeout": 20,
    },
    {
        "id": "fan_hotend",
        "name": "热端风扇 / Hotend fan",
        "type": "visual",
        "macro": "QC_FAN_HOTEND",
        "prompt": "热端风扇在转吗？\nIs the hotend fan spinning?",
        "timeout": 20,
    },
    {
        # Cutter : home X + chauffe 220 + insere le filament YMS-1 jusqu'a la
        # tete, extrude 60mm doucement, coupe, retracte 120mm. ~2-3min.
        "id": "cutter",
        "name": "切刀 (送料+挤出+切断) / Cutter (feed+extrude+cut)",
        "type": "visual",
        "macro": "QC_CUTTER",
        "prompt": "切刀正常切断了挤出的料吗？\nDid the cutter cleanly cut the extruded filament?",
        "cleanup": "M104 S0",
        "timeout": 400,
    },
    {
        # Z tap : home (auto-contenu) + tap + montee Zmax.
        "id": "z_tap_home",
        "name": "Z 触碰归位 + 升至最高 / Z tap home + Zmax",
        "type": "visual",
        "macro": "QC_Z_TAP_HOME",
        "prompt": "喷头已升到最高（Zmax）且第一次触碰正常？\nNozzle at top (Zmax) and first tap OK?",
        "timeout": 180,
    },
    # ── RESTE 100% AUTO : opérateur parti, plus aucune validation manuelle ──
    {
        "id": "mcu_check",
        "name": "主板 + 固件 / MCU + firmware",
        "type": "automated",
        "macro": "QC_MCU_CHECK",
        "timeout": 20,
    },
    {
        "id": "heat_extruder",
        "name": "喷头加热 220°C / Hotend heat 220°C",
        "type": "automated",
        "macro": "QC_HEAT_EXTRUDER",
        "timeout": 300,
    },
    {
        "id": "home_x",
        "name": "X 轴归位 / Home X",
        "type": "automated",
        "macro": "QC_HOME_X",
        "timeout": 60,
    },
    {
        "id": "home_y",
        "name": "Y 轴归位 / Home Y",
        "type": "automated",
        "macro": "QC_HOME_Y",
        "timeout": 60,
    },
    {
        # Auto : le plateau atteint 60C -> validé.
        "id": "heat_bed",
        "name": "热床加热 60°C / Bed heat 60°C",
        "type": "automated",
        "macro": "QC_HEAT_BED",
        "timeout": 300,
    },
    {
        "id": "z_tap_calib",
        "name": "Z 触碰重复性 / Z tap repeatability",
        "type": "automated",
        "macro": "QC_Z_TAP_CALIB",
        "timeout": 450,
    },
    {
        "id": "screws_tilt",
        "name": "螺丝调平 / Screws tilt adjust",
        "type": "automated",
        "macro": "QC_SCREWS_TILT",
        "timeout": 300,
    },
    {
        "id": "bed_mesh",
        "name": "热床网格 / Bed mesh",
        "type": "automated",
        "macro": "QC_BED_MESH",
        "timeout": 900,
    },
    {
        "id": "e0_head",
        "name": "YMS-1 传感器 + 送料到头 / YMS-1 sensor + feed to head",
        "type": "automated",
        "macro": "QC_HEAD_FEED TOOL=1",
        "timeout": 300,
    },
    {
        "id": "e1_head",
        "name": "YMS-2 传感器 + 送料到头 / YMS-2 sensor + feed to head",
        "type": "automated",
        "macro": "QC_HEAD_FEED TOOL=2",
        "timeout": 300,
    },
]

# Ordre d'exécution (modifiable ici sans toucher aux définitions ci-dessus) :
# home X/Y d'abord (le Z tap a besoin de Y homé + QC_HOME_X lance la chauffe
# plateau+tête), puis les contrôles VISUELS d'un coup (sans attendre la chauffe),
# puis tout l'automatique.
_QC_ORDER = [
    "mcu_check",
    "home_x", "home_y",                              # + lance chauffe tête/plateau
    "fan_motherboard", "fan_part", "fan_hotend",     # ventilos (visuel)
    "heat_extruder", "heat_bed",                     # confirme les temps EN AUTO
                                                     # avant d'inserer le filament
    "cutter",                                        # insere filament + coupe (tête
                                                     # déjà validée chaude)
    "z_tap_home",
    "z_tap_calib", "screws_tilt",
    # bed_mesh retiré : palpeur inductif galère (7min de retries) + BED_MESH_PROFILE
    # SAVE déclenche le prompt SAVE_CONFIG qui bloque l'écran, et le mesh est jeté
    # (cfg QC swappée après). screws_tilt valide déjà le palpeur/bed.
    # e0_head retiré : le cutter feed déjà YMS-1 jusqu'à la tête (E0 validé là).
    "e1_head",
]
# Construit la séquence depuis _QC_ORDER (ordre + inclusion). Un id défini mais
# absent de _QC_ORDER (ex: e0_head) est simplement non exécuté.
_QC_BY_ID = {t["id"]: t for t in QC_TESTS}
QC_TESTS = [_QC_BY_ID[i] for i in _QC_ORDER if i in _QC_BY_ID]


class QCEngine:
    def __init__(self):
        self.state = QCState.IDLE
        self.current_test_index = -1
        self.results = {}
        self.printer_id = ""
        self.technician = ""
        self.start_time = None
        self.test_start_time = None
        self._on_state_change = None
        self._on_test_complete = None
        self._on_visual_prompt = None
        self._on_qc_complete = None
        self._ztap_triggers = []  # trigger_z collectés pendant z_tap_calib
        self._test_log = {}       # lignes d'info/erreur capturées par test (rapport)

    def set_callbacks(self, on_state_change=None, on_test_complete=None,
                      on_visual_prompt=None, on_qc_complete=None):
        self._on_state_change = on_state_change
        self._on_test_complete = on_test_complete
        self._on_visual_prompt = on_visual_prompt
        self._on_qc_complete = on_qc_complete

    def start(self, printer_id, technician=""):
        self.printer_id = printer_id
        self.technician = technician
        self.start_time = datetime.now()
        self.current_test_index = -1
        self.results = {}
        self._ztap_triggers = []
        self._test_log = {}
        for test in QC_TESTS:
            self.results[test["id"]] = {
                "result": QCResult.PENDING,
                "timestamp": None,
                "details": "",
            }
        self._set_state(QCState.RUNNING)
        return self.next_test()

    def next_test(self):
        """Advance to the next test. Returns the test dict or None if done."""
        self.current_test_index += 1
        if self.current_test_index >= len(QC_TESTS):
            self._finish()
            return None
        test = QC_TESTS[self.current_test_index]
        self.test_start_time = datetime.now()
        logger.info(f"QC: Starting test {test['id']}")
        return test

    def get_current_test(self):
        if 0 <= self.current_test_index < len(QC_TESTS):
            return QC_TESTS[self.current_test_index]
        return None

    def get_progress(self):
        """Returns (current, total) tuple."""
        return (self.current_test_index + 1, len(QC_TESTS))

    def process_gcode_response(self, message):
        """Parse QC: prefixed gcode responses + captures trigger_z des taps."""
        cur = self.get_current_test()

        # Log par test : capture les lignes d'info (// ...) et erreurs (!! ...)
        # pour le rapport (distances feed, spread Z, corrections vis, mesh...).
        if cur and (message.startswith("// ") or message.startswith("!! ")):
            line = message[3:].strip()
            if line:
                buf = self._test_log.setdefault(cur["id"], [])
                if len(buf) < 40 and line not in buf:
                    buf.append(line)

        # Capture des trigger_z (YUMI_Z_TAP "VALIDATED: trigger_z=X.XXXX")
        # pendant le test de calibration Z plein course.
        if (cur and cur.get("id") == "z_tap_calib" and "trigger_z=" in message):
            m = re.search(r"trigger_z=(-?\d+\.?\d*)", message)
            if m:
                self._ztap_triggers.append(float(m.group(1)))
                logger.info(f"QC: z_tap trigger_z={m.group(1)}")
            return True

        if not message.startswith("QC:") and not message.startswith("echo: QC:"):
            return False

        # Strip echo/RESPOND prefix if present
        msg = message.replace("echo: ", "").replace("// ", "").strip()
        parts = msg.split(":")
        if len(parts) < 3:
            return False

        test_id = parts[1]
        status = parts[2]

        # Marqueur de progression interne (QC:ZTAP_ITER:i/n) — ignoré
        if test_id.upper() == "ZTAP_ITER":
            return True

        test = self.get_current_test()
        if not test or test["id"].lower() != test_id.lower():
            logger.warning(f"QC: Received response for {test_id} but current test is {test['id'] if test else 'none'}")
            return True

        # IMPORTANT : enregistrer sous l'id de QC_TESTS (test["id"], minuscule),
        # pas sous l'id du signal (test_id, majuscule) — sinon le rapport ne
        # retrouve pas le résultat (clés results != ids QC_TESTS).
        tid = test["id"]
        if status == "START":
            self._set_state(QCState.WAITING_GCODE)
            return True
        elif status == "PASS":
            self._record_result(tid, QCResult.PASS)
            return True
        elif status == "FAIL":
            self._record_result(tid, QCResult.FAIL, "Automated check failed")
            return True
        elif status == "VISUAL":
            self._set_state(QCState.WAITING_VISUAL)
            if self._on_visual_prompt and "prompt" in test:
                self._on_visual_prompt(test)
            return True
        elif status == "DONE" and tid == "z_tap_calib":
            # Fin des taps de calibration. Comme le homing : on cherche une
            # fenêtre de Z_TAP_WINDOW taps CONSECUTIFS qui convergent <= tol.
            # Dès qu'une fenêtre converge -> PASS (la machine SAIT taper au
            # même Z, le tassement des 1ers contacts est écarté).
            trigs = self._ztap_triggers
            if len(trigs) < Z_TAP_WINDOW:
                self._record_result(
                    tid, QCResult.FAIL,
                    "Z tap calib: %d tap(s), il en faut au moins %d"
                    % (len(trigs), Z_TAP_WINDOW))
            else:
                # On cherche les Z_TAP_WINDOW taps les PLUS PROCHES (cluster sur
                # la liste triée). Il suffit que 3 taps tombent dans la tolérance
                # n'importe où dans la série -> les rares outliers sont ignorés.
                st = sorted(trigs)
                best = min(st[i + Z_TAP_WINDOW - 1] - st[i]
                           for i in range(len(st) - Z_TAP_WINDOW + 1))
                allt = ", ".join("%.4f" % t for t in trigs)
                if best <= Z_TAP_SPREAD_TOL:
                    detail = ("OK: %d taps convergents spread=%.4fmm (tol=%.4f) sur %d taps "
                              "| taps=%s"
                              % (Z_TAP_WINDOW, best, Z_TAP_SPREAD_TOL, len(trigs), allt))
                    self._record_result(tid, QCResult.PASS, detail)
                else:
                    detail = ("aucun groupe de %d taps <= tol: meilleur=%.4fmm > %.4f sur %d taps "
                              "| taps=%s"
                              % (Z_TAP_WINDOW, best, Z_TAP_SPREAD_TOL, len(trigs), allt))
                    self._record_result(tid, QCResult.FAIL, detail)
            return True

        return False

    def record_visual_result(self, passed):
        """Called by the UI when operator responds to a visual check."""
        test = self.get_current_test()
        if not test:
            return
        if passed:
            self._record_result(test["id"], QCResult.PASS)
        else:
            self._record_result(test["id"], QCResult.FAIL, "Visual check: operator reported failure")

    def skip_current_test(self):
        """Skip the current test."""
        test = self.get_current_test()
        if test:
            self._record_result(test["id"], QCResult.SKIPPED, "Skipped by operator")

    def fail_current_test(self, details=""):
        """Fail the current test (e.g. timeout, Klipper error aborted the macro)."""
        test = self.get_current_test()
        if test:
            self._record_result(test["id"], QCResult.FAIL, details)

    def abort(self):
        """Abort the entire QC process."""
        self._set_state(QCState.ABORTED)
        if self._on_qc_complete:
            self._on_qc_complete(self.generate_report())

    def _record_result(self, test_id, result, details=""):
        self.results[test_id] = {
            "result": result,
            "timestamp": datetime.now().isoformat(),
            "details": details,
        }
        logger.info(f"QC: Test {test_id} = {result.value}")
        self._set_state(QCState.RUNNING)

        # Auto-advance to next test
        next_test = self.next_test()

        if self._on_test_complete:
            self._on_test_complete(test_id, result)

        if next_test is None:
            return  # QC finished, _finish() already called

    def _finish(self):
        self._set_state(QCState.COMPLETED)
        if self._on_qc_complete:
            self._on_qc_complete(self.generate_report())

    def _set_state(self, state):
        old = self.state
        self.state = state
        if self._on_state_change:
            self._on_state_change(old, state)

    def generate_report(self):
        end_time = datetime.now()
        duration = (end_time - self.start_time).total_seconds() if self.start_time else 0

        failed = [tid for tid, r in self.results.items() if r["result"] == QCResult.FAIL]
        skipped = [tid for tid, r in self.results.items() if r["result"] == QCResult.SKIPPED]

        if failed:
            overall = "FAIL"
        elif skipped:
            overall = "PARTIAL"
        else:
            overall = "PASS"

        report = {
            "version": "1.0",
            "printer_id": self.printer_id,
            "technician": self.technician,
            "date": self.start_time.isoformat() if self.start_time else "",
            "date_end": end_time.isoformat(),
            "duration_seconds": int(duration),
            "tests": [],
            "overall_result": overall,
            "failed_tests": failed,
            "skipped_tests": skipped,
        }

        for test in QC_TESTS:
            r = self.results.get(test["id"], {})
            report["tests"].append({
                "id": test["id"],
                "name": test["name"],
                "type": test["type"],
                "result": r.get("result", QCResult.PENDING).value if isinstance(r.get("result"), QCResult) else str(r.get("result", "pending")),
                "timestamp": r.get("timestamp", ""),
                "details": r.get("details", ""),
                # Log capturé pendant le test : distances feed, spread Z,
                # corrections de vis, mesh complete, erreurs... (mesures riches)
                "log": self._test_log.get(test["id"], []),
            })

        # Identité firmware de la machine (YUMI_CONFIG gravé). Le macro mcu_check
        # émet la constante comme "[mcu] board=... device=... lot=... uid=..."
        # (la VALEUR, sans le mot "YUMI_CONFIG"). On capture la ligne qui porte
        # device= et on retire le préfixe "[mcu name] " -> clé=valeur propres
        # pour la ventilation modèle/lot/uid côté compteur.
        _yc = next(
            (l for l in self._test_log.get("mcu_check", []) if "device=" in l.lower()),
            "")
        report["yumi_config"] = re.sub(r"^\s*\[[^\]]*\]\s*", "", _yc).strip()

        return report

    def save_report(self, report=None):
        """Save report as JSON file. Returns the file path."""
        if report is None:
            report = self.generate_report()

        report_dir = os.path.expanduser("~/printer_data/config/qc_reports")
        os.makedirs(report_dir, exist_ok=True)

        date_str = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"QC_{self.printer_id}_{date_str}.json"
        filepath = os.path.join(report_dir, filename)

        with open(filepath, "w") as f:
            json.dump(report, f, indent=2, ensure_ascii=False)

        logger.info(f"QC: Report saved to {filepath}")
        return filepath
