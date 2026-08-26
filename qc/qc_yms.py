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

# Le code alloué par le serveur porte le modèle+version en préfixe littéral
# (ex. "YMSLV1.020260822MGJSSTYP64") -- redondant sur l'étiquette avec
# {qc_model} affiché à côté, à retirer de l'affichage (demande du 22/08).


def strip_model_from_code(code, yms_version):
    """Retire le préfixe modèle+version EXACT (ex. "YMSLV1.0") d'un code YMS
    pour l'affichage étiquette (-> "20260822MGJSSTYP64"). Le préfixe attendu
    est dérivé de MODELS + yms_version, PAS d'une regex ouverte sur des
    chiffres : version et date sont tous deux numériques sans séparateur,
    donc un motif générique mangerait aussi la date. Code d'une autre forme
    (ex. QCFL- de FAIL) ou yms_version absent -> renvoyé inchangé."""
    if not code or not yms_version:
        return code or ""
    for _, prefix in MODELS.values():
        expected = "%sV%s" % (prefix.rstrip("-"), yms_version)
        if code.startswith(expected):
            return code[len(expected):]
    return code

# 7 valeurs normées de measures.fail_reason
def extract_measures(logs, passed):
    """Extrait les measures{} depuis les logs d'un test e<n>_head.

    Les champs restent None/[] quand ils ne sont pas observés. Si le test a
    échoué mais qu'aucune signature n'est trouvée, fail_reason vaut 'timeout'.
    """
    m = {
        "feed_mm": None,
        "feed_budget_mm": LOAD_DIST_MM,
        "head_reached": False,
        "motion_first_detect": False,
        "dropouts_e": [],
        "dropout_count": 0,
        "feed_dropout": False,
        "stress_segments_ok": 0,
        "stress_segments_total": 8,
        "stress_speeds_mms": [10, 30, 50, 80],
        "stress_points": [],
        "stress_segments_ignored": 8,
        "retract_mm": None,
        "tmc_error": None,
        "heat_target_c": None,
        "heat_reached_c": None,
        "heat_curve": [],
        "fail_reason": None,
    }
    for line in logs:
        if "state changed" in line:
            m["motion_first_detect"] = True
        # v3 (23/08) : plus de capteur tete -- charge groupee a distance
        # FIXE (load_all). "head_reached" reste toujours False (jamais
        # verifie), feed_mm vient de la charge reussie/echouee.
        r = re.search(r"loaded (\d+)mm, motion sensor OK", line)
        if r:
            m["feed_mm"] = int(r.group(1))
        r = re.search(r"no motion detected over (\d+)mm", line)
        if r:
            m["feed_mm"] = int(r.group(1))
            m["fail_reason"] = "no_motion_on_load"
        r = re.search(r"encoder dropout E=([\d.]+)", line)
        if r:
            m["dropouts_e"].append(float(r.group(1)))
        if "STOPPED tracking during feed" in line:
            m["feed_dropout"] = True
            m["fail_reason"] = "sensor_lost_feed"
            r = re.search(r"\(at (\d+)mm\)", line)
            if r:
                m["feed_mm"] = int(r.group(1))
        if "did NOT change state" in line:
            m["fail_reason"] = "sensor_mute"
        if "already at head before feed" in line:
            m["fail_reason"] = "already_at_head"
        # "LOST tracking at segment" (majuscules) = decrochage sur un segment
        # COMPTE -> echec reel. "lost tracking during ramp segment" (rampe,
        # non compte, 26/08) est volontairement une AUTRE chaine -- ignore
        # ici, jamais de fail_reason pour un decrochage pendant la rampe
        # d'acceleration/deceleration.
        if "LOST tracking at segment" in line:
            m["fail_reason"] = "sensor_lost_stress"
        # Point de mesure par segment (26/08) : reconstruit le detail du
        # sweep stress cote rapport (stress_points = [{"seg","speed_mms",
        # "detected","counted"}, ...]). "counted" distingue le plateau
        # vitesse constante (mesure retenue pour le verdict/total affiche)
        # de la rampe accel/decel qui l'entoure (mecanique uniquement, les 4
        # premiers/derniers segments -- demande du 26/08 : "mesure propre").
        r = re.search(
            r"stress (\d+)/(\d+) speed=(\d+)mm/s detected=(True|False) counted=(True|False)",
            line)
        if r:
            seg, total, speed, detected, counted = r.groups()
            m["stress_points"].append({
                "seg": int(seg), "speed_mms": int(speed),
                "detected": detected == "True", "counted": counted == "True",
            })
            if counted == "True" and detected == "True":
                m["stress_segments_ok"] += 1
            # stress_segments_total reste le defaut (8, le plateau compte) --
            # "total" capture ici est nseg (16, la rampe ENTIERE), pas la
            # bonne valeur pour le total affiche (26/08 : "mesure propre").
        r = re.search(r"stress OK.*?(\d+) segments", line)
        if r:
            m["stress_segments_ok"] = int(r.group(1))
        if "DRV_STATUS" in line:
            m["tmc_error"] = line.strip()[:200]
            m["fail_reason"] = "tmc_error"
        # YMS Pro : chauffe groupee (heat_all), positions cablees seulement.
        # Point de mesure toutes les 10s (25/08) -- reconstruit la courbe de
        # chauffe cote rapport (heat_curve = [[secondes, degres], ...]).
        r = re.search(r"heat (\d+)s ([\d.]+)C", line)
        if r:
            m["heat_curve"].append([int(r.group(1)), float(r.group(2))])
        r = re.search(r"heat OK, ([\d.]+)C reached \(target (\d+)C\)", line)
        if r:
            m["heat_reached_c"] = float(r.group(1))
            m["heat_target_c"] = int(r.group(2))
        r = re.search(r"heat timeout, ([\d.]+)C after \d+s \(target (\d+)C\)", line)
        if r:
            m["heat_reached_c"] = float(r.group(1))
            m["heat_target_c"] = int(r.group(2))
            m["fail_reason"] = "heat_timeout"
    m["dropout_count"] = len(m["dropouts_e"])
    if m["feed_mm"]:
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


LOAD_ALL_TEST_ID = "load_all"
STRESS_ALL_TEST_ID = "stress_all"
HEAT_START_TEST_ID = "heat_start"
HEAT_ALL_TEST_ID = "heat_wait"
# v9 (26/08) : 30 -> 80mm -- le sweep stress passe a une rampe accel/decel
# ±70mm (cf. generate_yms12_cfg.py QC_STRESS_ALL) pour une mesure "propre"
# sur le plateau haute vitesse (les 4 premiers/derniers segments, en rampe,
# ne comptent plus dans le verdict). 80mm de charge laisse 10mm de marge
# avant le point d'insertion initial (80-70=10) -- jamais en arriere de la
# ou l'operateur a engage le filament, contrairement a un ±70mm sur une
# charge de 30mm qui aurait recule jusqu'a -40mm (hors prise extrudeur).
LOAD_DIST_MM = 80

# YMS Pro seulement : plateau chauffant + sonde intégrés, câblés sur le banc
# UNIQUEMENT aux 3 premiers slots de chaque hyperdrive (positions 3,4,5 et
# 8,9,10 — cf. generate_yms12_cfg.py HEAT_SLOTS). Positions 1,2,6,7,11,12
# n'ont jamais cette option, quel que soit le modèle sélectionné.
HEAT_CAPABLE_POSITIONS = (3, 4, 5, 8, 9, 10)
HEAT_TARGET_C = 85


def heat_positions_for_run(disabled_positions=None, model=None):
    """Positions câblées chauffe ET actives pour ce run -- [] si model n'est
    pas "pro". Même filtre que build_yms_tests (extrait ici pour être
    réutilisé par le contrôle préalable des capteurs, cf. qc_wizard)."""
    if (model or "").lower() != "pro":
        return []
    disabled = set(disabled_positions or [])
    return [p for p in HEAT_CAPABLE_POSITIONS if p not in disabled]


def find_unready_heat_positions(positions, temperatures):
    """Contrôle avant de lancer un test PRO (demande du 26/08) : si une seule
    des positions câblées chauffe du lot ne renvoie pas une température
    plausible, on ne lance PAS le test -- évite de découvrir 30min plus tard
    qu'une sonde est débranchée/HS alors que c'était visible avant de
    démarrer.

    positions : positions câblées chauffe actives pour ce run.
    temperatures : {position: température°C|None}, lue EN DIRECT sur le
        printer juste avant de lancer.

    Renvoie la liste des positions en défaut (température absente ou <= 0 --
    une sonde qui fonctionne lit au moins la température ambiante, toujours
    positive). [] = toutes les sondes répondent, le test peut démarrer.
    """
    bad = []
    for p in positions:
        t = temperatures.get(p)
        if t is None or t <= 0:
            bad.append(p)
    return bad


def build_yms_tests(disabled_positions=None, model=None):
    """Construit la séquence YMS12 incluant les positions désactivées en SKIP.

    v3 (23/08) — le capteur tête est retiré du protocole banc : il ne
    validait que le chemin tube DU BANC (partagé, unique), jamais le YMS
    testé. Ce qui valide réellement le YMS = le feeder qui pousse + le
    motion sensor qui suit, y compris sous charge dynamique (le sweep
    stress). Sans capteur tête à atteindre, plus besoin de séquentiel du
    tout : TOUS les boîtiers actifs chargent ENSEMBLE (load_all, distance
    FIXE LOAD_DIST_MM, tous synchronisés) puis stressent ENSEMBLE
    (stress_all) — 2 étapes groupées au lieu de 12 individuelles + 1
    groupée. Chaque motion sensor YMS-n a sa PROPRE pin -> l'attribution
    par position reste correcte même en mouvement groupé.

    model="pro" : SEULEMENT les positions câblées chauffe (HEAT_CAPABLE_
    POSITIONS, 3/4/5/8/9/10) sont testées, sur TOUTES les étapes (load_all
    ET stress_all EN PLUS de heat_all) — un boîtier Pro ne se place que sur
    ces postes du banc, jamais sur 1/2/6/7/11/12 (demande du 23/08 : "on ne
    fait que le 3,4,5,8,9,10, on ne fait pas les autres"). model="light" (ou
    absent) garde le comportement large : toutes les positions actives.

    Returns:
        liste de dicts test (compatible avec QCEngine.tests) : mcu_check +
        load_all + stress_all [+ heat_all si model="pro"], portant TOOLS=
        la liste des positions ACTIVES (non désactivées, et restreintes aux
        positions câblées chauffe si model="pro") — source de vérité
        indépendante de tout état firmware résiduel d'un run précédent.
        Aucun test individuel par position : le rapport de chaque boîtier
        est reconstruit après coup depuis les logs partagés (cf.
        qc_wizard._dispatch_all_boxes_ordered).
    """
    disabled = set(disabled_positions or [])
    enabled = [p for p in range(1, YMS_BENCH_TOTAL + 1) if p not in disabled]
    is_pro = (model or "").lower() == "pro"
    if is_pro:
        enabled = heat_positions_for_run(disabled_positions, model)
    tests = [{
        "id": "mcu_check",
        "name": "主板×3 + 固件 / MCUs + firmware",
        "type": "automated",
        "macro": "QC_MCU_CHECK",
        "timeout": 60,
    }]
    if enabled:
        tools_arg = ",".join(str(p) for p in enabled)
        if is_pro:
            # v4 (23/08) : la chauffe DEMARRE en premier, non-bloquant --
            # elle monte en tâche de fond PENDANT load_all/stress_all
            # (qui n'ont pas besoin d'attendre), au lieu de s'ajouter APRÈS
            # coup au temps total. QC_HEAT_WAIT (fin de séquence) ne
            # décompte son timeout QUE depuis son propre appel -- le temps
            # déjà chauffé pendant load/stress est "gratuit".
            tests.append({
                "id": HEAT_START_TEST_ID,
                "name": "全部并行加热启动 / Heat start (parallel, %d°C)" % HEAT_TARGET_C,
                "type": "automated",
                "macro": "QC_HEAT_START TOOLS=%s TARGET=%d" % (tools_arg, HEAT_TARGET_C),
                "timeout": 30,
            })
        tests.append({
            "id": LOAD_ALL_TEST_ID,
            "name": "全部并行加载 / Load all (parallel, %dmm)" % LOAD_DIST_MM,
            "type": "automated",
            "macro": "QC_LOAD_ALL TOOLS=%s DIST=%d" % (tools_arg, LOAD_DIST_MM),
            "timeout": 60,
        })
        tests.append({
            "id": STRESS_ALL_TEST_ID,
            "name": "全部并行应力测试 / Stress sweep (parallel, all boxes)",
            "type": "automated",
            "macro": "QC_STRESS_ALL TOOLS=%s" % tools_arg,
            "timeout": 60,
        })
        if is_pro:
            tests.append({
                "id": HEAT_ALL_TEST_ID,
                "name": "全部并行加热测试 / Heat wait (parallel, %d°C)" % HEAT_TARGET_C,
                "type": "automated",
                "macro": "QC_HEAT_WAIT TOOLS=%s TARGET=%d" % (tools_arg, HEAT_TARGET_C),
                "timeout": 330,
            })
    return tests


def build_retest_sequence(position, model=None):
    """Mini-séquence pour re-tester un seul boîtier YMS en échec.

    v4 (23/08) : même chemin que la séquence principale — heat_start
    (non-bloquant, si model="pro" et position câblée chauffe) démarre AVANT
    load_all/stress_all pour chauffer en tâche de fond pendant ceux-ci,
    heat_wait ferme la séquence. TOOLS= réduit à cette seule position — le
    dispatch (qc_wizard._dispatch_all_boxes_ordered) fonctionne sans
    changement, les positions absentes du lot sont simplement ignorées.
    mcu_check inclus comme skipped (déjà validé en séquence principale)
    pour conserver la structure du rapport.
    """
    heat_capable = (model or "").lower() == "pro" and position in HEAT_CAPABLE_POSITIONS
    tests = [{
        "id": "mcu_check",
        "name": "主板×3 + 固件 / MCUs + firmware",
        "type": "automated",
        "macro": "",
        "timeout": 0,
        "skipped": True,
    }]
    if heat_capable:
        tests.append({
            "id": HEAT_START_TEST_ID,
            "name": "YMS-%d 加热启动 / heat start (%d°C)" % (position, HEAT_TARGET_C),
            "type": "automated",
            "macro": "QC_HEAT_START TOOLS=%d TARGET=%d" % (position, HEAT_TARGET_C),
            "timeout": 30,
        })
    tests.append({
        "id": LOAD_ALL_TEST_ID,
        "name": "YMS-%d 加载 / load" % position,
        "type": "automated",
        "macro": "QC_LOAD_ALL TOOLS=%d DIST=%d" % (position, LOAD_DIST_MM),
        "timeout": 60,
    })
    tests.append({
        "id": STRESS_ALL_TEST_ID,
        "name": "YMS-%d 应力测试 / stress" % position,
        "type": "automated",
        "macro": "QC_STRESS_ALL TOOLS=%d" % position,
        "timeout": 60,
    })
    if heat_capable:
        tests.append({
            "id": HEAT_ALL_TEST_ID,
            "name": "YMS-%d 加热测试 / heat wait (%d°C)" % (position, HEAT_TARGET_C),
            "type": "automated",
            "macro": "QC_HEAT_WAIT TOOLS=%d TARGET=%d" % (position, HEAT_TARGET_C),
            "timeout": 330,
        })
    return tests


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
                     extruder_model="", spring_model="", yms_version="1.0"):
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
        "yms_version": yms_version,
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


def _import_render_qc_tspl():
    # Import relatif si qc_yms est chargé comme partie d'un paquet (qc.qc_yms
    # depuis mes tests) ; repli sys.path sinon — c'est le cas RÉEL sur le pad,
    # où KlipperScreen charge ce fichier via le symlink ks_includes/qc_yms.py
    # (donc comme ks_includes.qc_yms, pas qc.qc_yms) : import relatif
    # chercherait alors ks_includes.render_qc_tspl, qui n'existe pas.
    try:
        from . import render_qc_tspl
    except ImportError:
        import os
        import sys
        sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))
        import render_qc_tspl
    return render_qc_tspl


def _label_kind_section_data(report):
    """kind/section/data communs à build_label_tspl et build_label_png_job --
    UNE SEULE fois la logique de mapping report -> placeholders gabarit."""
    overall = report.get("overall_result", "?")
    code = report.get("printer_id", "?")
    qc_model = report.get("qc_model", "")
    date = (report.get("date_end") or "")[:16].replace("T", " ")
    is_yms = qc_model.upper().startswith("YMS")
    kind = "yms" if is_yms else "machine"
    section = "pass" if overall == "PASS" else "fail"

    # {qc_model} affiché sur l'étiquette porte la version produit (demandé le
    # 22/08 : "YMS-LIGHT-V1.0", pas juste "YMS-LIGHT") -- calculé UNIQUEMENT
    # ici pour l'affichage, on ne touche pas report["qc_model"] lui-même
    # (utilisé tel quel côté serveur/dashboard, aucune raison de le changer).
    display_model = qc_model
    if is_yms and report.get("yms_version"):
        display_model = "%s-V%s" % (qc_model, report["yms_version"])

    data = {
        "overall_result": overall,
        # {code} affiché = sans le prefixe modele+version (deja dans
        # {qc_model}) ; le QR garde le code BRUT pour que /report/<code>
        # reste valide.
        "code": strip_model_from_code(code, report.get("yms_version")) if is_yms else code,
        "qc_model": display_model,
        "date": date,
        "qr": "%s%s" % (QC_REPORT_URL_BASE, code),
    }
    if is_yms:
        # bench_position existe que le boîtier passe ou échoue -- le gabarit
        # PASS peut vouloir l'afficher aussi (ajouté par Nicolas le 22/08,
        # jusque-là seul le FAIL le recevait -> "POS {bench_position}" sortait
        # tel quel, non substitué, dès qu'on l'ajoutait à la section pass).
        data["bench_position"] = report.get("bench_position", "?")
    if section == "fail":
        if is_yms:
            data["fail_reason"] = str((report.get("measures") or {}).get("fail_reason") or "")
        else:
            data["failed_tests"] = _failed_test_labels(report)
    return kind, section, data


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
    render_qc_tspl = _import_render_qc_tspl()
    kind, section, data = _label_kind_section_data(report)
    return render_qc_tspl.render_qc_label(kind, section, data)


# Média physique par kind, mm -- même table que render_qc_tspl.MEDIA, dupliquée
# ici pour rester un module PUR (pas d'import Pillow) tant que
# build_label_png_job n'a besoin QUE des dimensions, pas du rendu lui-même.
LABEL_MEDIA_MM = {"yms": (50, 30), "machine": (39, 39)}


def build_label_png_job(report, qty=1):
    """Génère le job JSON attendu par la file d'impression réseau (gs1-proxy
    /api/gs1/print/factory -> pos80l-cloud -> pos80l-bridge, cf. skill
    factory-printer-proxy) : {"image": "data:image/png;base64,...", "qty",
    "gap_mm", "width_mm", "height_mm", "peel"}. Relais réseau (26/08) utilisé
    quand l'impression LAN directe (build_label_tspl + lp -h smartpi-printer-
    factory.local:631) échoue -- le pad peut être hors du LAN usine tout en
    gardant un accès HTTPS normal (même chemin que l'upload de rapport QC)."""
    render_qc_tspl = _import_render_qc_tspl()
    kind, section, data = _label_kind_section_data(report)
    w_mm, h_mm = LABEL_MEDIA_MM[kind]
    image = render_qc_tspl.render_qc_label_png_data_url(kind, section, data)
    return {
        "image": image, "qty": qty, "gap_mm": 2,
        "width_mm": w_mm, "height_mm": h_mm, "peel": True,
    }
