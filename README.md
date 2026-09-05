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
   machine (C235/C335/C435), a HyperDrive board present means CHROMAX X12 + 7 YMS Pro, print head
   type and nozzle come from `printer_data/config/.yumi_product_prefs.json` (defaults in the
   catalog's `detection` section). The ports that answered are injected into `[mcu]` /
   `[mcu smartbox]`, then `printer.cfg` is written (previous one kept as
   `printer-<ts>-autoconfig.cfg`) and the state saved in `.detected_hardware.json`.
3. Policy: same main board (uid) → the `SAVE_CONFIG` block is preserved; different board →
   factory cfg; no main board → alert, nothing touched (exit 2); no matching product → minimal cfg
   (`[mcu]` sections + `kinematics: none`) so Klipper still connects (exit 3); identical cfg → exit 4.

Klipper is stopped and started through Moonraker's API (no sudo needed). Everything that decides
is data in the catalog (`detection`), not code. Tests: `python3 -m unittest discover -s generator/tests`.
