"""ADXL345 QC bench: one accelerometer at a time on the pad GPIO (SPI), read by the Linux host MCU.

The bench runs on its own, the operator only handles the ADXL: plug it, shake it, unplug it.
- Plugged: the chip answers (ACCELEROMETER_QUERY checks its id, 0xE5).
- Rest: about 1 g; a dead or mis-wired chip reads zero or garbage.
- Shake: measured in short windows; every axis must move. When some axes move and one
  stays still over several windows, that axis is named in the failure.
Thresholds come from a real board on 19/10/2026 (bench pad 02810E28F273): rest noise stdev
136-262 mm/s2 per axis, hand shake stdev 8 231 / 12 795 / 17 860 mm/s2 (X/Y/Z, peaks at the
+-16 g range), gravity at rest 1.1 g.
"""
import math
import statistics

ADXL_MODEL = "ADXL"
ADXL_DEVICE = "ADXL345"          # device= of the report, one family on qc.yumi-lab.com
GRAVITY_MM_S2 = 9810.0
GRAVITY_TOLERANCE = 0.25         # rest magnitude within +-25 % of 1 g
SHAKE_STDEV_MIN_MM_S2 = 2000.0   # per axis: ~8x the rest noise, ~4x below a real hand shake
SHAKE_MIN_SAMPLES = 100          # the ADXL345 streams ~3200 Hz: one second gives thousands
SHAKE_STRIKES_MAX = 3            # windows with a still axis while the others move -> FAIL
REST_S = 2                       # rest window, operator hands off
WINDOW_S = 2                     # shake windows (the stream needs ~1 s to start: 1 s gave ~200 samples)
MEASURE_CSV = "/tmp/adxl345-%s.csv"   # written by Klipper's ACCELEROMETER_MEASURE NAME=<name>
AXES = ("x", "y", "z")
# Klipper's answers to ACCELEROMETER_QUERY: no chip (wrong id) vs chip there but no samples. The
# second one is what a hot-swapped board gives until Klipper restarts (seen 19/10: 0 samples,
# 5/5 queries refused; after FIRMWARE_RESTART 5/5 fine).
BAD_ID_MSG = "Invalid adxl345 id"
NO_SAMPLES_MSG = "No accelerometer measurements found"

# Screen texts (Chinese / French, like the rest of the QC panel), one place.
TEXT = {
    "plug": "插入 ADXL / Branchez un ADXL",
    "rest": "不要动 / Ne bougez pas l'ADXL",
    "shake": "摇动 ADXL / Secouez l'ADXL !",
    "pass": "合格 / VALIDÉ",
    "fail": "不合格 / REFUSÉ",
    "unplug": "拔出 ADXL / Débranchez-le",
    "count": "今日 / Aujourd'hui : %d",
}


def rest_check(samples):
    """(ok, reason) from a rest window [(x, y, z), ...] in mm/s2: the mean vector is ~1 g."""
    if len(samples) < SHAKE_MIN_SAMPLES:
        return False, "No data at rest (%d samples)" % len(samples)
    mean = [statistics.fmean(axis) for axis in zip(*samples)]
    magnitude = math.sqrt(sum(a * a for a in mean))
    if abs(magnitude - GRAVITY_MM_S2) > GRAVITY_TOLERANCE * GRAVITY_MM_S2:
        return False, "No gravity at rest (%.0f mm/s2)" % magnitude
    return True, ""


def chip_state(error_text):
    """State of the board from a refused ACCELEROMETER_QUERY: "stuck" (chip id fine, no samples:
    Klipper must restart) or "absent" (no chip, or any other refusal)."""
    return "stuck" if NO_SAMPLES_MSG in error_text else "absent"


def axis_stdevs(samples):
    """Per-axis standard deviation of a window, {"x": ..., "y": ..., "z": ...} in mm/s2."""
    return {axis: statistics.pstdev(values) for axis, values in zip(AXES, zip(*samples))}


def still_axes(samples):
    """Axes that did not move in a window (stdev under SHAKE_STDEV_MIN_MM_S2), e.g. ["y"]."""
    return [axis for axis, sd in axis_stdevs(samples).items() if sd < SHAKE_STDEV_MIN_MM_S2]


def shake_step(samples, strikes):
    """One shake window -> (state, strikes, reason).
    state: "pass" (every axis moved), "fail" (an axis stayed still SHAKE_STRIKES_MAX windows in a
    row while others moved), "wait" (not shaken yet, or a partial shake to confirm)."""
    if len(samples) < SHAKE_MIN_SAMPLES:
        return "wait", strikes, ""
    still = still_axes(samples)
    if not still:
        return "pass", 0, ""
    if len(still) == len(AXES):
        return "wait", strikes, ""          # board not shaken yet: keep waiting
    strikes += 1
    if strikes >= SHAKE_STRIKES_MAX:
        return "fail", strikes, "Axis %s does not move" % "/".join(still).upper()
    return "wait", strikes, ""


def read_measure_csv(path):
    """Samples [(x, y, z), ...] from a Klipper ACCELEROMETER_MEASURE file
    (header "#time,accel_x,accel_y,accel_z", one sample per line)."""
    with open(path) as f:
        return [tuple(float(v) for v in line.split(",")[1:4]) for line in f if line[:1].isdigit()]


def build_adxl_report(passed, reason, pad_id, now, technician=""):
    """Report for qc.yumi-lab.com. An ADXL345 has no serial number: each test gets its own id
    (pad + time) so that every board counts once."""
    stamp = now.strftime("%Y%m%d-%H%M%S")
    return {
        "printer_id": "ADXL-%s-%s" % (pad_id, stamp),
        "machine_uid": "",
        "pad_mac": pad_id,
        "qc_model": ADXL_MODEL,
        "yumi_config": "device=" + ADXL_DEVICE,
        "overall_result": "PASS" if passed else "FAIL",
        "fail_reason": reason,
        "fail_reasons": [reason] if reason else [],
        "technician": technician,
        "date": now.isoformat(),
        "date_end": now.isoformat(),
        "tests": [],
    }
