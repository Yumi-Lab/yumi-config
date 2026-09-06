# yumi-config
cfg files for YUMI products

## printer.cfg from the detected hardware (`generator/`)

A pad configures itself from the boards that are plugged in:

```
generator/autoconfig.py [--dry-run] [--factory] [--minimal]
```

1. `yumi-detect.py` (with Klipper stopped) reads the `YUMI_CONFIG` descriptor burned in every
   MCU firmware over its serial port (RJ11 main board, smartbox, USB) and writes the composition
   to `~/.yumi_composition.json` — facts only, no decision.
2. `compose.py` turns it into a product of `YUMI-LAB_product-catalog.json`: `device=` gives the
   machine (C235/C335/C435), a HyperDrive board present means CHROMAX X12 + 7 YMS Pro. What the
   boards cannot tell — the print head (a direct drive and a CHROMAX X12 answer the same), hotend
   type, nozzle, and the machine itself when the descriptor names none — comes from the
   preferences file written by the wizard (`detection.prefs_file`; defaults in the catalog's
   `detection` section: head ChromaX12 + 2 YMS Lite — the same default as the OrcaSlicer profiles,
   Direct Drive being an explicit choice on both sides). The ports that answered are injected into `[mcu]` / `[mcu smartbox]`,
   then `printer.cfg` is written (previous one kept as `printer-<ts>-autoconfig.cfg`) and the
   state saved in `.detected_hardware.json`.
3. Policy: same main board (uid) → the `SAVE_CONFIG` block is preserved; different board →
   factory cfg; no main board → alert, nothing touched (exit 2); no matching product → minimal cfg
   (`[mcu]` sections + `kinematics: none`) so Klipper still connects (exit 3); identical cfg → exit 4.
4. Before every start of Klipper — boot (`yumi-autoconfig.service`) and every restart of
   `klipper.service` (drop-in `ExecStartPre`, so a Moonraker update, `YUMI_SETUP` or the Printer
   Config panel all go through it) — `autoconfig.py --boot` runs and nothing is rewritten while
   the boards, the preferences and the recipe (catalog + generator, hashed in the state) are the
   ones recorded. When yumi-config ships a new catalog or generator, every machine regenerates its
   cfg at the next start with its calibrations kept: a fix on the common trunk reaches the whole
   fleet. Both units are rendered by `install.sh` with the checkout's path (`@PROJECT_DIR@`): a
   systemd `%h` would point at `/root` for a system unit, whatever its `User=`.

When run by hand or from the panel, Klipper is stopped and started through Moonraker's API (no
sudo needed). Everything that decides is data in the catalog, not code.

### The catalog: one common trunk, machines carry their geometry only

`YUMI-LAB_product-catalog.json` is layered and deep-merged in chain order:

| layer | component(s) | carries |
|---|---|---|
| board | `SMART_MAKER_1X` | everything shared by every size: pins, currents, speeds, `[yumi_sensorless_homing]`, `[yumi_z_tap]`, probe, bed-mesh policy, heaters, fans, motor constants, all macros |
| machine | `C235`, `C335`, `C435` | endstops / `position_max`, `mesh_min` / `mesh_max` / `zero_reference_position`, screw positions — nothing else |
| hotend | `DIRECT_DRIVE` | `[extruder]` is the real head motor on the E0 driver (`motor_slot: 0`, TMC and autotune of `extruder_stepper`) |
| | `CHROMAX_X12` | `[extruder]` is fictive on free pins (bowden head, Klipper wants one); the YMS feeders are `extruder0..N` on the E slots, per-slot overrides (`extruder_slot_overrides`: direction, ratio) |
| hotend_type | `HIGH_FLOW`, `LOW_WASTE` | extruder PID |
| nozzle, yms | `NOZZLE_xx`, `YMS_2_LITE`, `YMS_7_PRO` | nozzle diameter, feeder count / smartbox / dryer |

Values are the factory/QC lineage (`qc/qc_printer_<M>.cfg`, pad backups of June 2026) and the
original inline comments travel with them: `_comment` (after the section header), `_comments`
(per option) and `_notes` (commented alternative lines) are rendered into the cfg. A value that
is the same for every machine belongs to the board layer; `generator.py` holds no value at all,
only the rendering.

### Macros know no machine

Every macro is byte-identical on every size. The single `[gcode_macro _YUMI_MACHINE]` — generated
from the machine list, a window of ±`detection.machine_x_tolerance` mm around each
`stepper_x.position_max` — derives the model, bed size and Z height from the X axis length at
startup; size-dependent macros (`BED_DETECTION`, `SCREWS_TILT_CALCULATE`, `CANCEL_PRINT`,
`WIPE_NOZZLE`, the welcome report) read `printer["gcode_macro _YUMI_MACHINE"]`. Homing is
`YUMI_SENSORLESS_HOME` (multi-tap validated X/Y) then `YUMI_Z_TAP` at the bed-mesh zero
reference; the `[homing_override]` carries no number.

A machine that was never meshed does not abort its first print: `BED_MESH_PROFILE LOAD=<name>`
of a missing profile (the sliced file's start g-code) builds it — `BED_MESH_CALIBRATE` at the
print's bed temperature, persisted in `printer.cfg` by `scripts/save_mesh.py` **without
restarting Klipper**, then `Z_TAP` puts the nozzle zero back — and the print goes on with the
fresh mesh. Standalone, `BED_MESH_CALIBRATE` heats to 65 °C, soaks, saves and switches the bed
off; no `SAVE_CONFIG` restart any more. The startup report only loads a mesh that exists.

`BED_DETECTION` probes the metal reference plate behind the bed (nozzle wiper) for its Z frame.
Its position is measured, never guessed: when `bed_detect_x/y` are not in `variables.cfg` yet,
`BED_SCAN_ZERO` (Klipper extra `klipper/klippy/extras/yumi_bed_scan.py`, settings in
`[yumi_bed_scan]` from the catalog) homes, sweeps a serpentine grid with the inductive probe
plane by plane at low acceleration, keeps the union of the triggered points over the planes
after first contact and saves the centre of their bounding box — synchronously, so the whole
`LOAD` → detection → scan → mesh → tap chain runs inside a start g-code without racing the print.
Bench C235: 25 points on planes 0.4/0.3/0.2, plate centre (121.5, 209) in nozzle coordinates.

The Klipper modules shipped by this repo (`klipper/klippy/extras/`: `[yumi_filament_head]`,
`[yumi_bed_scan]`, `[filament_yumi_smart_motion_sensor …]`) document themselves in `printer.cfg`:
the generator quotes each module's header — what it does, every option, every command and
parameter, the status fields — right above its section (`generator.module_doc`, files listed in
`DOCUMENTED_MODULES`, part of the recipe hash). A test fails when an option or command read by
the module is missing from its header.

### Wizard — KlipperScreen "Printer Config" (`cfg_wizard.py`, logic in `prefs.py`)

From the last scan: a **Yumi machine** is recognised → choose the print head, hotend type and
nozzle (the head is imposed when a smartbox answers); boards answer but **name no machine** →
choose the machine first (this is where other printers get added, as machine components of the
catalog); **no board** → check the cables, scan. Every option is a component of a selection
layer, Apply runs `autoconfig.py`. Installed by `install.sh` (symlink in `KlipperScreen/panels`,
menu entry in `KlipperScreen.conf`).

### Per-machine tuning (`overrides` in the preferences file)

The catalog stays common; a value that belongs to one machine — a calibrated `backlash_coef`,
or an option the trunk cannot carry yet because the fleet's firmware does not know it — goes
in `printer_data/config/.yumi_product_prefs.json` under `"overrides"` and is deep-merged over
the product at every regeneration:

```json
{"hotend": "CHROMAX_X12", "overrides": {"extruder_stepper": {"bowden_length": 400, "backlash_coef": 1.08}}}
```

### Tests

`python3 -m unittest discover -s generator/tests` — `test_generator.py` compares each generated
machine with its `qc/qc_printer_<M>.cfg` option by option (the few legitimate differences are
listed with their reason), checks that two sizes differ only in geometry with identical macros,
that a single macro classifies by X length, that comments reach the cfg and that each head wires
the extruder as described; `test_compose.py` covers the policy, the recipe propagation and the
wizard's machine choice; `test_prefs.py` the wizard logic.
