#!/bin/bash

#V08-03-2025 Maxime3d77
# Récupérer l'utilisateur qui exécute le script
REAL_USER="$USER"

# Initialisation de la variable OWNER
OWNER=""

# Récupérer le répertoire de l'utilisateur
if [ -n "$SUDO_USER" ]; then
    echo "shell script execute by with sudo :  user is $SUDO_USER"
    if [ "$SUDO_USER" = "runner" ]; then
        # Définir USER_HOME spécifiquement pour 'runner' et définir OWNER à 'pi'
        USER_HOME="/home/pi"
        OWNER="pi"
    else
        USER_HOME=$(getent passwd "$SUDO_USER" | cut -d: -f6)
        OWNER="$SUDO_USER"
    fi
else
    USER_HOME=$(getent passwd "$USER" | cut -d: -f6)
    OWNER="$USER"
    echo "shell script execute without sudo : user is $USER"
fi

echo "Real user: $REAL_USER"
echo "User's home directory: $USER_HOME"
echo "Owner for chown: $OWNER"

# Define the Klipper directory using USER_HOME instead of HOME
KLIPPER_DIR="$USER_HOME/klipper"
echo "Klipper directory: $KLIPPER_DIR"

# Define the project directory
PROJECT_DIR="$PWD"
echo "Project directory: $PROJECT_DIR"

KLIPPER_CONFIG_DIR="$USER_HOME/printer_data/config"
echo "Répertoire du config: $KLIPPER_CONFIG_DIR"

# Définition de la fonction d'installation
function install {
  # Remplacer les fichiers du projet dans le répertoire Klipper
  rm -f "$KLIPPER_CONFIG_DIR/smartpad-cpu-temp.cfg" && echo "smartpad-cpu-temp.cfg supprimé avec succès." || echo "Erreur lors de la suppression de smartpad-cpu-temp.cfg."
  cp "$PROJECT_DIR/smartpad-generic/smartpad-cpu-temp.cfg" "$KLIPPER_CONFIG_DIR" && echo "smartpad-cpu-temp.cfg copié avec succès." || echo "Erreur lors de la copie de smartpad-cpu-temp.cfg."
  rm -f "$KLIPPER_CONFIG_DIR/smartpad-adxl345.cfg" && echo "smartpad-adxl345.cfg supprimé avec succès." || echo "Erreur lors de la suppression de smartpad-adxl345.cfg."
  cp "$PROJECT_DIR/smartpad-generic/smartpad-adxl345.cfg" "$KLIPPER_CONFIG_DIR" && echo "smartpad-adxl345.cfg copié avec succès." || echo "Erreur lors de la copie de smartpad-adxl345.cfg."
  rm -f "$KLIPPER_CONFIG_DIR/crowsnest.conf" && echo "crowsnest.conf supprimé avec succès." || echo "Erreur lors de la suppression de crowsnest.conf."
  cp "$PROJECT_DIR/smartpad-generic/crowsnest.conf" "$KLIPPER_CONFIG_DIR" && echo "crowsnest.conf copié avec succès." || echo "Erreur lors de la copie de crowsnest.conf."

  # Check if the update_plr.cfg file exists
  if [ -f $KLIPPER_CONFIG_DIR/update_yumi-config.cfg ]; then
      echo "The file update_plr.cfg already exists, deleting the file..."
      rm $KLIPPER_CONFIG_DIR/update_yumi-config.cfg
  fi

  # Create a new update_yumi-config.cfg file with cat EOF
  echo "Creating a new update_yumi-config.cfg file with cat EOF..."
  cat > $KLIPPER_CONFIG_DIR/update_yumi-config.cfg << EOF
# yumi-config update_manager entry
[update_manager yumi-config]
type: git_repo
path: ~/yumi-config
origin: https://github.com/Yumi-Lab/yumi-config.git
primary_branch: main
install_script: install.sh
is_system_service: False

EOF

# Check if the string [include update_yumi-config.cfg] is already present in the file
  if grep -Fxq "[include update_yumi-config.cfg]" $KLIPPER_CONFIG_DIR/moonraker.conf; then
      echo "The string [include update_yumi-config.cfg] is already present in the file moonraker.conf."
  else
      echo "Adding the string [include update_yumi-config.cfg] to the file moonraker.conf..."
      # Create a temporary file
      temp_file=$(mktemp)

      # Add the line [include update_plr.cfg] at the beginning of the file
      echo "[include update_yumi-config.cfg]" > "$temp_file"
      cat $KLIPPER_CONFIG_DIR/moonraker.conf >> "$temp_file"

      # Replace the original file with the temporary file
      mv "$temp_file" $KLIPPER_CONFIG_DIR/moonraker.conf
  fi

  if [ "$1" == "smartpad-generic" ]; then
  cp "$KLIPPER_CONFIG_DIR/printer.cfg" "$KLIPPER_CONFIG_DIR/Backupupdate-printer.cfg"
  rm -f "$KLIPPER_CONFIG_DIR/printer.cfg" && echo "printer.cfg supprimé avec succès." || echo "Erreur lors de la suppression de printer.cfg."
  cp "$PROJECT_DIR/$1/printer.cfg" "$KLIPPER_CONFIG_DIR" && echo "printer.cfg copié avec succès." || echo "Erreur lors de la copie de printer.cfg."
  fi

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

echo "Enable QRCODE ..."
#position venv kliperscrenn
HOME="/home/pi/"
source $HOME/.KlipperScreen-env/bin/activate
#install module qrcod
pip3 install qrcode[pil]

# Définition du chemin du fichier klipperscreen.conf
CONFIG_FILE="/home/pi/printer_data/config/KlipperScreen.conf"
#copy icon klipperscreen
sudo cp /home/pi/yumi-config/Wanhao\ D12\ Expert/Icon_klipperscreen/Yumi-Lab-Picto.svg /home/pi/KlipperScreen/styles/material-dark/images/Yumi-Lab-Picto.svg
ls /home/pi/KlipperScreen/styles/material-dark/images/
sudo cp /home/pi/yumi-config/Wanhao\ D12\ Expert/Icon_klipperscreen/Yumi-Lab-Picto.svg /home/pi/KlipperScreen/styles/material-darker/images/Yumi-Lab-Picto.svg
ls /home/pi/KlipperScreen/styles/material-darker/images/

# Définition du bloc à ajouter
BLOCK="[menu __main more YumiApp]
name: Yumi | App
icon: Yumi-Lab-Picto
panel: yumilab"

# Vérifier si le bloc existe déjà dans le fichier
if grep -qF "[menu __main more YumiApp]" "$CONFIG_FILE"; then
    echo "Le menu 'YumiApp' est déjà présent dans le fichier."
else
    echo "Ajout du menu 'YumiApp' au début du fichier..."
    echo -e "$BLOCK\n$(cat "$CONFIG_FILE")" > "$CONFIG_FILE"
    echo "Ajout terminé."
fi


# Définition du fichier à modifier
FILE="/home/pi/moonraker-yumi-lab/scripts/yumilab.py"
cp /home/pi/moonraker-yumi-lab/scripts/klipper_screen_obico_panel.py $FILE


PANEL_SCRIPT="/home/pi/moonraker-yumi-lab/scripts/yumilab.py"
SYMLINK_TARGET="$HOME/KlipperScreen/panels/yumilab.py"

# Vérifier si le fichier existe et s'il est un lien symbolique
if [[ -L "$SYMLINK_TARGET" ]]; then
    echo "Un lien symbolique existe déjà vers $(readlink -f "$SYMLINK_TARGET"), suppression..."
    rm "$SYMLINK_TARGET"
elif [[ -e "$SYMLINK_TARGET" ]]; then
    echo "Attention : $SYMLINK_TARGET existe mais n'est pas un lien symbolique."
    echo "Suppression forcée pour recréer un lien symbolique."
    rm -f "$SYMLINK_TARGET"
fi

# Créer un nouveau lien symbolique
ln -s "$PANEL_SCRIPT" "$SYMLINK_TARGET"
echo "Nouveau lien symbolique créé : $SYMLINK_TARGET → $PANEL_SCRIPT"

# Vérification de l'existence du fichier
if [[ -f "$FILE" ]]; then
    echo "Modification du fichier : $FILE"

    # Remplacement lien doc
    sed -i "s|self.update_qr_code('https://obico.io/docs/user-guides/klipper-setup/')|self.update_qr_code('https://wiki.yumi-lab.com/KlipperSmartPad/SmartPad_Yumi_App/')|g" "$FILE"
    sed -i 's|guide_text = "Obico is state-of-the-art AI and mobile app for 3D printing."|guide_text = "Yumi is state-of-the-art AI and mobile app for 3D printing."|g' "$FILE"
    sed -i 's|self.qr_code_label.set_markup(f"<big><b>Scan to Set Up Obico</b></big>")|self.qr_code_label.set_markup(f"<big><b>Scan to Set Up Yumi</b></big>")|g' "$FILE"
    sed -i 's|setup_label3.set_markup(f"<big>Or enter the code below in the Obico app:</big>")|setup_label3.set_markup(f"<big>Or enter the code below in the Yumi app:</big>")|g' "$FILE"
    sed -i 's|self.qr_code_label.set_markup(f"<big><b>Scan to Link Obico</b></big>")|self.qr_code_label.set_markup(f"<big><b>Scan to Link Yumi</b></big>")|g' "$FILE"
    sed -i 's|self.bottom_label.set_markup(f"<big>Scan the QR code to learn more about Obico.</big>")|self.bottom_label.set_markup(f"<big>Scan the QR code to learn more about Yumi.</big>")|g' "$FILE"
    sed -i "s|self.update_qr_code('https://obico.io/')|self.update_qr_code('https://app.yumi-lab.com/')|g" "$FILE"
    sed -i 's|setup_label1.set_markup(f"<big>Printer is linked to Obico server.</big>")|setup_label1.set_markup(f"<big>Printer is linked to Yumi server.</big>")|g' "$FILE"



    # Remplacement de box_size=4 par box_size=12
    sed -i 's/box_size=4/box_size=6/g' "$FILE"

    # Remplacement de back_color="white" par back_color="grey"
    sed -i 's/img = qr.make_image(fill_color="black", back_color="white")/img = qr.make_image(fill_color="grey", back_color="black")/g' "$FILE"

    echo "Modifications appliquées avec succès."
else
    echo "Erreur : Le fichier $FILE n'existe pas."
fi

echo "Enable QRCODE ...[Done]"

echo "Motion Sensor ..."

# Vérification de l'existence du fichier
if [ -f "/home/pi/yumi-config/klipper/klippy/extras/filament_yumi_smart_motion_sensor.py" ]; then
  echo "✅ Fichier trouvé, copie en cours..."
  cp "/home/pi/yumi-config/klipper/klippy/extras/filament_yumi_smart_motion_sensor.py" "/home/pi/klipper/klippy/extras/"
  echo "🎉 Fichier copié dans /home/pi/klipper/klippy/extras"
else
  echo "❌ Fichier introuvable dans le dépôt !"
  exit 1
fi
if [ -f "/home/pi/yumi-config/klipper/klippy/extras/yumi_z_offset_calculator.py" ]; then
  echo "✅ Fichier trouvé, copie en cours..."
  cp "/home/pi/yumi-config/klipper/klippy/extras/yumi_z_offset_calculator.py" "/home/pi/klipper/klippy/extras/"
  echo "🎉 Fichier copié dans /home/pi/klipper/klippy/extras"
else
  echo "❌ Fichier introuvable dans le dépôt !"
  exit 1
fi
if [ -f "/home/pi/yumi-config/klipper/klippy/extras/probe_pressure.py" ]; then
  echo "✅ Fichier trouvé, copie en cours..."
  cp "/home/pi/yumi-config/klipper/klippy/extras/probe_pressure.py" "/home/pi/klipper/klippy/extras/"
  echo "🎉 Fichier copié dans /home/pi/klipper/klippy/extras"
else
  echo "❌ Fichier introuvable dans le dépôt !"
  exit 1
fi
echo "Motion Sensor ...[Done]"

echo "Installation terminée."
