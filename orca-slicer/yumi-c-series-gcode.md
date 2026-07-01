# YUMI C-Series — OrcaSlicer G-Code (Monolith)

Tous les G-Codes custom OrcaSlicer pour imprimantes YUMI C-Series (C235, C335, C435).
Chaque section correspond à un champ dans OrcaSlicer → Printer Settings → Custom G-Code.

---

## 1. Machine start G-Code

```gcode
;;;;;;;;;;;;;;;;;;;;;;;;;START G-CODE;;;;;;;;;;;;;;;;;;;;;;;;
PRINT_START EXTRUDER=[nozzle_temperature_initial_layer] BED=[bed_temperature_initial_layer_single] CHAMBER=[chamber_temperature]
M220 S100 ;Set the feed speed to 100%
M221 S100 ;Set the flow rate to 100%
G31
save_last_file
SAVE_VARIABLE VARIABLE=was_interrupted VALUE=True
SAVE_VARIABLE VARIABLE=printing_start VALUE=True
M106 S178 P3 ;set motherboard fan
SET_VELOCITY_LIMIT ACCEL=20000 ACCEL_TO_DECEL=10000
BED_MESH_PROFILE LOAD="default"
;;;;;;;;;;;;;;;;;;;;;;;;;BED + HOTEND PRE-HEAT;;;;;;;;;;;;;;;
M140 S[bed_temperature_initial_layer_single]
M104 S150                          ; Pre-heat nozzle for wipe + Z offset

;;;;;;;;;;;;;;;;;;;;;;;;;SAFE CHECK;;;;;;;;;;;;;;;;;;;;;;;;;;
BED_DETECTION
Z_TAP

;;;;;;;;;;;;;;;;;;;;;;;;;PROBE + PARK;;;;;;;;;;;;;;;;;;;;;;;;
G1 X{print_bed_max[0] + 11} F26000
G1 X{print_bed_max[0] + 25} F5000
G1 Y{print_bed_max[1]} F3000

;;;;;;;;;;;;;;;;;;;;;;;;;HEAT HOTEND + PURGE;;;;;;;;;;;;;;;;;
M109 S[nozzle_temperature_initial_layer]  ; wait hotend temperature

;;;;;;;;;;;;;;;;;;;;;;;;;PRE-LOAD + NOZZLE LINE;;;;;;;;;;;;;;
T{initial_tool}
M83                                ; relative extruder
G90                                ; absolute XYZ
G21                                ; metric
G92 E0
G1 E147 F600                      ; reload 147mm a 10mm/s
G92 E0
G1 E20 F200                       ; prime 20mm
M106 S255
G4 P5000
G92 E0
M106 S0
G90
G1 X{print_bed_max[0] + 11} F26000
G1 X{print_bed_max[0] + 25} F5000
G1 E5 F6000                       ; pre-load before first layer
M204 S[initial_layer_acceleration]
G92 E0
G90
;===== nozzle load line ===============================
G1 X{print_bed_max[0] - 50} Y1.000 F30000
G1 Z0.3 F1200
M83
G1 X{print_bed_max[0] - 10} Y1 Z0.3 E3 F{outer_wall_volumetric_speed/(24/20) * 60}
G1 X{print_bed_max[0] - 50} Y2 Z0.3 E3 F{outer_wall_volumetric_speed/(0.3*0.5)/4 * 60}
G1 E-7 F4800
G91
G1 Z1 F1200
G90
;===== nozzle load line end ===========================
;;;;;;;;;;;;;;;;;;;;;;;;;START G-CODE;;;;;;;;;;;;;;;;;;;;;;;;
```

---

## 2. Machine end G-Code

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
