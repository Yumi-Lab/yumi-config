#!/usr/bin/env python3
"""
Yumi Printer CFG Generator — Engine principal

Charge le products.json, resout l'arbre de composants selon la configuration
demandee (modele + options), et genere un printer.cfg complet.

Usage:
    python engine.py --model C235 --yms 7 --hotend chroma_x12 --output printer.cfg
    python engine.py --model C435 --yms 2 --hotend direct_drive --output printer.cfg
"""

import json
import os
import sys
import argparse
from pathlib import Path

from macros_yms import (
    render_tool_macros, render_yms_utility_macros,
    render_pressure_advance_override, render_heater_temperature_override,
    render_purge_macros
)
from macros_core import (
    render_pause_resume_cancel, render_print_start_end,
    render_filament_macros, render_gcode_offset,
    render_calibration_macros, render_bed_detection_macros,
    render_marlin_compat
)

# Repertoire du generateur
GENERATOR_DIR = Path(__file__).parent
PRODUCTS_FILE = GENERATOR_DIR / "products.json"


def load_products():
    """Charge le catalogue produits"""
    with open(PRODUCTS_FILE, 'r') as f:
        return json.load(f)


def deep_merge(base, override):
    """Merge recursif : override ecrase base, dicts fusionnes"""
    result = base.copy()
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def resolve_config(products, model, yms_count, hotend_type):
    """
    Resout l'arbre de composants et produit un dict cfg unifie.

    Parcourt : SMARTPAD → C_SERIES_3D_PRINTER (+ overrides modele) → HOTEND → YMS × N
    """
    components = products["components"]
    cfg = {}

    # 1. SmartPad (racine)
    smartpad = components["SMARTPAD"]["cfg"]
    cfg = deep_merge(cfg, smartpad)

    # 2. C Series 3D Printer (tronc commun)
    c_series = components["C_SERIES_3D_PRINTER"]["cfg"]
    cfg = deep_merge(cfg, c_series)

    # 3. Overrides par modele (dimensions, positions)
    overrides = components["C_SERIES_3D_PRINTER"]["overrides_by_model"]
    if model in overrides:
        cfg = deep_merge(cfg, overrides[model])

    # 4. Hotend
    if hotend_type == "chroma_x12":
        hotend_cfg = components["CHROMA_X12_HOTEND"]["cfg"]
    elif hotend_type == "direct_drive":
        hotend_cfg = components["DIRECT_DRIVE_HOTEND"]["cfg"]
    else:
        hotend_cfg = components["CHROMA_X12_HOTEND"]["cfg"]
    cfg = deep_merge(cfg, hotend_cfg)

    # 5. YMS — determiner combien et quels types
    yms_slots = []
    yms_lite = components["YMS_LITE"]

    # E0 et E1 toujours sur carte mere
    for i in range(min(yms_count, 2)):
        slot_key = f"E{i}"
        slot_cfg = yms_lite["overrides_by_position"][slot_key].copy()
        slot_cfg.update({k: v for k, v in yms_lite["cfg_base"].items() if k not in slot_cfg})
        yms_slots.append(slot_cfg)

    # E2+ sur HyperDrive (smartbox)
    if yms_count > 2:
        # Activer le HyperDrive
        hyperdrive = components["HYPERDRIVE_3P2L"]
        cfg = deep_merge(cfg, hyperdrive["cfg"])
        cfg["mcu_smartbox"]["serial"] = hyperdrive["detection"]["uart"]["serial"]

        # Slots E2-E4 : YMS PRO si disponible, sinon LITE
        yms_pro = components.get("YMS_PRO", {})
        for i in range(2, min(yms_count, 7)):
            slot_key = f"E{i}"
            # E2-E4 = PRO slots, E5-E6 = LITE only
            if i <= 4 and "overrides_by_position" in yms_pro and slot_key in yms_pro["overrides_by_position"]:
                slot_cfg = yms_pro["overrides_by_position"][slot_key].copy()
                slot_cfg.update({k: v for k, v in yms_pro["cfg_base"].items() if k not in slot_cfg})
            else:
                slot_cfg = yms_lite["overrides_by_position"][slot_key].copy()
                slot_cfg.update({k: v for k, v in yms_lite["cfg_base"].items() if k not in slot_cfg})
            yms_slots.append(slot_cfg)

    cfg["_yms_slots"] = yms_slots
    cfg["_yms_count"] = yms_count
    cfg["_model"] = model
    cfg["_motor_constants"] = products["motor_constants"]

    return cfg


def render_section(name, params, indent=""):
    """Rend une section Klipper [name] avec ses parametres"""
    lines = [f"[{name}]"]
    for key, value in params.items():
        if key.startswith("_"):
            continue
        if isinstance(value, bool):
            value = str(value).lower()
        elif isinstance(value, dict):
            continue  # sous-sections traitees separement
        lines.append(f"{key}: {value}")
    return "\n".join(lines) + "\n"


# ============================================================
# RENDERERS — un par type de composant
# ============================================================

def render_includes(cfg):
    """[include *.cfg]"""
    lines = []
    for inc in cfg.get("includes", []):
        lines.append(f"[include {inc}]")
    return "\n".join(lines) + "\n" if lines else ""


def render_mcu(cfg):
    """[mcu] + [mcu smartbox] + [mcu rpi]"""
    output = []

    # MCU principal
    mcu = cfg.get("mcu", {})
    output.append("[mcu]")
    output.append(f"serial: {mcu['serial']}")
    output.append(f"restart_method: {mcu.get('restart_method', 'command')}")
    output.append("")

    # Smartbox (si present)
    if "mcu_smartbox" in cfg:
        sb = cfg["mcu_smartbox"]
        output.append("[mcu smartbox]")
        output.append(f"serial: {sb['serial']}")
        output.append(f"restart_method: {sb.get('restart_method', 'command')}")
        output.append("")

    # RPi host MCU
    if "mcu_rpi" in cfg:
        output.append("[mcu rpi]")
        output.append(f"serial: {cfg['mcu_rpi']['serial']}")
        output.append("")

    return "\n".join(output)


def render_printer(cfg):
    """[printer] + [virtual_sdcard] + [save_variables] + [exclude_object] + [gcode_arcs] + [idle_timeout]"""
    output = []
    p = cfg["printer"]
    output.append("[printer]")
    output.append(f"kinematics: {p['kinematics']}")
    output.append(f"max_velocity: {p['max_velocity']}")
    output.append(f"max_accel: {p['max_accel']}")
    output.append(f"max_z_velocity: {p['max_z_velocity']}")
    output.append(f"max_z_accel: {p['max_z_accel']}")
    output.append("")

    vs = cfg.get("virtual_sdcard", {})
    output.append("[virtual_sdcard]")
    output.append(f"path: {vs.get('path', '~/printer_data/gcodes/')}")
    output.append("")

    sv = cfg.get("save_variables", {})
    output.append("[save_variables]")
    output.append(f"filename: {sv.get('filename', '~/printer_data/config/variables.cfg')}")
    output.append("")

    output.append("[exclude_object]")
    output.append("")

    ga = cfg.get("gcode_arcs", {})
    output.append("[gcode_arcs]")
    output.append(f"resolution: {ga.get('resolution', 0.1)}")
    output.append("")

    it = cfg.get("idle_timeout", {})
    output.append("[idle_timeout]")
    output.append(f"timeout: {it.get('timeout', 99999999)}")
    output.append("")

    output.append("[display_status]")
    output.append("")
    output.append("[pause_resume]")
    output.append("")

    return "\n".join(output)


def render_adxl(cfg):
    """[adxl345] + [resonance_tester] + [input_shaper]"""
    output = []
    if "adxl345" not in cfg:
        return ""

    adxl = cfg["adxl345"]
    output.append("[adxl345]")
    output.append(f"cs_pin: {adxl['cs_pin']}")
    output.append(f"spi_bus: {adxl['spi_bus']}")
    output.append("")

    rt = cfg.get("resonance_tester", {})
    output.append("[resonance_tester]")
    output.append("accel_chip: adxl345")
    output.append(f"probe_points: {rt.get('probe_points', '115,115,20')}")
    output.append("")

    output.append("[input_shaper]")
    output.append("")

    return "\n".join(output)


def render_steppers(cfg):
    """[stepper_x/y/z] + [tmc2209 stepper_x/y/z]"""
    output = []

    for axis in ['x', 'y', 'z']:
        s = cfg[f"stepper_{axis}"]
        output.append(f"[stepper_{axis}]")
        output.append(f"step_pin: {s['step_pin']}")
        output.append(f"dir_pin: {s['dir_pin']}")
        output.append(f"enable_pin: {s['enable_pin']}")
        output.append(f"microsteps: {s['microsteps']}")
        output.append(f"rotation_distance: {s['rotation_distance']}")
        output.append(f"endstop_pin: {s['endstop_pin']}")
        if 'position_endstop' in s:
            output.append(f"position_endstop: {s['position_endstop']}")
        if 'position_min' in s:
            output.append(f"position_min: {s['position_min']}")
        if 'position_max' in s:
            output.append(f"position_max: {s['position_max']}")
        output.append(f"homing_speed: {s['homing_speed']}")
        output.append(f"homing_retract_dist: {s['homing_retract_dist']}")
        if 'second_homing_speed' in s:
            output.append(f"second_homing_speed: {s['second_homing_speed']}")
        output.append("")

        # TMC2209
        tmc = cfg[f"tmc2209_stepper_{axis}"]
        output.append(f"[tmc2209 stepper_{axis}]")
        output.append(f"uart_pin: {tmc['uart_pin']}")
        output.append(f"run_current: {tmc['run_current']}")
        output.append(f"hold_current: {tmc['hold_current']}")
        output.append(f"stealthchop_threshold: {tmc['stealthchop_threshold']}")
        if 'driver_sgthrs' in tmc:
            output.append(f"driver_sgthrs: {tmc['driver_sgthrs']}")
            output.append(f"diag_pin: {tmc['diag_pin']}")
        output.append("")

    return "\n".join(output)


def render_extruder_main(cfg):
    """[thermistor] + [extruder] + [heater_fan hotend]"""
    output = []

    # Thermistor custom
    if "thermistor_100K4190YUMI" in cfg:
        t = cfg["thermistor_100K4190YUMI"]
        output.append("[thermistor 100K4190YUMI]")
        output.append(f"temperature1: {t['temperature1']}")
        output.append(f"resistance1: {t['resistance1']}")
        output.append(f"beta: {t['beta']}")
        output.append("")

    # Extruder
    e = cfg["extruder"]
    output.append("[extruder]")
    output.append(f"max_extrude_only_velocity: {e['max_extrude_only_velocity']}")
    output.append(f"step_pin: {e['step_pin']}")
    output.append(f"dir_pin: {e['dir_pin']}")
    output.append(f"enable_pin: {e['enable_pin']}")
    output.append(f"rotation_distance: {e['rotation_distance']}")
    if e.get('gear_ratio'):
        output.append(f"gear_ratio: {e['gear_ratio']}")
    output.append(f"microsteps: {e['microsteps']}")
    output.append(f"full_steps_per_rotation: {e['full_steps_per_rotation']}")
    if 'nozzle_diameter' in e:
        output.append(f"nozzle_diameter: {e['nozzle_diameter']}")
    output.append(f"filament_diameter: {e['filament_diameter']}")
    output.append(f"heater_pin: {e['heater_pin']}")
    output.append(f"sensor_type: {e['sensor_type']}")
    output.append(f"sensor_pin: {e['sensor_pin']}")
    output.append(f"min_temp: {e['min_temp']}")
    output.append(f"max_temp: {e['max_temp']}")
    output.append(f"max_extrude_only_distance: {e['max_extrude_only_distance']}")
    output.append(f"max_extrude_cross_section: {e['max_extrude_cross_section']}")
    output.append(f"min_extrude_temp: {e['min_extrude_temp']}")
    output.append(f"pressure_advance: {e['pressure_advance']}")
    output.append("")

    # Heater fan hotend
    if "heater_fan_hotend" in cfg:
        hf = cfg["heater_fan_hotend"]
        output.append("[heater_fan hotend_fan]")
        output.append(f"pin: {hf['pin']}")
        output.append(f"max_power: {hf['max_power']}")
        output.append(f"kick_start_time: {hf['kick_start_time']}")
        output.append(f"heater: {hf['heater']}")
        output.append(f"heater_temp: {hf['heater_temp']}")
        output.append(f"fan_speed: {hf['fan_speed']}")
        output.append(f"shutdown_speed: {hf['shutdown_speed']}")
        output.append("")

    return "\n".join(output)


def render_yms(cfg):
    """Genere N extruder_stepper + tmc2209 + filament_sensor"""
    output = []
    slots = cfg.get("_yms_slots", [])

    for i, slot in enumerate(slots):
        name = slot["name"]
        sensor_name = slot["sensor_name"]

        # extruder_stepper
        output.append(f"[extruder_stepper {name}]")
        output.append("extruder:")
        output.append(f"step_pin: {slot['step_pin']}")
        output.append(f"dir_pin: {slot['dir_pin']}")
        output.append(f"enable_pin: {slot['enable_pin']}")
        output.append(f"microsteps: {slot['microsteps']}")
        output.append(f"rotation_distance: {slot['rotation_distance']}")
        output.append(f"gear_ratio: {slot['gear_ratio']}")
        output.append(f"full_steps_per_rotation: {slot['full_steps_per_rotation']}")
        output.append(f"pressure_advance: {slot['pressure_advance']}")
        output.append("")

        # tmc2209
        output.append(f"[tmc2209 extruder_stepper {name}]")
        output.append(f"uart_pin: {slot['uart_pin']}")
        output.append(f"run_current: {slot['run_current']}")
        output.append(f"hold_current: {slot['hold_current']}")
        output.append(f"stealthchop_threshold: {slot['stealthchop_threshold']}")
        output.append("")

        # filament sensor
        sensor_type = slot.get("sensor_type", "filament_motion_sensor")
        output.append(f"[{sensor_type} {sensor_name}]")
        output.append(f"switch_pin: {slot['switch_pin']}")
        output.append(f"detection_length: {slot.get('detection_length', 50)}")
        output.append("pause_on_runout: False")
        output.append("extruder: extruder")
        output.append("runout_gcode:")
        output.append(f"  RESPOND TYPE=error MSG=\"Runout {sensor_name}\"")
        output.append(f"  M117 Runout {sensor_name}")
        output.append("  {% set print_state = printer.print_stats.state %}")
        output.append("  {% if print_state in ['printing', 'paused'] %}")
        output.append("    PAUSE")
        output.append(f"    RESPOND TYPE=error MSG=\"{sensor_name} filament encoder runout\"")
        output.append(f"    M117 {sensor_name} filament encoder runout")
        output.append("  {% else %}")
        output.append(f"    RESPOND TYPE=error MSG=\"{sensor_name} No printing running!\"")
        output.append(f"    M117 {sensor_name} No printing running!")
        output.append("  {% endif %}")
        output.append("insert_gcode:")
        output.append(f"  RESPOND TYPE=error MSG=\"Insert {sensor_name}\"")
        output.append(f"  M117 Insert {sensor_name}")
        output.append(f"  SAVE_VARIABLE VARIABLE={sensor_name.lower().replace('-','_')}_sensor VALUE=True")
        # Logique insert : en impression → activer le bon T, sinon → T suivant + LOAD
        tool_index_printing = i
        tool_index_idle = i + 1 if i + 1 < len(slots) else 0
        output.append("  {% if printer.save_variables.variables.printing_start %}")
        output.append(f"    T{tool_index_printing}")
        output.append(f"    RESPOND TYPE=error MSG=\"{sensor_name} Printing running!\"")
        output.append("  {% else %}")
        output.append(f"    T{tool_index_idle}")
        output.append(f"    RESPOND TYPE=error MSG=\"{sensor_name} No printing running!\"")
        output.append("  {% endif %}")
        output.append(f"  M117 {sensor_name} Loading")
        output.append(f"  RESPOND MSG=\"{sensor_name} Loading...\"")
        output.append("  LOAD_YMS")
        output.append("")

    return "\n".join(output)


def render_dryer(cfg):
    """[heater_generic YMS-3-PRO] + verify + fan (si HyperDrive avec dryer)"""
    if "dryer" not in cfg:
        return ""

    d = cfg["dryer"]
    output = []

    output.append(f"[heater_generic {d['name']}]")
    output.append(f"heater_pin: {d['heater_pin']}")
    output.append(f"sensor_type: {d['sensor_type']}")
    output.append(f"sensor_pin: {d['sensor_pin']}")
    output.append(f"max_power: {d['max_power']}")
    output.append(f"control: {d['control']}")
    output.append(f"pid_Kp: {d['pid_kp']}")
    output.append(f"pid_Ki: {d['pid_ki']}")
    output.append(f"pid_Kd: {d['pid_kd']}")
    output.append(f"min_temp: {d['min_temp']}")
    output.append(f"max_temp: {d['max_temp']}")
    output.append("")

    output.append("[heater_fan dryer_fan]")
    output.append(f"pin: {d['fan_pin']}")
    output.append(f"max_power: {d['fan_max_power']}")
    output.append(f"off_below: {d['fan_off_below']}")
    output.append(f"heater: {d['name']}")
    output.append(f"heater_temp: {d['fan_heater_temp']}")
    output.append("shutdown_speed: 0")
    output.append("")

    output.append(f"[verify_heater {d['name']}]")
    output.append(f"max_error: {d['verify_max_error']}")
    output.append(f"check_gain_time: {d['verify_check_gain_time']}")
    output.append(f"hysteresis: {d['verify_hysteresis']}")
    output.append(f"heating_gain: {d['verify_heating_gain']}")
    output.append("")

    return "\n".join(output)


def render_bed(cfg):
    """[heater_bed] + [verify_heater heater_bed]"""
    output = []
    hb = cfg["heater_bed"]

    output.append("[heater_bed]")
    output.append(f"heater_pin: {hb['heater_pin']}")
    output.append(f"sensor_type: {hb['sensor_type']}")
    output.append(f"sensor_pin: {hb['sensor_pin']}")
    output.append(f"control: {hb['control']}")
    output.append(f"pid_Kp: {hb['pid_kp']}")
    output.append(f"pid_Ki: {hb['pid_ki']}")
    output.append(f"pid_Kd: {hb['pid_kd']}")
    output.append(f"min_temp: {hb['min_temp']}")
    output.append(f"max_temp: {hb['max_temp']}")
    output.append("")

    vhb = cfg.get("verify_heater_bed", {})
    if vhb:
        output.append("[verify_heater heater_bed]")
        output.append(f"max_error: {vhb['max_error']}")
        output.append(f"hysteresis: {vhb['hysteresis']}")
        output.append("")

    return "\n".join(output)


def render_fans(cfg):
    """[fan] + [controller_fan] + [fan_generic]"""
    output = []

    # Part cooling fan
    if "fan" in cfg:
        output.append("[fan]")
        output.append(f"pin: {cfg['fan']['pin']}")
        output.append("")

    # Controller fan motherboard
    if "controller_fan_motherboard" in cfg:
        cf = cfg["controller_fan_motherboard"]
        output.append("[controller_fan Motherboard_Fan]")
        output.append(f"pin: {cf['pin']}")
        output.append(f"fan_speed: {cf['fan_speed']}")
        output.append(f"idle_timeout: {cf['idle_timeout']}")
        output.append(f"idle_speed: {cf['idle_speed']}")
        output.append(f"kick_start_time: {cf['kick_start_time']}")
        output.append(f"off_below: {cf['off_below']}")
        output.append("")

    # Controller fan smartbox
    if "controller_fan_smartbox" in cfg:
        cf = cfg["controller_fan_smartbox"]
        output.append("[controller_fan smartbox_Fan]")
        output.append(f"pin: {cf['pin']}")
        output.append(f"fan_speed: {cf['fan_speed']}")
        output.append(f"idle_timeout: {cf['idle_timeout']}")
        output.append(f"idle_speed: {cf['idle_speed']}")
        output.append(f"kick_start_time: {cf['kick_start_time']}")
        output.append(f"off_below: {cf['off_below']}")
        output.append("")

    # Aux fan
    if "fan_generic_aux" in cfg:
        output.append("[fan_generic Aux_Fan]")
        output.append(f"pin: {cfg['fan_generic_aux']['pin']}")
        output.append("")

    return "\n".join(output)


def render_probe(cfg):
    """[probe] + [probe_pressure] + [yumi_z_offset_calculator] + [bed_mesh] + [screws_tilt_adjust]"""
    output = []

    # Probe
    p = cfg.get("probe", {})
    if p:
        output.append("[probe]")
        output.append(f"pin: {p['pin']}")
        output.append(f"x_offset: {p['x_offset']}")
        output.append(f"y_offset: {p['y_offset']}")
        output.append(f"speed: {p['speed']}")
        output.append(f"samples: {p['samples']}")
        output.append(f"samples_result: {p['samples_result']}")
        output.append(f"sample_retract_dist: {p['sample_retract_dist']}")
        output.append(f"samples_tolerance: {p['samples_tolerance']}")
        output.append(f"samples_tolerance_retries: {p['samples_tolerance_retries']}")
        output.append("")

    # Probe pressure
    pp = cfg.get("probe_pressure", {})
    if pp:
        output.append("[probe_pressure]")
        output.append(f"pin: {pp['pin']}")
        output.append(f"speed: {pp['speed']}")
        output.append(f"lift_speed: {pp['lift_speed']}")
        output.append("")

    # Yumi Z offset calculator
    yz = cfg.get("yumi_z_offset_calculator", {})
    if yz:
        output.append("[yumi_z_offset_calculator]")
        output.append(f"pressure_switch_x: {yz['pressure_switch_x']}")
        output.append(f"pressure_switch_y: {yz['pressure_switch_y']}")
        output.append(f"compression_offset: {yz['compression_offset']}")
        output.append(f"approach_speed: {yz['approach_speed']}")
        output.append(f"retract_dist: {yz['retract_dist']}")
        output.append(f"max_probe_travel: {yz['max_probe_travel']}")
        output.append(f"max_probe_times: {yz['max_probe_times']}")
        output.append(f"probe_delay: {yz['probe_delay']}")
        output.append(f"z_hop: {yz['z_hop']}")
        output.append(f"samples_tolerance: {yz['samples_tolerance']}")
        output.append("")

    # Bed mesh
    bm = cfg.get("bed_mesh", {})
    if bm:
        output.append("[bed_mesh]")
        output.append(f"speed: {bm['speed']}")
        output.append(f"horizontal_move_z: {bm['horizontal_move_z']}")
        output.append(f"mesh_min: {bm.get('mesh_min', '10,10')}")
        output.append(f"mesh_max: {bm.get('mesh_max', '223,225')}")
        output.append(f"probe_count: {bm.get('probe_count', '10,10')}")
        output.append(f"algorithm: {bm['algorithm']}")
        if 'zero_reference_position' in bm:
            output.append(f"zero_reference_position: {bm['zero_reference_position']}")
        output.append(f"adaptive_margin: {bm['adaptive_margin']}")
        output.append("")

    # Screws tilt adjust
    sta = cfg.get("screws_tilt_adjust", {})
    if sta:
        output.append("[screws_tilt_adjust]")
        screws = sta.get("screws", [])
        for i, screw in enumerate(screws, 1):
            output.append(f"screw{i}: {screw['x']}, {screw['y']}")
            output.append(f"screw{i}_name: {screw['name']}")
        output.append(f"horizontal_move_z: {sta['horizontal_move_z']}")
        output.append(f"speed: {sta['speed']}")
        output.append(f"screw_thread: {sta['screw_thread']}")
        output.append("")

    return "\n".join(output)


def render_sensors(cfg):
    """[temperature_sensor] + [verify_heater extruder]"""
    output = []

    if "temperature_sensor_host" in cfg:
        ts = cfg["temperature_sensor_host"]
        output.append("[temperature_sensor NanoPi]")
        output.append(f"sensor_type: {ts['sensor_type']}")
        output.append(f"min_temp: {ts['min_temp']}")
        output.append(f"max_temp: {ts['max_temp']}")
        output.append("")

    vhe = cfg.get("verify_heater_extruder", {})
    if vhe:
        output.append("[verify_heater extruder]")
        output.append(f"max_error: {vhe['max_error']}")
        output.append(f"check_gain_time: {vhe['check_gain_time']}")
        output.append(f"hysteresis: {vhe['hysteresis']}")
        output.append(f"heating_gain: {vhe['heating_gain']}")
        output.append("")

    return "\n".join(output)


def render_motor_constants(cfg):
    """[motor_constants] pour chaque type de moteur utilise"""
    output = []
    motors = cfg.get("_motor_constants", {})

    for name, specs in motors.items():
        output.append(f"[motor_constants {name}]")
        if 'comment' in specs:
            output.append(f"# {specs['comment']}")
        output.append(f"resistance: {specs['resistance']}")
        output.append(f"inductance: {specs['inductance']}")
        output.append(f"holding_torque: {specs['holding_torque']}")
        output.append(f"max_current: {specs['max_current']}")
        output.append(f"steps_per_revolution: {specs['steps_per_revolution']}")
        output.append("")

    return "\n".join(output)


def render_tmc_autotune(cfg):
    """[autotune_tmc] pour chaque axe + extrudeurs"""
    output = []
    tmc = cfg.get("tmc_autotune", {})
    if not tmc:
        return ""

    voltage = tmc.get("voltage", 24)

    # Axes X, Y, Z
    for axis in ['stepper_x', 'stepper_y', 'stepper_z']:
        if axis in tmc:
            at = tmc[axis]
            output.append(f"[autotune_tmc {axis}]")
            output.append(f"motor: {at['motor']}")
            output.append(f"tuning_goal: {at['tuning_goal']}")
            output.append(f"toff: {at['toff']}")
            output.append(f"tbl: {at['tbl']}")
            output.append(f"voltage: {voltage}")
            output.append(f"sg4_thrs: {at['sg4_thrs']}")
            output.append(f"extra_hysteresis: {at['extra_hysteresis']}")
            output.append(f"pwm_freq_target: {at['pwm_freq_target']}")
            output.append("")

    # Extrudeurs YMS
    slots = cfg.get("_yms_slots", [])
    for slot in slots:
        at = slot.get("tmc_autotune", {})
        if at:
            name = slot["name"]
            output.append(f"[autotune_tmc extruder_stepper {name}]")
            output.append(f"motor: {at['motor']}")
            output.append(f"tuning_goal: {at['tuning_goal']}")
            output.append(f"toff: {at['toff']}")
            output.append(f"tbl: {at['tbl']}")
            output.append(f"voltage: {voltage}")
            output.append(f"sg4_thrs: {at['sg4_thrs']}")
            output.append(f"extra_hysteresis: {at['extra_hysteresis']}")
            output.append(f"pwm_freq_target: {at['pwm_freq_target']}")
            output.append("")

    return "\n".join(output)


def render_homing(cfg):
    """[homing_override]"""
    ho = cfg.get("homing_override", {})
    if not ho:
        return ""

    # Position max pour le centre
    sx = cfg.get("stepper_x", {})
    sy = cfg.get("stepper_y", {})
    center_x = sx.get("position_max", 235) / 2
    center_y = sy.get("position_max", 235) / 2
    pos_max_x = sx.get("position_max", 257)

    output = []
    output.append("[homing_override]")
    output.append("axes: xyz")
    output.append("set_position_z: 0")
    output.append("gcode:")
    output.append("  G1 Z10 F300")
    output.append("  {% set home_all = 'X' not in params and 'Y' not in params and 'Z' not in params %}")
    output.append("  {% if home_all or 'X' in params%}")
    output.append("    G1 Z10 F1800")
    output.append("    SET_KINEMATIC_POSITION X=0")
    output.append("    G1 X-5 F1800")
    output.append("    G28 X F1800")
    output.append(f"    G1 X{pos_max_x - 4} F1800")
    output.append("    G28 X F1800")
    output.append("  {% endif %}")
    output.append("")
    output.append("  {% if home_all or 'Y' in params%}")
    output.append("    G1 Z10")
    output.append("    G28 Y")
    output.append("  {% endif %}")
    output.append("")
    output.append("  {% if 'Z' in params %}")
    output.append("    G1 Z10")
    output.append(f"    G1 X{center_x} Y{center_y} F1800")
    output.append("    G4 P1000")
    output.append("    G28 Z")
    output.append("    G1 Z10")
    output.append("  {% endif %}")
    output.append("")
    output.append("  {% if home_all%}")
    output.append("    G1 Z10 F1800")
    output.append("    SET_KINEMATIC_POSITION X=0")
    output.append("    G1 X-5 F1800")
    output.append("    G28 X F1800")
    output.append(f"    G1 X{pos_max_x - 4} F1800")
    output.append("    G28 X F1800")
    output.append("")
    output.append("    G1 Z10")
    output.append("    G28 Y")
    output.append("")
    output.append("    G1 Z10")
    output.append(f"    G1 X{center_x} Y{center_y} F9000")
    output.append("    G4 P1000")
    output.append("    G28 Z")
    output.append("    G1 Z10")
    output.append("  {% endif %}")
    output.append("")

    # Welcome delayed gcode
    output.append("[delayed_gcode welcome]")
    output.append("initial_duration: 1")
    output.append("gcode:")
    output.append("  BED_MESH_PROFILE LOAD=default")
    output.append("  M117 Welcome!")
    output.append("")

    return "\n".join(output)


# ============================================================
# ASSEMBLAGE FINAL
# ============================================================

def generate(model, yms_count, hotend_type="chroma_x12"):
    """Genere le printer.cfg complet"""
    products = load_products()
    cfg = resolve_config(products, model, yms_count, hotend_type)

    sections = [
        "# Yumi C-Series printer.cfg",
        f"# Model: {model} | YMS: {yms_count} | Hotend: {hotend_type}",
        f"# Generated by Yumi CFG Generator",
        "",
        render_includes(cfg),
        render_mcu(cfg),
        render_printer(cfg),
        render_adxl(cfg),
        render_motor_constants(cfg),
        render_tmc_autotune(cfg),
        render_steppers(cfg),
        render_extruder_main(cfg),
        render_yms(cfg),
        render_dryer(cfg),
        render_bed(cfg),
        render_fans(cfg),
        render_sensors(cfg),
        render_probe(cfg),
        render_homing(cfg),
        # Macros
        render_heater_temperature_override(cfg),
        render_pressure_advance_override(cfg),
        render_purge_macros(cfg),
        render_yms_utility_macros(cfg),
        render_tool_macros(cfg),
        render_pause_resume_cancel(cfg),
        render_print_start_end(cfg),
        render_filament_macros(cfg),
        render_gcode_offset(cfg),
        render_calibration_macros(cfg),
        render_bed_detection_macros(cfg),
        render_marlin_compat(cfg),
    ]

    return "\n".join(sections)


def main():
    parser = argparse.ArgumentParser(description="Yumi Printer CFG Generator")
    parser.add_argument("--model", required=True, choices=["C235", "C335", "C435"],
                        help="Modele imprimante")
    parser.add_argument("--yms", type=int, required=True,
                        help="Nombre de YMS (2-12)")
    parser.add_argument("--hotend", default="chroma_x12",
                        choices=["chroma_x12", "direct_drive"],
                        help="Type de hotend")
    parser.add_argument("--output", "-o", default="-",
                        help="Fichier de sortie (- = stdout)")

    args = parser.parse_args()

    result = generate(args.model, args.yms, args.hotend)

    if args.output == "-":
        print(result)
    else:
        with open(args.output, 'w') as f:
            f.write(result)
        print(f"Generated: {args.output} ({len(result.splitlines())} lines)")


if __name__ == "__main__":
    main()
