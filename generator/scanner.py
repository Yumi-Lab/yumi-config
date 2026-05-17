#!/usr/bin/env python3
"""
Yumi Hardware Scanner — Detection MCU, TMC drivers, sensors

Detecte le hardware connecte au firstboot :
- Ports serie MCU (ttyS1, ttyS2, ttyUSB*, serial/by-id)
- Drivers TMC via DRV_STATUS (ola/olb = open load = moteur absent)
- Capteurs filament, thermistances, probe

Output : dict compatible avec engine.resolve_config()
"""

import os
import glob
import json
import subprocess
from pathlib import Path


# ============================================================
# MCU DETECTION
# ============================================================

def scan_serial_ports():
    """Detecte les ports serie disponibles"""
    ports = {}

    # UART ports (ttyS1, ttyS2)
    for uart in ["/dev/ttyS1", "/dev/ttyS2"]:
        if os.path.exists(uart):
            ports[uart] = {"type": "uart", "path": uart}

    # USB serial (ttyUSB*, ttyACM*)
    for pattern in ["/dev/ttyUSB*", "/dev/ttyACM*"]:
        for path in glob.glob(pattern):
            ports[path] = {"type": "usb", "path": path}

    # by-id (more stable)
    by_id_dir = "/dev/serial/by-id/"
    if os.path.isdir(by_id_dir):
        for entry in os.listdir(by_id_dir):
            full_path = os.path.join(by_id_dir, entry)
            real_path = os.path.realpath(full_path)
            ports[full_path] = {"type": "usb_by_id", "path": full_path, "real": real_path}

    return ports


def detect_mcu(ports):
    """
    Determine quel port est le MCU principal et quel port est la smartbox.
    Logique :
      - ttyS1 = MCU principal (UART, Nano board)
      - ttyS2 = Smartbox UART (HyperDrive en UART)
      - USB serial by-id = soit MCU V3.2 soit Smartbox USB
    """
    result = {
        "mcu_main": None,
        "mcu_smartbox": None,
        "connection_mode_smartbox": None,
    }

    # Priorite 1 : ttyS1 = main MCU
    if "/dev/ttyS1" in ports:
        result["mcu_main"] = {"serial": "/dev/ttyS1", "type": "uart"}

    # Priorite 2 : ttyS2 = smartbox UART
    if "/dev/ttyS2" in ports:
        result["mcu_smartbox"] = {"serial": "/dev/ttyS2", "type": "uart"}
        result["connection_mode_smartbox"] = "uart"

    # USB by-id : peut overrider main ou smartbox
    for path, info in ports.items():
        if info["type"] == "usb_by_id":
            if "1a86_USB_Serial" in path:
                # CH340 chip — peut etre main (V3.2) ou smartbox (HyperDrive 2)
                if result["mcu_main"] is None:
                    result["mcu_main"] = {"serial": path, "type": "usb"}
                elif result["mcu_smartbox"] is None:
                    result["mcu_smartbox"] = {"serial": path, "type": "usb"}
                    result["connection_mode_smartbox"] = "usb"

    return result


# ============================================================
# TMC DRIVER DETECTION
# ============================================================

def query_tmc_status(mcu_serial, driver_name):
    """
    Interroge DRV_STATUS d'un driver TMC via Klipper API (moonraker).
    Retourne dict avec ola, olb, cs_actual, etc.

    En mode firstboot sans Klipper running, on utilise le dump direct.
    """
    # Via Moonraker API si disponible
    try:
        import requests
        url = f"http://localhost:7125/printer/objects/query?tmc2209 {driver_name}"
        resp = requests.get(url, timeout=2)
        if resp.status_code == 200:
            data = resp.json()
            tmc_data = data.get("result", {}).get("status", {}).get(f"tmc2209 {driver_name}", {})
            drv_status = tmc_data.get("drv_status", {})
            return {
                "detected": True,
                "ola": drv_status.get("ola", 0),
                "olb": drv_status.get("olb", 0),
                "cs_actual": drv_status.get("cs_actual", 0),
            }
    except Exception:
        pass

    return {"detected": False, "ola": None, "olb": None}


def scan_tmc_drivers(motor_constants):
    """
    Scan tous les drivers TMC pour detecter les moteurs presents.

    Logique:
      - ola=1 AND olb=1 → moteur absent (open load sur les 2 phases)
      - ola=0 AND olb=0 → moteur connecte
      - cs_actual > 0 → moteur sous charge

    Retourne liste de moteurs detectes avec leur position.
    """
    drivers_to_scan = [
        "stepper_x", "stepper_y", "stepper_z",
        "extruder_stepper extruder0", "extruder_stepper extruder1",
        "extruder_stepper extruder2", "extruder_stepper extruder3",
        "extruder_stepper extruder4", "extruder_stepper extruder5",
        "extruder_stepper extruder6",
    ]

    results = {}
    for driver in drivers_to_scan:
        status = query_tmc_status(None, driver)
        if status["detected"]:
            motor_present = not (status["ola"] == 1 and status["olb"] == 1)
            results[driver] = {
                "present": motor_present,
                "ola": status["ola"],
                "olb": status["olb"],
            }
        else:
            results[driver] = {"present": None, "ola": None, "olb": None}

    return results


def identify_motor(resistance, inductance, motor_constants):
    """
    Match R/L mesures avec motor_constants connus.
    Retourne le nom du moteur ou None.
    """
    best_match = None
    best_score = float("inf")

    for name, specs in motor_constants.items():
        r_diff = abs(specs["resistance"] - resistance)
        l_diff = abs(specs["inductance"] - inductance) * 1000  # poids inductance
        score = r_diff + l_diff
        if score < best_score:
            best_score = score
            best_match = name

    # Seuil de tolerance
    if best_score < 0.5:
        return best_match
    return None


# ============================================================
# SENSOR DETECTION
# ============================================================

def scan_sensors():
    """
    Detecte les capteurs connectes.
    En mode firstboot, verifie les GPIO/ADC disponibles.
    """
    sensors = {
        "filament_sensors": [],
        "thermistors": [],
        "probe": None,
    }

    # Sur le hardware reel, on verifie si les pins repondent
    # Pour l'instant, on retourne la config attendue basee sur le hardware detecte
    return sensors


# ============================================================
# ASSEMBLAGE — OUTPUT COMPATIBLE ENGINE
# ============================================================

def scan_all():
    """
    Scan complet du hardware.
    Retourne un dict utilisable par engine.resolve_config().
    """
    ports = scan_serial_ports()
    mcu_info = detect_mcu(ports)

    # Determiner le nombre de YMS par presence des drivers
    motor_constants = {}  # Sera charge depuis products.json
    tmc_results = scan_tmc_drivers(motor_constants)

    # Compter les YMS presents (drivers avec moteur)
    yms_count = 0
    for driver_name, status in tmc_results.items():
        if "extruder" in driver_name and status.get("present"):
            yms_count += 1

    # Si on ne peut pas scanner (pas de Klipper), fallback sur detection port
    if yms_count == 0:
        # Si smartbox detectee, on assume 7 YMS
        if mcu_info["mcu_smartbox"]:
            yms_count = 7
        else:
            yms_count = 2

    result = {
        "ports": ports,
        "mcu": mcu_info,
        "tmc_drivers": tmc_results,
        "yms_count_detected": yms_count,
        "smartbox_detected": mcu_info["mcu_smartbox"] is not None,
        "smartbox_connection": mcu_info.get("connection_mode_smartbox"),
    }

    return result


def scanner_to_engine_params(scan_result):
    """
    Convertit le resultat du scanner en parametres pour engine.generate().
    """
    params = {
        "yms_count": scan_result["yms_count_detected"],
        "mcu_serial": scan_result["mcu"]["mcu_main"]["serial"] if scan_result["mcu"]["mcu_main"] else "/dev/ttyS1",
    }

    if scan_result["smartbox_detected"]:
        params["smartbox_serial"] = scan_result["mcu"]["mcu_smartbox"]["serial"]
        params["smartbox_connection"] = scan_result["smartbox_connection"]

    return params


# ============================================================
# MOCK pour tests sans hardware
# ============================================================

def mock_scan(scenario="7yms_uart"):
    """Simule un scan hardware pour les tests"""
    scenarios = {
        "7yms_uart": {
            "ports": {"/dev/ttyS1": {"type": "uart"}, "/dev/ttyS2": {"type": "uart"}},
            "mcu": {
                "mcu_main": {"serial": "/dev/ttyS1", "type": "uart"},
                "mcu_smartbox": {"serial": "/dev/ttyS2", "type": "uart"},
                "connection_mode_smartbox": "uart",
            },
            "tmc_drivers": {
                "stepper_x": {"present": True, "ola": 0, "olb": 0},
                "stepper_y": {"present": True, "ola": 0, "olb": 0},
                "stepper_z": {"present": True, "ola": 0, "olb": 0},
                "extruder_stepper extruder0": {"present": True, "ola": 0, "olb": 0},
                "extruder_stepper extruder1": {"present": True, "ola": 0, "olb": 0},
                "extruder_stepper extruder2": {"present": True, "ola": 0, "olb": 0},
                "extruder_stepper extruder3": {"present": True, "ola": 0, "olb": 0},
                "extruder_stepper extruder4": {"present": True, "ola": 0, "olb": 0},
                "extruder_stepper extruder5": {"present": True, "ola": 0, "olb": 0},
                "extruder_stepper extruder6": {"present": True, "ola": 0, "olb": 0},
            },
            "yms_count_detected": 7,
            "smartbox_detected": True,
            "smartbox_connection": "uart",
        },
        "2yms_no_smartbox": {
            "ports": {"/dev/ttyS1": {"type": "uart"}},
            "mcu": {
                "mcu_main": {"serial": "/dev/ttyS1", "type": "uart"},
                "mcu_smartbox": None,
                "connection_mode_smartbox": None,
            },
            "tmc_drivers": {
                "stepper_x": {"present": True, "ola": 0, "olb": 0},
                "stepper_y": {"present": True, "ola": 0, "olb": 0},
                "stepper_z": {"present": True, "ola": 0, "olb": 0},
                "extruder_stepper extruder0": {"present": True, "ola": 0, "olb": 0},
                "extruder_stepper extruder1": {"present": True, "ola": 0, "olb": 0},
            },
            "yms_count_detected": 2,
            "smartbox_detected": False,
            "smartbox_connection": None,
        },
        "7yms_usb": {
            "ports": {
                "/dev/ttyS1": {"type": "uart"},
                "/dev/serial/by-id/usb-1a86_USB_Serial-if00-port0": {"type": "usb_by_id"},
            },
            "mcu": {
                "mcu_main": {"serial": "/dev/ttyS1", "type": "uart"},
                "mcu_smartbox": {"serial": "/dev/serial/by-id/usb-1a86_USB_Serial-if00-port0", "type": "usb"},
                "connection_mode_smartbox": "usb",
            },
            "tmc_drivers": {
                "stepper_x": {"present": True, "ola": 0, "olb": 0},
                "stepper_y": {"present": True, "ola": 0, "olb": 0},
                "stepper_z": {"present": True, "ola": 0, "olb": 0},
                "extruder_stepper extruder0": {"present": True, "ola": 0, "olb": 0},
                "extruder_stepper extruder1": {"present": True, "ola": 0, "olb": 0},
                "extruder_stepper extruder2": {"present": True, "ola": 0, "olb": 0},
                "extruder_stepper extruder3": {"present": True, "ola": 0, "olb": 0},
                "extruder_stepper extruder4": {"present": True, "ola": 0, "olb": 0},
                "extruder_stepper extruder5": {"present": True, "ola": 0, "olb": 0},
                "extruder_stepper extruder6": {"present": True, "ola": 0, "olb": 0},
            },
            "yms_count_detected": 7,
            "smartbox_detected": True,
            "smartbox_connection": "usb",
        },
        "5yms_partial": {
            "ports": {"/dev/ttyS1": {"type": "uart"}, "/dev/ttyS2": {"type": "uart"}},
            "mcu": {
                "mcu_main": {"serial": "/dev/ttyS1", "type": "uart"},
                "mcu_smartbox": {"serial": "/dev/ttyS2", "type": "uart"},
                "connection_mode_smartbox": "uart",
            },
            "tmc_drivers": {
                "stepper_x": {"present": True, "ola": 0, "olb": 0},
                "stepper_y": {"present": True, "ola": 0, "olb": 0},
                "stepper_z": {"present": True, "ola": 0, "olb": 0},
                "extruder_stepper extruder0": {"present": True, "ola": 0, "olb": 0},
                "extruder_stepper extruder1": {"present": True, "ola": 0, "olb": 0},
                "extruder_stepper extruder2": {"present": True, "ola": 0, "olb": 0},
                "extruder_stepper extruder3": {"present": True, "ola": 0, "olb": 0},
                "extruder_stepper extruder4": {"present": True, "ola": 0, "olb": 0},
                "extruder_stepper extruder5": {"present": False, "ola": 1, "olb": 1},
                "extruder_stepper extruder6": {"present": False, "ola": 1, "olb": 1},
            },
            "yms_count_detected": 5,
            "smartbox_detected": True,
            "smartbox_connection": "uart",
        },
    }

    return scenarios.get(scenario, scenarios["7yms_uart"])


# ============================================================
# CLI
# ============================================================

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Yumi Hardware Scanner")
    parser.add_argument("--mock", default=None,
                        choices=["7yms_uart", "2yms_no_smartbox", "7yms_usb", "5yms_partial"],
                        help="Use mock data instead of real hardware scan")
    parser.add_argument("--json", action="store_true", help="Output raw JSON")
    args = parser.parse_args()

    if args.mock:
        result = mock_scan(args.mock)
    else:
        result = scan_all()

    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(f"MCU Main: {result['mcu']['mcu_main']}")
        print(f"Smartbox: {result['mcu']['mcu_smartbox']}")
        print(f"Connection: {result.get('smartbox_connection', 'none')}")
        print(f"YMS detected: {result['yms_count_detected']}")
        print()
        print("TMC Drivers:")
        for name, status in result.get("tmc_drivers", {}).items():
            present = "OK" if status.get("present") else "ABSENT" if status.get("present") is False else "?"
            ola = status.get("ola", "?")
            olb = status.get("olb", "?")
            print(f"  {name}: {present} (ola={ola}, olb={olb})")

    # Output engine params
    params = scanner_to_engine_params(result)
    print(f"\nEngine params: {json.dumps(params)}")
