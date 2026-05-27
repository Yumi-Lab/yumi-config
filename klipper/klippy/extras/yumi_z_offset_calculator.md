# YUMI Z Offset Calculator

Klipper module for precision Z=0 calibration via physical nozzle tap on a pressure switch.
Inspired by Bambu Lab A1 nozzle-tap Z homing approach.

## Dependencies

- `[probe_pressure]` section configured with pin, speed, and lift_speed
- Pressure switch physically installed at a known XY position

## G-code Command

### YUMI_CALCULATE_Z_OFFSET

```
YUMI_CALCULATE_Z_OFFSET [SAVE=0|1]
```

Sets Z=0 at the point where the nozzle physically contacts the pressure switch.

**Prerequisites:** X and Y must be homed. Z does not need to be homed — the module probes downward from the current position.

**Sequence:**
1. Move XY to pressure switch position (Z unchanged)
2. Wait `probe_delay` ms for stabilization
3. Multi-tap probe: descend until trigger, lift, repeat
4. Validate: require `samples` consecutive readings within `samples_tolerance`
5. Apply `compression_offset` (compensates switch/nozzle compression)
6. Set current position as Z=0

## Configuration

```ini
[yumi_z_offset_calculator]
pressure_switch_x: 49.5       # X position of pressure switch (mm)
pressure_switch_y: 175.5      # Y position of pressure switch (mm)
compression_offset: 0.3       # Offset after contact to compensate compression (mm)
max_probe_times: 50           # Maximum tap attempts before failure
z_hop: 3                      # Lift distance between taps (mm)
samples: 3                    # Consecutive stable readings required to validate
samples_tolerance: 0.01       # Max allowed deviation between consecutive taps (mm)
probe_delay: 2                # Delay before first probe (ms)
```

### Parameter Reference

| Parameter | Type | Default | Description |
|---|---|---|---|
| `pressure_switch_x` | float | 30.0 | X coordinate of the pressure switch |
| `pressure_switch_y` | float | 200.0 | Y coordinate of the pressure switch |
| `compression_offset` | float | 0.2 | Z compensation after contact (mm). Positive = up |
| `max_probe_times` | int | 6 | Total tap budget. Fails if no stable reading within this count |
| `z_hop` | float | 10.0 | Lift distance between taps (mm) |
| `samples` | int | 2 | Number of consecutive readings within tolerance to validate |
| `samples_tolerance` | float | 0.02 | Maximum Z deviation between consecutive taps (mm) |
| `probe_delay` | float | 1000.0 | Delay before first probe after positioning (ms) |

### Speeds (from probe_pressure)

The module reads speeds directly from `[probe_pressure]`:

| Speed | Source | Used for |
|---|---|---|
| `probe_pressure.speed` | `[probe_pressure] speed` | Probe descent (tap) |
| `probe_pressure.lift_speed` | `[probe_pressure] lift_speed` | Z hop, final lift, compression offset |
| `travel_speed` | Hardcoded 30 mm/s | XY move to switch position |

## Probe Validation Logic

```
Tap 1 → record Z position
Tap 2 → diff with Tap 1
  if diff <= tolerance → stable_count = 2
  else → stable_count = 1 (reset)
Tap 3 → diff with Tap 2
  if diff <= tolerance → stable_count = 3 → VALIDATED (if samples=3)
  else → stable_count = 1 (reset)
...
Tap N → if stable_count never reaches samples → ERROR
```

The counter requires **consecutive** stable readings. Any drift resets the counter.

## Integration with homing_override

To use as Z homing in `[homing_override]` (replaces G28 Z):

```gcode
# After homing X and Y:
{% set z_max = printer.configfile.settings.stepper_z.position_max|float %}
SET_KINEMATIC_POSITION Z={z_max}
YUMI_CALCULATE_Z_OFFSET SAVE=0
```

`SET_KINEMATIC_POSITION Z={z_max}` gives the probe full Z travel range to reach the bed from any position.

## CALCULATE_Z_OFFSET Wrapper Macro

The `CALCULATE_Z_OFFSET` macro auto-detects printing state:

```gcode
[gcode_macro CALCULATE_Z_OFFSET]
gcode:
    {% set is_printing = printer.idle_timeout.state == "Printing" %}
    {% if is_printing %}
        M109 S150           # Heat nozzle (thermal expansion accuracy)
        WIPE_NOZZLE         # Clean nozzle before tap
        YUMI_CALCULATE_Z_OFFSET SAVE={params.SAVE|default(0)|int}
        M104 S0
    {% else %}
        {% if printer.toolhead.homed_axes != "xyz" %}
            G28
        {% endif %}
        YUMI_CALCULATE_Z_OFFSET SAVE={params.SAVE|default(0)|int}
    {% endif %}
```
