#!/usr/bin/env python3
"""
Yumi SVG Renderer — Colore les SVG des boards selon le status hardware.

Charge un SVG template, applique les couleurs des status (detected/open_load/absent)
basees sur les resultats du scanner TMC.

Usage:
    python svg_renderer.py --board motherboard --scan-data scan.json --output board_status.svg
    python svg_renderer.py --board smart_maker --mock 5yms_partial --output smartbox_status.svg
"""

import json
import os
import re
import sys
from pathlib import Path

SVG_DIR = Path(__file__).parent / "svg"

# Mapping connecteur ID -> driver name (pour le scanner)
MOTHERBOARD_MAP = {
    "status_stepper_x": "stepper_x",
    "status_stepper_y": "stepper_y",
    "status_stepper_z": "stepper_z",
    "status_extruder": "extruder",
    "status_e0": "extruder_stepper extruder0",
    "status_e1": "extruder_stepper extruder1",
    "status_mcu": "_mcu_main",
    "status_heater_bed": "_heater_bed",
    "status_probe": "_probe",
}

SMARTBOX_MAP = {
    "status_e2": "extruder_stepper extruder2",
    "status_e3": "extruder_stepper extruder3",
    "status_e4": "extruder_stepper extruder4",
    "status_e5": "extruder_stepper extruder5",
    "status_e6": "extruder_stepper extruder6",
    "status_dryer": "_dryer",
    "status_smartbox_mcu": "_mcu_smartbox",
}

# CSS classes pour les status
STATUS_CLASSES = {
    "detected": "status-detected",
    "open_load": "status-open-load",
    "absent": "status-absent",
    "unknown": "status-unknown",
}


def resolve_status(driver_name, scan_data):
    """
    Determine le status d'un connecteur a partir des donnees scanner.
    Retourne: 'detected', 'open_load', 'absent', 'unknown'
    """
    tmc = scan_data.get("tmc_drivers", {})

    # Cas speciaux (pas des drivers TMC)
    if driver_name == "_mcu_main":
        mcu = scan_data.get("mcu", {}).get("mcu_main")
        return "detected" if mcu else "absent"
    if driver_name == "_mcu_smartbox":
        mcu = scan_data.get("mcu", {}).get("mcu_smartbox")
        return "detected" if mcu else "absent"
    if driver_name == "_heater_bed":
        return "detected"  # Toujours present sur la board
    if driver_name == "_probe":
        return "detected"  # Toujours present sur la board
    if driver_name == "_dryer":
        return "detected" if scan_data.get("smartbox_detected") else "absent"

    # Driver TMC
    if driver_name in tmc:
        info = tmc[driver_name]
        if info.get("present") is True:
            return "detected"
        elif info.get("present") is False:
            if info.get("ola") == 1 and info.get("olb") == 1:
                return "open_load"
            return "absent"
    return "unknown"


def render_svg(board_type, scan_data):
    """
    Charge le SVG template et applique les couleurs de status.
    Retourne le SVG modifie en string.
    """
    if board_type == "motherboard":
        svg_path = SVG_DIR / "yumi_motherboard.svg"
        connector_map = MOTHERBOARD_MAP
    elif board_type == "smart_maker":
        svg_path = SVG_DIR / "smart_maker.svg"
        connector_map = SMARTBOX_MAP
    else:
        raise ValueError(f"Unknown board type: {board_type}")

    with open(svg_path, 'r') as f:
        svg_content = f.read()

    # Pour chaque connecteur, remplacer la classe CSS du cercle status
    for element_id, driver_name in connector_map.items():
        status = resolve_status(driver_name, scan_data)
        css_class = STATUS_CLASSES.get(status, "status-unknown")

        # Remplacer class="status-unknown" dans l'element avec cet id
        pattern = f'id="{element_id}"([^/]*?)class="status-[a-z]+"'
        replacement = f'id="{element_id}"\\1class="{css_class}"'
        svg_content = re.sub(pattern, replacement, svg_content)

    return svg_content


def render_both(scan_data):
    """Rend les 2 SVGs (motherboard + smartbox si detectee)"""
    results = {}
    results["motherboard"] = render_svg("motherboard", scan_data)
    if scan_data.get("smartbox_detected"):
        results["smart_maker"] = render_svg("smart_maker", scan_data)
    return results


if __name__ == "__main__":
    import argparse
    sys.path.insert(0, str(Path(__file__).parent))
    from scanner import mock_scan

    parser = argparse.ArgumentParser(description="Yumi SVG Renderer")
    parser.add_argument("--board", required=True, choices=["motherboard", "smart_maker", "both"])
    parser.add_argument("--mock", default="7yms_uart",
                        choices=["7yms_uart", "2yms_no_smartbox", "7yms_usb", "5yms_partial"])
    parser.add_argument("--scan-data", help="JSON file with scan results")
    parser.add_argument("--output", "-o", default="-", help="Output file (- = stdout)")
    args = parser.parse_args()

    # Load scan data
    if args.scan_data:
        with open(args.scan_data, 'r') as f:
            scan_data = json.load(f)
    else:
        scan_data = mock_scan(args.mock)

    # Render
    if args.board == "both":
        results = render_both(scan_data)
        for name, svg in results.items():
            out_path = f"{name}_status.svg"
            with open(out_path, 'w') as f:
                f.write(svg)
            print(f"Written: {out_path}")
    else:
        svg = render_svg(args.board, scan_data)
        if args.output == "-":
            print(svg)
        else:
            with open(args.output, 'w') as f:
                f.write(svg)
            print(f"Written: {args.output}")
