#!/bin/bash
# =====================================================================
# INSTALLER — convertit un pad Yumi NORMAL en STATION QC D'USINE (live).
#
#   bash ~/yumi-config/qc/install_qc_station.sh [TOKEN_QC]
#
# Idempotent : ré-exécutable sans risque. Fait, dans l'ordre :
#   1. MAJ yumi-config (dernière version QC)
#   2. Panel QC : symlinks wizard/engine + entrée menu KlipperScreen + icône
#   3. Token QC (argument, hors repo)
#   4. cfg QC déployée + passage en MODE QC (backup de la cfg courante)
#   5. Daemon de retry d'envoi des rapports (timer systemd, retry INFINI)
#   6. Renvoi des rapports en attente
#   7. Reboot (applique cfg + panel + affichage proprement)
#
# Prérequis : pad Yumi avec ~/yumi-config (clone git) et ~/KlipperScreen.
# sudo est demandé (mot de passe du pad) pour les étapes systemd + reboot.
# =====================================================================
set -euo pipefail

QC="$HOME/yumi-config/qc"
CFG="$HOME/printer_data/config"
KS="$HOME/KlipperScreen"
TOKEN="${1:-}"

echo "== 1/7  MAJ yumi-config (dernière version QC) =="
git -C "$HOME/yumi-config" fetch origin
git -C "$HOME/yumi-config" reset --hard origin/main

echo "== 2/7  Panel QC (symlinks + menu KlipperScreen) =="
if [ -d "$KS/panels" ]; then
    ln -sf "$QC/qc_wizard.py" "$KS/panels/qc_wizard.py"
    ln -sf "$QC/qc_engine.py" "$KS/ks_includes/qc_engine.py"
    for sd in material-dark material-darker; do
        if [ -d "$KS/styles/$sd/images" ] && [ -f "$QC/qc-check.svg" ]; then
            cp "$QC/qc-check.svg" "$KS/styles/$sd/images/qc-check.svg"
        fi
    done
    echo "  symlinks panel OK"
else
    echo "  ATTENTION : pas de ~/KlipperScreen — panel non installé"
fi
KSCONF="$CFG/KlipperScreen.conf"
if [ -f "$KSCONF" ] && ! grep -q "panel: qc_wizard" "$KSCONF"; then
    printf '\n[menu __main more qc]\nname: Quality Control\nicon: qc-check\npanel: qc_wizard\n' >> "$KSCONF"
    echo "  entrée menu QC ajoutée"
fi

echo "== 3/7  Token QC =="
if [ -n "$TOKEN" ]; then
    printf '%s' "$TOKEN" > "$CFG/qc_token"
    echo "  token posé ($(wc -c < "$CFG/qc_token") octets)"
elif [ -f "$CFG/qc_token" ]; then
    echo "  token déjà présent (conservé)"
else
    echo "  ATTENTION : pas de token (passe-le en argument) -> l'envoi au compteur ne marchera pas"
fi

echo "== 4/7  cfg QC + passage en MODE QC =="
mkdir -p "$CFG/qc_reports"
# Copier TOUTES les cfg QC modele (C235, C335, ...) en local sur le pad, pour que
# tous les boutons du selecteur fonctionnent meme SANS connexion reseau.
cp "$QC"/qc_printer_*.cfg "$CFG"/ 2>/dev/null
echo "  cfg QC modeles copiees : $(ls "$QC"/qc_printer_*.cfg 2>/dev/null | xargs -n1 basename | tr '\n' ' ')"
# Backup de la cfg courante (nom attendu par le panel pour 'Exit QC mode'),
# une seule fois pour ne pas écraser un vrai backup de prod existant.
if [ ! -f "$CFG/printer.cfg.qc-backup" ]; then
    cp "$CFG/printer.cfg" "$CFG/printer.cfg.qc-backup"
    echo "  cfg courante sauvée -> printer.cfg.qc-backup"
fi
cp "$QC/qc_printer_C235.cfg" "$CFG/printer.cfg"
echo "  cfg QC active (mode QC)"

echo "== 5/7  Daemon de retry (timer systemd) =="
chmod +x "$QC/qc_upload_pending.py"
sudo cp "$QC/qc-upload.service" /etc/systemd/system/qc-upload.service
sudo cp "$QC/qc-upload.timer"   /etc/systemd/system/qc-upload.timer
sudo systemctl daemon-reload
sudo systemctl enable --now qc-upload.timer
echo "  qc-upload.timer actif (retry infini jusqu'au HTTP 200)"

echo "== 6/7  Renvoi des rapports en attente =="
python3 "$QC/qc_upload_pending.py"

echo "== 7/7  Reboot (applique cfg + panel + affichage) =="
echo "Station QC installée. Reboot dans 3 s..."
sleep 3
sudo reboot
