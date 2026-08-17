"""
qc_yms.py — Pure logic for the YMS QC bench.
No GTK/gi imports here so it can be imported by tests and by the thin UI layer.
"""
import json
import os
import re
import urllib.error
import urllib.request
from datetime import datetime, timezone

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


BENCH_CONFIG_DEFAULTS = {
    "yms_version": "1.0",
    "extruder_model": "",
    "spring_model": "",
}


def load_bench_config(path):
    """Config banc (contrat v1.5) : version PRODUIT des boîtiers testés +
    références EXACTES des composants montés (traçabilité). Fichier JSON
    éditable opérateur ; absent/illisible -> défauts (repli serveur V1.0).
    """
    cfg = dict(BENCH_CONFIG_DEFAULTS)
    try:
        with open(path) as f:
            data = json.load(f)
        for key in cfg:
            if isinstance(data.get(key), str) and data[key].strip():
                cfg[key] = data[key].strip()
    except Exception:
        pass
    return cfg


def load_disabled_positions(path):
    """Charge la liste des positions banc désactivées depuis un JSON.

    Format attendu : {"disabled": [2, 11]}. Fichier absent ou illisible -> [].
    """
    try:
        with open(path) as f:
            data = json.load(f)
        disabled = data.get("disabled", [])
        return sorted({
            int(p) for p in disabled
            if isinstance(p, int) or (isinstance(p, str) and p.isdigit())
        })
    except Exception:
        return []


def enabled_positions(disabled, total=YMS_BENCH_TOTAL):
    """Renvoie les positions actives (1..total) hors disabled."""
    disabled_set = set(disabled or [])
    return [p for p in range(1, total + 1) if p not in disabled_set]


def test_id_for_position(pos):
    """Position banc (1..N) -> identifiant de test e<n>_head."""
    return "e%d_head" % (pos - 1)


def build_yms_tests(disabled_positions=None):
    """Construit la séquence YMS12 incluant les positions désactivées en SKIP.

    Returns:
        liste de dicts test (compatible avec QCEngine.tests). Les positions
        désactivées portent la clé ``skipped: True`` pour que le wizard les
        saute sans envoyer de macro.
    """
    disabled = set(disabled_positions or [])
    tests = [{
        "id": "mcu_check",
        "name": "主板×3 + 固件 / MCUs + firmware",
        "type": "automated",
        "macro": "QC_MCU_CHECK",
        "timeout": 60,
    }]
    for pos in range(1, YMS_BENCH_TOTAL + 1):
        test_id = test_id_for_position(pos)
        name = "YMS-%d 送料+传感器 / feed+sensor" % pos
        if pos in disabled:
            tests.append({
                "id": test_id,
                "name": name,
                "type": "automated",
                "macro": "",
                "timeout": 0,
                "skipped": True,
            })
        else:
            tests.append({
                "id": test_id,
                "name": name,
                "type": "automated",
                "macro": "QC_HEAD_FEED TOOL=%d" % pos,
                "timeout": 420,
            })
    return tests


def build_retest_sequence(position):
    """Mini-séquence pour re-tester un seul boîtier YMS en échec.

    Le mcu_check est inclus comme skipped (déjà validé en séquence principale)
    pour conserver la structure du rapport ; seul e<n>_head est réellement
    exécuté.
    """
    test_id = test_id_for_position(position)
    return [
        {
            "id": "mcu_check",
            "name": "主板×3 + 固件 / MCUs + firmware",
            "type": "automated",
            "macro": "",
            "timeout": 0,
            "skipped": True,
        },
        {
            "id": test_id,
            "name": "YMS-%d 送料+传感器 / feed+sensor" % position,
            "type": "automated",
            "macro": "QC_HEAD_FEED TOOL=%d" % position,
            "timeout": 420,
        },
    ]


def position_from_test_id(test_id):
    """e<n>_head -> position banc (n+1)."""
    m = re.fullmatch(r"e(\d+)_head", test_id)
    if not m:
        raise ValueError("test_id YMS attendu e<n>_head, reçu %r" % test_id)
    return int(m.group(1)) + 1


def yms_code_for_position(pos, yms_ids, disabled=None):
    """Map une position active vers le code alloué, en sautant les désactivées."""
    enabled = enabled_positions(disabled, len(yms_ids) + len(disabled or []))
    idx = enabled.index(pos)
    return yms_ids[idx]


def build_box_report(test_id, result, yms_id, session, pad_mac, technician,
                     test_log, engine_results, model="light",
                     bench_total=YMS_BENCH_TOTAL, bench_slots=YMS_BENCH_SLOTS,
                     started=None, now=None,
                     extruder_model="", spring_model=""):
    """Construit le rapport JSON d'un boîtier YMS (contrat FORMAT-YMS.md v1.4).

    Args:
        test_id: identifiant du test (ex: "e5_head").
        result: résultat brut du test (passé à la fonction : "PASS" ou "FAIL").
        yms_id: code alloué EN FIN de test par le serveur (v1.4) — numéro de
            série YMSP-/YMSL- pour un PASS, code famille QCFL- pour un FAIL.
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
        dict conforme au contrat v1.4.
    """
    pos = position_from_test_id(test_id)
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
    # v1.5+ : timestamps UTC EXPLICITES (ISO 8601 avec fuseau) — le serveur
    # recalcule l'heure usine (UTC+8) sans dependre du fuseau/horloge du pad.
    started = started or datetime.now(timezone.utc)
    now = now or datetime.now(timezone.utc)
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
        "extruder_model": extruder_model,
        "spring_model": spring_model,
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


def allocate_yms_codes(url, token, count, model, timeout=15, result="pass",
                       yms_version="1.0"):
    """POST /api/qc/yms/allocate {"model","count"[,"result"]} -> (ids, erreur).

    v1.4 : l'allocation se fait en FIN de test, unitaire. result="fail" ->
    code famille QCFL- (boîtier recalé, compte le taux de défectueux) au
    lieu d'un numéro de série YMSP-/YMSL-.

    Returns:
        (ids, "") en cas de succès.
        (None, message_erreur) sinon.
    """
    if not token:
        return None, "Token QC manquant"
    # v1.3+ : mode nominal = UNITAIRE (sans count). Le mode groupe (count
    # present) est deprecie — encore servi par le serveur, ne plus l'utiliser.
    body = {"model": model, "yms_version": yms_version}
    if count != 1:
        body["count"] = count
    if result != "pass":
        body["result"] = result
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        url, data=data, method="POST",
        headers={"Content-Type": "application/json", "X-QC-Token": token})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return None, "Allocation refusée : HTTP %d" % e.code
    except Exception as e:
        return None, "Allocation impossible (réseau ?) : %s" % e
    ids = payload.get("yms_ids") or (
        [payload["yms_id"]] if payload.get("yms_id") else [])
    if payload.get("status") != "ok" or len(ids) != count:
        return None, "Réponse allocation invalide : %s" % str(payload)[:120]
    return ids, ""


# URL de consultation du rapport (contrat v1.1)
QC_REPORT_URL_BASE = "https://qc.yumi-lab.com/report/"


def _failed_test_labels(report):
    """Noms courts (anglais) des tests QC machine en échec, depuis
    report["failed_tests"] (liste d'id) + report["tests"] (id -> name
    bilingue "中文 / English"). Formatage des données uniquement — la
    pagination (combien de lignes tiennent sur l'étiquette, "+N autres")
    est la responsabilité du gabarit `list` côté renderer, pas d'ici."""
    names_by_id = {t.get("id"): t.get("name", "") for t in report.get("tests", [])}
    ids = report.get("failed_tests") or []
    shown = []
    for tid in ids:
        name = names_by_id.get(tid, tid)
        short = name.split("/")[-1].strip() if "/" in name else name
        # TSPL est envoyé en ASCII pur : "°" (ex. "220°C") tournerait en
        # "?" illisible sur l'étiquette.
        short = short.replace("°", "")
        shown.append((short or tid)[:26])
    return shown


def build_label_tspl(report):
    """Génère le TSPL de l'étiquette QC pour imprimante POS80L, via le gabarit
    template-driven (render_qc_tspl.py) — même principe que la plaque M3
    (m3-driver/render_plaque.py, repo YUMI-POS-Printer) : layout fetché en
    direct sur label.yumi-lab.com, éditable dans Label Expert (panneau QC
    Factory), repli sur un défaut embarqué si injoignable.

    Boîtiers YMS (qc_model commence par "YMS") : média 50x30 mm. QC machine
    (C235/C335/C435...) : média 39x39 mm.

    v1.4 : une étiquette à CHAQUE test (aucun décalage possible dans la pile
    de boîtiers). PASS -> étiquette numéro de série (code + QR). FAIL ->
    cadre gras (jamais confondue avec un PASS au coup d'œil) + QR (rapport
    complet accessible malgré le rejet) : YMS -> position banc + raison ;
    machine -> liste des tests en échec (pour savoir quoi réparer sans
    ouvrir le rapport).

    Returns:
        bytes encodés en ASCII, lignes terminées par CRLF.
    """
    from .render_qc_tspl import render_qc_label

    overall = report.get("overall_result", "?")
    code = report.get("printer_id", "?")
    qc_model = report.get("qc_model", "")
    date = (report.get("date_end") or "")[:16].replace("T", " ")
    is_yms = qc_model.upper().startswith("YMS")
    kind = "yms" if is_yms else "machine"
    section = "pass" if overall == "PASS" else "fail"

    data = {
        "overall_result": overall,
        "code": code,
        "qc_model": qc_model,
        "date": date,
        "qr": "%s%s" % (QC_REPORT_URL_BASE, code),
    }
    if section == "fail":
        if is_yms:
            data["bench_position"] = report.get("bench_position", "?")
            data["fail_reason"] = str((report.get("measures") or {}).get("fail_reason") or "")
        else:
            data["failed_tests"] = _failed_test_labels(report)

    return render_qc_label(kind, section, data)
