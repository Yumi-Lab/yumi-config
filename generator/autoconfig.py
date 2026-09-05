#!/usr/bin/env python3
"""
autoconfig — one shot on the pad: stop Klipper, scan the boards, compose printer.cfg, start Klipper.

    autoconfig.py [--boot] [--dry-run] [--factory] [--minimal] [PORT ...]

At boot, yumi-autoconfig.service (Before=klipper.service) runs this with --boot: Klipper is
not started yet, the ports are free, no service is touched — the scan happens once, and
compose.py leaves printer.cfg alone when the boards have not changed. Run by hand while
Klipper is up, it stops Klipper through Moonraker's API (the pi user has no passwordless sudo
on the pads; systemctl as a fallback) and starts it again afterwards. The ports probed are
the UART ports the catalog knows (every `serial` of its components: RJ11 main board,
smartbox) plus whatever yumi-scan finds on USB. Prints compose's JSON summary; exit code =
compose's.
"""
import argparse
import json
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import generator  # noqa: E402

MOONRAKER = "http://127.0.0.1:7125"
KLIPPER_SERVICE = "klipper"


def catalog_uart_ports(catalog):
    """Every fixed serial port declared in the catalog (main board RJ11, smartbox...)."""
    ports = []
    for comp in catalog["components"].values():
        if not isinstance(comp, dict):
            continue
        for key in ("mcu", "smartbox"):
            serial = (comp.get(key) or {}).get("serial")
            if serial and serial.startswith("/dev/ttyS") and serial not in ports:
                ports.append(serial)
    return ports


def moonraker(method, path):
    req = urllib.request.Request(MOONRAKER + path, method=method)
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.load(r)


def service(action):
    """stop/start Klipper via Moonraker, else systemctl (works when run as root)."""
    try:
        moonraker("POST", "/machine/services/%s?service=%s" % (action, KLIPPER_SERVICE))
        return "moonraker"
    except Exception:
        subprocess.run(["systemctl", action, KLIPPER_SERVICE], check=False)
        return "systemctl"


def klipper_active():
    return subprocess.run(["systemctl", "is-active", "--quiet", KLIPPER_SERVICE]).returncode == 0


def main():
    ap = argparse.ArgumentParser(description="Detect the boards and install the matching printer.cfg")
    ap.add_argument("ports", nargs="*", help="extra serial ports to probe")
    ap.add_argument("--boot", action="store_true",
                    help="boot mode (yumi-autoconfig.service): Klipper is not running, touch no service")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--factory", action="store_true")
    ap.add_argument("--minimal", action="store_true")
    a = ap.parse_args()

    catalog = generator.load_catalog()
    ports = catalog_uart_ports(catalog) + [p for p in a.ports if p not in catalog_uart_ports(catalog)]

    manage = not a.boot and klipper_active()
    how = service("stop") if manage else "none (klipper not running)"
    if manage:
        time.sleep(2)  # let klippy release the ports
    try:
        detect = subprocess.run([sys.executable, str(HERE / "yumi-detect.py"), *ports],
                                capture_output=True, text=True, timeout=240)
        sys.stderr.write(detect.stdout + detect.stderr)
        compose_cmd = [sys.executable, str(HERE / "compose.py")]
        for flag in ("dry_run", "factory", "minimal"):
            if getattr(a, flag):
                compose_cmd.append("--" + flag.replace("_", "-"))
        comp = subprocess.run(compose_cmd, capture_output=True, text=True, timeout=120)
        sys.stderr.write(comp.stderr)
        print(comp.stdout.strip())
        code = comp.returncode
    finally:
        if manage:
            service("start")
    sys.stderr.write("klipper stopped/started via %s\n" % how)
    return code


if __name__ == "__main__":
    sys.exit(main())
