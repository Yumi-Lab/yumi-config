#!/bin/bash
# Setup SPECIFIQUE pads d'usine LIVE — installe le daemon de retry d'envoi des
# rapports QC (timer systemd, retry infini jusqu'au 200, empile tous les
# rapports en attente). A lancer UNE fois par pad d'usine.
#
# NON inclus dans install.sh : les pads de banc/dev ne lancent pas ce daemon.
# Le token (qc_token) est pose separement (hors repo) avant/apres ce script.
set -euo pipefail

QC="$HOME/yumi-config/qc"
chmod +x "$QC/qc_upload_pending.py"

sudo cp "$QC/qc-upload.service" /etc/systemd/system/qc-upload.service
sudo cp "$QC/qc-upload.timer"   /etc/systemd/system/qc-upload.timer
sudo systemctl daemon-reload
sudo systemctl enable --now qc-upload.timer
echo "qc-upload.timer actif (retry toutes les 2 min)."

echo "Envoi immediat des rapports en attente :"
python3 "$QC/qc_upload_pending.py"

echo "OK : daemon retry QC installe et actif sur ce pad."
