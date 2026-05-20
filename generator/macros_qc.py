"""
Yumi QC Macros Renderer — generates quality control macros
adapted to the actual hardware configuration (YMS count, probe type, etc.)

Protocol: RESPOND MSG="QC:{test_id}:{STATUS}"
STATUS: START, PASS, FAIL, VISUAL (waiting for operator confirmation)
"""


def render_qc_macros(cfg):
    """Generate all QC macros adapted to the hardware config"""
    output = []
    yms_count = cfg.get("_yms_count", 2)
    slots = cfg.get("_yms_slots", [])
    probe_type = cfg.get("probe", {}).get("type", "pressure")

    output.append("####################################################################")
    output.append("# YUMI QC — Quality Control Macros")
    output.append("# RESPOND MSG=\"QC:{test_id}:{STATUS}\"")
    output.append("# STATUS: START, PASS, FAIL, VISUAL")
    output.append("####################################################################")
    output.append("")

    # === HOMING ===
    output.append(_qc_home_x())
    output.append(_qc_home_y())
    output.append(_qc_home_z())

    # === MOTOR DIRECTION ===
    output.append(_qc_motor_dir("X", 50, 3000))
    output.append(_qc_motor_dir("Y", 50, 3000))
    output.append(_qc_motor_dir_z())

    # === FULL TRAVEL ===
    output.append(_qc_travel("X", 3000))
    output.append(_qc_travel("Y", 3000))
    output.append(_qc_travel_z())

    # === FANS ===
    output.append(_qc_fan_part())
    output.append(_qc_fan_hotend())
    if cfg.get("mcu_smartbox", {}).get("enabled", False) or "_hyperdrive_mcu_name" in cfg:
        output.append(_qc_fan_smartbox())
    output.append(_qc_fan_stop())

    # === PROBE ===
    if probe_type == "pressure":
        output.append(_qc_probe_pressure())
    else:
        output.append(_qc_probe_bltouch())

    # === TEMPERATURE ===
    output.append(_qc_heat_extruder())
    output.append(_qc_heat_bed())

    # === YMS TESTS — adapted to actual count ===
    for i in range(yms_count):
        slot = slots[i] if i < len(slots) else {}
        sensor_name = slot.get("sensor_name", f"YMS-{i+1}")
        extruder_name = slot.get("name", f"extruder{i}")
        output.append(_qc_yms_motor(i, extruder_name, sensor_name))

    output.append(_qc_yms_sensor_check(yms_count, slots))

    # === PID ===
    output.append(_qc_pid_extruder())
    output.append(_qc_pid_bed())

    # === BED MESH ===
    output.append(_qc_bed_mesh())

    # === CLEANUP + SAVE ===
    output.append(_qc_cleanup())
    output.append(_qc_save())

    return "\n".join(output)


# ============================================================
# HOMING
# ============================================================

def _qc_home_x():
    return """[gcode_macro QC_HOME_X]
description: QC - Home X axis
gcode:
    RESPOND MSG="QC:HOME_X:START"
    G28 X
    {% if "x" in printer.toolhead.homed_axes %}
        RESPOND MSG="QC:HOME_X:PASS"
    {% else %}
        RESPOND MSG="QC:HOME_X:FAIL"
    {% endif %}
"""


def _qc_home_y():
    return """[gcode_macro QC_HOME_Y]
description: QC - Home Y axis
gcode:
    RESPOND MSG="QC:HOME_Y:START"
    G28 Y
    {% if "y" in printer.toolhead.homed_axes %}
        RESPOND MSG="QC:HOME_Y:PASS"
    {% else %}
        RESPOND MSG="QC:HOME_Y:FAIL"
    {% endif %}
"""


def _qc_home_z():
    return """[gcode_macro QC_HOME_Z]
description: QC - Home Z axis (requires X and Y homed first)
gcode:
    RESPOND MSG="QC:HOME_Z:START"
    {% if "xy" in printer.toolhead.homed_axes %}
        G28 Z
        {% if "z" in printer.toolhead.homed_axes %}
            RESPOND MSG="QC:HOME_Z:PASS"
        {% else %}
            RESPOND MSG="QC:HOME_Z:FAIL"
        {% endif %}
    {% else %}
        RESPOND MSG="QC:HOME_Z:FAIL"
        {action_respond_info("QC: X and Y must be homed before Z")}
    {% endif %}
"""


# ============================================================
# MOTOR DIRECTION
# ============================================================

def _qc_motor_dir(axis, distance, speed):
    axis_upper = axis.upper()
    axis_lower = axis.lower()
    return f"""[gcode_macro QC_MOTOR_DIR_{axis_upper}]
description: QC - Move {axis_upper} +{distance}mm for direction check
gcode:
    RESPOND MSG="QC:MOTOR_DIR_{axis_upper}:START"
    G28 {axis_upper}
    G90
    G1 {axis_upper}{distance} F{speed}
    RESPOND MSG="QC:MOTOR_DIR_{axis_upper}:VISUAL"
"""


def _qc_motor_dir_z():
    return """[gcode_macro QC_MOTOR_DIR_Z]
description: QC - Move Z +30mm for direction check
gcode:
    RESPOND MSG="QC:MOTOR_DIR_Z:START"
    {% if "xy" in printer.toolhead.homed_axes %}
        G28 Z
    {% else %}
        G28
    {% endif %}
    G90
    G1 Z30 F600
    RESPOND MSG="QC:MOTOR_DIR_Z:VISUAL"
"""


# ============================================================
# FULL TRAVEL
# ============================================================

def _qc_travel(axis, speed):
    axis_upper = axis.upper()
    return f"""[gcode_macro QC_TRAVEL_{axis_upper}]
description: QC - Move {axis_upper} to max position
gcode:
    RESPOND MSG="QC:TRAVEL_{axis_upper}:START"
    G28 {axis_upper}
    G90
    {{% set max = printer.toolhead.axis_maximum.{axis_upper.lower()}|float %}}
    G1 {axis_upper}{{max - 5}} F{speed}
    G1 {axis_upper}5 F{speed}
    RESPOND MSG="QC:TRAVEL_{axis_upper}:VISUAL"
"""


def _qc_travel_z():
    return """[gcode_macro QC_TRAVEL_Z]
description: QC - Move Z full travel
gcode:
    RESPOND MSG="QC:TRAVEL_Z:START"
    {% if "xyz" not in printer.toolhead.homed_axes %}
        G28
    {% endif %}
    G90
    {% set z_max = printer.toolhead.axis_maximum.z|float %}
    G1 Z{z_max - 10} F600
    G1 Z5 F600
    RESPOND MSG="QC:TRAVEL_Z:VISUAL"
"""


# ============================================================
# FANS
# ============================================================

def _qc_fan_part():
    return """[gcode_macro QC_FAN_PART]
description: QC - Turn on part cooling fan
gcode:
    RESPOND MSG="QC:FAN_PART:START"
    M106 S255
    G4 P2000
    RESPOND MSG="QC:FAN_PART:VISUAL"
"""


def _qc_fan_hotend():
    return """[gcode_macro QC_FAN_HOTEND]
description: QC - Turn on hotend fan (heats to trigger temp)
gcode:
    RESPOND MSG="QC:FAN_HOTEND:START"
    M104 S55
    G4 P15000
    RESPOND MSG="QC:FAN_HOTEND:VISUAL"
"""


def _qc_fan_smartbox():
    return """[gcode_macro QC_FAN_SMARTBOX]
description: QC - Check smartbox fan spins
gcode:
    RESPOND MSG="QC:FAN_SMARTBOX:START"
    G4 P3000
    RESPOND MSG="QC:FAN_SMARTBOX:VISUAL"
"""


def _qc_fan_stop():
    return """[gcode_macro QC_FAN_STOP]
description: QC - Stop all fans and heaters
gcode:
    M106 S0
    M104 S0
    M140 S0
"""


# ============================================================
# PROBE
# ============================================================

def _qc_probe_pressure():
    return """[gcode_macro QC_PROBE_CHECK]
description: QC - Probe pressure test
gcode:
    RESPOND MSG="QC:PROBE_CHECK:START"
    {% if "xyz" not in printer.toolhead.homed_axes %}
        G28
    {% endif %}
    G90
    {% set x = printer.configfile.settings.yumi_z_offset_calculator.pressure_switch_x|default(35) %}
    {% set y = printer.configfile.settings.yumi_z_offset_calculator.pressure_switch_y|default(185) %}
    G1 X{x} Y{y} F3000
    G1 Z10 F600
    PROBE
    RESPOND MSG="QC:PROBE_CHECK:PASS"
"""


def _qc_probe_bltouch():
    return """[gcode_macro QC_PROBE_CHECK]
description: QC - BLTouch deploy/retract test
gcode:
    RESPOND MSG="QC:PROBE_CHECK:START"
    BLTOUCH_DEBUG COMMAND=pin_down
    G4 P1000
    BLTOUCH_DEBUG COMMAND=pin_up
    G4 P1000
    QUERY_PROBE
    RESPOND MSG="QC:PROBE_CHECK:VISUAL"
"""


# ============================================================
# TEMPERATURE
# ============================================================

def _qc_heat_extruder():
    return """[gcode_macro QC_HEAT_EXTRUDER]
description: QC - Heat extruder to 200C and verify
gcode:
    RESPOND MSG="QC:HEAT_EXTRUDER:START"
    M104 S200
    TEMPERATURE_WAIT SENSOR=extruder MINIMUM=195 MAXIMUM=210
    RESPOND MSG="QC:HEAT_EXTRUDER:PASS"
    M104 S0
"""


def _qc_heat_bed():
    return """[gcode_macro QC_HEAT_BED]
description: QC - Heat bed to 60C and verify
gcode:
    RESPOND MSG="QC:HEAT_BED:START"
    M140 S60
    TEMPERATURE_WAIT SENSOR=heater_bed MINIMUM=56 MAXIMUM=70
    RESPOND MSG="QC:HEAT_BED:PASS"
    M140 S0
"""


# ============================================================
# YMS — adapted to detected hardware
# ============================================================

def _qc_yms_motor(index, extruder_name, sensor_name):
    """Test individual YMS motor direction"""
    return f"""[gcode_macro QC_YMS_{index + 1}_MOTOR]
description: QC - Test {sensor_name} motor direction
gcode:
    RESPOND MSG="QC:YMS_{index + 1}_MOTOR:START"
    T{index}
    G92 E0
    G1 E20 F300
    G92 E0
    RESPOND MSG="QC:YMS_{index + 1}_MOTOR:VISUAL"
"""


def _qc_yms_sensor_check(yms_count, slots):
    """Test all filament sensors detect filament"""
    lines = []
    lines.append("[gcode_macro QC_YMS_SENSORS]")
    lines.append("description: QC - Check all YMS filament sensors")
    lines.append("gcode:")
    lines.append('    RESPOND MSG="QC:YMS_SENSORS:START"')
    for i in range(yms_count):
        sensor_name = slots[i].get("sensor_name", f"YMS-{i+1}") if i < len(slots) else f"YMS-{i+1}"
        lines.append(f'    QUERY_FILAMENT_SENSOR SENSOR={sensor_name}')
    lines.append('    RESPOND MSG="QC:YMS_SENSORS:VISUAL"')
    lines.append("")
    return "\n".join(lines)


# ============================================================
# PID
# ============================================================

def _qc_pid_extruder():
    return """[gcode_macro QC_PID_EXTRUDER]
description: QC - PID calibrate extruder
gcode:
    RESPOND MSG="QC:PID_EXTRUDER:START"
    {% if "xyz" not in printer.toolhead.homed_axes %}
        G28
    {% endif %}
    M106 S255
    PID_CALIBRATE HEATER=extruder TARGET=190
    M106 S0
    RESPOND MSG="QC:PID_EXTRUDER:PASS"
"""


def _qc_pid_bed():
    return """[gcode_macro QC_PID_BED]
description: QC - PID calibrate bed
gcode:
    RESPOND MSG="QC:PID_BED:START"
    {% if "xyz" not in printer.toolhead.homed_axes %}
        G28
    {% endif %}
    M106 S255
    PID_CALIBRATE HEATER=heater_bed TARGET=60
    M106 S0
    RESPOND MSG="QC:PID_BED:PASS"
"""


# ============================================================
# BED MESH
# ============================================================

def _qc_bed_mesh():
    return """[gcode_macro QC_BED_MESH]
description: QC - Full bed mesh calibration
gcode:
    RESPOND MSG="QC:BED_MESH:START"
    {% if "xyz" not in printer.toolhead.homed_axes %}
        G28
    {% endif %}
    BED_MESH_CLEAR
    BED_MESH_CALIBRATE
    RESPOND MSG="QC:BED_MESH:PASS"
"""


# ============================================================
# CLEANUP + SAVE
# ============================================================

def _qc_cleanup():
    return """[gcode_macro QC_CLEANUP]
description: QC - Cooldown and disable motors
gcode:
    RESPOND MSG="QC:CLEANUP:START"
    M104 S0
    M140 S0
    M106 S0
    G28
    M84
    RESPOND MSG="QC:CLEANUP:PASS"
"""


def _qc_save():
    return """[gcode_macro QC_SAVE]
description: QC - Save config (triggers Klipper restart)
gcode:
    RESPOND MSG="QC:SAVE:START"
    SAVE_CONFIG
"""
