#!/usr/bin/env python3
"""Harnais SANDBOX du rapport QC machine contre la prod qc.yumi-lab.com.

Poste un rapport machine COMPLET (construit par le VRAI QCEngine, session
simulée 13 tests PASS : measures sur les 13 tests, software_versions,
option retest) avec "sandbox": true sur POST /api/qc/report. Le serveur
PROD accepte ce drapeau : il VALIDE le payload sans rien écrire (CDC §4 :
réponse marquée "sandbox": true — vérifiée par ce script). Aucune donnée
de test ne pollue donc le compteur.

Token lu dans l'ordre : env QC_TOKEN, ~/printer_data/config/qc_token (fichier
du pad), puis .env à la racine du repo (ligne QC_TOKEN=..., jamais committé).

Usage : python3 scripts/sandbox_machine_test.py [--url URL] [--retest]
  --retest : simule un SECOND QC de la même machine (HOME temporaire + rapport
  précédent seedé) -> le rapport posté porte retest: true + retest_reason.
Sortie : code HTTP + corps de l'ack. Exit 0 si HTTP 200 ET ack sandbox
confirmé, 1 sinon.

Stdlib uniquement (les pads n'ont pas de pip fiable).
"""
import argparse
import json
import os
import sys
import tempfile
import urllib.error
import urllib.request
from datetime import datetime, timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from qc import qc_engine  # noqa: E402

URL = "https://qc.yumi-lab.com/api/qc/report"
TOKEN_FILE = os.path.expanduser("~/printer_data/config/qc_token")

# UID STM32 simulé (format réel : 24 hex). NE PAS mettre un UID de machine
# réelle : le mode --retest seede un rapport précédent avec cet UID.
SIMULATED_UID = "2D0046000D51353234323830"

# Détails réalistes par test (formats réels : docs/AUDIT-MESURES.md).
_DETAILS = {
    "mcu_check": "OK: 1 MCU(s), device=C235 lot=SB001 uid=" + SIMULATED_UID,
    "home_x": "OK",
    "home_y": "OK",
    "fan_motherboard": "OK",
    "fan_part": "OK",
    "fan_hotend": "OK",
    "heat_extruder": "OK: 220C atteint en 68s",
    "heat_bed": "OK: 60C atteint en 95s, stable",
    "cutter": "OK: filament feed + coupe nette",
    "e1_head": "OK: filament a la tete apres 412mm",
    "z_tap_home": "OK: Z home + montee Zmax",
    "z_tap_calib": ("OK: 3 taps convergents spread=0.0312mm (tol=0.0500) sur "
                    "15 taps | taps=486.1025, 486.1050, 486.1075, 486.1075, "
                    "486.1100, 486.1100, 486.1125, 486.1125, 486.1150, "
                    "486.1175, 486.1200, 486.1225, 486.1250, 486.1275, "
                    "486.1300"),
    "screws_tilt": "OK: 3 vis ajustees, max deviation 0.0412mm",
}

# Logs réalistes par test (formats exacts des macros/klippy, cf.
# docs/AUDIT-MESURES.md) : chaque extracteur produit ses measures. Les tests
# sans ligne mesurable aujourd'hui (heat_* : reached_c ; fan_* : visuel)
# gardent les nulls documentés — le rapport reflète la prod réelle.
_LOGS = {
    # Identité machine : YUMI_CONFIG + UID STM32. La ligne hôte (MCU linux =
    # version Klipper du 'printer info') alimente klipper_version du bloc
    # racine software_versions (L6).
    "mcu_check": [
        "[mcu] version: v0.12.0-159-gabcd1234",
        "[mcu SmartPiOne] version: v0.12.0-159-gabcd1234 (host SmartPi One)",
        "[mcu] board=F401 device=C235 lot=SB001 uid=" + SIMULATED_UID,
        "MCU_UID=" + SIMULATED_UID,
    ],
    "home_x": [
        "YUMI_SENSORLESS_HOME X: home base... (sgthrs=63)",
        "tap 1: pos=0.0000 gap=4.5210 (1/3)",
        "tap 2 rejete: vibration",
        "YUMI_SENSORLESS_HOME X OK: 3 taps valides (1 rejetes) -> "
        "moyenne=0.0000 spread=0.0080mm (tol=0.0500). Zero pose en "
        "butee=0.0000",
    ],
    "home_y": [
        "YUMI_SENSORLESS_HOME Y: home base... (sgthrs=70)",
        "tap 1: pos=0.0000 gap=3.8120 (1/3)",
        "YUMI_SENSORLESS_HOME Y OK: 3 taps valides (0 rejetes) -> "
        "moyenne=0.0000 spread=0.0065mm (tol=0.0500). Zero pose en "
        "butee=0.0000",
    ],
    "cutter": [
        "QC CUTTER: motion sensor YMS-1 a change d'etat (mouvement detecte)",
        "QC CUTTER: extrude 60 + refroidit poop 5s + coupe + retracte 120",
    ],
    "e1_head": [
        "QC E1: motion sensor YMS-2 a change d'etat (mouvement detecte)",
        "filament a la tete apres 412mm",
    ],
    "z_tap_home": [
        "VALIDATED: trigger_z=486.1075 -> Z=0 pose 0.5500 au-dessus du tap "
        "(3/3 stables)",
    ],
    "z_tap_calib": [
        "VALIDATED: trigger_z=486.1025 -> Z=0 pose 0.5500 au-dessus du tap",
        "VALIDATED: trigger_z=486.1075 -> Z=0 pose 0.5500 au-dessus du tap",
    ],
    "screws_tilt": [
        "front left screw (base) : x=49.5, y=175.5, z=0.00000",
        "rear left screw : x=49.5, y=2.0, z=0.02125 : adjust CCW 00:08",
        "rear right screw : x=224.0, y=2.0, z=0.04123 : adjust CCW 00:19",
        "front right screw : x=224.0, y=175.5, z=0.01000 : adjust CW 00:05",
    ],
}

# Durées simulées par test (s) -> measures.ramp_s / duration_s de l'engine.
_DURATIONS = {
    "mcu_check": 4, "home_x": 12, "home_y": 11,
    "fan_motherboard": 5, "fan_part": 5, "fan_hotend": 5,
    "heat_extruder": 68, "heat_bed": 95, "cutter": 40, "e1_head": 55,
    "z_tap_home": 45, "z_tap_calib": 210, "screws_tilt": 60,
}


def build_report():
    """Rapport machine complet via le vrai QCEngine, session 13 tests simulée.

    Réutilise generate_report() : garantit que le payload sandbox a EXACTEMENT
    la forme d'un rapport de production (clés, types, invariants), au lieu
    d'une maquette qui dériverait du code réel."""
    eng = qc_engine.QCEngine()
    eng.start(printer_id="AABBCCDDEEFF", model="C235")
    now = datetime.now().isoformat()
    for test in eng.tests:
        tid = test["id"]
        eng.results[tid] = {
            "result": qc_engine.QCResult.PASS,
            "timestamp": now,
            "details": _DETAILS.get(tid, "OK"),
            "duration_s": _DURATIONS.get(tid),
        }
        eng._test_log[tid] = list(_LOGS.get(tid, []))
    report = eng.generate_report()
    report["sandbox"] = True  # serveur : valide sans écrire
    return report


def seed_previous_report():
    """Seede un rapport QC PRÉCÉDENT (même machine_uid, date J-1, overall
    PASS) dans qc_reports/ — miroir de ce que save_report écrit sur le pad.
    Le build_report() suivant détecte alors un retest (contrat §3.3).
    À appeler sous un HOME contrôlé (mode --retest : tempdir)."""
    report_dir = os.path.expanduser(qc_engine.QC_REPORT_DIR)
    os.makedirs(report_dir, exist_ok=True)
    prev = {
        "version": "1.0",
        "printer_id": SIMULATED_UID,
        "machine_uid": SIMULATED_UID,
        "date": (datetime.now() - timedelta(days=1)).isoformat(),
        "overall_result": "PASS",
    }
    name = "QC_%s_%s.json" % (
        SIMULATED_UID,
        (datetime.now() - timedelta(days=1)).strftime("%Y%m%d_%H%M%S"))
    path = os.path.join(report_dir, name)
    with open(path, "w") as f:
        json.dump(prev, f, indent=2, ensure_ascii=False)
    return path


def load_token():
    """Token QC : env QC_TOKEN, fichier du pad, puis .env (repo, non committé)."""
    token = os.environ.get("QC_TOKEN", "").strip()
    if token:
        return token
    try:
        with open(TOKEN_FILE) as f:
            return f.read().strip()
    except OSError:
        pass
    env_path = os.path.join(ROOT, ".env")
    try:
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if line.startswith("QC_TOKEN="):
                    return line.split("=", 1)[1].strip().strip('"').strip("'")
    except OSError:
        pass
    return ""


def post_report(report, token, url=URL, timeout=15):
    """POST le rapport. Renvoie (status HTTP, corps décodé). Lève urllib.error
    en cas d'échec réseau ; HTTPError est capturée pour exposer le corps."""
    data = json.dumps(report).encode("utf-8")
    req = urllib.request.Request(
        url, data=data, method="POST",
        headers={"Content-Type": "application/json", "X-QC-Token": token})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")


def check_ack_sandbox(body):
    """Vérifie l'ack serveur (CDC §4 : réponse marquée "sandbox": true).

    Renvoie l'ack décodé. Lève ValueError si le corps n'est pas un objet JSON
    ou si le marqueur sandbox est absent — un 200 sans marqueur signifierait
    que le serveur a traité le rapport comme un vrai (écriture réelle)."""
    try:
        ack = json.loads(body)
    except ValueError:
        raise ValueError("ack non JSON: %r" % body[:200])
    if not isinstance(ack, dict):
        raise ValueError("ack inattendu (pas un objet JSON): %r" % body[:200])
    if ack.get("sandbox") is not True:
        raise ValueError("ack sans marqueur sandbox=true: %r" % body[:200])
    return ack


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default=URL, help="endpoint /api/qc/report")
    parser.add_argument("--retest", action="store_true",
                        help="simule un 2e QC de la meme machine (retest: true)")
    args = parser.parse_args(argv)
    if args.retest:
        # HOME temporaire : le rapport précédent seedé et la détection retest
        # (qc_reports/) vivent dans un tempdir — rien n'écrit dans le vrai
        # HOME du poste/du pad. Le token retombe alors sur env QC_TOKEN/.env.
        os.environ["HOME"] = tempfile.mkdtemp(prefix="qc-sandbox-retest-")
        seed = seed_previous_report()
        print("mode retest: HOME=%s" % os.environ["HOME"])
        print("rapport precedent seede: %s" % seed)
    token = load_token()
    if not token:
        print("ERREUR: aucun token QC (env QC_TOKEN, %s, ou .env)" % TOKEN_FILE,
              file=sys.stderr)
        return 1
    report = build_report()
    assert report.get("sandbox") is True
    if args.retest:
        assert report.get("retest") is True, "retest non détecté après seed"
        print("retest: %s | retest_reason: %s"
              % (report["retest"], report.get("retest_reason")))
    status, body = post_report(report, token, url=args.url)
    print("HTTP", status)
    print(body)
    if status != 200:
        print("ERREUR: ack attendu HTTP 200", file=sys.stderr)
        return 1
    try:
        check_ack_sandbox(body)
    except ValueError as e:
        print("ERREUR: %s" % e, file=sys.stderr)
        return 1
    print("ACK SANDBOX confirme (sandbox=true dans la reponse)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
