# YUMI C-Series — OrcaSlicer G-Code (Monolith)

Tous les G-Codes custom OrcaSlicer pour imprimantes YUMI C-Series (C235, C335, C435).
Chaque section correspond à un champ dans OrcaSlicer → Printer Settings → Custom G-Code.

---

## 1. Machine start G-Code

Source de vérité : le fork OrcaSlicer, `resources/profiles/YUMi/machine/fdm_yumi_common.json`,
clé `machine_start_gcode` (branche `feature/yumi-c-series`, commits `4a1560f37e` « hand the bed size
to PRINT_START » puis `d790bb76ff` tête/hotend/buse ; bloc d'identité en tête de fichier `f1ff3dfdb9`). Ce qui suit en est la copie vérifiée ; en cas de doute, c'est le profil qui fait foi.

Le bloc est identique pour C235 / C335 / C435 : toutes les positions dérivent de la taille de plateau
déclarée dans le profil (`print_bed_max`). Cette même taille est passée en **première ligne** à
`PRINT_START` (macro du tronc commun yumi-config) : le firmware la compare à sa propre longueur
d'axe et **refuse, à froid et sans aucun mouvement**, un fichier tranché pour un autre modèle
(« Wrong slicer profile: 335x335 bed (YUMi C335) on a C235 (235 mm). Re-slice with the C235
profile. »). Un fichier sans `BED_X` (ancien start G-code) passe avec un simple avertissement.

```gcode
;===== YUMi C-Series - START ==================================
; Every position is derived from the bed size declared in the slicer, so
; this block is identical on C235 / C335 / C435:
;   approach = bed_x + 7   -> 242 / 342 / 442   (axis max 256 / 357 / 457)
;   pop tool = bed_x + 20  -> 255 / 355 / 455
;   park Y   = bed_y       -> 235 / 335 / 435   (axis max 246 / 345 / 445)
; The same bed size goes to PRINT_START first: the firmware compares it with
; its own axis length and refuses a file sliced for another model while the
; machine is still cold and nothing has moved.
PRINT_START BED_X={print_bed_max[0]} BED_Y={print_bed_max[1]} MODEL="{printer_model}" HEAD="{curr_print_head}" HOTEND="{curr_hotend}" NOZZLE={nozzle_diameter[0]}
M220 S100                                      ; feedrate 100%
M221 S100                                      ; flow 100%
G31                                            ; arm power-loss recovery
save_last_file
SAVE_VARIABLE VARIABLE=was_interrupted VALUE=True
SAVE_VARIABLE VARIABLE=printing_start VALUE=True
SET_VELOCITY_LIMIT ACCEL=20000 ACCEL_TO_DECEL=10000

;----- preheat -----------------------------------------------
M140 S[bed_temperature_initial_layer_single]   ; bed, without waiting
M104 S150                                      ; soft nozzle for homing + Z_TAP

;----- home + Z reference ------------------------------------
G28
M190 S[bed_temperature_initial_layer_single]   ; bed hot BEFORE probing
Z_TAP                                          ; Z offset from the piezo probe
BED_MESH_PROFILE LOAD="default"

;----- park at the pop tool + reach temperature --------------
G90
G21
G1 X{print_bed_max[0] + 7} F26000
G1 X{print_bed_max[0] + 20} F5000
G1 Y{print_bed_max[1]} F3000
M109 S[nozzle_temperature_initial_layer]       ; wait for the nozzle, at the pop tool

;----- select tool + prime -----------------------------------
T{initial_tool}
{if printhead_pressure_advance > 0}SET_PRESSURE_ADVANCE ADVANCE={printhead_pressure_advance}   ; PA of the fitted head, nothing emitted when 0
{endif}M83
G92 E0
G1 E20 F200                                    ; prime 20mm
M106 S255                                      ; freeze the ooze so it snaps off
G4 P5000
M106 S0
G92 E0
G1 X{print_bed_max[0] + 7} F26000
G1 X{print_bed_max[0] + 20} F5000
G1 E5 F600                                     ; pre-load before the first layer
G92 E0
M204 S[initial_layer_acceleration]

;----- purge line along the front edge -----------------------
G1 X{print_bed_max[0] - 50} Y1 F30000
G1 Z0.3 F1200
G1 X{print_bed_max[0] - 10} Y1 Z0.3 E3 F{outer_wall_volumetric_speed/(24/20) * 60}
G1 X{print_bed_max[0] - 50} Y2 Z0.3 E3 F{outer_wall_volumetric_speed/(0.3*0.5)/4 * 60}
G1 E-{retraction_length[0]} F{retraction_speed[0] * 60}
G91
G1 Z1 F1200
G90
;===== YUMi C-Series - START END ==============================
```

Notes :

- `printer_model` vaut exactement `YUMi C235`, `YUMi C335` ou `YUMi C435` (il est seulement repris
  dans le message de refus ; la comparaison porte sur `BED_X`/`BED_Y`, tolérance 1 mm).
- `HEAD` (`curr_print_head` : `Direct Drive` / `ChromaX12`) et `HOTEND` (`curr_hotend` : `Low waste` /
  `High Flow`) sont comparés à ce pour quoi le `printer.cfg` a été **généré** (choix du wizard ou
  smartbox détectée, macro `_YUMI_PRODUCT`, libellés `slicer_label` du catalogue yumi-config) ;
  `NOZZLE` (`nozzle_diameter[0]`) à `[extruder] nozzle_diameter` (tolérance 0,01). Comparaison
  insensible à la casse et aux espaces. `HOTEND=""` (machine mono-hotend) = non applicable.
  Messages, un seul à la fois, puis annulation propre de l'impression :
  `File sliced for YUMi C335. Please re-slice with the C235 profile.` ·
  `File sliced for ChromaX12 head. This machine is set as Direct Drive. Re-slice, or change the head in Printer Config.` ·
  `File sliced for High Flow hotend. This machine is set as Low waste. Re-slice, or change the hotend in Printer Config.` ·
  `File sliced for 0.6 nozzle. This machine is set as 0.4. Re-slice, or change the nozzle in Printer Config.`
  (tête, hotend et buse sont ce que le wizard « Printer Config » de l'écran a déclaré : la faute peut être
  d'un côté comme de l'autre, le message nomme les deux issues)
  Un fichier sans ces paramètres (ancien start G-code) passe avec un simple avertissement.
- `BED_MESH_PROFILE LOAD="default"` reste : si le profil n'existe pas encore (machine jamais meshée),
  le firmware construit le mesh lui-même (scan de la plaque métal de référence → `BED_DETECTION` →
  mesh à la température plateau de l'impression → sauvegarde sans redémarrage → `Z_TAP`) puis
  l'impression continue. `BED_DETECTION` n'a donc plus sa place dans le start G-code. Le `Z_TAP`
  explicite avant `LOAD` est conservé : le second, à l'intérieur de la construction du mesh, remet
  le zéro buse déplacé par la détection et ne se produit qu'à cette première impression.
- `printhead_pressure_advance` est une option imprimante **du fork** (surcharge de PA par tête,
  0 = rien n'est émis). Sur un OrcaSlicer standard c'est un placeholder inconnu : qui colle ce bloc
  dans un Orca amont doit supprimer la ligne `{if …}`.
- La ligne de purge suppose un filament déjà chargé : le bloc amorce 20 mm et ne pousse pas la
  longueur de bowden. Le chargement jusqu'au capteur reste volontairement côté firmware (pas de
  macro machine inventée depuis le slicer ; valeurs à venir).

---

## 2. Machine end G-Code

**Contract agreed with the Orca fork session on 2026-09-06 (trunk `feat/generator-common-layer`):
the end block is exactly one line, `PRINT_END`.** The machine owns the sequence: off the part
(short retract, lift), pop tool (`_YUMI_POP_TOOL`: bed+7 then bed+20, Y max), tip-shaping unload
until the head switch releases (`YUMI_UNLOAD_TIP` + `YUMI_UNLOAD_CHECK`, nozzle still hot),
then heaters and fans off, `printing_start`/`was_interrupted` back to false, PLR cleared, motors
off. The block below is the previous one: its inline retracts (E-7/E-10/E-20/E-110), park
coordinates, `SAVE_VARIABLE`, `G31`, `clear_last_file` duplicate machine business and left the
filament at the head switch between two prints. Transition for pads not yet on the trunk: the
block may keep a 3 mm retract, a lift and the pop-tool park before `PRINT_END` (no heater
command); the trunk `PRINT_END` tolerates entering already parked and retracted.

`PRINT_START` on the trunk sets `printing_start`/`was_interrupted` and arms PLR itself once the
profile guard passed; the same lines in the start block below are redundant and will be dropped
with the next block revision. The colour-change block stays **100 % scripted in Orca** (Nicolas, 2026-09-06 evening: profile
updates reach the fleet more easily than machine macros). The slicer only calls the primitives that
need machine knowledge: `T[next_extruder]` (feeder select, load to the head switch, **and stops
there**: the prime from the switch to the nozzle and the purge are the block's, tunable in Orca),
`YUMI_UNLOAD_CHECK` (pull until the head switch releases) and `EXTRA_FLUSH`
(operator's extra purge from the panel). `YUMI_TOOL_CHANGE` on the trunk is a bench test macro,
not a production path.


```gcode
;;;;;;;;;;;;;;;;;;;;;;;;;END G-CODE;;;;;;;;;;;;;;;;;;;;;;;;
M220 S100 ;Set the feed speed to 100%
M221 S100 ;Set the flow rate to 100%
SET_VELOCITY_LIMIT ACCEL=20000 ACCEL_TO_DECEL=10000
G1 E-7 F4800                      ; retract 7mm a 80mm/s
G1 E-10 F2100                     ; 10mm a 35mm/s traverse heatbreak PEEK
G1 E-20 F300                      ; 20mm a 5mm/s lent — tip refroidit
G1 X{print_bed_max[0] + 11} F26000    ; pop tool side position
G1 X{print_bed_max[0] + 21} F5000     ; pop tool position
SET_VELOCITY_LIMIT ACCEL=9000 ACCEL_TO_DECEL=4500
G1 Y{print_bed_max[1]} F3000
SET_VELOCITY_LIMIT ACCEL=20000 ACCEL_TO_DECEL=10000
G92 E0
G1 E-110 F2100                    ; unload 110mm a 35mm/s
M106 S0
G92 E0
SAVE_VARIABLE VARIABLE=was_interrupted VALUE=False
SAVE_VARIABLE VARIABLE=printing_start VALUE=False
clear_last_file
G31
M106 S0 ;STOP PART FAN to 0%
M106 S0 P2 ;STOP AUX FAN to 0%
PRINT_END
;;;;;;;;;;;;;;;;;;;;;;;;;END G-CODE;;;;;;;;;;;;;;;;;;;;;;;;
```

---

## 3. Before layer change G-Code

```gcode
;;;;;;;;;;;;;;;;;;;;;;;;;BEFORE_LAYER_CHANGE;;;;;;;;;;;;;;;;;;;;;;;;
G92 E0
;;;;;;;;;;;;;;;;;;;;;;;;;BEFORE_LAYER_CHANGE;;;;;;;;;;;;;;;;;;;;;;;;
```

---

## 4. After layer change G-Code (Layer change G-Code)

```gcode
;;;;;;;;;;;;;;;;;;;;;;;;;LAYER_CHANGE;;;;;;;;;;;;;;;;;;;;;;;;
SET_PRINT_STATS_INFO CURRENT_LAYER={layer_num + 1}
G92 E0
;;;;;;;;;;;;;;;;;;;;;;;;;LAYER_CHANGE;;;;;;;;;;;;;;;;;;;;;;;;
```

---

## 5. Change filament G-Code (Tool change)

```gcode
;;;;;;;;;;;;;;;;;;;;;;;;;CHANGE FILAMENT G CODE;;;;;;;;;;;;;;;;;;;;;;;;
SET_VELOCITY_LIMIT ACCEL=20000 ACCEL_TO_DECEL=10000
M106 S0
M104 S[nozzle_temperature_range_high]
; slicer a deja retract 7mm a 80mm/s avant d'appeler ce gcode
G1 E-10 F2100                     ; 10mm a 35mm/s traverse le heatbreak PEEK
G1 E-20 F300                      ; 20mm a 5mm/s lent — refroidit le tip dans le PEEK
{if toolchange_count > 0}
G17
G2 Z{max_layer_z + 0.4} I0.86 J0.86 P1 F10000 ; spiral lift a little from second lift
{endif}
G1 Z{max_layer_z + 3.0} F1200
G1 X{print_bed_max[0] + 11} F26000    ; pop tool side position
G1 X{print_bed_max[0] + 25} F5000     ; pop tool position
G92 E0
G1 E-110 F2100                    ; unload 110mm a 35mm/s sort du hotend
M106 S0
T[next_extruder]
G92 E0
G1 E147 F600                      ; reload 147mm a 10mm/s
G92 E0
G1 E20 F200                       ; prime 20mm
; FLUSH_START
G92 E0
M104 S[new_filament_temp]
{if flush_length / 2 < 15}
G1 E15 F300                       ; minimum flush (slicer veut moins de 15mm)
{else}
G1 E{(flush_length / 2)} F300     ; flush adaptatif slicer (coef 1, divise par 2)
{endif}
EXTRA_FLUSH                        ; extra flush add by user in the printer
; FLUSH_END
M106 S255
G92 E0
G4 P3000
M106 S0
G1 X{print_bed_max[0] + 11} F26000
G90
{if layer_z <= (initial_layer_print_height + 0.001)}
M204 S[initial_layer_acceleration]
{else}
M204 S[default_acceleration]
{endif}
G1 E1.5 F11000
G92 E0
;;;;;;;;;;;;;;;;;;;;;;;;;CHANGE FILAMENT G CODE;;;;;;;;;;;;;;;;;;;;;;;;
```

---

## 6. Pause G-Code

```gcode
;;;;;;;;;;;;;;;;;;;;;;;;;PAUSE FILAMENT G CODE;;;;;;;;;;;;;;;;;;;;;;;;
G1 Z30
PAUSE
;;;;;;;;;;;;;;;;;;;;;;;;;PAUSE FILAMENT G CODE;;;;;;;;;;;;;;;;;;;;;;;;
```

---

## 7. Template custom G-Code (Filament Settings → Custom G-Code)

```gcode
;;;;;;;;;;;;;;;;;;;;;;;;;CUSTOM G-CODE;;;;;;;;;;;;;;;;;;;;;;;;
{if curr_bed_type=="Textured PEI Plate"}
 SET_GCODE_OFFSET Z=-0.2
{endif}
{if curr_bed_type=="Cool Plate"}
 SET_GCODE_OFFSET Z=-0.15
{endif}
{if curr_bed_type=="Engineering Plate"}
 SET_GCODE_OFFSET Z=-0.15
{endif}
{if curr_bed_type=="High Temp Plate"}
 SET_GCODE_OFFSET Z=-0.15
{else}
SET_GCODE_OFFSET Z=-0.15
{endif}
;;;;;;;;;;;;;;;;;;;;;;;;;CUSTOM G-CODE;;;;;;;;;;;;;;;;;;;;;;;;
```

Bed types disponibles : `Cool Plate`, `Engineering Plate`, `High Temp Plate`, `Textured PEI Plate`
