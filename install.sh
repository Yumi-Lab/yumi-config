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

# Helper: run with sudo if available, otherwise run directly
run_privileged() {
    if sudo -n true 2>/dev/null; then
        sudo "$@"
    else
        echo "WARNING: no sudo access, skipping privileged command: $*"
        return 1
    fi
}

# Define the installation function
function install {
  # Replace project files in the Klipper directory
  rm -f "$KLIPPER_CONFIG_DIR/smartpad-cpu-temp.cfg" && echo "smartpad-cpu-temp.cfg deleted successfully." || echo "Error deleting smartpad-cpu-temp.cfg."
  cp "$PROJECT_DIR/smartpad-generic/smartpad-cpu-temp.cfg" "$KLIPPER_CONFIG_DIR" && echo "smartpad-cpu-temp.cfg copied successfully." || echo "Error copying smartpad-cpu-temp.cfg."
  rm -f "$KLIPPER_CONFIG_DIR/smartpad-adxl345.cfg" && echo "smartpad-adxl345.cfg deleted successfully." || echo "Error deleting smartpad-adxl345.cfg."
  cp "$PROJECT_DIR/smartpad-generic/smartpad-adxl345.cfg" "$KLIPPER_CONFIG_DIR" && echo "smartpad-adxl345.cfg copied successfully." || echo "Error copying smartpad-adxl345.cfg."
  rm -f "$KLIPPER_CONFIG_DIR/crowsnest.conf" && echo "crowsnest.conf deleted successfully." || echo "Error deleting crowsnest.conf."
  cp "$PROJECT_DIR/smartpad-generic/crowsnest.conf" "$KLIPPER_CONFIG_DIR" && echo "crowsnest.conf copied successfully." || echo "Error copying crowsnest.conf."

  # Generate update_yumi-config.cfg ATOMICALLY (temp file in the same dir + mv).
  # The old code did `rm` then recreated the file: during that window a Moonraker
  # config reload (install.sh restarts klipper/moonraker) could read the file as
  # missing/partial, dropping [update_manager yumi-config] from the OTA list.
  # An atomic rename removes that window; cmp avoids a needless mtime bump.
  echo "Ensuring update_yumi-config.cfg is up to date (atomic write)..."
  _umc_target="$KLIPPER_CONFIG_DIR/update_yumi-config.cfg"
  _umc_tmp="$KLIPPER_CONFIG_DIR/.update_yumi-config.cfg.tmp"
  cat > "$_umc_tmp" << EOF
# yumi-config update_manager entry
[update_manager yumi-config]
type: git_repo
path: ~/yumi-config
origin: https://github.com/Yumi-Lab/yumi-config.git
primary_branch: main
system_dependencies: system_dependencies.json
is_system_service: False
managed_services: klipper

EOF
  if [ -f "$_umc_target" ] && cmp -s "$_umc_tmp" "$_umc_target"; then
      echo "update_yumi-config.cfg already up to date, no change."
      rm -f "$_umc_tmp"
  else
      mv "$_umc_tmp" "$_umc_target"
      echo "update_yumi-config.cfg written atomically."
  fi

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
cp "$PROJECT_DIR/Wanhao D12 Expert/Icon_klipperscreen/Yumi-Lab-Picto.svg" "$USER_HOME/KlipperScreen/styles/material-dark/images/Yumi-Lab-Picto.svg"
ls "$USER_HOME/KlipperScreen/styles/material-dark/images/"
cp "$PROJECT_DIR/Wanhao D12 Expert/Icon_klipperscreen/Yumi-Lab-Picto.svg" "$USER_HOME/KlipperScreen/styles/material-darker/images/Yumi-Lab-Picto.svg"
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
FILE="$USER_HOME/moonraker-app-yumi-lab/scripts/yumilab.py"
cp "$USER_HOME/moonraker-app-yumi-lab/scripts/klipper_screen_obico_panel.py" "$FILE"


PANEL_SCRIPT="$USER_HOME/moonraker-app-yumi-lab/scripts/yumilab.py"
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

# Symlink NEW klippy extras from yumi-config into klipper. These are ADDITIONS
# (files that do not exist in stock klipper), so a symlink keeps klipper's git
# working tree clean and does not interfere with Moonraker updates.
# Git pull on yumi-config automatically updates these modules.
# NOTE: motion_queuing.py is intentionally NOT in this list — it is a PATCHED
# CORE klipper file and is deployed as a real COPY below (see deploy_patched_core).
# A symlink over a git-tracked file makes the tree "dirty" and breaks update_manager.
YUMI_EXTRAS=(
  "filament_yumi_smart_motion_sensor.py"
  "yumi_z_tap.py"
  "yumi_sensorless_homing.py"
  "probe_pressure.py"
  "gcode_shell_command.py"
)

echo "Symlinking klippy extras..."
for module in "${YUMI_EXTRAS[@]}"; do
  SOURCE_FILE="$PROJECT_DIR/klipper/klippy/extras/$module"
  TARGET_FILE="$KLIPPER_EXTRAS_DIR/$module"
  if [ -f "$SOURCE_FILE" ]; then
    rm -f "$TARGET_FILE"
    ln -sf "$SOURCE_FILE" "$TARGET_FILE"
    echo "✅ $module -> symlinked"
  else
    echo "⚠ $module not found in yumi-config, skipping"
  fi
done
echo "Klippy extras symlinked ...[Done]"

# Deploy PATCHED CORE klipper files as REAL COPIES (NOT symlinks).
# extruder.py (kinematics) + motion_queuing.py (extras) REPLACE files that exist
# in stock klipper. A symlink over a git-tracked file makes klipper's working tree
# permanently "dirty": Moonraker's update_manager then flags it invalid and runs
# a recovery (hard-reset / re-clone) that the symlink can break (we saw it fail on
# a root-owned __pycache__). A plain copy lets git update/recover cleanly to stock;
# yumi-sync (repair_klipper_lead) then re-copies our version on top, detecting the
# drift by hash. kin_extruder.c stays STOCK (pure-Python patch). On upstream
# Klipper update, rebase these files (see klipper/_LEAD_PATCH.md).
deploy_patched_core() {
  local src="$1" dst="$2"
  if [ -f "$src" ]; then
    rm -f "$dst"
    cp -f "$src" "$dst"
    echo "✅ $(basename "$dst") -> copied"
  else
    echo "⚠ $(basename "$dst") source not found ($src), skipping"
  fi
}
echo "Deploying patched core klipper files (copies)..."
deploy_patched_core "$PROJECT_DIR/klipper/klippy/kinematics/extruder.py" \
                    "$KLIPPER_DIR/klippy/kinematics/extruder.py"
deploy_patched_core "$PROJECT_DIR/klipper/klippy/extras/motion_queuing.py" \
                    "$KLIPPER_DIR/klippy/extras/motion_queuing.py"
echo "Patched core klipper files deployed ...[Done]"

# Purge stale Python bytecode so the freshly deployed .py (extruder.py,
# motion_queuing.py, extras) get recompiled. Without this, Python keeps loading
# the old .pyc (compiled before the lead_time patch) and the new code is silently
# ignored -> lead_time / patched modules appear "not to work". A copy/symlink alone
# does not reliably bump the source mtime that CPython uses to invalidate .pyc.
echo "Purging klippy __pycache__ (force recompile of deployed modules)..."
if [ -d "$KLIPPER_DIR/klippy" ]; then
    find "$KLIPPER_DIR/klippy" -name '__pycache__' -type d -prune -exec rm -rf {} +
    echo "✅ klippy __pycache__ purged"
fi

# Restart Klipper only if the script is NOT called by Moonraker
# Moonraker automatically restarts services via managed_services
if [ -z "$MOONRAKER_PROCESS_UID" ]; then
    echo "Restarting Klipper service to load new modules..."
    run_privileged systemctl restart klipper
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
            chown pi:pi "$KLIPPER_CONFIG_DIR/printer.cfg"
            echo "Added [include qc_macros.cfg] to printer.cfg"
        else
            echo "[include qc_macros.cfg] already in printer.cfg"
        fi
    fi

    # KlipperScreen panel (symlinks)
    if [ -d "$USER_HOME/KlipperScreen/panels" ]; then
        # QC wizard panel
        rm -f "$USER_HOME/KlipperScreen/panels/qc_wizard.py"
        ln -sf "$QC_DIR/qc_wizard.py" "$USER_HOME/KlipperScreen/panels/qc_wizard.py"
        echo "Symlink created: panels/qc_wizard.py"

        # QC engine module
        rm -f "$USER_HOME/KlipperScreen/ks_includes/qc_engine.py"
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

    # Exclude symlinked files from KlipperScreen git tracking
    KS_EXCLUDE="$USER_HOME/KlipperScreen/.git/info/exclude"
    if [ -f "$KS_EXCLUDE" ]; then
        for entry in "panels/qc_wizard.py" "panels/yumilab.py" "ks_includes/qc_engine.py" "styles/material-dark/images/Yumi-Lab-Picto.svg" "styles/material-dark/images/qc-check.svg" "styles/material-darker/images/Yumi-Lab-Picto.svg" "styles/material-darker/images/qc-check.svg"; do
            if ! grep -qF "$entry" "$KS_EXCLUDE"; then
                echo "$entry" >> "$KS_EXCLUDE"
            fi
        done
        echo "KlipperScreen git exclude updated"
    fi

    # QC reports directory
    mkdir -p "$KLIPPER_CONFIG_DIR/qc_reports"
    chown -R "$OWNER:$OWNER" "$KLIPPER_CONFIG_DIR/qc_reports"
    echo "QC reports directory ready."
else
    echo "QC directory not found, skipping QC installation."
fi
echo "QC System ...[Done]"

# === Generic DEVICE macro (reads YUMI_CONFIG burned into MCU firmware) ===
echo "Installing generic DEVICE macro..."
DEVICE_CFG="$PROJECT_DIR/smartpad-generic/yumi-device.cfg"
if [ -f "$DEVICE_CFG" ]; then
    # Symlink so yumi-sync propagates updates without recopying
    rm -f "$KLIPPER_CONFIG_DIR/yumi-device.cfg"
    ln -sf "$DEVICE_CFG" "$KLIPPER_CONFIG_DIR/yumi-device.cfg"
    echo "Symlink created: yumi-device.cfg"

    # Add include in printer.cfg if not present
    if [ -f "$KLIPPER_CONFIG_DIR/printer.cfg" ]; then
        if ! grep -q "yumi-device.cfg" "$KLIPPER_CONFIG_DIR/printer.cfg"; then
            # Prepend at line 1 — robust regardless of which includes a given config has
            sed -i '1i [include yumi-device.cfg]' "$KLIPPER_CONFIG_DIR/printer.cfg"
            chown "$OWNER:$OWNER" "$KLIPPER_CONFIG_DIR/printer.cfg"
            echo "Added [include yumi-device.cfg] to printer.cfg"
        else
            echo "[include yumi-device.cfg] already in printer.cfg"
        fi
    fi
else
    echo "yumi-device.cfg not found, skipping DEVICE macro installation."
fi
echo "Generic DEVICE macro ...[Done]"

echo "Configuring Mainsail settings..."
# NOTE: we deliberately do NOT deploy a config.json into ~/mainsail anymore.
# Mainsail already ships its own valid config.json inside the release zip (re-extracted
# on every update). Our old template was functionally identical to it — the only
# difference was "defaultTheme": "yumi", a key Mainsail's code never reads (the theme
# is delivered by .theme/default.json below). So the copy changed nothing about the UI
# and only risked leaving a 0-byte file (served as HTTP 200) that breaks Mainsail's
# i18n bootstrap -> the whole UI renders raw keys. Yumi branding lives in .theme/, the
# proper update-safe place; the Mainsail project files are left untouched.

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
    run_privileged ln -sf /etc/systemd/system/yumi_sync.service /etc/systemd/system/YUMI_SYNC.service
    run_privileged systemctl daemon-reload
    echo "YUMI_SYNC.service symlink created."
fi

# === USB Offline Update Service ===
echo "Installing USB offline update service..."

# Install script to a location accessible without sudo
mkdir -p "$USER_HOME/.local/bin"
cp "$PROJECT_DIR/yumi-usb-update-check.sh" "$USER_HOME/.local/bin/yumi-usb-update-check.sh"
chmod +x "$USER_HOME/.local/bin/yumi-usb-update-check.sh"

# Install systemd service (requires sudo)
if run_privileged cp "$PROJECT_DIR/yumi-usb-update.service" /etc/systemd/system/yumi-usb-update.service; then
    run_privileged systemctl daemon-reload
    run_privileged systemctl enable yumi-usb-update.service
    echo "yumi-usb-update.service enabled at boot"
else
    echo "WARNING: no sudo access, skipping systemd service install"
fi
echo "USB offline update service ...[Done]"

# === Fix WiFi USB dongle autosuspend ===
echo "Fixing USB autosuspend for WiFi dongles..."
UDEV_RULE="/etc/udev/rules.d/99-usb-no-autosuspend.rules"
if [ ! -f "$UDEV_RULE" ]; then
    if run_privileged tee "$UDEV_RULE" > /dev/null <<'UDEV'
# Disable USB autosuspend for all USB devices
# Prevents WiFi dongles (RTL8188EUS etc.) from failing to re-enumerate after reboot
ACTION=="add", SUBSYSTEM=="usb", ATTR{power/control}="on"
UDEV
    then
        run_privileged udevadm control --reload-rules
        echo "USB autosuspend udev rule installed"
    fi
else
    echo "USB autosuspend udev rule already present"
fi
# Disable autosuspend immediately for current session (best effort)
echo -1 2>/dev/null | run_privileged tee /sys/module/usbcore/parameters/autosuspend > /dev/null 2>&1
echo "USB autosuspend fix ...[Done]"

# === CFG Wizard KlipperScreen Panel ===
echo "Installing CFG Wizard panel..."
CFG_WIZARD_SRC="$PROJECT_DIR/generator/cfg_wizard.py"
if [ -f "$CFG_WIZARD_SRC" ] && [ -d "$USER_HOME/KlipperScreen/panels" ]; then
    # Symlink panel
    rm -f "$USER_HOME/KlipperScreen/panels/cfg_wizard.py"
    ln -sf "$CFG_WIZARD_SRC" "$USER_HOME/KlipperScreen/panels/cfg_wizard.py"
    echo "Symlink created: panels/cfg_wizard.py"

    # Add menu entry to KlipperScreen.conf
    CONFIG_FILE="$KLIPPER_CONFIG_DIR/KlipperScreen.conf"
    if [ -f "$CONFIG_FILE" ]; then
        if ! grep -q "cfg_wizard" "$CONFIG_FILE"; then
            cat >> "$CONFIG_FILE" << 'CFGMENU'

[menu __main more CfgWizard]
name: Printer Config
icon: settings
panel: cfg_wizard
CFGMENU
            echo "Added CFG Wizard menu entry to KlipperScreen.conf"
        else
            echo "CFG Wizard menu entry already in KlipperScreen.conf"
        fi
    fi

    # Exclude from KlipperScreen git tracking
    KS_EXCLUDE="$USER_HOME/KlipperScreen/.git/info/exclude"
    if [ -f "$KS_EXCLUDE" ]; then
        if ! grep -qF "panels/cfg_wizard.py" "$KS_EXCLUDE"; then
            echo "panels/cfg_wizard.py" >> "$KS_EXCLUDE"
        fi
        echo "KlipperScreen git exclude updated for cfg_wizard"
    fi
fi
echo "CFG Wizard panel ...[Done]"

# === Maintenance Panel ===
echo "Installing Maintenance panel..."
MAINT_PANEL_SRC="$PROJECT_DIR/maintenance/maintenance_panel.py"
if [ -f "$MAINT_PANEL_SRC" ] && [ -d "$USER_HOME/KlipperScreen/panels" ]; then
    # Symlink panel
    rm -f "$USER_HOME/KlipperScreen/panels/maintenance_panel.py"
    ln -sf "$MAINT_PANEL_SRC" "$USER_HOME/KlipperScreen/panels/maintenance_panel.py"
    echo "Symlink created: panels/maintenance_panel.py"

    # Add menu entry to KlipperScreen.conf
    CONFIG_FILE="$KLIPPER_CONFIG_DIR/KlipperScreen.conf"
    if [ -f "$CONFIG_FILE" ]; then
        if ! grep -q "maintenance_panel" "$CONFIG_FILE"; then
            cat >> "$CONFIG_FILE" << 'MAINTMENU'

[menu __main more Maintenance]
name: Maintenance
icon: heat-up
panel: maintenance_panel
MAINTMENU
            echo "Added Maintenance menu entry to KlipperScreen.conf"
        else
            echo "Maintenance menu entry already in KlipperScreen.conf"
        fi
    fi

    # Exclude from KlipperScreen git tracking
    KS_EXCLUDE="$USER_HOME/KlipperScreen/.git/info/exclude"
    if [ -f "$KS_EXCLUDE" ]; then
        if ! grep -qF "panels/maintenance_panel.py" "$KS_EXCLUDE"; then
            echo "panels/maintenance_panel.py" >> "$KS_EXCLUDE"
        fi
    fi
fi
echo "Maintenance panel ...[Done]"

# === Webcam udev rules for stable /dev/webcamN symlinks ===
echo "Installing webcam udev rules..."
WEBCAM_RULE="/etc/udev/rules.d/99-webcam.rules"
WEBCAM_ENUM="/usr/local/bin/webcam-enumerate.sh"
WEBCAM_HOTPLUG="/usr/local/bin/crowsnest-hotplug.sh"
NEED_RELOAD=0

run_privileged ln -sf "$PROJECT_DIR/system/webcam-enumerate.sh" "$WEBCAM_ENUM"
run_privileged chmod +x "$PROJECT_DIR/system/webcam-enumerate.sh"
run_privileged ln -sf "$PROJECT_DIR/system/crowsnest-hotplug.sh" "$WEBCAM_HOTPLUG"
run_privileged chmod +x "$PROJECT_DIR/system/crowsnest-hotplug.sh"
run_privileged ln -sf "$PROJECT_DIR/system/99-webcam.rules" "$WEBCAM_RULE"
run_privileged udevadm control --reload-rules
run_privileged udevadm trigger --subsystem-match=video4linux
echo "Webcam udev symlinks installed"
echo "Webcam udev rules ...[Done]"

# === Bed mesh persist sans reboot (RUN_SHELL_COMMAND -> scripts/save_mesh.py) ===
# Persiste le profil bed_mesh dans le bloc #*# de printer.cfg comme SAVE_CONFIG,
# mais sans redemarrer Klipper. Deploye en symlink pour suivre les mises a jour git.
echo "Installing bed mesh save script..."
MESH_SCRIPTS_DIR="$KLIPPER_CONFIG_DIR/scripts"
mkdir -p "$MESH_SCRIPTS_DIR"
chmod +x "$PROJECT_DIR/scripts/save_mesh.py"
ln -sf "$PROJECT_DIR/scripts/save_mesh.py" "$MESH_SCRIPTS_DIR/save_mesh.py"
echo "Bed mesh save script symlinked -> $MESH_SCRIPTS_DIR/save_mesh.py"
echo "Bed mesh save script ...[Done]"

echo "Installation completed."
