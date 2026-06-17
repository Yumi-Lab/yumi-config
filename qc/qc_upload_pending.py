#!/usr/bin/env python3
"""Renvoi des rapports QC au compteur central (qc.yumi-lab.com).

Filet de sécurité : si l'upload en fin de QC échoue (réseau coupé), le rapport
reste dans qc_reports/ SANS marqueur ".sent". Ce script (lancé par un timer
systemd toutes les 2 min + au boot) renvoie tous les rapports non confirmés et
ne pose le marqueur ".sent" QUE sur un HTTP 200 (succès serveur).

RETRY INFINI : aucune limite de tentatives ni de nombre de rapports. Tant qu'un
rapport n'a pas son ".sent", il est retenté à chaque tick, indéfiniment. "200"
ci-dessous = code HTTP de succès, PAS un plafond de tentatives.

Stdlib uniquement (urllib). Idempotent côté serveur (dédup printer_id+date).
"""
import os
import json
import glob
import urllib.request
import urllib.error

CONFIG = os.path.expanduser("~/printer_data/config")
REPORTS_DIR = os.path.join(CONFIG, "qc_reports")
TOKEN_FILE = os.path.join(CONFIG, "qc_token")
URL = "https://qc.yumi-lab.com/api/qc/report"


def _token():
    try:
        with open(TOKEN_FILE) as f:
            return f.read().strip()
    except OSError:
        return ""


def _post(report, token):
    data = json.dumps(report).encode("utf-8")
    req = urllib.request.Request(
        URL, data=data, method="POST",
        headers={"Content-Type": "application/json", "X-QC-Token": token})
    with urllib.request.urlopen(req, timeout=15) as resp:
        return resp.status == 200


def main():
    token = _token()
    if not token:
        return  # pas de token posé sur ce pad -> rien à faire
    if not os.path.isdir(REPORTS_DIR):
        return
    for path in sorted(glob.glob(os.path.join(REPORTS_DIR, "*.json"))):
        sent_marker = path + ".sent"
        if os.path.exists(sent_marker):
            continue  # déjà confirmé envoyé
        try:
            with open(path) as f:
                report = json.load(f)
        except Exception:
            continue  # fichier illisible -> on saute (sera retenté plus tard)
        try:
            if _post(report, token):
                open(sent_marker, "w").close()
                print("envoyé:", os.path.basename(path))
        except Exception as e:
            # Réseau / serveur indispo -> on laisse pending, retry au prochain tick
            print("retry plus tard:", os.path.basename(path), "->", e)


if __name__ == "__main__":
    main()
