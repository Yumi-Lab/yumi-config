"""
qc_yms.py — Pure logic for the YMS QC bench.
No GTK/gi imports here so it can be imported by tests and by the thin UI layer.
"""
import json
import os
import re
from datetime import datetime

# Contrat FORMAT-YMS.md v1.1
YMS_BENCH_TOTAL = 12
YMS_BENCH_SLOTS = (
    ["main:E0", "main:E1"]
    + ["hyperdrive_uart:%d" % i for i in range(1, 6)]
    + ["hyperdrive_usb:%d" % i for i in range(1, 6)]
)

# Modèles supportés : (device=, préfixe code YMS)
MODELS = {
    "light": ("YMS-LIGHT", "YMSL-"),
    "pro": ("YMS-PRO", "YMSP-"),
}
DEFAULT_MODEL = "light"

# 7 valeurs normées de measures.fail_reason
def extract_measures(logs, passed):
    """Extrait les measures{} depuis les logs d'un test e<n>_head.

    Les champs restent None/[] quand ils ne sont pas observés. Si le test a
    échoué mais qu'aucune signature n'est trouvée, fail_reason vaut 'timeout'.
    """
    m = {
        "feed_mm": None,
        "feed_budget_mm": 900,
        "head_reached": False,
        "motion_first_detect": False,
        "dropouts_e": [],
        "dropout_count": 0,
        "feed_dropout": False,
        "stress_segments_ok": 0,
        "stress_segments_total": 16,
        "stress_speeds_mms": [10, 30, 60, 100],
        "retract_mm": None,
        "tmc_error": None,
        "fail_reason": None,
    }
    for line in logs:
        if "a change d'etat" in line:
            m["motion_first_detect"] = True
        r = re.search(r"filament a la tete apres (\d+)mm", line)
        if r:
            m["feed_mm"] = int(r.group(1))
            m["head_reached"] = True
        r = re.search(r"pas a la tete apres (\d+)mm", line)
        if r:
            m["feed_mm"] = int(r.group(1))
            m["head_reached"] = False
            m["fail_reason"] = "head_not_reached"
        r = re.search(r"decrochage encodeur E=([\d.]+)", line)
        if r:
            m["dropouts_e"].append(float(r.group(1)))
        if "a CESSE de suivre pendant le feed" in line:
            m["feed_dropout"] = True
            m["fail_reason"] = "sensor_lost_feed"
            r = re.search(r"\(a (\d+)mm\)", line)
            if r:
                m["feed_mm"] = int(r.group(1))
        if "n'a PAS change d'etat" in line:
            m["fail_reason"] = "sensor_mute"
        if "deja a la tete avant feed" in line:
            m["fail_reason"] = "already_at_head"
        if "PERDU le suivi au segment" in line:
            m["fail_reason"] = "sensor_lost_stress"
        r = re.search(r"stress OK.*?(\d+) segments", line)
        if r:
            m["stress_segments_ok"] = int(r.group(1))
        r = re.search(r"stress (\d+)/(\d+) detected=True", line)
        if r:
            m["stress_segments_ok"] = max(m["stress_segments_ok"], int(r.group(1)))
            m["stress_segments_total"] = int(r.group(2))
        if "DRV_STATUS" in line:
            m["tmc_error"] = line.strip()[:200]
            m["fail_reason"] = "tmc_error"
    m["dropout_count"] = len(m["dropouts_e"])
    if m["head_reached"] and m["feed_mm"]:
        m["retract_mm"] = m["feed_mm"]
    if passed:
        m["fail_reason"] = None
    elif not m["fail_reason"]:
        m["fail_reason"] = "timeout"
    return m
