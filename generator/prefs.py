#!/usr/bin/env python3
"""
Wizard support — what the pad knows about itself, what the user may choose, how it is applied.

Shared by the KlipperScreen panel (cfg_wizard.py) and the tests; no GTK here. Everything the
user can choose is a selection layer of the catalog: the print head cannot be read from the
boards (a direct drive and a CHROMAX X12 answer the same), the machine only when the firmware
descriptor names one.

    state   detection.state_file   written by compose.py at every autoconfig run
    prefs   detection.prefs_file   written here, read by compose.select()
"""
import json
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import urllib.parse
import urllib.request

import autoconfig  # noqa: E402  (MOONRAKER url)
import compose     # noqa: E402
import generator   # noqa: E402

# Layers the wizard offers, in screen order. "machine" is added when the boards name none.
HEAD_LAYERS = ("hotend", "hotend_type", "nozzle")
MACHINE_LAYER = "machine"

SITUATION_YUMI, SITUATION_UNKNOWN, SITUATION_NONE = "yumi", "unknown", "none"


def state_path(catalog, config_dir):
    return Path(config_dir) / catalog["detection"]["state_file"]


def prefs_path(catalog, config_dir):
    return Path(config_dir) / catalog["detection"]["prefs_file"]


def load_state(catalog, config_dir):
    return compose.load_json(state_path(catalog, config_dir), {}) or {}


def load_prefs(catalog, config_dir):
    return compose.load_json(prefs_path(catalog, config_dir), {}) or {}


def save_prefs(catalog, config_dir, prefs):
    compose.atomic_write(prefs_path(catalog, config_dir), json.dumps(prefs, indent=2, ensure_ascii=False) + "\n")


def layer_label(catalog, layer):
    for entry in catalog.get("selection_layers", []):
        if entry.get("id") == layer:
            return entry.get("label", layer)
    return layer


def layer_options(catalog, layer, board=None):
    """[(component id, label)] of a selection layer, in catalog order; a component declared
    incompatible with the machine's board (e.g. a HyperDrive head on a Smart Maker) is not offered."""
    return [(cid, c.get("name", cid)) for cid, c in catalog["components"].items()
            if isinstance(c, dict) and c.get("layer") == layer
            and not (board and board in c.get("incompatible_with", []))]


def board_of(catalog, state):
    """The board component of the machine in the state: from the main board's descriptor, else
    from the machine the wizard named."""
    main = state.get("main") or {}
    rules = catalog["detection"]
    board = rules["main_boards"].get(main.get("board"))
    if not board and main.get("device") in catalog["components"]:
        board = catalog["components"][main["device"]].get("parent")
    return board


def situation(state):
    """yumi: the main board names a machine of the catalog. unknown: boards answer but none
    does (the wizard names the machine). none: no board answered, or never scanned."""
    if state.get("situation") in (SITUATION_YUMI, SITUATION_UNKNOWN, SITUATION_NONE):
        return state["situation"]
    return SITUATION_UNKNOWN if (state.get("composition") or {}).get("boards") else SITUATION_NONE


def selection(catalog, state, prefs):
    """Effective choice per layer: prefs, else the catalog default. With a smartbox the head is
    imposed by the detection (detection.with_smartbox) and shown as forced."""
    rules = catalog["detection"]
    defaults = rules["defaults"]
    forced_head = state.get("smartbox") is not None
    board = board_of(catalog, state)
    out = {}
    if situation(state) == SITUATION_UNKNOWN:
        out[MACHINE_LAYER] = {"value": prefs.get(MACHINE_LAYER), "forced": False,
                              "options": layer_options(catalog, MACHINE_LAYER)}
    for layer in HEAD_LAYERS:
        value = prefs.get(layer, defaults.get(layer))
        forced = forced_head and layer == "hotend"
        if forced:
            value = rules["with_smartbox"]["hotend"]
        out[layer] = {"value": value, "forced": forced, "options": layer_options(catalog, layer, board)}
    return out


def describe(catalog, state):
    """The header lines of the screen: what the last scan found."""
    sit = situation(state)
    if sit == SITUATION_NONE:
        return ["No board answered." if state else "Boards not scanned yet.",
                "Check the RJ11 cable of the main board, then scan."]
    lines = []
    main = state.get("main") or {}
    if sit == SITUATION_YUMI:
        name = catalog["components"].get(main.get("device"), {}).get("name", main.get("device"))
        lines.append("%s — board %s on %s (uid %s)" % (name, main.get("board"), main.get("port"), main.get("uid")))
    else:
        for b in (state.get("composition") or {}).get("boards", []):
            lines.append("Board %s on %s answers but names no known machine (device %s)"
                         % (b.get("board") or "?", b.get("port"), b.get("device") or "-"))
    sb = state.get("smartbox")
    if sb:
        lines.append("Smartbox %s on %s: CHROMAX X12 with 7 YMS" % (sb.get("device"), sb.get("port")))
    if state.get("product"):
        lines.append("Current printer.cfg: %s (%s, %s)" % (state["product"], state.get("mode"), state.get("ts")))
    return lines


HEAD_SENSOR_OBJECT = "yumi_filament_head"
# YUMI_SETUP / CLI keys -> selection layers
SETUP_KEYS = {"HEAD": "hotend", "HOTEND": "hotend_type", "NOZZLE": "nozzle", "MACHINE": MACHINE_LAYER}
HEAD_SENSOR_BYPASS_CMD = "SET_HEAD_SENSOR_BYPASS ENABLE=%d"
# filament cutter: CUT_FILAMENT reads this saved variable at every call (1 = skipped); the
# slicer G-code keeps calling it, the printer decides, live during a print
CUTTER_BYPASS_VARIABLE = "cut_filament_bypass"
CUTTER_BYPASS_CMD = "SET_CUT_FILAMENT_BYPASS ENABLE=%d"


def head_sensor_state():
    """{'bypass': bool, 'present': bool|None} from Klipper through Moonraker, None if unavailable
    (Klipper down, or the module not in this cfg)."""
    try:
        url = "%s/printer/objects/query?%s" % (autoconfig.MOONRAKER, HEAD_SENSOR_OBJECT)
        with urllib.request.urlopen(url, timeout=5) as r:
            st = json.load(r)["result"]["status"].get(HEAD_SENSOR_OBJECT)
    except Exception:
        return None
    if not st:
        return None
    return {"bypass": bool(st.get("bypass")), "present": st.get("present")}


def set_head_sensor_bypass(enable):
    """Broken head sensor: loading still runs, blind; detection is skipped. Persisted by Klipper."""
    script = urllib.parse.quote(HEAD_SENSOR_BYPASS_CMD % (1 if enable else 0))
    req = urllib.request.Request("%s/printer/gcode/script?script=%s" % (autoconfig.MOONRAKER, script), method="POST")
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.load(r).get("result") == "ok"


def cutter_state():
    """{'bypass': bool} from the saved variables through Moonraker, None if unavailable (Klipper
    down) or when this cfg has no CUT_FILAMENT macro."""
    try:
        url = "%s/printer/objects/query?save_variables&configfile=settings" % autoconfig.MOONRAKER
        with urllib.request.urlopen(url, timeout=5) as r:
            st = json.load(r)["result"]["status"]
    except Exception:
        return None
    if "gcode_macro cut_filament" not in (st.get("configfile") or {}).get("settings", {}):
        return None
    variables = (st.get("save_variables") or {}).get("variables") or {}
    return {"bypass": int(variables.get(CUTTER_BYPASS_VARIABLE, 0) or 0) == 1}


def set_cutter_bypass(enable):
    """Skip every CUT_FILAMENT call (1) or restore the cut (0); persisted by Klipper."""
    script = urllib.parse.quote(CUTTER_BYPASS_CMD % (1 if enable else 0))
    req = urllib.request.Request("%s/printer/gcode/script?script=%s" % (autoconfig.MOONRAKER, script), method="POST")
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.load(r).get("result") == "ok"


def resolve_option(catalog, layer, text):
    """The component of a layer named by its id, its name or its slicer label, case-insensitive
    ("CHROMAX_X12", "ChromaX12", "chromax x12" all work). None if nothing matches."""
    wanted = str(text).strip().lower().replace(" ", "").replace("-", "_")
    for cid, comp in catalog["components"].items():
        if not isinstance(comp, dict) or comp.get("layer") != layer:
            continue
        names = [cid, comp.get("name", ""), comp.get("slicer_label", "")]
        if wanted in [n.lower().replace(" ", "").replace("-", "_") for n in names if n]:
            return cid
    return None


def apply_settings(catalog, config_dir, settings):
    """One line of KEY=VALUE (YUMI_SETUP HEAD=... HOTEND=... NOZZLE=... MACHINE=... HEAD_SENSOR=0|1 CUTTER=0|1):
    writes the preferences (and the head sensor / cutter bypass through Klipper). Returns what changed."""
    prefs = load_prefs(catalog, config_dir)
    changed, errors = {}, []
    for key, value in settings.items():
        key = key.upper()
        if key == "HEAD_SENSOR":
            enabled = str(value).strip().lower() in ("1", "true", "on", "enabled", "yes")
            set_head_sensor_bypass(not enabled)
            changed["head_sensor"] = "enabled" if enabled else "bypassed"
        elif key == "CUTTER":
            enabled = str(value).strip().lower() in ("1", "true", "on", "enabled", "yes")
            set_cutter_bypass(not enabled)
            changed["cutter"] = "enabled" if enabled else "bypassed"
        elif key in SETUP_KEYS:
            layer = SETUP_KEYS[key]
            cid = resolve_option(catalog, layer, value)
            if cid is None:
                errors.append("%s=%s: no %s named like that (%s)" % (
                    key, value, layer, ", ".join(o[0] for o in layer_options(catalog, layer))))
            else:
                prefs[layer] = cid
                changed[layer] = cid
        else:
            errors.append("%s: unknown setting (HEAD, HOTEND, NOZZLE, MACHINE, HEAD_SENSOR, CUTTER)" % key)
    if errors:
        raise ValueError("; ".join(errors))
    if any(k not in ("head_sensor", "cutter") for k in changed):
        save_prefs(catalog, config_dir, prefs)
    return changed


BUSY_PRINT_STATES = ("printing", "paused")


def print_state():
    """print_stats.state through Moonraker ('printing', 'paused', 'standby', ...), None if unknown."""
    try:
        with urllib.request.urlopen("%s/printer/objects/query?print_stats=state" % autoconfig.MOONRAKER, timeout=5) as r:
            return json.load(r)["result"]["status"]["print_stats"]["state"]
    except Exception:
        return None


def restart_klipper():
    """Ask Moonraker to restart the Klipper service: its ExecStartPre (autoconfig --boot)
    regenerates printer.cfg from the boards and the preferences before Klipper starts.
    Refused while a print runs or is paused (a restart kills it; bench, 2026-09-06 23:26)."""
    state = print_state()
    if state in BUSY_PRINT_STATES:
        raise RuntimeError("Klipper not restarted: a print is %s" % state)
    req = urllib.request.Request("%s/machine/services/restart?service=klipper" % autoconfig.MOONRAKER, method="POST")
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r).get("result") == "ok"


def run_autoconfig(extra_args=(), timeout=420):
    """Scan the boards and install the matching printer.cfg (autoconfig.py: Klipper stopped
    and started through Moonraker). Returns (exit code, compose summary or None, log).
    Refused while a print runs or is paused."""
    state = print_state()
    if state in BUSY_PRINT_STATES:
        return 3, None, "not now: a print is %s (Klipper would be stopped)" % state
    cmd = [sys.executable, str(HERE / "autoconfig.py"), *extra_args]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    summary = None
    for line in reversed(r.stdout.strip().splitlines()):
        try:
            summary = json.loads(line)
            break
        except ValueError:
            continue
    return r.returncode, summary, r.stderr


def result_lines(code, summary, log):
    """What to show once autoconfig ran."""
    if summary is None:
        return ["Configuration failed (exit %s)." % code] + [l for l in (log or "").strip().splitlines()[-6:]]
    lines = []
    if summary.get("alert"):
        lines.append("Nothing written: %s" % summary["alert"])
    elif code == compose.EXIT_UNCHANGED:
        lines.append("printer.cfg already matches the boards: nothing changed.")
    elif summary.get("minimal"):
        lines.append("Minimal printer.cfg written: Klipper connects, nothing can move.")
    else:
        lines.append("printer.cfg written: %s (%s)" % (summary.get("product"), summary.get("mode")))
    if summary.get("backup"):
        lines.append("Previous cfg kept as %s" % Path(summary["backup"]).name)
    lines += summary.get("reasons", [])
    return lines


def main():
    import argparse
    ap = argparse.ArgumentParser(description="Declare the machine's configuration (what the Printer Config panel does)")
    ap.add_argument("--set", nargs="+", metavar="KEY=VALUE", default=[],
                    help="HEAD= HOTEND= NOZZLE= MACHINE= HEAD_SENSOR=0|1 CUTTER=0|1 (ids, names or slicer labels)")
    ap.add_argument("--apply", action="store_true", help="restart Klipper through Moonraker: printer.cfg is regenerated")
    ap.add_argument("--config-dir", default=str(compose.DEFAULT_CONFIG_DIR))
    a = ap.parse_args()
    catalog = generator.load_catalog()
    settings = dict(kv.split("=", 1) for kv in a.set if "=" in kv)
    try:
        changed = apply_settings(catalog, a.config_dir, settings) if settings else {}
    except ValueError as e:
        print(json.dumps({"error": str(e)}))
        return 2
    out = {"changed": changed, "prefs": load_prefs(catalog, a.config_dir)}
    if a.apply:
        try:
            out["klipper_restart"] = restart_klipper()
        except RuntimeError as e:
            out["klipper_restart"] = False
            out["error"] = str(e)
            print(json.dumps(out))
            return 3
    print(json.dumps(out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
