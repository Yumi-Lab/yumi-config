#!/usr/bin/env python3
"""
Generates the hot end bench cfgs (qc_printer_X12.cfg, qc_printer_DD.cfg) from qc_printer_C235.cfg.

Bench = a C-series board, one print head plugged at a time: no axes (kinematics none), only what
a head carries (heater, thermistor, hotend fan, part fan, extruder motor for a DD head).
A head whose sequence checks the head switch (X12) also gets the switch and the bench's YMS-3
feeder (qc_engine.HOTEND_FEEDER, HyperDrive slot 1: a HyperDrive must stay plugged on the bench).
The C235 and HyperDrive cfgs stay the single source of the pins: their sections are copied
verbatim. The test sequences live in qc_engine.HOTEND_TESTS.

Usage:  python3 generate_hotend_cfg.py   (from yumi-config/qc/)
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from generate_qc_cfg import parse_sections
from qc_engine import HOTEND_DEVICES, HOTEND_FEEDER, HOTEND_TESTS

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, "qc_printer_C235.cfg")
HD_SRC = os.path.join(HERE, "qc_printer_HYPERDRIVE.cfg")

# Copied verbatim from the C235 cfg, in this order.
C235_SECTIONS = [
    "idle_timeout",
    "mcu",
    "thermistor 100K4190YUMI",
    "extruder",
    "verify_heater extruder",
    "fan",
    "fan_generic hotend_fan",
    "gcode_macro QC_FAN_HOTEND",
    "gcode_macro QC_FAN_PART",
]
# Head switch check (qc_engine head_switch test): switch from the C235, YMS-3 from the HyperDrive.
SWITCH_C235_SECTIONS = ["filament_switch_sensor head_sensor"]
SWITCH_HD_SECTIONS = ["mcu hyperdrive_uart", "extruder_stepper " + HOTEND_FEEDER,
                      "tmc2209 extruder_stepper " + HOTEND_FEEDER]

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
%(limits)s

"""

MACROS = """\
[gcode_macro QC_HOTEND_HEAT_START]
description: QC hot end - hotend fan on, start heating (the fan checks run while it heats)
gcode:
    RESPOND MSG="QC:HEAT_START:START"
    SET_FAN_SPEED FAN=hotend_fan SPEED=1.0
    M104 S{params.TEMP}
    RESPOND MSG="QC:HEAT_START:PASS"

[gcode_macro QC_HOTEND_TEMP]
description: QC hot end - M109 returns once the PID has settled (within 1 C, slope under 0.1 C/s)
gcode:
    RESPOND MSG="QC:HOTEND_TEMP:START"
    M109 S{params.TEMP}
    RESPOND MSG="QC:HOTEND_TEMP:VISUAL"

[gcode_macro QC_HOTEND_EXTRUDE]
description: QC hot end - extrude MM mm at SPEED mm/s through the hot nozzle: the head motor (DD), or FEEDER synced to it (X12, YMS-3)
gcode:
    RESPOND MSG="QC:HOTEND_EXTRUDE:START"
    {% if params.FEEDER %}
        SYNC_EXTRUDER_MOTION EXTRUDER={params.FEEDER} MOTION_QUEUE=extruder
    {% endif %}
    M83
    G1 E{params.MM} F{params.SPEED|float * 60}
    M400
    {% if params.FEEDER %}
        SYNC_EXTRUDER_MOTION EXTRUDER={params.FEEDER} MOTION_QUEUE=
    {% endif %}
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


SWITCH_MACROS = """\
[gcode_macro QC_HOTEND_SWITCH]
description: QC hot end - the head switch is free, then FEEDER (YMS-3) feeds until it triggers (MAXD mm at most)
variable_pushed: 0
variable_maxd: 0
variable_speed: 0
variable_feeder: ""
variable_chunk: 25
gcode:
    RESPOND MSG="QC:HEAD_SWITCH:START"
    {% if printer["filament_switch_sensor head_sensor"].filament_detected %}
        RESPOND TYPE=error MSG="QC HEAD_SWITCH: switch triggered before the feed (stuck switch or filament left in the head)"
        RESPOND MSG="QC:HEAD_SWITCH:FAIL"
    {% else %}
        SET_GCODE_VARIABLE MACRO=QC_HOTEND_SWITCH VARIABLE=pushed VALUE=0
        SET_GCODE_VARIABLE MACRO=QC_HOTEND_SWITCH VARIABLE=maxd VALUE={params.MAXD|int}
        SET_GCODE_VARIABLE MACRO=QC_HOTEND_SWITCH VARIABLE=speed VALUE={params.SPEED|float}
        SET_GCODE_VARIABLE MACRO=QC_HOTEND_SWITCH VARIABLE=feeder VALUE="'{params.FEEDER}'"
        UPDATE_DELAYED_GCODE ID=_qc_hotend_switch DURATION=0.1
    {% endif %}

# One chunk per step, the switch read between chunks (same loop as the machine _qc_head_step).
# M83 and the sync are set again in each step: they do not persist across delayed_gcode runs.
# No retract: the operator cuts the filament by hand.
[delayed_gcode _qc_hotend_switch]
gcode:
    {% set v = printer["gcode_macro QC_HOTEND_SWITCH"] %}
    {% set pushed = v.pushed|int %}
    {% if printer["filament_switch_sensor head_sensor"].filament_detected %}
        SYNC_EXTRUDER_MOTION EXTRUDER={v.feeder} MOTION_QUEUE=
        {action_respond_info("QC HEAD_SWITCH: filament at the head switch after %dmm" % pushed)}
        RESPOND MSG="QC:HEAD_SWITCH:PASS"
    {% elif pushed >= v.maxd|int %}
        SYNC_EXTRUDER_MOTION EXTRUDER={v.feeder} MOTION_QUEUE=
        RESPOND TYPE=error MSG="QC HEAD_SWITCH: no trigger after {pushed}mm of feed (switch, head cable or YMS-3)"
        RESPOND MSG="QC:HEAD_SWITCH:FAIL"
    {% else %}
        M83
        SYNC_EXTRUDER_MOTION EXTRUDER={v.feeder} MOTION_QUEUE=extruder
        G1 E{v.chunk|int} F{v.speed|float * 60}
        M400
        SET_GCODE_VARIABLE MACRO=QC_HOTEND_SWITCH VARIABLE=pushed VALUE={pushed + v.chunk|int}
        UPDATE_DELAYED_GCODE ID=_qc_hotend_switch DURATION=0.1
    {% endif %}

[gcode_macro QC_HOTEND_CUT]
description: QC hot end - the operator cuts the filament; the answer runs QC_HOTEND_RETRACT
gcode:
    RESPOND MSG="QC:FILAMENT_CUT:START"
    RESPOND MSG="QC:FILAMENT_CUT:VISUAL"

[gcode_macro QC_HOTEND_RETRACT]
description: QC hot end - FEEDER (YMS-3) pulls the cut filament back MM mm
gcode:
    M83
    SYNC_EXTRUDER_MOTION EXTRUDER={params.FEEDER} MOTION_QUEUE=extruder
    G1 E-{params.MM|float} F{params.SPEED|float * 60}
    M400
    SYNC_EXTRUDER_MOTION EXTRUDER={params.FEEDER} MOTION_QUEUE=

"""


def _section(text):
    """A section without the trailing blank and comment lines (they introduce the next one)."""
    lines = text.splitlines()
    while lines and (not lines[-1].strip() or lines[-1].lstrip().startswith("#")):
        lines.pop()
    return "\n".join(lines) + "\n\n"


def _sections(path, names):
    with open(path) as f:
        sections = dict(parse_sections(f.read()))
    missing = [n for n in names if n not in sections]
    if missing:
        raise SystemExit("Missing in %s: %s" % (os.path.basename(path), ", ".join(missing)))
    return [sections[n] for n in names]


def _copy(path, names):
    return "".join(_section(text) for text in _sections(path, names))


def _printer_limits():
    """The C235 speed and acceleration limits. Klipper derives the extrude-only acceleration from
    max_accel: a placeholder 1 made every 25 mm feed step last ~20 s (21/10)."""
    printer = _sections(SRC, ["printer"])[0].splitlines()
    return "\n".join(l for l in printer if l.split(":")[0].strip() in ("max_velocity", "max_accel"))


def build(model):
    cfg = HEADER % {"model": model, "limits": _printer_limits()} + _copy(SRC, C235_SECTIONS)
    if any(t["id"] == "head_switch" for t in HOTEND_TESTS[model]):
        cfg += _copy(SRC, SWITCH_C235_SECTIONS) + _copy(HD_SRC, SWITCH_HD_SECTIONS) + SWITCH_MACROS
    return cfg + MACROS


def main():
    for model in HOTEND_DEVICES:
        with open(os.path.join(HERE, "qc_printer_%s.cfg" % model), "w") as f:
            f.write(build(model))


if __name__ == "__main__":
    main()
