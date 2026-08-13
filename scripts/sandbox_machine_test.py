#!/usr/bin/env python3
"""Harnais SANDBOX du rapport QC machine contre la prod qc.yumi-lab.com.

Poste un rapport machine RÉALISTE (construit par le VRAI QCEngine, session
simulée 13 tests PASS) avec "sandbox": true sur POST /api/qc/report. Le
serveur PROD accepte ce drapeau : il VALIDE le payload sans rien écrire.
Aucune donnée de test ne pollue donc le compteur.

Token lu dans l'ordre : env QC_TOKEN, ~/printer_data/config/qc_token (fichier
du pad), puis .env à la racine du repo (ligne QC_TOKEN=..., jamais committé).

Usage : python3 scripts/sandbox_machine_test.py [--url URL]
Sortie : code HTTP + corps de l'ack. Exit 0 si HTTP 200, 1 sinon.

Stdlib uniquement (les pads n'ont pas de pip fiable).
"""
import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from qc import qc_engine  # noqa: E402

URL = "https://qc.yumi-lab.com/api/qc/report"
TOKEN_FILE = os.path.expanduser("~/printer_data/config/qc_token")

# Détails réalistes par test (formats réels : docs/AUDIT-MESURES.md).
_DETAILS = {
    "mcu_check": "OK: 1 MCU(s), device=C235 lot=SB001 uid=2D0046000D51353234323830",
    "z_tap_calib": "OK: 3 taps convergents spread=0.0312mm (tol=0.0500) sur 15 taps",
    "heat_bed": "OK: 60C atteint en 95s, stable",
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
        }
        eng._test_log[tid] = []
    # Identité machine réaliste : YUMI_CONFIG + UID STM32 (capture mcu_check).
    eng._test_log["mcu_check"] = [
        "[mcu] version: v0.12.0-159-gabcd1234",
        "[mcu] board=F401 device=C235 lot=SB001 uid=2D0046000D51353234323830",
        "MCU_UID=2D0046000D51353234323830",
    ]
    report = eng.generate_report()
    report["sandbox"] = True  # serveur : valide sans écrire
    return report


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


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default=URL, help="endpoint /api/qc/report")
    args = parser.parse_args(argv)
    token = load_token()
    if not token:
        print("ERREUR: aucun token QC (env QC_TOKEN, %s, ou .env)" % TOKEN_FILE,
              file=sys.stderr)
        return 1
    report = build_report()
    assert report.get("sandbox") is True
    status, body = post_report(report, token, url=args.url)
    print("HTTP", status)
    print(body)
    if status != 200:
        print("ERREUR: ack attendu HTTP 200", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
