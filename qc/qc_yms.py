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


def device_for_model(model):
    """Renvoie (qc_model, device_string, prefix) pour un modèle YMS."""
    key = (model or DEFAULT_MODEL).lower()
    qc_model, prefix = MODELS.get(key, MODELS[DEFAULT_MODEL])
    return qc_model, "device=%s" % qc_model, prefix


def position_from_test_id(test_id):
    """e<n>_head -> position banc (n+1)."""
    m = re.fullmatch(r"e(\d+)_head", test_id)
    if not m:
        raise ValueError("test_id YMS attendu e<n>_head, reçu %r" % test_id)
    return int(m.group(1)) + 1


def build_box_report(test_id, result, yms_ids, session, pad_mac, technician,
                     test_log, engine_results, model="light",
                     bench_total=YMS_BENCH_TOTAL, bench_slots=YMS_BENCH_SLOTS,
                     started=None, now=None):
    """Construit le rapport JSON d'un boîtier YMS (contrat FORMAT-YMS.md v1.1).

    Args:
        test_id: identifiant du test (ex: "e5_head").
        result: résultat brut du test (passé à la fonction : "PASS" ou "FAIL").
        yms_ids: liste des codes alloués par le serveur (indexée position-1).
        session: identifiant de session banc.
        pad_mac: adresse MAC / identifiant du pad QC.
        technician: nom de l'opérateur.
        test_log: dict {test_id: [ligne, ...]}.
        engine_results: dict {test_id: {"result": ..., "timestamp": ..., "details": ...}}.
        model: "light" ou "pro".
        bench_total: nombre total de positions banc.
        bench_slots: mapping position -> slot physique.
        started: datetime de début du test.
        now: datetime de fin du test.

    Returns:
        dict conforme au contrat v1.1.
    """
    pos = position_from_test_id(test_id)
    yms_id = yms_ids[pos - 1]
    logs = list(test_log.get(test_id, []))
    passed = (result == "PASS")
    res = engine_results.get(test_id, {})
    qc_model, device_str, _prefix = device_for_model(model)
    mcu_res = engine_results.get("mcu_check", {})
    mcu_result = mcu_res.get("result")
    if hasattr(mcu_result, "value"):
        mcu_result_value = mcu_result.value
    else:
        mcu_result_value = str(mcu_result) if mcu_result is not None else "pending"
    started = started or datetime.now()
    now = now or datetime.now()
    test_entry_name = "YMS-%d 送料+传感器 / feed+sensor" % pos
    return {
        "version": "1.0",
        "printer_id": yms_id,
        "technician": technician,
        "date": started.isoformat(),
        "date_end": now.isoformat(),
        "duration_seconds": int((now - started).total_seconds()),
        "overall_result": "PASS" if passed else "FAIL",
        "failed_tests": [] if passed else [test_id],
        "skipped_tests": [],
        "qc_model": qc_model,
        "yumi_config": device_str,
        "machine_uid": "",
        "pad_mac": pad_mac,
        "bench_position": pos,
        "bench_slot": bench_slots[pos - 1],
        "bench_session": session,
        "bench_total": bench_total,
        "measures": extract_measures(logs, passed),
        "tests": [
            {
                "id": "mcu_check",
                "name": "主板×3 + 固件 / MCUs + firmware",
                "type": "automated",
                "result": mcu_result_value,
                "timestamp": mcu_res.get("timestamp", ""),
                "details": mcu_res.get("details", ""),
                "log": list(test_log.get("mcu_check", [])),
            },
            {
                "id": test_id,
                "name": test_entry_name,
                "type": "automated",
                "result": "pass" if passed else "fail",
                "timestamp": res.get("timestamp", ""),
                "details": res.get("details", ""),
                "log": logs,
            },
        ],
    }
