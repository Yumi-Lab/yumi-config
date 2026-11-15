#!/bin/bash
# Name a USB camera /dev/webcamN from its physical USB port, so a camera keeps
# its number across boots as long as it stays plugged into the same port.
# Called by udev: PROGRAM="/usr/local/bin/webcam-enumerate.sh %k"
# Outputs the symlink name to stdout.
#
# The rank used to come from the /dev/videoN numbers, which follow the kernel
# probe order: two cameras swapped between boots, and both got webcam0 when
# they were probed together (webcam1 was then never created).
# The USB devices are enumerated long before the video driver loads, so the
# list of camera ports is already complete when this runs.
# ponytail: a camera hot-plugged on a lower port than a running one does not
# rename the running one until the next boot ("udevadm trigger" would).

USB_VIDEO_CONTROL="0e01"  # bInterfaceClass + bInterfaceSubClass

own=$(basename "$(readlink -f "/sys/class/video4linux/$1/device")")
if [ ! -e "/sys/bus/usb/devices/$own" ]; then
    echo "webcam-enumerate: $1 is not on a USB port" >&2
    exit 1
fi

N=0
for iface in /sys/bus/usb/devices/*:*; do
    kind="$(cat "$iface/bInterfaceClass" 2>/dev/null)$(cat "$iface/bInterfaceSubClass" 2>/dev/null)"
    [ "$kind" = "$USB_VIDEO_CONTROL" ] || continue
    if [[ "$(basename "$iface")" < "$own" ]]; then
        N=$((N + 1))
    fi
done

echo "webcam${N}"
