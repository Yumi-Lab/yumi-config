#!/usr/bin/env python3
"""Analyse YUMI_Z_TAP TRACE=1 recordings: does a Z that skips steps leave a pattern in the
driver's load (SG_RESULT) that a clean descent does not?

Runs on a workstation (numpy + matplotlib), not on the pad.

    z_tap_trace.py OUT.png trace.csv [trace.csv ...]

Each CSV comes from `YUMI_Z_TAP TRACE=<path>` (columns t, sg_result, z, event; event rows mark
`descent`, `contact z=...` and `lift`). For every descent the report gives the sampling rate
actually reached, the commanded descent speed (slope of the stepper position: the steps
sent, which keep advancing when the rotor slips), the electrical frequency a
skipping motor would beat at (one full electrical cycle = 4 full steps), the load level and
spread, how often the load swings across the middle of its range, and the dominant frequency.

On the machine (C335 07/11 protocol, ~10 min, operator next to the printer):
  1. G28
  2. YUMI_Z_TAP TRACE=/tmp/tap_07A.csv                 clean taps at tap_current (0.7 A)
  3. YUMI_Z_TAP CURRENT=0.4 TRACE=/tmp/tap_04A.csv     stalls on the bed: watchdog it and
     stop it (emergency stop) after a few seconds; the samples are flushed every 0.5 s
  4. copy both CSVs to the workstation and run this script on them.
"""
import csv
import re
import sys

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

ROTATION_DISTANCE_MM = 8.0      # [stepper_z] rotation_distance (T8 leadscrew)
FULL_STEPS_PER_REV = 200        # 1.8 degree motor
FULL_STEPS_PER_ELECTRICAL_CYCLE = 4


def load(path):
    samples, events = [], []
    for row in csv.DictReader(open(path)):
        t = float(row["t"])
        if row["event"]:
            events.append((t, row["event"]))
        else:
            samples.append((t, float(row["sg_result"]), float(row["z"])))
    return np.array(samples), events


def descents(samples, events):
    """(start, end) time of each descent: from a `descent` mark to the next mark."""
    out = []
    for (t0, e0), nxt in zip(events, events[1:] + [(samples[-1, 0], "end")]):
        if e0 == "descent":
            out.append((t0, nxt[0], nxt[1]))
    return out


# A swing must cross a band around the middle of the range, never less than this many SG units
# either side: a free descent spread 224-246 (C335 07/11, 0.5 A), so its noise alone counted
# dozens of mid-line crossings per second.
MIN_SWING_HALF_BAND = 30


def count_swings(sg):
    """Low-to-high and high-to-low crossings of a hysteresis band around the middle."""
    p10, p90 = np.percentile(sg, 10), np.percentile(sg, 90)
    mid = (p10 + p90) / 2.
    half = max((p90 - p10) / 4., MIN_SWING_HALF_BAND)
    state, swings = None, 0
    for v in sg:
        new = "high" if v > mid + half else "low" if v < mid - half else state
        if state is not None and new != state:
            swings += 1
        state = new
    return swings


def describe(seg):
    t, sg, z = seg[:, 0], seg[:, 1], seg[:, 2]
    rate = (len(t) - 1) / (t[-1] - t[0]) if len(t) > 1 else 0.
    speed = abs(np.polyfit(t, z, 1)[0]) if len(t) > 2 else 0.
    f_skip = speed / ROTATION_DISTANCE_MM * FULL_STEPS_PER_REV / FULL_STEPS_PER_ELECTRICAL_CYCLE
    swings = count_swings(sg)
    dom = 0.
    if len(sg) >= 16 and rate > 0:
        spec = np.abs(np.fft.rfft(sg - sg.mean()))
        freqs = np.fft.rfftfreq(len(sg), 1. / rate)
        dom = freqs[1:][np.argmax(spec[1:])]
    return {"n": len(t), "rate_hz": rate, "speed_mm_s": speed, "f_skip_hz": f_skip,
            "sg_median": float(np.median(sg)), "sg_p10": float(np.percentile(sg, 10)),
            "sg_p90": float(np.percentile(sg, 90)),
            "swings_per_s": swings / (t[-1] - t[0]) if len(t) > 1 else 0., "dominant_hz": dom}


def main(out, paths):
    fig, axes = plt.subplots(len(paths), 1, figsize=(15, 4 * len(paths)), squeeze=False)
    for ax, path in zip(axes[:, 0], paths):
        samples, events = load(path)
        print("== %s: %d samples, %d events" % (path, len(samples), len(events)))
        for i, (t0, t1, how) in enumerate(descents(samples, events)):
            seg = samples[(samples[:, 0] >= t0) & (samples[:, 0] < t1)]
            if len(seg) < 3:
                print("  descent %d: %d samples, too few" % (i + 1, len(seg)))
                continue
            d = describe(seg)
            print("  descent %d (%.2f s, ends with %s): %d samples at %.0f Hz | %.2f mm/s -> "
                  "skip beat %.1f Hz | SG median %.0f (p10 %.0f, p90 %.0f) | %.1f swings/s | "
                  "dominant %.1f Hz" % (i + 1, t1 - t0, how, d["n"], d["rate_hz"], d["speed_mm_s"],
                                         d["f_skip_hz"], d["sg_median"], d["sg_p10"], d["sg_p90"],
                                         d["swings_per_s"], d["dominant_hz"]))
            ax.axvspan(t0, t1, color="0.9")
        ax.plot(samples[:, 0], samples[:, 1], lw=0.7, label="SG_RESULT")
        for t, e in events:
            ax.axvline(t, color="r" if e.startswith("contact") else "0.6", lw=0.6)
        ax2 = ax.twinx()
        ax2.plot(samples[:, 0], samples[:, 2], color="g", lw=0.6, label="live Z")
        ax.set_title(path)
        ax.set_xlabel("s")
        ax.set_ylabel("SG_RESULT")
        ax2.set_ylabel("Z (mm)")
    fig.tight_layout()
    fig.savefig(out, dpi=110)
    print("plot:", out)


def _self_check():
    """Synthetic descents: a flat load must give few swings, a 37.5 Hz skip beat must show."""
    rate, v = 120., 6.
    t = np.arange(0, 2, 1 / rate)
    flat = 235 + np.random.default_rng(1).normal(0, 4, len(t))
    beat = 140 + 100 * np.sign(np.sin(2 * np.pi * 37.5 * t))
    for sg, expect_beat in ((flat, False), (beat, True)):
        seg = np.column_stack([t, sg, 300 - v * t])
        d = describe(seg)
        assert abs(d["f_skip_hz"] - 37.5) < 0.1, d
        assert (d["swings_per_s"] > 30) == expect_beat, d
        if expect_beat:
            assert abs(d["dominant_hz"] - 37.5) < 1.0, d


if __name__ == "__main__":
    _self_check()
    if len(sys.argv) < 3 or not re.search(r"\.png$", sys.argv[1]):
        sys.exit(__doc__)
    main(sys.argv[1], sys.argv[2:])
