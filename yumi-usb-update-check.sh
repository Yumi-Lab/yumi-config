#!/bin/bash
# yumi-usb-update-check.sh — Offline USB update mechanism for YumiOS
# Scans USB mount point for .yumi-update trigger file and executes update script

LOG="/var/log/yumi-update.log"
USB_DIR="/home/pi/printer_data/gcodes/USB"
TRIGGER="$USB_DIR/.yumi-update"
UPDATE_SCRIPT="$USB_DIR/yumi-update.sh"

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

if [ ! -f "$TRIGGER" ]; then
    log "No .yumi-update trigger found — nothing to do"
    exit 0
fi

log "Trigger .yumi-update found"

if [ ! -f "$UPDATE_SCRIPT" ]; then
    log "ERROR: trigger present but yumi-update.sh not found in $USB_DIR"
    rm -f "$TRIGGER"
    exit 1
fi

if [ ! -x "$UPDATE_SCRIPT" ]; then
    chmod +x "$UPDATE_SCRIPT"
    log "Made yumi-update.sh executable"
fi

log "Executing $UPDATE_SCRIPT ..."
bash "$UPDATE_SCRIPT" >> "$LOG" 2>&1
EXIT_CODE=$?

if [ $EXIT_CODE -eq 0 ]; then
    log "yumi-update.sh completed successfully (exit $EXIT_CODE)"
else
    log "WARNING: yumi-update.sh exited with code $EXIT_CODE"
fi

# Remove trigger — one-shot
rm -f "$TRIGGER"
log "Trigger .yumi-update removed — update cycle complete"
log "=== yumi-usb-update-check finished ==="
