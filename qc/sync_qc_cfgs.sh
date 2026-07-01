#!/usr/bin/env bash
# sync_qc_cfgs.sh — Copie les cfg QC modele (qc_printer_*.cfg) du repo vers
# ~/printer_data/config/ pour que le panel QC les voie (tous les boutons modele).
#
# Appele AUTOMATIQUEMENT par Moonraker update_manager apres chaque "update yumi-config"
# (via 'install_script: qc/sync_qc_cfgs.sh' dans update_yumi-config.cfg).
# N'ECRASE PAS printer.cfg ni le token : ne fait que deposer les cfg modele.
set -e
CFG="$HOME/printer_data/config"
QC="$(cd "$(dirname "$0")" && pwd)"
mkdir -p "$CFG"
n=0
for f in "$QC"/qc_printer_*.cfg; do
    [ -e "$f" ] || continue
    cp "$f" "$CFG/$(basename "$f")"
    n=$((n+1))
done
echo "sync_qc_cfgs: $n cfg QC modele copiees vers $CFG"
