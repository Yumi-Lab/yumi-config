#!/bin/bash
# Script to update Klipper extras modules
# This script is called automatically by Moonraker after git pull

echo "=== Updating Klipper Extras Modules ==="

KLIPPER_EXTRAS_DIR="/home/pi/klipper/klippy/extras"
REPO_DIR="/home/pi/yumi-config"

# Motion Sensor
echo "Motion Sensor ..."
SOURCE_FILE="$REPO_DIR/klipper/klippy/extras/filament_yumi_smart_motion_sensor.py"
if [ -f "$SOURCE_FILE" ]; then
  echo "✅ Fichier trouvé, copie en cours..."
  rm -f "$KLIPPER_EXTRAS_DIR/filament_yumi_smart_motion_sensor.py" && echo "Ancienne version supprimée." || echo "Pas d'ancienne version à supprimer."
  cp "$SOURCE_FILE" "$KLIPPER_EXTRAS_DIR/" && echo "🎉 Fichier copié dans $KLIPPER_EXTRAS_DIR" || echo "❌ Erreur lors de la copie !"
else
  echo "❌ Fichier introuvable dans le dépôt !"
  exit 1
fi
echo "Motion Sensor ...[Done]"

# Yumi Z Offset Calculator
echo "Yumi Z Offset Calculator ..."
SOURCE_FILE="$REPO_DIR/klipper/klippy/extras/yumi_z_offset_calculator.py"
if [ -f "$SOURCE_FILE" ]; then
  echo "✅ Fichier trouvé, copie en cours..."
  rm -f "$KLIPPER_EXTRAS_DIR/yumi_z_offset_calculator.py" && echo "Ancienne version supprimée." || echo "Pas d'ancienne version à supprimer."
  cp "$SOURCE_FILE" "$KLIPPER_EXTRAS_DIR/" && echo "🎉 Fichier copié dans $KLIPPER_EXTRAS_DIR" || echo "❌ Erreur lors de la copie !"
else
  echo "❌ Fichier introuvable dans le dépôt !"
  exit 1
fi
echo "Yumi Z Offset Calculator ...[Done]"

# Probe Pressure
echo "Probe Pressure ..."
SOURCE_FILE="$REPO_DIR/klipper/klippy/extras/probe_pressure.py"
if [ -f "$SOURCE_FILE" ]; then
  echo "✅ Fichier trouvé, copie en cours..."
  rm -f "$KLIPPER_EXTRAS_DIR/probe_pressure.py" && echo "Ancienne version supprimée." || echo "Pas d'ancienne version à supprimer."
  cp "$SOURCE_FILE" "$KLIPPER_EXTRAS_DIR/" && echo "🎉 Fichier copié dans $KLIPPER_EXTRAS_DIR" || echo "❌ Erreur lors de la copie !"
else
  echo "❌ Fichier introuvable dans le dépôt !"
  exit 1
fi
echo "Probe Pressure ...[Done]"

echo "=== Klipper modules updated successfully ==="
echo "Klipper will be restarted by Moonraker's managed_services..."
