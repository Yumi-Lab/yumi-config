#!/usr/bin/env python3
"""
Printer CFG Generator — YUMI C-Series
Renders a complete printer.cfg from YUMI-LAB_product-catalog.json.

The catalog is layered: board (common trunk: pins, currents, probes, homing, macros — valid
for every machine size) -> machine (geometry only) -> print head -> hotend type -> nozzle
-> YMS. The layers of a product are deep-merged, then every Klipper section is rendered
from the merged data with the comments the catalog carries:

    "_comment"  on a dict  -> written after the [section] header
    "_comments" {key: txt} -> written inline after "key: value"
    "_notes"    [txt, ...] -> written as commented lines at the end of the section
                              (alternative values kept for reference)

Nothing machine-specific lives in this file: a value that is the same for every machine
belongs to the board layer of the catalog, a value that differs belongs to the machine.
Macros carry no machine value either — they read printer.configfile.settings and the single
_YUMI_MACHINE macro (machine class derived from the X axis length).

Usage:
    python3 generator.py C235_DD_LW_04
    python3 generator.py C235_CX12_HF_04_7YMS -o /tmp/printer.cfg
    python3 generator.py --list
"""
import argparse
import copy
import json
import sys
from pathlib import Path

BASE_DIR = Path(__file__).parent
CATALOG_FILE = BASE_DIR / "YUMI-LAB_product-catalog.json"

# Keys of a catalog dict that are documentation, not Klipper options.
META_KEYS = {"_comment", "_comments", "_notes", "_status", "_TODO", "_doc"}

# Klipper modules shipped by this repo (symlinked into klippy/extras by install.sh). Their
# section in printer.cfg is preceded by the module's own header — what it does, every option,
# every command, the status fields — read from the module file: one source, never a copy.
EXTRAS_DIR = Path(__file__).resolve().parent.parent / "klipper" / "klippy" / "extras"
DOCUMENTED_MODULES = {
    "yumi_filament_head": "yumi_filament_head.py",
    "yumi_bed_scan": "yumi_bed_scan.py",
    "filament_yumi_smart_motion_sensor": "filament_yumi_smart_motion_sensor.py",
    "yumi_sensorless_homing": "yumi_sensorless_homing.py",
}


def module_doc(section):
    """The leading comment block of a documented module, under a title line naming the file."""
    fname = DOCUMENTED_MODULES[section]
    header = []
    for raw in (EXTRAS_DIR / fname).read_text(encoding="utf-8").splitlines():
        if not raw.startswith("#"):
            break
        header.append(raw)
    return "\n".join([f"# ═══ {section} — yumi-config/klipper/klippy/extras/{fname} ═══"] + header)


def with_module_doc(section, block):
    return f"{module_doc(section)}\n{block}" if block else block
# Nested dicts rendered as their own Klipper section, never as an option of the parent.
SUBSECTION_KEYS = {"tmc2209", "autotune"}
# Keys carried by a merged component that describe the product, not a Klipper section.
PRODUCT_KEYS = {"layer", "children", "terminates", "incompatible_with", "parent", "requires",
                "requires_any", "terminates_if", "yms_constraints", "max_instances"}

# Preferred option order per section: pins first, then geometry, then tuning.
STEPPER_ORDER = ("step_pin", "dir_pin", "enable_pin", "microsteps", "rotation_distance", "gear_ratio",
                 "full_steps_per_rotation", "endstop_pin", "position_endstop", "position_min",
                 "position_max", "homing_speed", "second_homing_speed", "homing_retract_dist")
TMC_ORDER = ("uart_pin", "run_current", "hold_current", "stealthchop_threshold", "driver_sgthrs", "diag_pin")
AUTOTUNE_ORDER = ("motor", "tuning_goal")
EXTRUDER_ORDER = ("step_pin", "dir_pin", "enable_pin", "rotation_distance", "gear_ratio", "microsteps",
                  "full_steps_per_rotation", "nozzle_diameter", "filament_diameter", "heater_pin",
                  "sensor_type", "sensor_pin", "min_temp", "max_temp")
EXTRUDER_STEPPER_ORDER = ("extruder", "step_pin", "dir_pin", "enable_pin", "microsteps", "rotation_distance",
                          "gear_ratio", "full_steps_per_rotation", "pressure_advance")
HEATER_BED_ORDER = ("heater_pin", "sensor_type", "sensor_pin", "control", "pid_Kp", "pid_Ki", "pid_Kd",
                    "max_power", "min_temp", "max_temp")
PROBE_ORDER = ("pin", "x_offset", "y_offset", "z_offset", "speed", "samples", "samples_result",
               "sample_retract_dist", "samples_tolerance", "samples_tolerance_retries")
BED_MESH_ORDER = ("speed", "horizontal_move_z", "mesh_min", "mesh_max", "probe_count", "algorithm",
                  "bicubic_tension", "mesh_pps", "zero_reference_position", "adaptive_margin")
SCREWS_ORDER = tuple(f"screw{i}{s}" for i in range(1, 9) for s in ("", "_name"))


# ─── Catalog loader ─────────────────────────────────────────────────

def load_catalog():
    with open(CATALOG_FILE, encoding="utf-8") as f:
        return json.load(f)


def deep_merge(base, override):
    result = copy.deepcopy(base)
    for key, val in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(val, dict):
            result[key] = deep_merge(result[key], val)
        else:
            result[key] = copy.deepcopy(val)
    return result


def machines_of(catalog, board=None):
    """The machine components (optionally of one board), sorted by X axis length."""
    rows = []
    for mid, comp in catalog["components"].items():
        if not isinstance(comp, dict) or comp.get("layer") != "machine":
            continue
        if board and comp.get("parent") != board:
            continue
        rows.append((mid, comp))
    rows.sort(key=lambda r: float(r[1]["stepper_x"]["position_max"]))
    return rows


def resolve_product(product_id, catalog=None):
    catalog = catalog or load_catalog()
    products = catalog.get("products", {})
    components = catalog.get("components", {})

    if product_id not in products:
        print(f"ERROR: Product '{product_id}' not found.")
        print(f"Available: {', '.join(k for k, v in products.items() if isinstance(v, dict) and 'chain' in v)}")
        sys.exit(1)

    product_def = products[product_id]
    chain = product_def["chain"]

    for comp_id in chain:
        if comp_id not in components:
            print(f"ERROR: Component '{comp_id}' not found.")
            sys.exit(1)

    chain_set = set(chain)
    for comp_id in chain:
        comp = components[comp_id]
        for forbidden in comp.get("incompatible_with", []):
            if forbidden in chain_set:
                print(f"ERROR: '{comp_id}' is incompatible with '{forbidden}'.")
                sys.exit(1)

    merged = {}
    for comp_id in chain:
        comp = components[comp_id]
        data = {k: v for k, v in comp.items() if k not in PRODUCT_KEYS and k not in META_KEYS}
        if "config" in data:
            cfg = data.pop("config")
            data.update(cfg)
        merged = deep_merge(merged, data)

    merged["name"] = product_def["name"]
    merged["_chain"] = chain
    merged["_board"] = chain[0]
    return merged


# ─── Condition evaluator ────────────────────────────────────────────

def check_condition(condition, product):
    if condition == "always":
        return True
    if condition.startswith("feature:"):
        feat = condition.split(":", 1)[1]
        return product.get("features", {}).get(feat, False)
    if condition == "yms_count > 0":
        return product.get("yms_count", 0) > 0
    if condition == "yms_count == 0":
        return product.get("yms_count", 0) == 0
    if condition == "has_dryer":
        return product.get("has_dryer", False)
    if condition == "has_smartbox":
        return product.get("has_smartbox", False)
    return True


# ─── Section emitter ────────────────────────────────────────────────

def fmt(value):
    if isinstance(value, bool):
        return "True" if value else "False"
    return str(value)


def emit(header, data, order=(), skip=()):
    """One Klipper section from a catalog dict, comments included (see module doc)."""
    if not data:
        return ""
    comments = data.get("_comments") or {}
    lines = [f"[{header}]" + (f"  # {data['_comment']}" if data.get("_comment") else "")]
    keys = [k for k in order if k in data] + [k for k in data if k not in order]
    for k in keys:
        if k in META_KEYS or k in SUBSECTION_KEYS or k in skip:
            continue
        v = data[k]
        if isinstance(v, (dict, list)):
            continue
        line = f"{k}: {fmt(v)}"
        if comments.get(k):
            line += f"  # {comments[k]}"
        lines.append(line)
    for note in data.get("_notes") or []:
        lines.append(f"#{note}")
    return "\n".join(lines)


def join(*blocks):
    return "\n\n".join(b for b in blocks if b)


# ─── Hardware section renderers ─────────────────────────────────────

def render_header(p):
    yms = p.get('yms_count', 0)
    bed = p.get('bed_size', 0)
    return f"""########################################################################
# {p['name']}
# Bed: {bed}x{bed}mm — Z: {p.get('z_height', 0)}mm — {p.get('printer', {}).get('kinematics', 'cartesian')}
# YMS: {yms}
# Generated by Printer CFG Generator
########################################################################"""


def render_includes(p):
    lines = []
    feat = p.get('features', {})
    if feat.get('plr'):
        lines.append("[include plr.cfg]")
    if feat.get('timelapse'):
        lines.append("[include timelapse.cfg]")
    if feat.get('obico'):
        lines.append("[include moonraker_obico_macros.cfg]")
    return "\n".join(lines)


def render_probe_pressure(p):
    if not p.get('features', {}).get('probe_pressure'):
        return ""
    tap = dict(p.get('z_tap', {}))
    # yumi_z_tap taps at [bed_mesh] zero_reference_position by default (mesh-zero mode). In
    # that mode the extra does not read pressure_switch_x/y and Klipper refuses unread options
    # ("Option 'pressure_switch_x' is not valid"): the switch coordinates are only written for
    # a machine with a dedicated switch off the bed (tap_at_bed_mesh_zero_position: false).
    switch_mode = tap.get('tap_at_bed_mesh_zero_position') is False \
        or not p.get('bed_mesh', {}).get('zero_reference_position')
    if not switch_mode:
        tap.pop('pressure_switch_x', None)
        tap.pop('pressure_switch_y', None)
    return join(emit("probe_pressure", p.get('probe_pressure', {})),
                emit("yumi_z_tap", tap))


def render_head_sensor(p):
    """[yumi_filament_head]: the head sensor pin (as an endstop) and the load/unload settings."""
    return with_module_doc("yumi_filament_head", emit("yumi_filament_head", p.get('filament_head')))


def render_sensorless_homing(p):
    return with_module_doc("yumi_sensorless_homing", emit("yumi_sensorless_homing", p.get('sensorless_homing', {})))


def render_motor_constants(p):
    return join(*(emit(f"motor_constants {name}", mc) for name, mc in p.get('motor_constants', {}).items()))


def render_tmc_autotune(p):
    if not p.get('features', {}).get('tmc_autotune'):
        return ""
    blocks = []
    for axis in ('x', 'y', 'z'):
        at = p.get(f'stepper_{axis}', {}).get('autotune')
        if at and at.get('motor'):
            blocks.append(emit(f"autotune_tmc stepper_{axis}", at, AUTOTUNE_ORDER))
    at = p.get('extruder_stepper', {}).get('autotune')
    if at and at.get('motor'):
        if extruder_motor_slot(p) is not None:
            blocks.append(emit("autotune_tmc extruder", at, AUTOTUNE_ORDER))
        for i, _slot in enumerate(extruder_steppers(p)):
            blocks.append(emit(f"autotune_tmc extruder_stepper extruder{i}", at, AUTOTUNE_ORDER))
    return join(*blocks)


def render_mcu(p):
    return emit("mcu", p.get('mcu', {}))


def render_smartbox_mcu(p):
    if not p.get('has_smartbox', False):
        return ""
    sb = p.get('smartbox', {})
    return emit(f"mcu {sb.get('mcu_name', 'smartbox')}",
                {"serial": sb.get('serial'), "restart_method": sb.get('restart_method', 'command')})


def render_printer(p):
    return emit("printer", p.get('printer', {}))


def render_adxl(p):
    if not p.get('features', {}).get('adxl'):
        return ""
    host = p.get('host_mcu', {})
    name = host.get('name', 'rpi')
    adxl = p.get('adxl', {})
    bed = p.get('bed_size', 0)
    return join(
        emit(f"mcu {name}", {"serial": host.get('serial')}),
        emit("adxl345", {"cs_pin": f"{name}:{adxl.get('cs_pin')}", "spi_bus": adxl.get('spi_bus')}),
        f"[resonance_tester]\naccel_chip: adxl345\nprobe_points:\n    {bed / 2}, {bed / 2}, 20",
        "[input_shaper]")


def render_axis(p, axis):
    s = p.get(f'stepper_{axis}', {})
    return join(emit(f"stepper_{axis}", s, STEPPER_ORDER),
                emit(f"tmc2209 stepper_{axis}", s.get('tmc2209'), TMC_ORDER))


def render_steppers(p):
    return join(*(render_axis(p, axis) for axis in ('x', 'y', 'z')))


def render_thermistors(p):
    return join(*(emit(f"thermistor {t['name']}", t, skip=("name",)) for t in p.get('thermistors', [])))


def all_slots(p):
    """Every E slot of the machine in extruder0..N order: main board first, then the smartbox."""
    slots = [dict(s) for s in p.get('extruder_slots', [])]
    if p.get('has_smartbox'):
        sb = p.get('smartbox', {})
        for slot in sb.get('extruder_slots', []):
            s = dict(slot)
            s['mcu'] = sb.get('mcu_name', 'smartbox')
            slots.append(s)
    return slots


def extruder_motor_slot(p):
    """The slot the [extruder] motor is wired to (direct drive head), or None when the head has
    no motor (CHROMAX: bowden, the YMS feeders push — [extruder] is then declared on free pins)."""
    slot = p.get('extruder', {}).get('motor_slot')
    return None if slot is None else all_slots(p)[int(slot)]


def render_extruder(p):
    e = dict(p.get('extruder', {}))
    if 'nozzle_diameter' in p:
        e['nozzle_diameter'] = p['nozzle_diameter']
    slot = extruder_motor_slot(p)
    if slot is None:
        return emit("extruder", e, EXTRUDER_ORDER, skip=("motor_slot",))
    # Direct drive: real motor on the slot's driver. TMC (and autotune) as any E slot motor.
    e.update({"step_pin": _slot_pin(slot, 'step_pin'), "dir_pin": _slot_pin(slot, 'dir_pin'),
              "enable_pin": _slot_pin(slot, 'enable_pin')})
    tmc = deep_merge(p.get('extruder_stepper', {}).get('tmc2209', {}), {"uart_pin": _slot_pin(slot, 'uart_pin')})
    return join(emit("extruder", e, EXTRUDER_ORDER, skip=("motor_slot",)),
                emit("tmc2209 extruder", tmc, TMC_ORDER))


def extruder_steppers(p):
    """The YMS feeders, in extruder0..N order (none for a direct drive: its motor IS the
    extruder). A head may override a slot (extruder_slot_overrides: direction, ratio) — the
    boards cannot tell which head is mounted, the wizard does."""
    slots = all_slots(p)
    count = p.get('yms_count', 0)
    overrides = p.get('extruder_slot_overrides', {})
    result = []
    for i in range(min(count, len(slots))):
        slot = deep_merge(slots[i], overrides.get(str(i), {}))
        if 'dir_invert' in slot:
            slot['dir_pin'] = ('!' if slot['dir_invert'] else '') + slot['dir_pin'].lstrip('!')
        result.append(slot)
    return result


def _slot_pin(slot, pin):
    """A slot pin on its MCU. Klipper wants the modifiers before the chip: "!smartbox:PB10",
    never "smartbox:!PB10" (which is what a plain prefix produced, and no smartbox cfg could load)."""
    raw = slot[pin]
    mods = ""
    while raw and raw[0] in "!^~":
        mods += raw[0]
        raw = raw[1:]
    mcu = slot.get('mcu')
    prefix = f"{mcu}:" if mcu and mcu != 'main' else ""
    return f"{mods}{prefix}{raw}"


def render_extruder_stepper(p, i, slot):
    common = p.get('extruder_stepper', {})
    data = deep_merge(common, {k: slot[k] for k in slot if k in ('rotation_distance', 'gear_ratio', '_comments')})
    data.update({"extruder": "",
                 "step_pin": _slot_pin(slot, 'step_pin'), "dir_pin": _slot_pin(slot, 'dir_pin'),
                 "enable_pin": _slot_pin(slot, 'enable_pin')})
    tmc = deep_merge(common.get('tmc2209', {}), {"uart_pin": _slot_pin(slot, 'uart_pin')})
    return join(emit(f"extruder_stepper extruder{i}", data, EXTRUDER_STEPPER_ORDER),
                emit(f"tmc2209 extruder_stepper extruder{i}", tmc, TMC_ORDER))


def render_filament_sensor_single(p, slot):
    """Direct drive: one runout sensor on the head slot, pauses the print."""
    return f"""[filament_motion_sensor filament_sensor]
switch_pin: {_slot_pin(slot, 'filament_sensor_pin')}
detection_length: 50
extruder: extruder
pause_on_runout: True
event_delay: 0.0001
runout_gcode:
    {{% set print_state = printer.print_stats.state %}}
    {{% if print_state in ['printing', 'paused'] %}}
        PAUSE
        RESPOND TYPE=error MSG="Filament runout detected!"
        M117 Filament runout detected!
    {{% else %}}
        RESPOND TYPE=error MSG="Filament removed (not printing)"
        M117 Filament removed
    {{% endif %}}
insert_gcode:
    RESPOND MSG="Filament inserted\""""


def render_extruder_steppers(p):
    steppers = extruder_steppers(p)
    if not steppers:
        # Direct drive: no feeder, one runout sensor on the head's slot.
        slot = extruder_motor_slot(p) or all_slots(p)[0]
        return render_filament_sensor_single(p, slot)

    lines = []
    for i, slot in enumerate(steppers):
        lines.append(render_extruder_stepper(p, i, slot))
        lines.append("")

        # Filament sensor — YMS-2 carries the smart motion sensor (jam detection)
        yms_num = i + 1
        sensor_type = "filament_yumi_smart_motion_sensor" if i == 1 else "filament_motion_sensor"
        if sensor_type in DOCUMENTED_MODULES:
            lines.append(module_doc(sensor_type))
        lines.append(f"[{sensor_type} YMS-{yms_num}]")
        lines.append(f"switch_pin: {_slot_pin(slot, 'filament_sensor_pin')}")
        lines.append("detection_length: 50")
        lines.append("pause_on_runout: False")
        lines.append("extruder: extruder")
        # no dead time after an event: a re-insertion right after "INSERT NEXT FILAMENT" would
        # otherwise flip the state without firing (Klipper's default event_delay is 3 s)
        lines.append("event_delay: 0.0001")
        if i == 1:
            lines.append(f"motor: extruder{i}  # this YMS's feeder: a tick while the extruder moves counts only when it drives")
            lines.append("blockage_detection: True")
            lines.append("min_pitch: 0.7")
            lines.append("max_pitch: 2.6")
            lines.append("blockage_threshold: 2")
            lines.append("reset_motion_sensor_threshold: 16")
            lines.append("pitch_view: True")
            lines.append("low_pitch_filter: 0.015")
        lines.append("runout_gcode:")
        lines.append(f"  SAVE_VARIABLE VARIABLE=yms{yms_num}_sensor VALUE=False")
        lines.append("   {% set print_state = printer.print_stats.state %}")
        lines.append("    {% if print_state in ['printing', 'paused'] %}")
        lines.append("        PAUSE")
        lines.append(f"        RESPOND TYPE=error MSG=\"YMS-{yms_num} filament encoder runout\"")
        lines.append("    {% else %}")
        lines.append(f"        RESPOND TYPE=error MSG=\"YMS-{yms_num} No printing running!\"")
        lines.append("    {% endif %}")
        lines.append("insert_gcode:")
        lines.append(f"  SAVE_VARIABLE VARIABLE=yms{yms_num}_sensor VALUE=True")
        lines.append("  {% if printer.save_variables.variables.printing_start %}")
        lines.append(f"        T{i}")
        lines.append("  {% else %}")
        lines.append(f"        T{i+1}")
        lines.append("  {% endif %}")
        lines.append(f"  M117 YMS-{yms_num} Loading")
        lines.append("  LOAD_YMS")
        lines.append("")

        # Dryer after YMS-3
        if i == 2 and p.get('has_dryer') and p.get('has_smartbox'):
            sb = p.get('smartbox', {})
            d = sb.get('dryer', {})
            mcu_name = sb.get('mcu_name', 'smartbox')
            lines.append(f"""[heater_generic YMS-3-PRO]
heater_pin: {mcu_name}:{d.get('heater_pin', 'PC8')}
sensor_type: 100K4190YUMI
sensor_pin: {mcu_name}:{d.get('sensor_pin', 'PC1')}
max_power: 1
control: pid
pid_Kp: 50
pid_Ki: 50
pid_Kd: 50
min_temp: -50
max_temp: 110

[heater_fan dryer_fan]
pin: {mcu_name}:{d.get('fan_pin', 'PC6')}
max_power: 1
off_below: 0.31
heater: YMS-3-PRO
heater_temp: 25
shutdown_speed: 0

[verify_heater YMS-3-PRO]
max_error: 180000
check_gain_time: 3000
hysteresis: 10
heating_gain: 2
""")
    return "\n".join(lines).rstrip()


def render_heater_bed(p):
    return emit("heater_bed", p.get('heater_bed', {}), HEATER_BED_ORDER)


def render_verify_heaters(p):
    vh = p.get('verify_heater', {})
    return join(emit("verify_heater extruder", vh.get('extruder')),
                emit("verify_heater heater_bed", vh.get('heater_bed')))


def render_fans(p):
    f = p.get('fans', {})
    blocks = [emit("fan", f.get('part_cooling')),
              emit("heater_fan hotend_fan", f.get('hotend')),
              emit("controller_fan Motherboard_Fan", f.get('motherboard'))]
    if p.get('has_smartbox'):
        sb = p.get('smartbox', {})
        box = dict(sb.get('fans', {}).get('box', {}))
        if box:
            box['pin'] = f"{sb.get('mcu_name', 'smartbox')}:{box['pin']}"
            blocks.append(emit("controller_fan smartbox_Fan", box))
    blocks.append(emit("fan_generic Aux_Fan", f.get('aux')))
    return join(*blocks)


def render_temp_sensors(p):
    return emit("temperature_sensor NanoPi", p.get('host_temperature_sensor'))


def render_probe(p):
    return emit("probe", p.get('probe', {}), PROBE_ORDER)


def render_bed_scan(p):
    """[yumi_bed_scan]: the inductive scan of the metal reference plate (BED_SCAN_ZERO)."""
    return with_module_doc("yumi_bed_scan", emit("yumi_bed_scan", p.get('bed_scan')))


def render_bed_mesh(p):
    return emit("bed_mesh", p.get('bed_mesh', {}), BED_MESH_ORDER)


def render_screws_tilt(p):
    return emit("screws_tilt_adjust", p.get('screws_tilt', {}), SCREWS_ORDER)


# ─── Macros ─────────────────────────────────────────────────────────

def machine_windows(catalog, board):
    """(model, x_min, x_max, bed_size, z_height) recognition windows of the board's machines,
    from stepper_x.position_max ± detection.machine_x_tolerance. Windows must not overlap."""
    tol = float(catalog["detection"]["machine_x_tolerance"])
    rows = []
    for mid, comp in machines_of(catalog, board):
        x = float(comp["stepper_x"]["position_max"])
        rows.append((mid, x - tol, x + tol, comp.get("bed_size", 0), comp.get("z_height", 0)))
    for a, b in zip(rows, rows[1:]):
        if a[2] >= b[1]:
            raise ValueError(f"machine X windows overlap: {a[0]} and {b[0]} (tolerance {tol})")
    return rows


def render_machine_macro(catalog, board):
    """The single macro every other macro asks for the machine class. Byte-identical for all
    the machines of a board: the class is derived at run time from the X axis length."""
    lines = ['[gcode_macro _YUMI_MACHINE]',
             'description: Single source of the machine class, derived from the X axis length (stepper_x position_max)',
             'variable_model: "UNKNOWN"',
             'variable_bed_size: 0',
             'variable_z_height: 0',
             'gcode:',
             '    {% set x_max = printer.configfile.settings.stepper_x.position_max|float %}']
    for i, (mid, lo, hi, bed, z) in enumerate(machine_windows(catalog, board)):
        kw = "if" if i == 0 else "elif"
        lines.append(f'    {{% {kw} x_max >= {lo:g} and x_max <= {hi:g} %}}')
        lines.append(f'        {{% set model, bed_size, z_height = "{mid}", {bed}, {z} %}}')
    lines += ['    {% else %}',
              '        {% set model, bed_size, z_height = "UNKNOWN", 0, 0 %}',
              '    {% endif %}',
              '    SET_GCODE_VARIABLE MACRO=_YUMI_MACHINE VARIABLE=model VALUE="\'{model}\'"',
              '    SET_GCODE_VARIABLE MACRO=_YUMI_MACHINE VARIABLE=bed_size VALUE={bed_size}',
              '    SET_GCODE_VARIABLE MACRO=_YUMI_MACHINE VARIABLE=z_height VALUE={z_height}',
              '    {% if model == "UNKNOWN" %}',
              '        RESPOND TYPE=error MSG="_YUMI_MACHINE: X axis {x_max}mm matches no known machine"',
              '    {% endif %}']
    return "\n".join(lines)


def render_product_macro(catalog, p):
    """What this printer.cfg was generated for — the head and hotend chosen in the wizard (or
    imposed by a detected smartbox) and the nozzle. Labels are the slicer's (slicer_label of the
    catalog components), PRINT_START compares the sliced file against them. No machine size
    here: that is _YUMI_MACHINE, so this macro is identical on every size of a product line."""
    labels = {}
    for cid in p.get("_chain", []):
        comp = catalog["components"].get(cid, {})
        if comp.get("layer") in ("hotend", "hotend_type") and comp.get("slicer_label"):
            labels[comp["layer"]] = comp["slicer_label"]
    return "\n".join([
        "[gcode_macro _YUMI_PRODUCT]",
        "description: What this printer.cfg was generated for (wizard choice / detected smartbox); PRINT_START checks the sliced file against it",
        'variable_head: "%s"' % labels.get("hotend", ""),
        'variable_hotend: "%s"' % labels.get("hotend_type", ""),
        "variable_nozzle: %s" % p.get("nozzle_diameter", 0),
        "gcode:",
        '    RESPOND MSG="Head: {printer[\'gcode_macro _YUMI_PRODUCT\'].head} | Hotend: {printer[\'gcode_macro _YUMI_PRODUCT\'].hotend} | Nozzle: {printer[\'gcode_macro _YUMI_PRODUCT\'].nozzle}"',
    ])


def render_gcode_macros(p):
    """Render gcode macros from the catalog JSON."""
    blocks = []
    for macro in p.get('gcode_macros', []):
        if check_condition(macro.get('condition', 'always'), p):
            blocks.append(macro['gcode'])
    return "\n\n".join(blocks)


def render_yms_tool_macros(p):
    """Generate T0-TN, TOFF, CURRENT_UNLOAD, SET_PRESSURE_ADVANCE for YMS."""
    yms_count = p.get('yms_count', 0)
    if yms_count == 0:
        return ""

    lines = []
    all_ext = [f"extruder{i}" for i in range(yms_count)]

    # TOFF
    lines.append("[gcode_macro TOFF]")
    lines.append("description: Disable all extruders")
    lines.append("gcode:")
    for i in range(yms_count):
        lines.append(f"    SET_FILAMENT_SENSOR SENSOR=YMS-{i+1} ENABLE=0")
    lines.append("    SYNC_EXTRUDER_MOTION EXTRUDER=extruder MOTION_QUEUE=extruder")
    for name in all_ext:
        lines.append(f"    SYNC_EXTRUDER_MOTION EXTRUDER={name} MOTION_QUEUE=\"\"")
    lines.append("    RESPOND MSG=\"DISABLE ALL YMS\"")
    lines.append("")

    # T0 (special — init mode)
    lines.append("[gcode_macro T0]")
    lines.append("description: ACTIVATE YMS-1 OR INSERT_DETECTION")
    lines.append("gcode:")
    lines.append("  {% set svv = printer.save_variables.variables %}")
    lines.append("  {% if printer.save_variables.variables.printing_start %}")
    for i in range(yms_count):
        lines.append(f"    SET_FILAMENT_SENSOR SENSOR=YMS-{i+1} ENABLE={'1' if i == 0 else '0'}")
    lines.append("    SYNC_EXTRUDER_MOTION EXTRUDER=extruder MOTION_QUEUE=extruder")
    lines.append("    SYNC_EXTRUDER_MOTION EXTRUDER=extruder0 MOTION_QUEUE=extruder")
    for name in all_ext[1:]:
        lines.append(f"    SYNC_EXTRUDER_MOTION EXTRUDER={name} MOTION_QUEUE=\"\"")
    lines.append("    RESPOND MSG=\"ACTIVATION YMS-1\"")
    lines.append("    SAVE_VARIABLE VARIABLE=active_tool VALUE=1")
    lines.append("    YUMI_LOAD_TO_HEAD")
    lines.append("  {% else %}")
    lines.append("    RESPOND MSG=\"YMS INITIALISATION STARTING\"")
    lines.append("    G92 E0")
    lines.append("    SYNC_EXTRUDER_MOTION EXTRUDER=extruder MOTION_QUEUE=extruder")
    for name in all_ext:
        lines.append(f"    SYNC_EXTRUDER_MOTION EXTRUDER={name} MOTION_QUEUE=\"\"")
    lines.append("    SAVE_VARIABLE VARIABLE=active_tool VALUE=0")
    lines.append("    MOTION_SENSOR_INIT")
    for i in range(yms_count):
        lines.append(f"    SET_FILAMENT_SENSOR SENSOR=YMS-{i+1} ENABLE=1")
    lines.append("    M400")
    lines.append("    SAVE_VARIABLE VARIABLE=yms_sensor_initialisation VALUE=True")
    lines.append("    RESPOND MSG=\"INSERT NEXT FILAMENT\"")
    lines.append("  {% endif %}")
    lines.append("")

    # T1..TN
    for t in range(1, yms_count):
        yms_num = t + 1
        lines.append(f"[gcode_macro T{t}]")
        lines.append("gcode:")
        lines.append("  {% set svv = printer.save_variables.variables %}")
        lines.append("  {% if printer.save_variables.variables.printing_start %}")
        for i in range(yms_count):
            lines.append(f"      SET_FILAMENT_SENSOR SENSOR=YMS-{i+1} ENABLE={'1' if i == t else '0'}")
        lines.append("      SYNC_EXTRUDER_MOTION EXTRUDER=extruder MOTION_QUEUE=extruder")
        for i, name in enumerate(all_ext):
            q = "extruder" if i == t else '""'
            lines.append(f"      SYNC_EXTRUDER_MOTION EXTRUDER={name} MOTION_QUEUE={q}")
        lines.append(f"  RESPOND MSG=\"ACTIVATION YMS-{yms_num}\"")
        lines.append("      YUMI_LOAD_TO_HEAD")
        lines.append("  {% else %}")
        prev = t - 1
        for i in range(yms_count):
            lines.append(f"      SET_FILAMENT_SENSOR SENSOR=YMS-{i+1} ENABLE={'1' if i == prev else '0'}")
        lines.append("      SYNC_EXTRUDER_MOTION EXTRUDER=extruder MOTION_QUEUE=extruder")
        for i, name in enumerate(all_ext):
            q = "extruder" if i == prev else '""'
            lines.append(f"      SYNC_EXTRUDER_MOTION EXTRUDER={name} MOTION_QUEUE={q}")
        lines.append("  {% endif %}")
        lines.append(f"  SAVE_VARIABLE VARIABLE=active_tool VALUE={t}")
        lines.append("")

    # CURRENT_UNLOAD
    lines.append("[gcode_macro CURRENT_UNLOAD]")
    lines.append("gcode:")
    for name in all_ext:
        lines.append(f"      SET_TMC_CURRENT STEPPER={name} CURRENT=0.5 HOLDCURRENT=0.5")
    lines.append("")

    # SET_PRESSURE_ADVANCE override: the same setting goes to the extruder and every feeder.
    # Every parameter is forwarded as given (ADVANCE, SMOOTH_TIME, LEAD_TIME, BACKLASH_*...):
    # a macro that only re-emitted the names it knew silently dropped the new ones.
    lines.append("[gcode_macro SET_PRESSURE_ADVANCE]")
    lines.append("rename_existing: SET_PA_ORIG")
    lines.append("description: Applies the given parameters to the extruder and to every YMS feeder (EXTRUDER=name targets one)")
    lines.append("gcode:")
    lines.append("    {% set args = [] %}")
    lines.append("    {% for k, v in params.items() if k != 'EXTRUDER' %}{% set _ = args.append(k ~ '=' ~ v) %}{% endfor %}")
    lines.append("    {% set argstr = args|join(' ') %}")
    lines.append("    {% if params.EXTRUDER is defined %}")
    lines.append("      SET_PA_ORIG EXTRUDER={params.EXTRUDER} {argstr}")
    lines.append("    {% elif argstr %}")
    lines.append("      SET_PA_ORIG EXTRUDER=extruder {argstr}")
    for name in all_ext:
        lines.append(f"      SET_PA_ORIG EXTRUDER={name} {{argstr}}")
    lines.append("    {% else %}")
    lines.append("      SET_PA_ORIG EXTRUDER=extruder")
    lines.append("    {% endif %}")

    return "\n".join(lines)


def render_save_config():
    return """#*# <---------------------- SAVE_CONFIG ---------------------->
#*# DO NOT EDIT THIS BLOCK OR BELOW. The contents are auto-generated.
#*#"""


# ─── Assembler ──────────────────────────────────────────────────────

def generate(product_id, overrides=None, catalog=None):
    """Render the printer.cfg of a catalog product.

    overrides: optional dict deep-merged over the resolved product, used by compose.py to
    inject the serial ports actually detected on the pad without touching the catalog.
    """
    catalog = catalog or load_catalog()
    p = resolve_product(product_id, catalog)
    if overrides:
        p = deep_merge(p, overrides)

    sections = [
        render_header(p),
        render_includes(p),
        render_probe_pressure(p),
        render_head_sensor(p),
        render_sensorless_homing(p),
        render_motor_constants(p),
        render_tmc_autotune(p),
        render_mcu(p),
        render_smartbox_mcu(p),
        render_printer(p),
        render_adxl(p),
        render_steppers(p),
        render_thermistors(p),
        render_extruder(p),
        render_extruder_steppers(p),
        render_heater_bed(p),
        render_verify_heaters(p),
        render_fans(p),
        render_temp_sensors(p),
        render_probe(p),
        render_bed_scan(p),
        render_bed_mesh(p),
        render_screws_tilt(p),
        render_machine_macro(catalog, p["_board"]),
        render_product_macro(catalog, p),
        render_gcode_macros(p),
        render_yms_tool_macros(p),
        render_save_config(),
    ]
    output = join(*sections)
    while "\n\n\n" in output:
        output = output.replace("\n\n\n", "\n\n")
    return output.rstrip() + "\n"


def list_products():
    catalog = load_catalog()
    for pid, prod in catalog.get("products", {}).items():
        if isinstance(prod, dict) and "chain" in prod:
            print(f"  {pid:28s} {prod['name']}")


def main():
    ap = argparse.ArgumentParser(description="Render a printer.cfg from the YUMI product catalog")
    ap.add_argument("product", nargs="?", help="product id (see --list)")
    ap.add_argument("-o", "--output", help="write to this file instead of stdout")
    ap.add_argument("--list", action="store_true", help="list the products of the catalog")
    args = ap.parse_args()
    if args.list or not args.product:
        list_products()
        return 0
    cfg = generate(args.product)
    if args.output:
        Path(args.output).write_text(cfg, encoding="utf-8")
        print(f"written: {args.output} ({len(cfg.splitlines())} lines)")
    else:
        sys.stdout.write(cfg)
    return 0


if __name__ == "__main__":
    sys.exit(main())
