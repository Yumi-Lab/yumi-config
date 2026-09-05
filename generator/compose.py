#!/usr/bin/env python3
"""
compose — from the hardware composition of a pad to the printer.cfg that matches it.

    yumi-detect.py  ->  ~/.yumi_composition.json          facts only: boards, cameras
    compose.py      ->  printer_data/config/printer.cfg    + .detected_hardware.json

The scanner is dumb and factual. Every decision lives here, driven by the
"detection" rules of YUMI-LAB_product-catalog.json:

  - which catalog product the boards describe: the main board says device=C235/C335/C435,
    a HyperDrive board present means CHROMAX X12 + 7 YMS Pro, the print head type and the
    nozzle come from the user preferences file (defaults in the catalog);
  - which serial ports go into [mcu] and [mcu smartbox]: the ones that actually answered;
  - the policy against the previous state (.detected_hardware.json):
        same main board (uid)   PRESERVE  keep the SAVE_CONFIG block of the current cfg
        different main board    FACTORY   fresh cfg, calibrations dropped
        no main board           ALERT     never destructive
        unknown product         MINIMAL   [mcu] sections + kinematics none, Klipper connects

Runs at every boot (yumi-autoconfig.service, before Klipper): the boards are compared with
the ones recorded in .detected_hardware.json and, when the hardware has not changed, nothing
is generated and printer.cfg is not touched — a cfg edited by hand stays as it is until a
board is added, removed or replaced. --factory forces a fresh cfg whatever the state.

Exit codes: 0 applied, 2 alert (no usable main board), 3 minimal cfg written, 4 nothing to do.

Usage:
    compose.py [--composition FILE] [--config-dir DIR] [--dry-run] [--factory] [--minimal]
"""
import argparse
import datetime
import hashlib
import importlib.util
import json
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import generator  # noqa: E402  (load_catalog, generate, deep_merge)

SAVE_CONFIG_MARKER = generator.render_save_config().splitlines()[0]
DEFAULT_CONFIG_DIR = Path.home() / "printer_data" / "config"

EXIT_APPLIED, EXIT_ALERT, EXIT_MINIMAL, EXIT_UNCHANGED = 0, 2, 3, 4


def _yumi_detect_default_out():
    """The composition file is owned by yumi-detect.py: read its DEFAULT_OUT, never retype it."""
    spec = importlib.util.spec_from_file_location("yumi_detect", HERE / "yumi-detect.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return Path(mod.DEFAULT_OUT)


def load_json(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


# ─── Selection: composition -> product ──────────────────────────────

def classify_boards(composition, catalog):
    """Split the detected boards into main board, smartbox and the rest."""
    rules = catalog["detection"]
    machines = {k for k, v in catalog["components"].items()
                if isinstance(v, dict) and v.get("layer") == "machine"}
    main, main_unknown, smartbox, others = None, None, None, []
    for b in composition.get("boards", []):
        # A HyperDrive is a Smart Maker board flashed as device=HYPERDRIVE_3P2L: the device
        # decides, so the smartbox test comes before the main-board one.
        if b.get("device") in rules["smartbox_devices"]:
            if smartbox is None:
                smartbox = b
            else:
                others.append(b)
        elif b.get("board") in rules["main_boards"]:
            if b.get("device") in machines:
                if main is None:
                    main = b
                else:
                    others.append(b)
            elif main_unknown is None:
                main_unknown = b
            else:
                others.append(b)
        else:
            others.append(b)
    return main, main_unknown, smartbox, others


def hardware_fingerprint(composition):
    """What identifies the machine: which board (uid, device) answers on which port.
    Cameras are left out on purpose: plugging a webcam must not rewrite printer.cfg."""
    return sorted((b.get("port") or "", b.get("uid") or "", b.get("device") or "")
                  for b in composition.get("boards", []))


def find_product(catalog, chain):
    wanted = set(chain)
    for pid, prod in catalog["products"].items():
        if isinstance(prod, dict) and set(prod.get("chain", [])) == wanted:
            return pid
    return None


def select(composition, catalog, prefs=None):
    """Decide what to generate. Returns a dict with product/chain/overrides or minimal/alert."""
    prefs = prefs or {}
    rules = catalog["detection"]
    main, main_unknown, smartbox, others = classify_boards(composition, catalog)
    sel = {"main": main, "smartbox": smartbox, "others": others,
           "product": None, "chain": None, "overrides": None, "minimal": False, "alert": None,
           "reasons": []}

    if main is None:
        if main_unknown is not None:
            sel["main"] = main_unknown
            sel["minimal"] = True
            sel["reasons"].append("main board %s answers on %s but device=%r is not a machine of the catalog"
                                  % (main_unknown.get("board"), main_unknown.get("port"), main_unknown.get("device")))
        elif smartbox is not None:
            sel["alert"] = "smartbox %s answers on %s but no main board does" % (smartbox.get("device"), smartbox.get("port"))
        elif composition.get("boards"):
            sel["alert"] = "boards answer but none is a known main board: %s" % ", ".join(
                "%s@%s" % (b.get("device") or b.get("board") or "?", b.get("port")) for b in composition["boards"])
        else:
            sel["alert"] = "no MCU answered"
        return sel

    board_comp = rules["main_boards"][main["board"]]
    defaults = rules["defaults"]
    if smartbox is not None:
        hotend, yms = rules["with_smartbox"]["hotend"], rules["with_smartbox"]["yms"]
        sel["reasons"].append("smartbox %s on %s -> %s + %s" % (smartbox.get("device"), smartbox.get("port"), hotend, yms))
    else:
        hotend = prefs.get("hotend", defaults["hotend"])
        yms = rules["chromax_without_smartbox_yms"] if hotend == rules["with_smartbox"]["hotend"] else None
        sel["reasons"].append("no smartbox -> hotend %s (%s)" % (hotend, "preference" if "hotend" in prefs else "default"))
    hotend_type = prefs.get("hotend_type", defaults["hotend_type"])
    nozzle = prefs.get("nozzle", defaults["nozzle"])

    chain = [board_comp, main["device"], hotend, hotend_type, nozzle] + ([yms] if yms else [])
    product = find_product(catalog, chain)
    sel["chain"] = chain
    if product is None:
        sel["minimal"] = True
        sel["reasons"].append("no product in the catalog for chain %s" % " + ".join(chain))
        return sel

    overrides = {"mcu": {"serial": main["port"]}}
    if smartbox is not None:
        overrides["smartbox"] = {"serial": smartbox["port"]}
    sel["product"] = product
    sel["overrides"] = overrides
    return sel


# ─── Rendering ──────────────────────────────────────────────────────

def render_minimal(sel, catalog, config_dir):
    """[mcu] sections for the boards that answered + kinematics none: Klipper connects, nothing moves."""
    main, smartbox = sel["main"], sel["smartbox"]
    lines = ["# printer.cfg minimal — written by compose.py: the boards answer but no catalog",
             "# product matches yet. Klipper connects so the boards can be read (DEVICE, TMC);",
             "# nothing can move with this configuration.",
             "# " + "; ".join(sel["reasons"]), ""]
    if (Path(config_dir) / "yumi-device.cfg").exists():
        lines += ["[include yumi-device.cfg]", ""]
    lines += ["[mcu]", "serial: %s" % main["port"], "restart_method: command", ""]
    if smartbox is not None:
        board_comp = catalog["detection"]["main_boards"].get(main.get("board"))
        sb = catalog["components"].get(board_comp, {}).get("smartbox", {}) if board_comp else {}
        lines += ["[mcu %s]" % sb.get("mcu_name", "smartbox"), "serial: %s" % smartbox["port"],
                  "restart_method: command", ""]
    lines += ["[printer]", "kinematics: none", "max_velocity: 1", "max_accel: 1", ""]
    return "\n".join(lines)


def save_config_block(cfg_text):
    """The SAVE_CONFIG block of an existing printer.cfg (marker to end of file), or None."""
    if not cfg_text:
        return None
    i = cfg_text.find(SAVE_CONFIG_MARKER)
    return cfg_text[i:] if i >= 0 else None


def with_save_config(cfg_text, block):
    """Replace the empty SAVE_CONFIG tail of a generated cfg with a preserved block."""
    i = cfg_text.find(SAVE_CONFIG_MARKER)
    if i < 0 or not block:
        return cfg_text
    return cfg_text[:i] + block.rstrip("\n") + "\n"


# ─── Apply ──────────────────────────────────────────────────────────

def atomic_write(path, text):
    tmp = str(path) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
    os.replace(tmp, path)


def build(composition, catalog, config_dir, prefs=None, factory=False, minimal=False):
    """Everything but the disk writes: returns (exit_code, summary, new_cfg_text or None)."""
    config_dir = Path(config_dir)
    rules = catalog["detection"]
    state = load_json(config_dir / rules["state_file"], {}) or {}
    current = None
    try:
        current = (config_dir / "printer.cfg").read_text(encoding="utf-8")
    except OSError:
        pass

    sel = select(composition, catalog, prefs)
    summary = {"main": sel["main"], "smartbox": sel["smartbox"], "others": sel["others"],
               "product": sel["product"], "chain": sel["chain"], "reasons": sel["reasons"],
               "mode": None, "alert": sel["alert"], "minimal": False}

    # Same boards on the same ports as last time -> the machine has not changed: leave
    # printer.cfg alone (it may carry manual edits and calibrations), unless forced.
    recorded = state.get("composition")
    if (not factory and not minimal and recorded is not None and current is not None
            and hardware_fingerprint(recorded) == hardware_fingerprint(composition)):
        summary["mode"] = "unchanged"
        summary["reasons"].append("same boards as recorded on %s: printer.cfg left untouched" % state.get("ts"))
        return EXIT_UNCHANGED, summary, None

    if sel["alert"]:
        summary["mode"] = "alert"
        return EXIT_ALERT, summary, None

    if minimal or sel["minimal"]:
        summary["minimal"] = True
        summary["mode"] = "minimal"
        return EXIT_MINIMAL, summary, render_minimal(sel, catalog, config_dir)

    prev_uid = (state.get("main") or {}).get("uid")
    new_uid = sel["main"].get("uid")
    if factory:
        mode = "factory"
    elif prev_uid and new_uid and prev_uid != new_uid:
        mode = "factory"
        sel["reasons"].append("main board changed (uid %s -> %s): factory reset" % (prev_uid, new_uid))
    else:
        mode = "preserve"
    summary["mode"] = mode

    cfg = generator.generate(sel["product"], sel["overrides"])
    if mode == "preserve":
        block = save_config_block(current)
        if block:
            cfg = with_save_config(cfg, block)
            sel["reasons"].append("SAVE_CONFIG block preserved")
    if current is not None and cfg == current:
        return EXIT_UNCHANGED, summary, cfg
    return EXIT_APPLIED, summary, cfg


def apply(composition, catalog, config_dir, prefs=None, factory=False, minimal=False, dry_run=False):
    config_dir = Path(config_dir)
    rules = catalog["detection"]
    code, summary, cfg = build(composition, catalog, config_dir, prefs, factory, minimal)
    summary["dry_run"] = dry_run
    summary["written"] = False
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")

    if cfg is not None and code in (EXIT_APPLIED, EXIT_MINIMAL) and not dry_run:
        target = config_dir / "printer.cfg"
        if target.exists():
            backup = config_dir / rules["backup_pattern"].format(ts=ts)
            os.replace(target, backup)
            summary["backup"] = str(backup)
        atomic_write(target, cfg)
        summary["written"] = True

    if not dry_run:
        state = {"ts": ts, "main": summary["main"], "smartbox": summary["smartbox"],
                 "product": summary["product"], "chain": summary["chain"], "mode": summary["mode"],
                 "cfg_sha256": hashlib.sha256(cfg.encode()).hexdigest() if cfg else None,
                 "composition": composition}
        atomic_write(config_dir / rules["state_file"], json.dumps(state, ensure_ascii=False, indent=2) + "\n")
    return code, summary


def main():
    ap = argparse.ArgumentParser(description="Compose printer.cfg from the detected hardware")
    ap.add_argument("--composition", default=None,
                    help="composition JSON written by yumi-detect.py (default: its own output file)")
    ap.add_argument("--config-dir", default=str(DEFAULT_CONFIG_DIR))
    ap.add_argument("--prefs", default=None, help="user preferences JSON (default: <config-dir>/<detection.prefs_file>)")
    ap.add_argument("--factory", action="store_true", help="drop the SAVE_CONFIG block even if the main board is the same")
    ap.add_argument("--minimal", action="store_true", help="write the minimal connect-only cfg whatever the product")
    ap.add_argument("--dry-run", action="store_true", help="decide and report, write nothing")
    args = ap.parse_args()

    catalog = generator.load_catalog()
    comp_path = Path(args.composition) if args.composition else _yumi_detect_default_out()
    composition = load_json(comp_path, None)
    if composition is None:
        print(json.dumps({"mode": "alert", "alert": "composition file missing or invalid: %s" % comp_path}))
        return EXIT_ALERT
    prefs_path = Path(args.prefs) if args.prefs else Path(args.config_dir) / catalog["detection"]["prefs_file"]
    prefs = load_json(prefs_path, {}) or {}

    code, summary = apply(composition, catalog, args.config_dir, prefs, args.factory, args.minimal, args.dry_run)
    print(json.dumps(summary, ensure_ascii=False))
    return code


if __name__ == "__main__":
    sys.exit(main())
