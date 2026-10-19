"""ADXL345 QC bench: one accelerometer at a time, plugged into the pad over USB and shaken by hand.

Verdict from two readings of the board under test:
- at rest (ACCELEROMETER_QUERY): the acceleration vector is gravity, about 1 g; a dead or
  mis-wired chip reads zero or garbage;
- while the operator shakes it (ACCELEROMETER_MEASURE, a few seconds): every axis must move,
  so a stuck axis fails even when the chip answers.
Thresholds are first guesses, to be tuned on real units (rest and shake recordings).
"""
import math
import statistics

ADXL_MODEL = "ADXL"
GRAVITY_MM_S2 = 9810.0
GRAVITY_TOLERANCE = 0.25        # rest magnitude within +-25 % of 1 g
SHAKE_STDEV_MIN_MM_S2 = 2000.0  # per axis, during the hand shake
SHAKE_MIN_SAMPLES = 100         # the ADXL345 streams ~3200 Hz: a few seconds give thousands
AXES = ("x", "y", "z")


def adxl_verdict(rest, samples):
    """(passed, reason). rest = (x, y, z) in mm/s^2 at rest; samples = [(x, y, z), ...] recorded
    while the board is shaken. The reason names what is wrong, in English (report text)."""
    magnitude = math.sqrt(sum(a * a for a in rest))
    if abs(magnitude - GRAVITY_MM_S2) > GRAVITY_TOLERANCE * GRAVITY_MM_S2:
        return False, "No gravity at rest (%.0f mm/s2)" % magnitude
    if len(samples) < SHAKE_MIN_SAMPLES:
        return False, "No data while shaking (%d samples)" % len(samples)
    still = [axis for axis, values in zip(AXES, zip(*samples))
             if statistics.pstdev(values) < SHAKE_STDEV_MIN_MM_S2]
    if still:
        return False, "Axis %s does not move" % "/".join(still).upper()
    return True, ""


def read_measure_csv(path):
    """Samples [(x, y, z), ...] from a Klipper ACCELEROMETER_MEASURE file
    (header "#time,accel_x,accel_y,accel_z", one sample per line)."""
    with open(path) as f:
        return [tuple(float(v) for v in line.split(",")[1:4]) for line in f if line[:1].isdigit()]
