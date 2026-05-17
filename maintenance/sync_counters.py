#!/usr/bin/env python3
"""
Yumi Sync Counters — Extension pour yumi_sync

Synchronise les compteurs de maintenance avec le serveur yumi-id.
Protection anti-triche : HMAC local + max(local, serveur) cote serveur.

Integration :
  - Appele par yumi_sync.py toutes les 5 min en impression
  - Lit les compteurs depuis Moonraker API (save_variables)
  - Envoie au serveur, recoit la valeur autorite
  - Signe localement avec HMAC pour detecter tampering

Fichier local : ~/.yumi_counters.json (signe HMAC)
Endpoint serveur : POST https://yumi-id.yumi-lab.com/api/v1/counters
"""

import json
import os
import time
import hashlib
import hmac
import logging

logger = logging.getLogger("yumi_sync_counters")

# Fichier local des compteurs (signe)
COUNTERS_FILE = os.path.expanduser("~/.yumi_counters.json")
# Secret derive du serial MCU (unique par machine)
HMAC_KEY_SOURCE = "/dev/ttyS1"


def _get_hmac_key():
    """
    Derive la cle HMAC du serial MCU + machine-id.
    Unique par machine, pas modifiable par l'utilisateur.
    """
    machine_id = ""
    try:
        with open("/etc/machine-id", "r") as f:
            machine_id = f.read().strip()
    except FileNotFoundError:
        machine_id = "fallback"

    mcu_serial = HMAC_KEY_SOURCE
    raw = f"{machine_id}:{mcu_serial}:yumi_counters_v1"
    return hashlib.sha256(raw.encode()).digest()


def _sign_data(data):
    """Signe un dict JSON avec HMAC-SHA256"""
    key = _get_hmac_key()
    payload = json.dumps(data, sort_keys=True, separators=(",", ":"))
    signature = hmac.new(key, payload.encode(), hashlib.sha256).hexdigest()
    return signature


def _verify_signature(data, signature):
    """Verifie la signature HMAC d'un dict"""
    expected = _sign_data(data)
    return hmac.compare_digest(expected, signature)


def load_local_counters():
    """
    Charge les compteurs locaux signes.
    Retourne (counters_dict, valid_signature).
    Si signature invalide = tampering detecte.
    """
    if not os.path.exists(COUNTERS_FILE):
        return {}, True  # Premier lancement, pas de fichier

    try:
        with open(COUNTERS_FILE, "r") as f:
            container = json.load(f)
    except (json.JSONDecodeError, IOError):
        logger.warning("Counters file corrupted, will force sync")
        return {}, False

    counters = container.get("counters", {})
    signature = container.get("signature", "")
    timestamp = container.get("timestamp", 0)

    if not _verify_signature(counters, signature):
        logger.warning("HMAC signature mismatch — possible tampering detected")
        return counters, False

    return counters, True


def save_local_counters(counters):
    """Sauvegarde les compteurs locaux avec signature HMAC"""
    signature = _sign_data(counters)
    container = {
        "counters": counters,
        "signature": signature,
        "timestamp": int(time.time()),
        "version": 1,
    }
    with open(COUNTERS_FILE, "w") as f:
        json.dump(container, f, indent=2)
    # Permissions restrictives
    os.chmod(COUNTERS_FILE, 0o600)


def read_counters_from_moonraker(moonraker_url="http://localhost:7125"):
    """Lit les compteurs actuels depuis Moonraker (save_variables)"""
    try:
        import requests
        resp = requests.get(
            f"{moonraker_url}/printer/objects/query?save_variables",
            timeout=5
        )
        if resp.status_code != 200:
            return None

        data = resp.json()
        variables = data.get("result", {}).get("status", {}).get("save_variables", {}).get("variables", {})

        counters = {}
        for key, value in variables.items():
            if key.startswith("maint_"):
                counter_name = key[6:]  # strip "maint_"
                counters[counter_name] = value

        return counters
    except Exception as e:
        logger.error(f"Failed to read Moonraker: {e}")
        return None


def sync_to_server(counters, printer_serial, server_url="https://yumi-id.yumi-lab.com"):
    """
    Envoie les compteurs au serveur, recoit la valeur autorite.

    Protocol:
      POST /api/v1/counters
      Body: { "serial": "xxx", "counters": {...}, "timestamp": ..., "hmac": "..." }
      Response: { "counters": {...}, "server_timestamp": ... }

    Logique serveur : max(local, serveur) pour chaque compteur.
    → Si l'utilisateur reset localement, le serveur restaure la vraie valeur.
    """
    try:
        import requests

        # Signer la requete
        payload = {
            "serial": printer_serial,
            "counters": counters,
            "timestamp": int(time.time()),
        }
        payload["hmac"] = _sign_request(payload)

        resp = requests.post(
            f"{server_url}/api/v1/counters",
            json=payload,
            timeout=10,
            headers={"Content-Type": "application/json"}
        )

        if resp.status_code == 200:
            server_data = resp.json()
            server_counters = server_data.get("counters", {})
            return server_counters
        else:
            logger.warning(f"Server returned {resp.status_code}")
            return None

    except Exception as e:
        logger.error(f"Failed to sync to server: {e}")
        return None


def _sign_request(payload):
    """Signe la requete vers le serveur"""
    key = _get_hmac_key()
    # Exclure le champ hmac lui-meme
    data = {k: v for k, v in payload.items() if k != "hmac"}
    raw = json.dumps(data, sort_keys=True, separators=(",", ":"))
    return hmac.new(key, raw.encode(), hashlib.sha256).hexdigest()


def merge_counters(local, server):
    """
    Fusionne les compteurs : max(local, server) pour chaque cle.
    Les compteurs ne font que monter (jamais baisser).
    """
    merged = {}
    all_keys = set(list(local.keys()) + list(server.keys()))
    for key in all_keys:
        local_val = local.get(key, 0)
        server_val = server.get(key, 0)
        if isinstance(local_val, (int, float)) and isinstance(server_val, (int, float)):
            merged[key] = max(local_val, server_val)
        else:
            merged[key] = server_val if server_val else local_val
    return merged


def sync_counters(printer_serial, moonraker_url="http://localhost:7125",
                  server_url="https://yumi-id.yumi-lab.com"):
    """
    Point d'entree principal — appele par yumi_sync toutes les 5 min.

    Flux:
      1. Lire compteurs Moonraker (source de verite locale)
      2. Charger compteurs locaux signes
      3. Si signature invalide → forcer sync serveur
      4. Envoyer au serveur
      5. Recevoir valeur autorite (max)
      6. Ecrire localement avec HMAC
      7. Si serveur > local → mettre a jour Moonraker
    """
    # 1. Lire Moonraker
    moonraker_counters = read_counters_from_moonraker(moonraker_url)
    if moonraker_counters is None:
        logger.warning("Cannot read Moonraker, skipping sync")
        return False

    # 2. Charger local
    local_counters, signature_valid = load_local_counters()

    # 3. Si tampering detecte, forcer sync
    if not signature_valid:
        logger.warning("Local counters tampered, forcing server sync")

    # Utiliser max(moonraker, local) comme valeur a envoyer
    to_send = merge_counters(moonraker_counters, local_counters)

    # 4. Sync serveur
    server_counters = sync_to_server(to_send, printer_serial, server_url)

    if server_counters:
        # 5. Merge avec serveur (autorite)
        final = merge_counters(to_send, server_counters)
    else:
        # Pas de serveur, garder local
        final = to_send

    # 6. Sauvegarder localement
    save_local_counters(final)

    # 7. Si serveur avait des valeurs plus hautes, mettre a jour Moonraker
    if server_counters:
        _update_moonraker_if_needed(moonraker_counters, final, moonraker_url)

    return True


def _update_moonraker_if_needed(old_counters, new_counters, moonraker_url):
    """Met a jour Moonraker si les compteurs serveur sont superieurs"""
    try:
        import requests
        for key, new_val in new_counters.items():
            old_val = old_counters.get(key, 0)
            if isinstance(new_val, (int, float)) and new_val > old_val:
                gcode = f"SAVE_VARIABLE VARIABLE=maint_{key} VALUE={new_val}"
                requests.post(
                    f"{moonraker_url}/printer/gcode/script",
                    json={"script": gcode},
                    timeout=5
                )
    except Exception as e:
        logger.error(f"Failed to update Moonraker: {e}")


# ============================================================
# CLI pour debug
# ============================================================

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Yumi Sync Counters")
    parser.add_argument("--read", action="store_true", help="Read local counters")
    parser.add_argument("--verify", action="store_true", help="Verify local signature")
    parser.add_argument("--sync", action="store_true", help="Force sync cycle")
    parser.add_argument("--serial", default="TEST-001", help="Printer serial number")
    parser.add_argument("--mock-counters", action="store_true", help="Write mock counters for testing")
    args = parser.parse_args()

    if args.mock_counters:
        mock = {
            "print_hours": 42.5,
            "extrusion_total_mm": 1500000,
            "bed_heat_hours": 38.2,
            "hotend_heat_hours": 40.1,
            "cuts_count": 234,
            "homing_count": 567,
            "extrusion_yms_0": 500000,
            "extrusion_yms_1": 400000,
            "extrusion_yms_2": 300000,
        }
        save_local_counters(mock)
        print(f"Mock counters saved to {COUNTERS_FILE}")

    if args.read:
        counters, valid = load_local_counters()
        print(f"Signature valid: {valid}")
        print(json.dumps(counters, indent=2))

    if args.verify:
        _, valid = load_local_counters()
        print(f"Signature valid: {valid}")
        if not valid:
            print("WARNING: Counters may have been tampered with!")

    if args.sync:
        print(f"Syncing counters for serial {args.serial}...")
        result = sync_counters(args.serial)
        print(f"Sync result: {'OK' if result else 'FAILED'}")
