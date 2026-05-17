"""
Renderer macros YMS — T0..TN, TOFF, LOAD_YMS, INIT_YMS, MOTION_SENSOR_INIT,
CUT_FILAMENT, CURRENT_UNLOAD, SET_PRESSURE_ADVANCE override, SET_HEATER_TEMPERATURE override
"""


def render_tool_macros(cfg):
    """Genere T0..TN avec logique correcte (pas de bugs copy-paste)"""
    yms_count = cfg.get("_yms_count", 2)
    slots = cfg.get("_yms_slots", [])
    output = []

    # TOFF — desactive tout
    output.append("[gcode_macro TOFF]")
    output.append("description: Disable all YMS extruders")
    output.append("gcode:")
    for i in range(yms_count):
        output.append(f"  SET_FILAMENT_SENSOR SENSOR=YMS-{i+1} ENABLE=0")
    output.append("  SYNC_EXTRUDER_MOTION EXTRUDER=extruder MOTION_QUEUE=extruder")
    for i in range(yms_count):
        name = slots[i]["name"]
        output.append(f"  SYNC_EXTRUDER_MOTION EXTRUDER={name} MOTION_QUEUE=\"\"")
    output.append("  RESPOND MSG=\"DISABLE ALL YMS\"")
    output.append("")

    # MOTION_SENSOR_INIT
    output.append("[gcode_macro MOTION_SENSOR_INIT]")
    output.append("description: Initialize YMS and simulate extrusion length")
    output.append("gcode:")
    output.append("  M82")
    output.append("  M18")
    output.append("  G92 E0")
    output.append("  G1 E50 F400000")
    output.append("  G92 E0")
    output.append("  M18")
    output.append("")

    # T0 — special : mode initialisation + mode impression
    output.append("[gcode_macro T0]")
    output.append("description: ACTIVATE YMS-1 OR INIT YMS SYSTEM")
    output.append("gcode:")
    output.append("  {% set svv = printer.save_variables.variables %}")
    output.append("  {% if printer.save_variables.variables.printing_start %}")
    # Mode impression : activer YMS-1
    for i in range(yms_count):
        enable = 1 if i == 0 else 0
        output.append(f"    SET_FILAMENT_SENSOR SENSOR=YMS-{i+1} ENABLE={enable}")
    output.append("    SYNC_EXTRUDER_MOTION EXTRUDER=extruder MOTION_QUEUE=extruder")
    for i in range(yms_count):
        name = slots[i]["name"]
        queue = "extruder" if i == 0 else "\"\""
        output.append(f"    SYNC_EXTRUDER_MOTION EXTRUDER={name} MOTION_QUEUE={queue}")
    output.append("    RESPOND MSG=\"ACTIVATION YMS-1\"")
    output.append("    SAVE_VARIABLE VARIABLE=active_tool VALUE=1")
    output.append("  {% else %}")
    # Mode initialisation : desync tout, activer tous les sensors, attendre insert
    output.append("    RESPOND MSG=\"YMS INITIALISATION STARTING\"")
    output.append("    G92 E0")
    output.append("    SET_KINEMATIC_POSITION E=0")
    output.append("    SYNC_EXTRUDER_MOTION EXTRUDER=extruder MOTION_QUEUE=extruder")
    for i in range(yms_count):
        name = slots[i]["name"]
        output.append(f"    SYNC_EXTRUDER_MOTION EXTRUDER={name} MOTION_QUEUE=\"\"")
    output.append("    SAVE_VARIABLE VARIABLE=active_tool VALUE=0")
    output.append("    MOTION_SENSOR_INIT")
    for i in range(yms_count):
        output.append(f"    SET_FILAMENT_SENSOR SENSOR=YMS-{i+1} ENABLE=1")
    output.append("    M400")
    output.append("    SAVE_VARIABLE VARIABLE=yms_sensor_initialisation VALUE=True")
    output.append("    RESPOND MSG=\"INSERT NEXT FILAMENT\"")
    output.append("  {% endif %}")
    output.append("")

    # T1..TN — macros tool change
    for t in range(1, yms_count):
        output.append(f"[gcode_macro T{t}]")
        output.append(f"description: ACTIVATE YMS-{t+1}")
        output.append("gcode:")
        output.append("  {% set svv = printer.save_variables.variables %}")
        output.append("  {% if printer.save_variables.variables.printing_start %}")
        # Mode impression : activer le bon YMS
        for i in range(yms_count):
            enable = 1 if i == t else 0
            output.append(f"    SET_FILAMENT_SENSOR SENSOR=YMS-{i+1} ENABLE={enable}")
        output.append("    SYNC_EXTRUDER_MOTION EXTRUDER=extruder MOTION_QUEUE=extruder")
        for i in range(yms_count):
            name = slots[i]["name"]
            queue = "extruder" if i == t else "\"\""
            output.append(f"    SYNC_EXTRUDER_MOTION EXTRUDER={name} MOTION_QUEUE={queue}")
        output.append(f"    RESPOND MSG=\"ACTIVATION YMS-{t+1}\"")
        output.append(f"    SAVE_VARIABLE VARIABLE=active_tool VALUE={t+1}")
        output.append("  {% else %}")
        # Mode insertion chaine : activer le precedent pour detection
        prev = t - 1
        for i in range(yms_count):
            enable = 1 if i == prev else 0
            output.append(f"    SET_FILAMENT_SENSOR SENSOR=YMS-{i+1} ENABLE={enable}")
        output.append("    SYNC_EXTRUDER_MOTION EXTRUDER=extruder MOTION_QUEUE=extruder")
        for i in range(yms_count):
            name = slots[i]["name"]
            queue = "extruder" if i == prev else "\"\""
            output.append(f"    SYNC_EXTRUDER_MOTION EXTRUDER={name} MOTION_QUEUE={queue}")
        output.append(f"    SAVE_VARIABLE VARIABLE=active_tool VALUE={t+1}")
        output.append("  {% endif %}")
        output.append("")

    # CURRENT_UNLOAD
    output.append("[gcode_macro CURRENT_UNLOAD]")
    output.append("description: Set low current on all YMS extruders for unload")
    output.append("gcode:")
    for i in range(yms_count):
        name = slots[i]["name"]
        output.append(f"  SET_TMC_CURRENT STEPPER={name} CURRENT=0.5 HOLDCURRENT=0.5")
    output.append("")

    return "\n".join(output)


def render_yms_utility_macros(cfg):
    """LOAD_YMS, INIT_YMS, CUT_FILAMENT, CHECK_FILAMENT"""
    yms_count = cfg.get("_yms_count", 2)
    output = []

    # LOAD_YMS
    output.append("[gcode_macro LOAD_YMS]")
    output.append("description: Load filament into the YMS box")
    output.append("gcode:")
    output.append("  {% if printer.save_variables.variables.printing_start %}")
    output.append("    {% set insert_length = 200|int %}")
    output.append("    {% set speed = 1000|int %}")
    output.append("    G92 E0")
    output.append("    G1 E{insert_length} F{speed}")
    output.append("    G92 E0")
    output.append("    RESPOND MSG=\"Filament loaded {insert_length}mm at {speed}mm/min\"")
    output.append("  {% else %}")
    output.append("    {% set insert_length = 50|int %}")
    output.append("    {% set speed = 1000|int %}")
    output.append("    G92 E0")
    output.append("    G1 E{insert_length} F{speed}")
    output.append("    G92 E0")
    output.append("    RESPOND MSG=\"Filament loaded {insert_length}mm at {speed}mm/min\"")
    output.append("    T0")
    output.append("  {% endif %}")
    output.append("")

    # INIT_YMS (delayed gcode)
    output.append("[delayed_gcode INIT_YMS]")
    output.append("initial_duration: 0.01")
    output.append("gcode:")
    output.append("  RESPOND TYPE=error MSG=\"INIT YMS\"")
    output.append("  M117 INIT YMS")
    output.append("  INIT_PURGE_VARS")
    output.append("  T0")
    output.append("")

    # CUT_FILAMENT
    output.append("[gcode_macro CUT_FILAMENT]")
    output.append("description: Cut filament with speed profile")
    output.append("gcode:")
    output.append("  G1 X8 F42000")
    output.append("  G1 X-8 F300")
    output.append("  G1 X8 F42000")
    output.append("")

    # CAT_FILAMENT (retract complet)
    output.append("[gcode_macro CAT_FILAMENT]")
    output.append("description: Full retract filament from hotend")
    output.append("gcode:")
    output.append("  G1 X258 F8000")
    output.append("  G92 E0")
    output.append("  G1 E-30 F12000")
    output.append("  G92 E0")
    output.append("  G4 P5000")
    output.append("  G1 E-90 F5000")
    output.append("")

    # CHECK_FILAMENT
    output.append("[gcode_macro CHECK_FILAMENT]")
    output.append("gcode:")
    output.append("  {% set SENSOR = \"YMS-1\" %}")
    output.append("  SET_FILAMENT_SENSOR SENSOR={SENSOR} STATUS=0")
    output.append("  G4 P1000")
    output.append("  M83")
    output.append("  G1 E5 F100")
    output.append("  QUERY_FILAMENT_SENSOR SENSOR={SENSOR}")
    output.append("")

    return "\n".join(output)


def render_pressure_advance_override(cfg):
    """SET_PRESSURE_ADVANCE override pour tous les extrudeurs"""
    yms_count = cfg.get("_yms_count", 2)
    slots = cfg.get("_yms_slots", [])
    output = []

    output.append("[gcode_macro SET_PRESSURE_ADVANCE]")
    output.append("rename_existing: SET_PA_ORIG")
    output.append("gcode:")
    output.append("  {% set pa = params.ADVANCE|default(none) %}")
    output.append("  {% set st = params.SMOOTH_TIME|default(0.040) %}")
    output.append("  {% if pa is not none %}")
    output.append("    SET_PA_ORIG EXTRUDER=extruder ADVANCE={pa} SMOOTH_TIME={st}")
    for slot in slots:
        name = slot["name"]
        output.append(f"    SET_PA_ORIG EXTRUDER={name} ADVANCE={{pa}} SMOOTH_TIME={{st}}")
    output.append("  {% else %}")
    output.append("    SET_PA_ORIG {printer.gcode.move_parameters}")
    output.append("  {% endif %}")
    output.append("")

    return "\n".join(output)


def render_heater_temperature_override(cfg):
    """SET_HEATER_TEMPERATURE override — limite dryer a 110C"""
    if "dryer" not in cfg:
        return ""

    dryer_name = cfg["dryer"]["name"]
    max_temp = cfg["dryer"]["max_temp"]
    output = []

    output.append("[gcode_macro SET_HEATER_TEMPERATURE]")
    output.append("rename_existing: SET_HEATER_TEMPERATURE_ORIG")
    output.append("gcode:")
    output.append("  {% set heater = params.HEATER|default(\"\") %}")
    output.append("  {% set target = params.TARGET|default(0)|float %}")
    output.append(f"  {{% if heater == \"{dryer_name}\" and target > {max_temp} %}}")
    output.append(f"    RESPOND TYPE=error MSG=\"ERROR: {{{{ heater }}}} temperature limited to {max_temp}C\"")
    output.append(f"    SET_HEATER_TEMPERATURE_ORIG HEATER={{heater}} TARGET={max_temp}")
    output.append("  {% else %}")
    output.append("    SET_HEATER_TEMPERATURE_ORIG HEATER={heater} TARGET={target}")
    output.append("  {% endif %}")
    output.append("")

    return "\n".join(output)


def render_purge_macros(cfg):
    """SET_EXTRA_FLUSH, EXTRA_FLUSH, INIT_FLUSH_VARS, INIT_PURGE_VARS"""
    output = []

    output.append("[gcode_macro SET_EXTRA_FLUSH]")
    output.append("description: Set purge parameters")
    output.append("gcode:")
    output.append("  {% if 'LENGTH' in params %}")
    output.append("    {% set new_length = params.LENGTH|float %}")
    output.append("    SAVE_VARIABLE VARIABLE=extra_purge_length VALUE={new_length}")
    output.append("    { action_respond_info(\"Purge length set to %.1fmm\" % new_length) }")
    output.append("  {% endif %}")
    output.append("  {% if 'SPEED' in params %}")
    output.append("    {% set new_speed = params.SPEED|float %}")
    output.append("    SAVE_VARIABLE VARIABLE=extra_purge_speed VALUE={new_speed}")
    output.append("    { action_respond_info(\"Purge speed set to %.1fmm/s\" % new_speed) }")
    output.append("  {% endif %}")
    output.append("  {% if not 'LENGTH' in params and not 'SPEED' in params %}")
    output.append("    { action_respond_info(\"Usage: SET_EXTRA_PURGE LENGTH=<value> SPEED=<value>\") }")
    output.append("  {% endif %}")
    output.append("")

    output.append("[gcode_macro EXTRA_FLUSH]")
    output.append("description: Extrude extra filament for purge")
    output.append("gcode:")
    output.append("  {% set svv = printer.save_variables.variables %}")
    output.append("  {% set length = svv.extra_purge_length|default(0)|float %}")
    output.append("  {% set speed = svv.extra_purge_speed|default(5)|float %}")
    output.append("  {% if printer.extruder.can_extrude|lower == 'true' %}")
    output.append("    G92 E0")
    output.append("    G1 E{length} F{speed * 60}")
    output.append("    { action_respond_info(\"Purge: %.1fmm @ %.1fmm/s\" % (length, speed)) }")
    output.append("  {% else %}")
    output.append("    { action_respond_error(\"ERROR: Temperature too low!\") }")
    output.append("  {% endif %}")
    output.append("")

    output.append("[gcode_macro INIT_PURGE_VARS]")
    output.append("description: Initialize purge variables")
    output.append("gcode:")
    output.append("  {% if 'extra_purge_length' not in printer.save_variables.variables %}")
    output.append("    SAVE_VARIABLE VARIABLE=extra_purge_length VALUE=0.0")
    output.append("  {% endif %}")
    output.append("  {% if 'extra_purge_speed' not in printer.save_variables.variables %}")
    output.append("    SAVE_VARIABLE VARIABLE=extra_purge_speed VALUE=5.0")
    output.append("  {% endif %}")
    output.append("")

    return "\n".join(output)
