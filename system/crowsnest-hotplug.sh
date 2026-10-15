#!/bin/bash
# Restart crowsnest after USB webcam hotplug (called by udev, 99-webcam.rules).
#
# crowsnest only looks for cameras when it starts, so it must be restarted once the
# camera is back. The restart is handed to systemd with a 3 s delay (device settle):
# udev kills whatever a RUN program leaves in the background when the event is done,
# so the former "( sleep 3; systemctl restart ) &" could die before restarting -- seen
# 15/10 on the HyperDrive bench, camera replugged and never picked up until a reboot.
# One unit name: an event arriving while a restart is already scheduled is absorbed by
# it (systemd-run refuses the duplicate), the scheduled restart still runs after it.
exec /usr/bin/systemd-run --no-block --collect --quiet --unit=crowsnest-hotplug \
    --on-active=3 /usr/bin/systemctl restart crowsnest.service
