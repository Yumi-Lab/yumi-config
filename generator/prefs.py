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
import compose    # noqa: E402
import generator  # noqa: E402

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


def run_autoconfig(extra_args=(), timeout=420):
    """Scan the boards and install the matching printer.cfg (autoconfig.py: Klipper stopped
    and started through Moonraker). Returns (exit code, compose summary or None, log)."""
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
