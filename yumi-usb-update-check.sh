#!/bin/bash
# yumi-usb-update-check.sh — Offline USB update mechanism for YumiOS
# Scans USB mount point for yumi-update.zip, extracts and executes yumi-update.sh

LOG="/var/log/yumi-update.log"
USB_DIR="/home/pi/printer_data/gcodes/USB"
UPDATE_ZIP="$USB_DIR/yumi-update.zip"
WORK_DIR="/tmp/yumi-update"

log() {
    echo "$(date '+%Y-%m-%d %H:%M:%S') $1" >> "$LOG"
}

log "=== yumi-usb-update-check started ==="

# Wait for USB mount (up to 30s)
for i in $(seq 1 30); do
    if [ -d "$USB_DIR" ]; then
        break
    fi
    sleep 1
done

if [ ! -d "$USB_DIR" ]; then
    log "USB directory not found: $USB_DIR — skipping"
    exit 0
fi

if [ ! -f "$UPDATE_ZIP" ]; then
    log "No yumi-update.zip found — nothing to do"
    exit 0
fi

log "yumi-update.zip found — extracting..."

# Clean and create work directory
rm -rf "$WORK_DIR"
mkdir -p "$WORK_DIR"

unzip -o "$UPDATE_ZIP" -d "$WORK_DIR" >> "$LOG" 2>&1
if [ $? -ne 0 ]; then
    log "ERROR: failed to extract yumi-update.zip"
    rm -rf "$WORK_DIR"
    exit 1
fi

UPDATE_SCRIPT="$WORK_DIR/yumi-update.sh"

if [ ! -f "$UPDATE_SCRIPT" ]; then
    log "ERROR: yumi-update.sh not found inside zip"
    rm -rf "$WORK_DIR"
    exit 1
fi

chmod +x "$UPDATE_SCRIPT"
log "Executing yumi-update.sh ..."
bash "$UPDATE_SCRIPT" >> "$LOG" 2>&1
EXIT_CODE=$?

if [ $EXIT_CODE -eq 0 ]; then
    log "yumi-update.sh completed successfully (exit $EXIT_CODE)"
else
    log "WARNING: yumi-update.sh exited with code $EXIT_CODE"
fi

# Cleanup — one-shot
rm -f "$UPDATE_ZIP"
rm -rf "$WORK_DIR"
log "yumi-update.zip removed — update cycle complete"
log "=== yumi-usb-update-check finished ==="
