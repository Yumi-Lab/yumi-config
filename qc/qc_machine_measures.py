"""
qc_machine_measures.py — Extraction measures{} + fail_reason pour les tests
machine C-series (contrat additif serveur, cf. docs/AUDIT-MESURES.md).

Module pur (aucun import GTK), testable, style qc_yms.extract_measures :
les mesures sont extraites des logs DÉJÀ capturés par QCEngine._test_log
(lignes "//"/"!!" pendant le test) et, pour z_tap_calib, du `details` calculé
par l'engine. Aucun champ nouveau obligatoire : un test sans extracteur
renvoie None (le rapport reste identique à aujourd'hui).
"""
import re

# Cible plateau : qc_macros.cfg [gcode_macro QC_HEAT_BED] (params.TEMP, défaut
# 60) — le test QC appelle la macro sans paramètre.
HEAT_BED_TARGET_C = 60

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
# thermal_timeout, thermal_runaway, unknown_fail.


def extract_measures(test_id, logs, passed, details="", duration_s=None,
                     timed_out=True):
    """Extrait le bloc measures d'un test machine, ou None si pas d'extracteur.

    Args:
        test_id: id du test (QC_TESTS, minuscule).
        logs: lignes capturées par l'engine pendant le test.
        passed: True si le test a PASS.
        details: champ details du résultat engine (verdict z_tap_calib...).
        duration_s: durée mesurée du test par l'engine (ramp_s, duration_s).
        timed_out: True si l'échec vient d'un timeout engine (fallback
            fail_reason) ; False -> 'unknown_fail'.

    Returns:
        dict measures (clé 'fail_reason' incluse, style YMS) ou None quand le
        test n'a pas d'extracteur (rapport inchangé pour ce test — additif).
    """
    entry = _EXTRACTORS.get(test_id)
    if entry is None:
        return None
    fn, default_fail = entry
    m = fn(list(logs or []), passed, details or "", duration_s)
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


def _extract_z_tap_calib(logs, passed, details, duration_s):
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
    def fn(logs, passed, details, duration_s):
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


# ── heat_bed (auto, TEMPERATURE_WAIT plateau) ────────────────────────────────
# La macro n'émet aucun log de température aujourd'hui : reached_c/stable
# restent None tant que l'instrumentation additive "HEAT_OK bed t=%.1f" (cf.
# AUDIT-MESURES.md) n'est pas déployée. ramp_s = durée mesurée par l'engine.

def _extract_heat_bed(logs, passed, details, duration_s):
    m = {
        "target_c": HEAT_BED_TARGET_C,
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


# test_id -> (extracteur, fail_reason par défaut en cas de timeout sans
# signature reconnue). Les tests sans extracteur (visuels, mcu_check...) sont
# couverts par L5 ; extract_measures renvoie None -> rapport inchangé.
_EXTRACTORS = {
    "z_tap_calib": (_extract_z_tap_calib, "timeout"),
    "home_x": (_extract_home("X"), "timeout"),
    "home_y": (_extract_home("Y"), "timeout"),
    "heat_bed": (_extract_heat_bed, "thermal_timeout"),
}
