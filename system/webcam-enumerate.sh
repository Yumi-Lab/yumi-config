#!/bin/bash
# Assign stable /dev/webcamN name based on device order
# Called by udev: PROGRAM="/usr/local/bin/webcam-enumerate.sh %k"
# Outputs symlink name to stdout

DEVICE="$1"

# Count existing USB webcams with lower device numbers
N=0
for dev in /sys/class/video4linux/video*; do
    name=$(basename "$dev")
    # Skip metadata devices (index != 0)
    idx=$(cat "$dev/index" 2>/dev/null)
    [ "$idx" != "0" ] && continue
    # Skip non-USB devices
    bus=$(udevadm info -q property "/dev/$name" 2>/dev/null | grep "^ID_BUS=usb$")
    [ -z "$bus" ] && continue
    # If this device comes before ours, increment counter
    if [ "$name" \< "$DEVICE" ]; then
        N=$((N + 1))
    fi
done

echo "webcam${N}"
