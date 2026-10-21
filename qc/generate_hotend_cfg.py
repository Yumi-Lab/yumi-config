#!/usr/bin/env python3
"""
Generates the hot end bench cfgs (qc_printer_X12.cfg, qc_printer_DD.cfg) from qc_printer_C235.cfg.

Bench = a C-series board alone, one print head plugged at a time: no axes (kinematics none), only
what a head carries (heater, thermistor, hotend fan, part fan, head switch, extruder motor for a
DD head).
The C235 cfg stays the single source of the pins: its sections are copied verbatim. The two
files differ only by the model of the _QC_MODE marker (the panel button); the test sequences
live in qc_engine.HOTEND_TESTS.

Usage:  python3 generate_hotend_cfg.py   (from yumi-config/qc/)
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from generate_qc_cfg import parse_sections
from qc_engine import HOTEND_DEVICES

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, "qc_printer_C235.cfg")

# Copied verbatim from the C235 cfg, in this order.
C235_SECTIONS = [
    "idle_timeout",
    "mcu",
    "thermistor 100K4190YUMI",
    "extruder",
    "verify_heater extruder",
    "fan",
    "fan_generic hotend_fan",
    "filament_switch_sensor head_sensor",
    "gcode_macro QC_FAN_HOTEND",
    "gcode_macro QC_FAN_PART",
]

HEADER = """\
#####################################################################
# YUMI QC - hot end bench (%(model)s): one print head at a time
# GENERATED from qc_printer_C235.cfg by qc/generate_hotend_cfg.py - do not edit.
#####################################################################

[gcode_macro _QC_MODE]
description: QC mode marker
variable_active: 1
variable_model: "%(model)s"
gcode:
    RESPOND MSG="QC mode active"

[respond]

# Not used by the bench: KlipperScreen pops "Missing Klipper configuration" without them.
[virtual_sdcard]
path: ~/printer_data/gcodes

[pause_resume]

[display_status]

[printer]
kinematics: none
max_velocity: 1
max_accel: 1

"""

MACROS = """\
[gcode_macro QC_HOTEND_HEAT_START]
description: QC hot end - hotend fan on, start heating (the fan checks run while it heats)
gcode:
    RESPOND MSG="QC:HEAT_START:START"
    SET_FAN_SPEED FAN=hotend_fan SPEED=1.0
    M104 S{params.TEMP}
    RESPOND MSG="QC:HEAT_START:PASS"

[gcode_macro QC_HOTEND_SWITCH]
description: QC hot end - the head switch is free, then triggers within WAIT s when a filament is pushed in
variable_left: 0
gcode:
    RESPOND MSG="QC:HEAD_SWITCH:START"
    {% if printer["filament_switch_sensor head_sensor"].filament_detected %}
        RESPOND TYPE=error MSG="QC HEAD_SWITCH: switch triggered before any filament (stuck switch)"
        RESPOND MSG="QC:HEAD_SWITCH:FAIL"
    {% else %}
        SET_GCODE_VARIABLE MACRO=QC_HOTEND_SWITCH VARIABLE=left VALUE={params.WAIT|int * 2}
        UPDATE_DELAYED_GCODE ID=_qc_hotend_switch DURATION=0.5
    {% endif %}

[delayed_gcode _qc_hotend_switch]
gcode:
    {% set left = printer["gcode_macro QC_HOTEND_SWITCH"].left|int %}
    {% if printer["filament_switch_sensor head_sensor"].filament_detected %}
        RESPOND MSG="QC:HEAD_SWITCH:PASS"
    {% elif left <= 0 %}
        RESPOND TYPE=error MSG="QC HEAD_SWITCH: no trigger while waiting for the filament"
        RESPOND MSG="QC:HEAD_SWITCH:FAIL"
    {% else %}
        SET_GCODE_VARIABLE MACRO=QC_HOTEND_SWITCH VARIABLE=left VALUE={left - 1}
        UPDATE_DELAYED_GCODE ID=_qc_hotend_switch DURATION=0.5
    {% endif %}

[gcode_macro QC_HOTEND_TEMP]
description: QC hot end - M109 returns once the PID has settled (within 1 C, slope under 0.1 C/s)
gcode:
    RESPOND MSG="QC:HOTEND_TEMP:START"
    M109 S{params.TEMP}
    RESPOND MSG="QC:HOTEND_TEMP:VISUAL"

[gcode_macro QC_HOTEND_EXTRUDE]
description: QC DD head - extrude MM mm at SPEED mm/s through the hot nozzle (extruder motor check)
gcode:
    RESPOND MSG="QC:HOTEND_EXTRUDE:START"
    M83
    G1 E{params.MM} F{params.SPEED|float * 60}
    M400
    RESPOND MSG="QC:HOTEND_EXTRUDE:VISUAL"

[gcode_macro QC_CLEANUP]
description: QC hot end - heater, part fan and motor off. The hotend fan keeps cooling the head
gcode:
    RESPOND MSG="QC:CLEANUP:START"
    UPDATE_DELAYED_GCODE ID=_qc_hotend_switch DURATION=0
    M104 S0
    M106 S0
    M84
    RESPOND MSG="QC:CLEANUP:PASS"
"""


def _section(text):
    """A section without the trailing blank and comment lines (they introduce the next one)."""
    lines = text.splitlines()
    while lines and (not lines[-1].strip() or lines[-1].lstrip().startswith("#")):
        lines.pop()
    return "\n".join(lines) + "\n\n"


def build(model):
    with open(SRC) as f:
        sections = dict(parse_sections(f.read()))
    missing = [name for name in C235_SECTIONS if name not in sections]
    if missing:
        raise SystemExit("Missing in qc_printer_C235.cfg: %s" % ", ".join(missing))
    return HEADER % {"model": model} + "".join(_section(sections[n]) for n in C235_SECTIONS) + MACROS


def main():
    for model in HOTEND_DEVICES:
        with open(os.path.join(HERE, "qc_printer_%s.cfg" % model), "w") as f:
            f.write(build(model))


if __name__ == "__main__":
    main()
