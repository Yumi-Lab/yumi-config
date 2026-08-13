"""
qc_machine_measures.py — Extraction measures{} + fail_reason pour les tests
machine C-series (contrat additif serveur, cf. docs/AUDIT-MESURES.md) et
bloc racine software_versions (versions logicielles, contrat §3.2) et
détection retest locale (contrat §3.3).

Module pur (aucun import GTK), testable, style qc_yms.extract_measures :
les mesures sont extraites des logs DÉJÀ capturés par QCEngine._test_log
(lignes "//"/"!!" pendant le test) et, pour z_tap_calib, du `details` calculé
par l'engine. Aucun champ nouveau obligatoire : un test sans extracteur
renvoie None (le rapport reste identique à aujourd'hui).
"""
import hashlib
import json
import os
import re

# Réutilisation du parseur feed du banc YMS pour e1_head (mêmes lignes de log).
# Double chemin : package qc/ (repo/tests) ou module seul symlinke dans
# ks_includes sur le pad (miroir de l'import qc_machine_measures de qc_engine).
try:
    from qc import qc_yms
except ImportError:
    import qc_yms

# Cible plateau : qc_macros.cfg [gcode_macro QC_HEAT_BED] (params.TEMP, défaut
# 60) — le test QC appelle la macro sans paramètre.
HEAT_BED_TARGET_C = 60

# Cible extruder : qc_macros.cfg [gcode_macro QC_HEAT_EXTRUDER] (M104 S220).
HEAT_EXTRUDER_TARGET_C = 220

# Budget feed machine : qc_macros.cfg _QC_HEAD_FEED variable_maxd=800
# (le banc YMS pousse jusqu'à 900).
E1_FEED_BUDGET_MM = 800

# Défauts fenêtre/tolérance Z tap : miroir de qc_engine.Z_TAP_WINDOW /
# Z_TAP_SPREAD_TOL. NON importés de qc_engine pour éviter un import circulaire
# (l'engine appellera ce module au rapport). Quand le `details` de l'engine
# est disponible, la tolérance est relue depuis la ligne elle-même.
Z_TAP_DEFAULT_WINDOW = 3
Z_TAP_DEFAULT_TOL = 0.05

# fail_reason normés (docs/AUDIT-MESURES.md §fail_reason). Ceux identiques au
# banc YMS sont réutilisés tels quels : tmc_error, timeout, head_not_reached,
# sensor_mute, already_at_head. Nouveaux machines couverts ici :
# tap_not_converging, too_few_taps, endstop_not_triggered, spread_too_wide,
# thermal_timeout, thermal_runaway, visual_reject, no_yumi_config,
# mcu_uid_error, not_homed, screws_tilt_aborted, unknown_fail.


def extract_measures(test_id, logs, passed, details="", duration_s=None,
                     timed_out=False):
    """Extrait le bloc measures d'un test machine, ou None si pas d'extracteur.

    Args:
        test_id: id du test (QC_TESTS, minuscule).
        logs: lignes capturées par l'engine pendant le test.
        passed: True si le test a PASS.
        details: champ details du résultat engine (verdict z_tap_calib...).
        duration_s: durée mesurée du test par l'engine (ramp_s, duration_s).
        timed_out: True si l'échec vient d'un timeout engine (fallback
            fail_reason) ; False -> 'unknown_fail' (ou 'visual_reject' pour
            les tests à validation opérateur). Défaut False volontairement :
            un appelant qui ne sait pas ne doit PAS prétendre au timeout —
            l'engine passe toujours la valeur explicitement.

    Returns:
        dict measures (clé 'fail_reason' incluse, style YMS) ou None quand le
        test n'a pas d'extracteur (rapport inchangé pour ce test — additif).
    """
    entry = _EXTRACTORS.get(test_id)
    if entry is None:
        return None
    fn, default_fail = entry
    m = fn(list(logs or []), passed, details or "", duration_s, timed_out)
    if passed:
        m["fail_reason"] = None
    elif not m["fail_reason"]:
        m["fail_reason"] = default_fail if timed_out else "unknown_fail"
    return m


def _check_tmc(line, m):
    """Signature commune : erreur driver TMC (DRV_STATUS) -> tmc_error."""
    if "DRV_STATUS" in line:
        m["tmc_error"] = line.strip()[:200]
        m["fail_reason"] = "tmc_error"


# ── z_tap_calib (auto, 15 taps plein course) ─────────────────────────────────
# details engine (qc_engine.py, signal DONE) :
#   PASS: "OK: 3 taps convergents spread=0.0000mm (tol=0.0500) sur 15 taps
#          | taps=486.1075, 486.1025, ..."
#   FAIL: "aucun groupe de 3 taps <= tol: meilleur=0.0700mm > 0.0500 sur 15
#          taps | taps=..."
#   FAIL: "Z tap calib: 2 tap(s), il en faut au moins 3"
# Repli sans details : re-calcul depuis les lignes "VALIDATED: trigger_z=..."
# capturées dans le log (même fenêtrage que l'engine : cluster trié).

def _best_window_spread(taps, window):
    """Spread de la fenêtre de `window` taps la plus resserrée (liste triée)."""
    st = sorted(taps)
    return min(st[i + window - 1] - st[i]
               for i in range(len(st) - window + 1))


def _extract_z_tap_calib(logs, passed, details, duration_s, timed_out):
    m = {
        "taps_mm": [],
        "spread_mm": None,
        "tolerance_mm": None,
        "n_taps": 0,
        "converged_n": None,
        "fail_reason": None,
    }
    window = Z_TAP_DEFAULT_WINDOW
    for line in logs:
        _check_tmc(line, m)
        r = re.search(r"trigger_z=(-?\d+\.?\d*)", line)
        if r:
            m["taps_mm"].append(float(r.group(1)))
    r = re.search(r"taps=(-?[\d., ]+)", details)
    if r:
        m["taps_mm"] = [float(v) for v in r.group(1).split(",") if v.strip()]
    m["n_taps"] = len(m["taps_mm"])
    r = re.search(r"(\d+) taps convergents spread=([\d.]+)mm \(tol=([\d.]+)\)",
                  details)
    if r:  # verdict PASS calculé par l'engine : on le relit tel quel
        m["converged_n"] = int(r.group(1))
        m["spread_mm"] = float(r.group(2))
        m["tolerance_mm"] = float(r.group(3))
        return m
    r = re.search(r"aucun groupe de (\d+) taps <= tol: meilleur=([\d.]+)mm > "
                  r"([\d.]+)", details)
    if r:
        window = int(r.group(1))
        m["converged_n"] = 0
        m["spread_mm"] = float(r.group(2))
        m["tolerance_mm"] = float(r.group(3))
        m["fail_reason"] = "tap_not_converging"
        return m
    r = re.search(r"(\d+) tap\(s\), il en faut au moins (\d+)", details)
    if r:
        m["converged_n"] = 0
        m["fail_reason"] = "too_few_taps"
        return m
    # Repli : pas de details engine -> re-calcul depuis les trigger_z du log.
    if m["tolerance_mm"] is None:
        m["tolerance_mm"] = Z_TAP_DEFAULT_TOL
    if m["n_taps"] >= window:
        m["spread_mm"] = _best_window_spread(m["taps_mm"], window)
        if m["spread_mm"] <= m["tolerance_mm"]:
            m["converged_n"] = window
        else:
            m["converged_n"] = 0
            m["fail_reason"] = "tap_not_converging"
    elif m["n_taps"] > 0:
        m["converged_n"] = 0
        m["fail_reason"] = "too_few_taps"
    return m


# ── home_x / home_y (auto, sensorless 2 phases) ──────────────────────────────
# logs klippy/extras/yumi_sensorless_homing.py :
#   "YUMI_SENSORLESS_HOME X: home base... (sgthrs=63)"
#   "tap 1: pos=0.0000 gap=4.5210 (1/3)" / "... fenetre=0.0080/0.0500"
#   "tap 1 rejete: <raison>"
#   "YUMI_SENSORLESS_HOME X OK: 3 taps valides (1 rejetes) -> moyenne=0.0000
#    spread=0.0080mm (tol=0.0500). Zero pose en butee=0.0000" (OK ou IMPRECIS)
#   erreurs: "repetabilite NON etablie (1 taps valides / 3 requis, 2 rejetes)",
#   "spread=0.2100mm sur 3 taps (tol=0.0500) -> butee non repetable",
#   "aucun contact apres N re-home(s)".

def _extract_home(axis):
    def fn(logs, passed, details, duration_s, timed_out):
        m = {
            "axis": axis,
            "sg_thrs": None,
            "taps_valides": 0,
            "taps_rejetes": 0,
            "spread_mm": None,
            "tolerance_mm": None,
            "zero_pos_mm": None,
            "duration_s": duration_s,
            "fail_reason": None,
        }
        for line in logs:
            _check_tmc(line, m)
            r = re.search(r"home base\.\.\. \(sgthrs=(\d+)\)", line)
            if r:
                m["sg_thrs"] = int(r.group(1))
            r = re.search(
                r"(OK|IMPRECIS): (\d+) taps valides \((\d+) rejetes\) -> "
                r"moyenne=(-?[\d.]+) spread=([\d.]+)mm \(tol=([\d.]+)\)\. "
                r"Zero pose en butee=(-?[\d.]+)", line)
            if r:
                m["taps_valides"] = int(r.group(2))
                m["taps_rejetes"] = int(r.group(3))
                m["spread_mm"] = float(r.group(5))
                m["tolerance_mm"] = float(r.group(6))
                m["zero_pos_mm"] = float(r.group(7))
            r = re.search(r"repetabilite NON etablie \((\d+) taps valides / "
                          r"(\d+) requis, (\d+) rejetes\)", line)
            if r:
                m["taps_valides"] = int(r.group(1))
                m["taps_rejetes"] = int(r.group(3))
                m["fail_reason"] = "endstop_not_triggered"
            r = re.search(r"spread=([\d.]+)mm sur (\d+) taps \(tol=([\d.]+)\) "
                          r"-> butee non repetable", line)
            if r:
                m["spread_mm"] = float(r.group(1))
                m["taps_valides"] = int(r.group(2))
                m["tolerance_mm"] = float(r.group(3))
                m["fail_reason"] = "spread_too_wide"
            if "aucun contact apres" in line:
                m["fail_reason"] = m["fail_reason"] or "endstop_not_triggered"
            if re.match(r"tap \d+ rejete", line) and m["spread_mm"] is None:
                m["taps_rejetes"] += 1  # comptage de repli (pas de ligne finale)
        return m
    return fn


# ── heat_extruder / heat_bed (auto, TEMPERATURE_WAIT) ────────────────────────
# Les macros n'émettent aucun log de température aujourd'hui : reached_c/stable
# restent None tant que l'instrumentation additive "HEAT_OK <sensor> t=%.1f"
# (cf. AUDIT-MESURES.md) n'est pas déployée. ramp_s = durée mesurée par
# l'engine. Cibles : constantes des macros QC_HEAT_EXTRUDER / QC_HEAT_BED.

def _extract_heat(target_c):
    def fn(logs, passed, details, duration_s, timed_out):
        m = {
            "target_c": target_c,
            "reached_c": None,
            "ramp_s": duration_s,
            "stable": None,
            "fail_reason": None,
        }
        for line in logs:
            _check_tmc(line, m)
            r = re.search(r"HEAT_OK\s+\S+\s+t=(-?[\d.]+)", line)
            if r:  # instrumentation additive (absente des macros actuelles)
                m["reached_c"] = float(r.group(1))
                m["stable"] = True
            if re.search(r"not heating at expected rate|thermal runaway",
                         line, re.IGNORECASE):
                m["fail_reason"] = "thermal_runaway"
        return m
    return fn


# ── mcu_check (auto, QC_MCU_CHECK + QUERY_MCU_UID) ───────────────────────────
# logs réels :
#   "[mcu] version: v0.12.0-159-gabcd1234"           (une ligne par MCU)
#   "[mcu SmartPiOne] version: v0.12.0-159-gabcd1234 (host SmartPi One)"
#   "[mcu] board=CR-FDM-v2.5.s1 device=YUMI-C235 lot=2026-08 ... uid=ABC123"
#   "MCU_UID=2D0046000D51353234323830"
#   FAIL : "QC: aucune identite YUMI gravee sur les MCU (...)" / "MCU_UID_ERROR:"

_VERSION_LINE_RE = re.compile(r"\[([^\]]+)\] version: (\S+)")

# MCU "hôte" = process Linux Klipper (renommé SmartPiOne par generate_qc_cfg.py,
# "mcu rpi" dans les cfg stock). Sa mcu_version EST la version du logiciel
# Klipper hôte (klipper_version), pas un firmware flashé : exclue de
# firmware_version dans le bloc racine software_versions.
HOST_MCU_NAMES = ("mcu SmartPiOne", "mcu rpi")


def firmware_versions_from_log(logs):
    """{nom_mcu: version} depuis les lignes '[<mcu>] version: X' du mcu_check."""
    versions = {}
    for line in logs:
        r = _VERSION_LINE_RE.match(line.strip())
        if r:
            versions[r.group(1)] = r.group(2)
    return versions


def klipper_version_from_log(logs):
    """Version Klipper hôte = version du MCU hôte (SmartPi One / rpi).

    Le MCU hôte (process linux) rapporte la version du logiciel Klipper, ce
    que 'printer info' donnerait — déjà loggué par QC_MCU_CHECK, aucune
    instrumentation macro nécessaire. None si le MCU hôte est absent de la cfg.
    """
    versions = firmware_versions_from_log(logs)
    for name in HOST_MCU_NAMES:
        if name in versions:
            return versions[name]
    return None


# Fichiers release YumiOS candidats (1re ligne = version image). Convention
# best-effort : la clé image_version n'apparaît au rapport que si l'un de ces
# fichiers existe sur le pad (tolérant à l'absence, ex. dev hors pad).
IMAGE_VERSION_FILES = ("/etc/yumi-image-version", "/etc/yumi-release")


def image_version_from_files(paths=None):
    """Première ligne non vide du premier fichier release présent, sinon None."""
    for path in (IMAGE_VERSION_FILES if paths is None else paths):
        try:
            with open(path) as f:
                version = f.readline().strip()
            if version:
                return version
        except OSError:
            continue
    return None


def qc_cfg_hash(path):
    """Empreinte sha256 courte (12 hex) d'une cfg QC, None si illisible."""
    try:
        with open(path, "rb") as f:
            return hashlib.sha256(f.read()).hexdigest()[:12]
    except OSError:
        return None


def software_versions(mcu_check_logs, image_version=None, qc_cfg_version=None):
    """Bloc racine software_versions (contrat §3.2), additif et tolérant :
    chaque clé absente de sa source est omise ; {} si rien n'est disponible
    (l'engine omet alors le bloc entier — rapport inchangé)."""
    sv = {}
    klipper_version = klipper_version_from_log(mcu_check_logs)
    if klipper_version:
        sv["klipper_version"] = klipper_version
    firmware = {name: version
                for name, version in firmware_versions_from_log(mcu_check_logs).items()
                if name not in HOST_MCU_NAMES}
    if firmware:
        sv["firmware_version"] = firmware
    if image_version:
        sv["image_version"] = image_version
    if qc_cfg_version:
        sv["qc_cfg_version"] = qc_cfg_version
    return sv


# Raisons retest normées (contrat §3.3, liste figée avec le serveur en L8) :
# l'heuristique locale est DOCUMENTEE — un rapport précédent du même
# machine_uid existe dans qc_reports/ du pad ; la raison porte son verdict.
RETEST_REASONS = {
    "PASS": "previous_report_pass",
    "FAIL": "previous_report_fail",
    "PARTIAL": "previous_report_partial",
}


def previous_qc_overall(report_dir, machine_uid, exclude_date=None):
    """Verdict (overall_result) du QC précédent le plus récent de la même
    machine sur CE pad, None si jamais passée ici.

    Heuristique locale retest (contrat §3.3) : les rapports machine sont
    sauvegardés en JSON dans qc_reports/ (save_report) ; un rapport antérieur
    portant le même machine_uid (UID STM32, identité machine fiable) signifie
    que ce QC est un re-test. exclude_date = date du run courant : un rapport
    de la MÊME session (generate_report appelé deux fois) n'est pas un retest.
    Tolérant : répertoire absent, JSON illisible, clés manquantes -> ignorés.
    """
    if not machine_uid or not os.path.isdir(report_dir):
        return None
    uid = machine_uid.strip().upper()
    best_date, best_overall = "", None
    for name in os.listdir(report_dir):
        # Les marqueurs store-and-forward "<fichier>.json.sent" ne matchent
        # pas "*.json" (suffixe .sent) — seuls les rapports sont relus.
        if not name.endswith(".json"):
            continue
        try:
            with open(os.path.join(report_dir, name)) as f:
                prev = json.load(f)
        except (OSError, ValueError):
            continue
        if str(prev.get("machine_uid", "")).strip().upper() != uid:
            continue
        prev_date = str(prev.get("date", ""))
        if exclude_date and prev_date == exclude_date:
            continue
        if prev_date >= best_date:
            best_date, best_overall = prev_date, prev.get("overall_result")
    return best_overall


def _extract_mcu_check(logs, passed, details, duration_s, timed_out):
    m = {
        "mcu_uid": None,
        "mcu_count": 0,
        "firmware_versions": firmware_versions_from_log(logs),
        "yumi_config_found": False,
        "fail_reason": None,
    }
    for line in logs:
        _check_tmc(line, m)
        r = re.search(r"MCU_UID=([0-9A-Fa-f]+)", line)
        if r:
            m["mcu_uid"] = r.group(1)
        if "MCU_UID_ERROR" in line:
            m["fail_reason"] = "mcu_uid_error"
        if "device=" in line.lower():
            m["yumi_config_found"] = True
        if "aucune identite YUMI" in line:
            m["fail_reason"] = "no_yumi_config"
    m["mcu_count"] = len(m["firmware_versions"])
    return m


# ── fan_* (visuels, validation opérateur) ────────────────────────────────────
# Aucun log mesurable (pas de tachymètre câblé) : le seul signal est le verdict
# opérateur. Un FAIL hors timeout = rejet visuel.

def _extract_visual(logs, passed, details, duration_s, timed_out):
    m = {"visual_ack": passed, "fail_reason": None}
    for line in logs:
        _check_tmc(line, m)
    if not passed and not m["fail_reason"]:
        m["fail_reason"] = "timeout" if timed_out else "visual_reject"
    return m


# ── cutter (visuel, feed YMS-1 + extrusion + coupe) ──────────────────────────
# logs réels (_QC_HEAD_FEED mode cutter) :
#   "QC CUTTER: motion sensor YMS-1 a change d'etat (mouvement detecte)"
#   "QC CUTTER: extrude 60 + refroidit poop 5s + coupe + retracte 120"
#   FAIL : "filament pas a la tete apres 800mm (...)" / "...motion sensor
#          YMS-1 n'a PAS change d'etat" / "filament deja a la tete avant feed"
# feed_mm n'est PAS loggé en mode cutter (le pushed interne n'est pas émis) :
# il reste None sauf échec "pas a la tete apres Nmm" (budget atteint).
# cut_ok = verdict opérateur sur la coupe (visual).

def _extract_cutter(logs, passed, details, duration_s, timed_out):
    m = {
        "motion_first_detect": False,
        "feed_mm": None,
        "cut_ok": passed,
        "fail_reason": None,
    }
    for line in logs:
        _check_tmc(line, m)
        if "a change d'etat" in line:
            m["motion_first_detect"] = True
        r = re.search(r"pas a la tete apres (\d+)mm", line)
        if r:
            m["feed_mm"] = int(r.group(1))
            m["fail_reason"] = "head_not_reached"
        if "n'a PAS change d'etat" in line:
            m["fail_reason"] = "sensor_mute"
        if "deja a la tete avant feed" in line:
            m["fail_reason"] = "already_at_head"
    if not passed and not m["fail_reason"]:
        m["fail_reason"] = "timeout" if timed_out else "visual_reject"
    return m


# ── e1_head (auto, feed YMS-2) — réutilise le parseur du banc YMS ────────────
# Mêmes lignes de log que le banc (même boucle _QC_HEAD_FEED, mode feed) :
# mêmes clés measures. On élague les clés propres au banc (stress/dropouts,
# absents de la séquence machine) et on recale le budget feed (800 vs 900).

_YMS_ONLY_KEYS = (
    "dropouts_e", "dropout_count", "feed_dropout", "stress_segments_ok",
    "stress_segments_total", "stress_speeds_mms", "retract_mm",
)


def _extract_e1_head(logs, passed, details, duration_s, timed_out):
    m = qc_yms.extract_measures(logs, passed)
    for key in _YMS_ONLY_KEYS:
        del m[key]
    m["feed_budget_mm"] = E1_FEED_BUDGET_MM
    # Le fallback YMS est 'timeout' ; la machine distingue le vrai timeout
    # engine d'un échec sans signature (unknown_fail), comme les autres tests.
    if not passed and m["fail_reason"] == "timeout" and not timed_out:
        m["fail_reason"] = "unknown_fail"
    return m


# ── z_tap_home (visuel, home complet + montée Zmax) ──────────────────────────
# logs réels (YUMI_Z_TAP du G28) :
#   "VALIDATED: trigger_z=486.1075 -> Z=0 pose 0.5500 au-dessus du tap (...)"
#   FAIL : "Pressure probe failed: 1/3 stable after 10 taps (2 rejets vibration)"
# z_max_mm : connu de la macro mais NON loggé — parse forward-compatible de
# l'instrumentation additive "ZMAX=%.1f" (None aujourd'hui).

def _extract_z_tap_home(logs, passed, details, duration_s, timed_out):
    m = {
        "tap_z_mm": None,
        "z_max_mm": None,
        "visual_ack": passed,
        "fail_reason": None,
    }
    for line in logs:
        _check_tmc(line, m)
        r = re.search(r"trigger_z=(-?\d+\.?\d*)", line)
        if r and m["tap_z_mm"] is None:
            m["tap_z_mm"] = float(r.group(1))  # 1er tap = le home Z
        r = re.search(r"ZMAX=([\d.]+)", line)
        if r:  # instrumentation additive (absente des macros actuelles)
            m["z_max_mm"] = float(r.group(1))
        if "Pressure probe failed" in line:
            m["fail_reason"] = "tap_not_converging"
        if "must be homed first" in line:
            m["fail_reason"] = "not_homed"
    if not passed and not m["fail_reason"]:
        m["fail_reason"] = "timeout" if timed_out else "visual_reject"
    return m


# ── screws_tilt (auto, SCREWS_TILT_CALCULATE Klipper standard) ───────────────
# logs réels (4 vis CCW-M3 sur C235, klippy/extras/screws_tilt_adjust.py) :
#   "front left screw (base) : x=49.5, y=175.5, z=0.00000"
#   "rear right screw : x=224.0, y=2.0, z=0.04123 : adjust CCW 00:19"
#   FAIL : "bed level exceeds configured limits (0.1mm)! ..." / erreur de
#   sondage ("probe triggered prior to movement") / "machine non homee".
# corrections : tours décimaux signés (CW = +, CCW = -) par vis, base exclue.
# max_deviation_mm : écart max des z sondés.

def _extract_screws_tilt(logs, passed, details, duration_s, timed_out):
    m = {
        "corrections": {},
        "max_deviation_mm": None,
        "n_retries": 0,
        "fail_reason": None,
    }
    zs = []
    for line in logs:
        _check_tmc(line, m)
        r = re.match(
            r"(.+?) : x=(-?[\d.]+), y=(-?[\d.]+), z=(-?[\d.]+)"
            r"(?: : adjust (CW|CCW) (\d+):(\d+))?$", line.strip())
        if r:
            name = r.group(1).replace(" (base)", "")
            zs.append(float(r.group(4)))
            if r.group(5):
                turns = int(r.group(6)) + int(r.group(7)) / 60.0
                m["corrections"][name] = round(
                    turns if r.group(5) == "CW" else -turns, 4)
        if "Retrying" in line:
            m["n_retries"] += 1
        if "machine non homee" in line:
            m["fail_reason"] = "not_homed"
        if ("bed level exceeds" in line or "triggered prior" in line):
            m["fail_reason"] = "screws_tilt_aborted"
    if zs:
        m["max_deviation_mm"] = round(max(zs) - min(zs), 5)
    return m


# test_id -> (extracteur, fail_reason par défaut en cas de timeout sans
# signature reconnue). bed_mesh / e0_head (hors séquence machine, cf.
# qc_engine._QC_ORDER) restent SANS extracteur : extract_measures renvoie
# None -> rapport inchangé.
_EXTRACTORS = {
    "mcu_check": (_extract_mcu_check, "timeout"),
    "fan_motherboard": (_extract_visual, "timeout"),
    "fan_part": (_extract_visual, "timeout"),
    "fan_hotend": (_extract_visual, "timeout"),
    "heat_extruder": (_extract_heat(HEAT_EXTRUDER_TARGET_C), "thermal_timeout"),
    "heat_bed": (_extract_heat(HEAT_BED_TARGET_C), "thermal_timeout"),
    "cutter": (_extract_cutter, "timeout"),
    "e1_head": (_extract_e1_head, "timeout"),
    "home_x": (_extract_home("X"), "timeout"),
    "home_y": (_extract_home("Y"), "timeout"),
    "z_tap_home": (_extract_z_tap_home, "timeout"),
    "z_tap_calib": (_extract_z_tap_calib, "timeout"),
    "screws_tilt": (_extract_screws_tilt, "timeout"),
}
