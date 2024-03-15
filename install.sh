#!/bin/bash

# Vérifiez si le script est exécuté avec sudo et déterminez l'utilisateur réel
if [ ! -z "$SUDO_USER" ]; then
    REAL_USER="$SUDO_USER"
else
    REAL_USER="$(whoami)"
fi
echo "Utilisateur réel: $REAL_USER"

# Utilisez getent pour obtenir le chemin du répertoire personnel de l'utilisateur réel
USER_HOME=$(getent passwd "$REAL_USER" | cut -d: -f6)
echo "Répertoire personnel de l'utilisateur: $USER_HOME"
KLIPPER_DIR="$USER_HOME/klipper"
echo "Répertoire Klipper: $KLIPPER_DIR"
PROJECT_DIR="$PWD"
echo "Répertoire du projet: $PROJECT_DIR"
KLIPPER_CONFIG_DIR="$KLIPPER_DIR/printer_data/config"
echo "Répertoire du config: $KLIPPER_CONFIG_DIR"

# Définition de la fonction d'installation
function install {
  # Remplacer les fichiers du projet dans le répertoire Klipper
  rm -f "$KLIPPER_CONFIG_DIR/smartpad-cpu-temp.cfg" && echo "smartpad-cpu-temp.cfg supprimé avec succès." || echo "Erreur lors de la suppression de smartpad-cpu-temp.cfg."
  cp "$PROJECT_DIR/smartpad-generic/smartpad-cpu-temp.cfg" "$KLIPPER_CONFIG_DIR" && echo "smartpad-cpu-temp.cfg copié avec succès." || echo "Erreur lors de la copie de smartpad-cpu-temp.cfg."
  rm -f "$KLIPPER_CONFIG_DIR/smartpad-adxl345.cfg" && echo "smartpad-adxl345.cfg supprimé avec succès." || echo "Erreur lors de la suppression de smartpad-adxl345.cfg."
  cp "$PROJECT_DIR/smartpad-generic/smartpad-adxl345.cfg" "$KLIPPER_CONFIG_DIR" && echo "smartpad-adxl345.cfg copié avec succès." || echo "Erreur lors de la copie de smartpad-adxl345.cfg."
  rm -f "$KLIPPER_CONFIG_DIR/printer.cfg" && echo "printer.cfg supprimé avec succès." || echo "Erreur lors de la suppression de printer.cfg."
  cp "$PROJECT_DIR/$1/printer.cfg" "$KLIPPER_CONFIG_DIR" && echo "printer.cfg copié avec succès." || echo "Erreur lors de la copie de printer.cfg."

  # Modifier les permissions pour que l'utilisateur "pi" conserve les droits sur les fichiers créés ou modifiés
  chown -R pi:pi "$KLIPPER_CONFIG_DIR"
}

# Vérifier si le script a été appelé avec un argument
if [ "$1" == "" ]; then
  # Si aucun argument n'est passé, installer smartpad-generic
  echo "Aucun argument passé. Installation de smartpad-generic."
  install "smartpad-generic"
else
  # Vérifier si le dossier correspondant à l'argument existe
  if [ ! -d "$PROJECT_DIR/$1" ]; then
    echo "Erreur : $PROJECT_DIR/$1 n'existe pas. Installation de smartpad-generic."
    install "smartpad-generic"
  else
    echo "Installation du projet : $1"
    install "$1"
  fi
fi

echo "Installation terminée."
