#!/bin/bash
# Restart crowsnest after USB webcam hotplug
# Called by udev — runs in background with delay to let device stabilize

(
    sleep 3
    systemctl restart crowsnest
) &
