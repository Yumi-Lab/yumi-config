#!/bin/bash

#V08-03-2025 Maxime3d77
# Get the user executing the script
REAL_USER="$USER"

# Initialize the OWNER variable
OWNER=""

# Get the user's home directory
if [ -n "$SUDO_USER" ]; then
    echo "shell script execute by with sudo :  user is $SUDO_USER"
    if [ "$SUDO_USER" = "runner" ]; then
        # Set USER_HOME specifically for 'runner' and set OWNER to 'pi'
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
echo "Config directory: $KLIPPER_CONFIG_DIR"

KLIPPER_EXTRAS_DIR="$KLIPPER_DIR/klippy/extras"
echo "Klipper extras directory: $KLIPPER_EXTRAS_DIR"

# Define the installation function
function install {
  # Replace project files in the Klipper directory
  rm -f "$KLIPPER_CONFIG_DIR/smartpad-cpu-temp.cfg" && echo "smartpad-cpu-temp.cfg deleted successfully." || echo "Error deleting smartpad-cpu-temp.cfg."
  cp "$PROJECT_DIR/smartpad-generic/smartpad-cpu-temp.cfg" "$KLIPPER_CONFIG_DIR" && echo "smartpad-cpu-temp.cfg copied successfully." || echo "Error copying smartpad-cpu-temp.cfg."
  rm -f "$KLIPPER_CONFIG_DIR/smartpad-adxl345.cfg" && echo "smartpad-adxl345.cfg deleted successfully." || echo "Error deleting smartpad-adxl345.cfg."
  cp "$PROJECT_DIR/smartpad-generic/smartpad-adxl345.cfg" "$KLIPPER_CONFIG_DIR" && echo "smartpad-adxl345.cfg copied successfully." || echo "Error copying smartpad-adxl345.cfg."
  rm -f "$KLIPPER_CONFIG_DIR/crowsnest.conf" && echo "crowsnest.conf deleted successfully." || echo "Error deleting crowsnest.conf."
  cp "$PROJECT_DIR/smartpad-generic/crowsnest.conf" "$KLIPPER_CONFIG_DIR" && echo "crowsnest.conf copied successfully." || echo "Error copying crowsnest.conf."

  # Check if the update_plr.cfg file exists
  if [ -f $KLIPPER_CONFIG_DIR/update_yumi-config.cfg ]; then
      echo "The file update_plr.cfg already exists, deleting the file..."
      rm $KLIPPER_CONFIG_DIR/update_yumi-config.cfg
  fi

  # Create a new update_yumi-config.cfg file with cat EOF
  echo "Creating a new update_yumi-config.cfg file with cat EOF..."
  cat > $KLIPPER_CONFIG_DIR/update_yumi-config.cfg << EOF
# yumi-config update_manager entry
# Moonraker will automatically:
# 1. Pull the latest changes from the git repository
# 2. Execute install.sh after each update
# 3. Restart Klipper service automatically
[update_manager yumi-config]
type: git_repo
path: ~/yumi-config
origin: https://github.com/Yumi-Lab/yumi-config.git
primary_branch: main
install_script: install.sh
system_dependencies: system_dependencies.json
is_system_service: False
managed_services: klipper

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

  # Only replace printer.cfg on first install, never during Moonraker updates
  if [ "$1" == "smartpad-generic" ] && [ -z "$MOONRAKER_PROCESS_UID" ]; then
      cp "$KLIPPER_CONFIG_DIR/printer.cfg" "$KLIPPER_CONFIG_DIR/Backupupdate-printer.cfg"
      rm -f "$KLIPPER_CONFIG_DIR/printer.cfg" && echo "printer.cfg deleted successfully." || echo "Error deleting printer.cfg."
      cp "$PROJECT_DIR/$1/printer.cfg" "$KLIPPER_CONFIG_DIR" && echo "printer.cfg copied successfully." || echo "Error copying printer.cfg."
  elif [ -n "$MOONRAKER_PROCESS_UID" ]; then
      echo "Moonraker update detected — skipping printer.cfg replacement"
  fi

  # Modify permissions so user "pi" retains rights on created or modified files
  chown -R pi:pi "$KLIPPER_CONFIG_DIR"
}

# Check if the script was called with an argument
if [ "$1" == "" ]; then
  # If no argument is passed, install smartpad-generic
  echo "No argument passed. Installing smartpad-generic."
  install "smartpad-generic"
else
  # Check if the folder corresponding to the argument exists
  if [ ! -d "$PROJECT_DIR/$1" ]; then
    echo "Error: $PROJECT_DIR/$1 does not exist. Installing smartpad-generic."
    install "smartpad-generic"
  else
    echo "Installing project: $1"
    install "$1"
  fi
fi

echo "Enable QRCODE ..."
# Activate KlipperScreen virtual environment
source $USER_HOME/.KlipperScreen-env/bin/activate
# Install qrcode module
pip3 install qrcode[pil]

# Define the klipperscreen.conf file path
CONFIG_FILE="$KLIPPER_CONFIG_DIR/KlipperScreen.conf"
# Copy KlipperScreen icons
sudo cp "$PROJECT_DIR/Wanhao D12 Expert/Icon_klipperscreen/Yumi-Lab-Picto.svg" "$USER_HOME/KlipperScreen/styles/material-dark/images/Yumi-Lab-Picto.svg"
ls "$USER_HOME/KlipperScreen/styles/material-dark/images/"
sudo cp "$PROJECT_DIR/Wanhao D12 Expert/Icon_klipperscreen/Yumi-Lab-Picto.svg" "$USER_HOME/KlipperScreen/styles/material-darker/images/Yumi-Lab-Picto.svg"
ls "$USER_HOME/KlipperScreen/styles/material-darker/images/"

# Define the block to add
BLOCK="[menu __main more YumiApp]
name: Yumi | App
icon: Yumi-Lab-Picto
panel: yumilab"

# Check if the block already exists in the file
if grep -qF "[menu __main more YumiApp]" "$CONFIG_FILE"; then
    echo "The 'YumiApp' menu is already present in the file."
else
    echo "Adding the 'YumiApp' menu at the beginning of the file..."
    echo -e "$BLOCK\n$(cat "$CONFIG_FILE")" > "$CONFIG_FILE"
    echo "Addition completed."
fi


# Define the file to modify
FILE="$USER_HOME/moonraker-yumi-lab/scripts/yumilab.py"
cp "$USER_HOME/moonraker-yumi-lab/scripts/klipper_screen_obico_panel.py" "$FILE"


PANEL_SCRIPT="$USER_HOME/moonraker-yumi-lab/scripts/yumilab.py"
SYMLINK_TARGET="$USER_HOME/KlipperScreen/panels/yumilab.py"

# Check if the file exists and is a symbolic link
if [[ -L "$SYMLINK_TARGET" ]]; then
    echo "A symbolic link already exists to $(readlink -f "$SYMLINK_TARGET"), deleting..."
    rm "$SYMLINK_TARGET"
elif [[ -e "$SYMLINK_TARGET" ]]; then
    echo "Warning: $SYMLINK_TARGET exists but is not a symbolic link."
    echo "Force deletion to recreate a symbolic link."
    rm -f "$SYMLINK_TARGET"
fi

# Create a new symbolic link
ln -s "$PANEL_SCRIPT" "$SYMLINK_TARGET"
echo "New symbolic link created: $SYMLINK_TARGET → $PANEL_SCRIPT"

# Check file existence
if [[ -f "$FILE" ]]; then
    echo "Modifying file: $FILE"

    # Replace documentation links
    sed -i "s|self.update_qr_code('https://obico.io/docs/user-guides/klipper-setup/')|self.update_qr_code('https://wiki.yumi-lab.com/KlipperSmartPad/SmartPad_Yumi_App/')|g" "$FILE"
    sed -i 's|guide_text = "Obico is state-of-the-art AI and mobile app for 3D printing."|guide_text = "Yumi is state-of-the-art AI and mobile app for 3D printing."|g' "$FILE"
    sed -i 's|self.qr_code_label.set_markup(f"<big><b>Scan to Set Up Obico</b></big>")|self.qr_code_label.set_markup(f"<big><b>Scan to Set Up Yumi</b></big>")|g' "$FILE"
    sed -i 's|setup_label3.set_markup(f"<big>Or enter the code below in the Obico app:</big>")|setup_label3.set_markup(f"<big>Or enter the code below in the Yumi app:</big>")|g' "$FILE"
    sed -i 's|self.qr_code_label.set_markup(f"<big><b>Scan to Link Obico</b></big>")|self.qr_code_label.set_markup(f"<big><b>Scan to Link Yumi</b></big>")|g' "$FILE"
    sed -i 's|self.bottom_label.set_markup(f"<big>Scan the QR code to learn more about Obico.</big>")|self.bottom_label.set_markup(f"<big>Scan the QR code to learn more about Yumi.</big>")|g' "$FILE"
    sed -i "s|self.update_qr_code('https://obico.io/')|self.update_qr_code('https://app.yumi-lab.com/')|g" "$FILE"
    sed -i 's|setup_label1.set_markup(f"<big>Printer is linked to Obico server.</big>")|setup_label1.set_markup(f"<big>Printer is linked to Yumi server.</big>")|g' "$FILE"



    # Replace box_size=4 with box_size=6
    sed -i 's/box_size=4/box_size=6/g' "$FILE"

    # Replace back_color="white" with back_color="grey"
    sed -i 's/img = qr.make_image(fill_color="black", back_color="white")/img = qr.make_image(fill_color="grey", back_color="black")/g' "$FILE"

    echo "Modifications applied successfully."
else
    echo "Error: File $FILE does not exist."
fi

echo "Enable QRCODE ...[Done]"

echo "Motion Sensor ..."
SOURCE_FILE="$PROJECT_DIR/klipper/klippy/extras/filament_yumi_smart_motion_sensor.py"

# Check file existence
if [ -f "$SOURCE_FILE" ]; then
  echo "✅ File found, copying..."
  rm -f "$KLIPPER_EXTRAS_DIR/filament_yumi_smart_motion_sensor.py" && echo "Old version deleted." || echo "No old version to delete."
  cp "$SOURCE_FILE" "$KLIPPER_EXTRAS_DIR/" && echo "🎉 File copied to $KLIPPER_EXTRAS_DIR" || echo "❌ Error during copy!"
else
  echo "❌ File not found in repository!"
  exit 1
fi
echo "Motion Sensor ...[Done]"

echo "Yumi Z Offset Calculator ..."
SOURCE_FILE="$PROJECT_DIR/klipper/klippy/extras/yumi_z_offset_calculator.py"
# Check file existence
if [ -f $SOURCE_FILE ]; then
  echo "✅ File found, copying..."
  rm -f "$KLIPPER_EXTRAS_DIR/yumi_z_offset_calculator.py" && echo "Old version deleted." || echo "No old version to delete."
  cp "$SOURCE_FILE" "$KLIPPER_EXTRAS_DIR/" && echo "🎉 File copied to $KLIPPER_EXTRAS_DIR" || echo "❌ Error during copy!"
else
  echo "❌ File not found in repository!"
  exit 1
fi
echo "Yumi Z Offset Calculator ...[Done]"

echo "Probe Pressure ..."
SOURCE_FILE="$PROJECT_DIR/klipper/klippy/extras/probe_pressure.py"
# Check file existence
if [ -f $SOURCE_FILE ]; then
  echo "✅ File found, copying..."
  rm -f "$KLIPPER_EXTRAS_DIR/probe_pressure.py" && echo "Old version deleted." || echo "No old version to delete."
  cp "$SOURCE_FILE" "$KLIPPER_EXTRAS_DIR/" && echo "🎉 File copied to $KLIPPER_EXTRAS_DIR" || echo "❌ Error during copy!"
else
  echo "❌ File not found in repository!"
  exit 1
fi
echo "Probe Pressure ...[Done]"

# Restart Klipper only if the script is NOT called by Moonraker
# Moonraker automatically restarts services via managed_services
if [ -z "$MOONRAKER_PROCESS_UID" ]; then
    echo "Restarting Klipper service to load new modules..."
    sudo systemctl restart klipper
    if [ $? -eq 0 ]; then
        echo "✅ Klipper restarted successfully!"
    else
        echo "⚠️ Warning: Failed to restart Klipper. Please restart manually with: sudo systemctl restart klipper"
    fi
else
    echo "ℹ️ Script called by Moonraker - Klipper will be restarted automatically by Moonraker"
fi

# === QC System (Quality Control) ===
echo "Installing QC System..."
QC_DIR="$PROJECT_DIR/qc"
if [ -d "$QC_DIR" ]; then
    # QC Macros for Klipper
    cp "$QC_DIR/qc_macros.cfg" "$KLIPPER_CONFIG_DIR/qc_macros.cfg" && echo "qc_macros.cfg copied successfully." || echo "Error copying qc_macros.cfg."

    # Add include in printer.cfg if not present
    if [ -f "$KLIPPER_CONFIG_DIR/printer.cfg" ]; then
        if ! grep -q "qc_macros.cfg" "$KLIPPER_CONFIG_DIR/printer.cfg"; then
            sed -i '/include mainsail.cfg/a [include qc_macros.cfg]' "$KLIPPER_CONFIG_DIR/printer.cfg"
            echo "Added [include qc_macros.cfg] to printer.cfg"
        else
            echo "[include qc_macros.cfg] already in printer.cfg"
        fi
    fi

    # KlipperScreen panel (symlinks)
    if [ -d "$USER_HOME/KlipperScreen/panels" ]; then
        # QC wizard panel
        if [ -L "$USER_HOME/KlipperScreen/panels/qc_wizard.py" ]; then
            rm "$USER_HOME/KlipperScreen/panels/qc_wizard.py"
        fi
        ln -sf "$QC_DIR/qc_wizard.py" "$USER_HOME/KlipperScreen/panels/qc_wizard.py"
        echo "Symlink created: panels/qc_wizard.py"

        # QC engine module
        if [ -L "$USER_HOME/KlipperScreen/ks_includes/qc_engine.py" ]; then
            rm "$USER_HOME/KlipperScreen/ks_includes/qc_engine.py"
        fi
        ln -sf "$QC_DIR/qc_engine.py" "$USER_HOME/KlipperScreen/ks_includes/qc_engine.py"
        echo "Symlink created: ks_includes/qc_engine.py"

        # QC icon
        for style_dir in material-dark material-darker; do
            if [ -d "$USER_HOME/KlipperScreen/styles/$style_dir/images" ]; then
                cp "$QC_DIR/qc-check.svg" "$USER_HOME/KlipperScreen/styles/$style_dir/images/qc-check.svg"
                echo "QC icon copied to $style_dir"
            fi
        done
    fi

    # KlipperScreen menu entry
    if [ -f "$CONFIG_FILE" ]; then
        if ! grep -q "qc_wizard" "$CONFIG_FILE"; then
            cat >> "$CONFIG_FILE" <<'QCMENU'

[menu __main more qc]
name: Quality Control
icon: qc-check
panel: qc_wizard
QCMENU
            echo "Added QC menu entry to KlipperScreen.conf"
        else
            echo "QC menu entry already in KlipperScreen.conf"
        fi
    fi

    # QC reports directory
    mkdir -p "$KLIPPER_CONFIG_DIR/qc_reports"
    chown -R "$OWNER:$OWNER" "$KLIPPER_CONFIG_DIR/qc_reports"
    echo "QC reports directory ready."
else
    echo "QC directory not found, skipping QC installation."
fi
echo "QC System ...[Done]"

echo "Configuring Mainsail settings..."
# Replace Mainsail config.json with Yumi template
MAINSAIL_DIR="$USER_HOME/mainsail"
if [ -f "$MAINSAIL_DIR/config.json" ]; then
    rm -f "$MAINSAIL_DIR/config.json" && echo "Old config.json deleted." || echo "Error deleting config.json."
fi
cp "$PROJECT_DIR/mainsail/config.json" "$MAINSAIL_DIR/" && echo "Yumi config.json copied successfully." || echo "Error copying config.json."
chown -R "$OWNER:$OWNER" "$MAINSAIL_DIR/config.json"

# Copy default theme template for factory reset
THEME_DIR="$USER_HOME/printer_data/config/.theme"
if [ ! -d "$THEME_DIR" ]; then
    mkdir -p "$THEME_DIR" && echo ".theme directory created." || echo "Error creating .theme directory."
fi
cp "$PROJECT_DIR/printer_data/config/theme/default.json" "$THEME_DIR/" && echo "Default theme template copied successfully." || echo "Error copying default.json."
chown -R "$OWNER:$OWNER" "$THEME_DIR"

# Fix YUMI_SYNC service name: ensure YUMI_SYNC.service exists
# V1 pads have yumi_sync.service (lowercase) but moonraker expects YUMI_SYNC
if [ -f /etc/systemd/system/yumi_sync.service ] && [ ! -e /etc/systemd/system/YUMI_SYNC.service ]; then
    echo "Fixing YUMI_SYNC service name..."
    sudo ln -sf /etc/systemd/system/yumi_sync.service /etc/systemd/system/YUMI_SYNC.service
    sudo systemctl daemon-reload
    echo "YUMI_SYNC.service symlink created."
fi

# === USB Offline Update Service ===
echo "Installing USB offline update service..."
USB_SCRIPT="/usr/local/bin/yumi-usb-update-check.sh"
USB_SERVICE="/etc/systemd/system/yumi-usb-update.service"

sudo cp "$PROJECT_DIR/yumi-usb-update-check.sh" "$USB_SCRIPT"
sudo chmod +x "$USB_SCRIPT"
echo "yumi-usb-update-check.sh installed to $USB_SCRIPT"

sudo cp "$PROJECT_DIR/yumi-usb-update.service" "$USB_SERVICE"
sudo systemctl daemon-reload
sudo systemctl enable yumi-usb-update.service
echo "yumi-usb-update.service enabled at boot"
echo "USB offline update service ...[Done]"

# === Fix WiFi USB dongle autosuspend ===
echo "Fixing USB autosuspend for WiFi dongles..."
UDEV_RULE="/etc/udev/rules.d/99-usb-no-autosuspend.rules"
if [ ! -f "$UDEV_RULE" ]; then
    cat <<'UDEV' | sudo tee "$UDEV_RULE" > /dev/null
# Disable USB autosuspend for all USB devices
# Prevents WiFi dongles (RTL8188EUS etc.) from failing to re-enumerate after reboot
ACTION=="add", SUBSYSTEM=="usb", ATTR{power/control}="on"
UDEV
    sudo udevadm control --reload-rules
    echo "USB autosuspend udev rule installed"
else
    echo "USB autosuspend udev rule already present"
fi
# Disable autosuspend immediately for current session
echo -1 | sudo tee /sys/module/usbcore/parameters/autosuspend > /dev/null 2>&1
echo "USB autosuspend fix ...[Done]"

echo "Installation completed."
