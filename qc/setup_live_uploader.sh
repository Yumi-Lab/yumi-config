#!/bin/bash
# Convertit un pad NORMAL en STATION QC D'USINE (live).
#
#   bash ~/yumi-config/qc/setup_live_uploader.sh [TOKEN_QC]
#
# Fait : MAJ yumi-config (dernière version QC) + pose le token (si fourni) +
# déploie la cfg QC + installe le daemon de retry d'envoi des rapports (timer
# systemd, retry INFINI jusqu'au HTTP 200, empile tous les rapports en attente)
# + recharge le panel. NON inclus dans install.sh : seuls les pads d'usine sur
# lesquels on lance ce script ont le daemon.
set -euo pipefail

QC="$HOME/yumi-config/qc"
CFG="$HOME/printer_data/config"

echo "== 1. MAJ yumi-config (dernière version QC) =="
git -C "$HOME/yumi-config" fetch origin
git -C "$HOME/yumi-config" reset --hard origin/main

echo "== 2. Token QC =="
if [ "${1:-}" ]; then
    printf '%s' "$1" > "$CFG/qc_token"
    echo "  token posé ($(wc -c < "$CFG/qc_token") octets)"
elif [ -f "$CFG/qc_token" ]; then
    echo "  token déjà présent (conservé)"
else
    echo "  ATTENTION : pas de token (passe-le en argument) -> l'envoi ne marchera pas"
fi

echo "== 3. Déploie la cfg QC (le panel est symlinké, à jour via le git) =="
cp "$QC/qc_printer_C235.cfg" "$CFG/qc_printer_C235.cfg"
mkdir -p "$CFG/qc_reports"

echo "== 4. Daemon de retry (timer systemd) =="
chmod +x "$QC/qc_upload_pending.py"
sudo cp "$QC/qc-upload.service" /etc/systemd/system/qc-upload.service
sudo cp "$QC/qc-upload.timer"   /etc/systemd/system/qc-upload.timer
sudo systemctl daemon-reload
sudo systemctl enable --now qc-upload.timer
echo "  qc-upload.timer actif (retry toutes les 2 min, infini jusqu'au 200)"

echo "== 5. Envoi immédiat des rapports en attente =="
python3 "$QC/qc_upload_pending.py"

echo "== 6. Recharge le panel QC =="
sudo systemctl restart KlipperScreen

echo "OK : pad converti en station QC d'usine (panel à jour + daemon retry actif)."
