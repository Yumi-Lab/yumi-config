# Banc QC YMS — Documentation

> Ce document décrit le banc QC YMS 12 positions (branche `yms-dev`, dossier `qc/`).
> Le contrat serveur détaillé est dans `docs/FORMAT-YMS.md` du repo
> `Yumi-Lab/yumi-qc-counter` (v1.1).

## Architecture

Le banc teste 12 boîtiers YMS en une seule séquence automatisée :

- **Carte principale C235** : YMS-1 et YMS-2 sur `main:E0` et `main:E1`.
- **HyperDrive UART** (`/dev/ttyS2`) : YMS-3 à YMS-7.
- **HyperDrive USB** (`/dev/ttyACM0`) : YMS-8 à YMS-12.

Chaque boîtier est identifié par un code alloué au début de la séquence par le
serveur QC (`POST /api/qc/yms/allocate`). Deux modèles sont supportés :

- **YMS-LIGHT** → préfixe `YMSL-`
- **YMS-PRO** → préfixe `YMSP-`

## Mapping YMS ↔ extruder ↔ slot

| Boîtier | Extruder | Test id | Slot physique |
|---------|----------|---------|---------------|
| YMS-1  | extruder0 | `e0_head` | `main:E0` |
| YMS-2  | extruder1 | `e1_head` | `main:E1` |
| YMS-3  | extruder2 | `e2_head` | `hyperdrive_uart:1` |
| YMS-4  | extruder3 | `e3_head` | `hyperdrive_uart:2` |
| YMS-5  | extruder4 | `e4_head` | `hyperdrive_uart:3` |
| YMS-6  | extruder5 | `e5_head` | `hyperdrive_uart:4` |
| YMS-7  | extruder6 | `e6_head` | `hyperdrive_uart:5` |
| YMS-8  | extruder7 | `e7_head` | `hyperdrive_usb:1` |
| YMS-9  | extruder8 | `e8_head` | `hyperdrive_usb:2` |
| YMS-10 | extruder9 | `e9_head` | `hyperdrive_usb:3` |
| YMS-11 | extruder10 | `e10_head` | `hyperdrive_usb:4` |
| YMS-12 | extruder11 | `e11_head` | `hyperdrive_usb:5` |

## Macros et phases du test `QC_HEAD_FEED`

La macro générique `QC_HEAD_FEED TOOL=n` (n=1..12) exécute pour chaque boîtier :

1. **Reset** : désactive tous les capteurs motion, extrusion à vide, puis arme
   seulement le capteur du YMS testé.
2. **Feed** : pousse le filament jusqu'au capteur tête (`!PA8`) sur la C235,
   budget max 900 mm. Un décrochage en cours de feed = FAIL immédiat.
3. **Stress** : une fois la tête atteinte, 16 segments aller-retour ±100 mm
   à vitesses croissantes/décroissantes (10→100→10 mm/s). Le motion sensor
   doit suivre sur chaque segment.
4. **Rétraction** : vide complètement le tube partagé avant le boîtier suivant.

## Critères PASS / FAIL

Un boîtier est **PASS** si :

- le filament atteint la tête (`filament a la tete apres Xmm`) ;
- le motion sensor a changé d'état au moins une fois (`a change d'etat`) ;
- le stress 16/16 est validé (`stress OK — 16 segments`) ;
- aucune erreur TMC ni perte de suivi capteur.

Sinon le résultat est **FAIL** avec un `fail_reason` normé :

| `fail_reason` | Cause observée |
|---------------|----------------|
| `sensor_mute` | Filament à la tête mais le motion sensor n'a pas changé d'état. |
| `sensor_lost_feed` | Perte de suivi du capteur pendant le feed (`a CESSE de suivre pendant le feed`). |
| `sensor_lost_stress` | Perte de suivi pendant la phase stress (`PERDU le suivi au segment`). |
| `head_not_reached` | Filament non à la tête après 900 mm. |
| `tmc_error` | Erreur TMC (`DRV_STATUS`). |
| `already_at_head` | Filament déjà présent à la tête avant le feed. |
| `timeout` | Macro morte, pas de signal QC:* après le budget timeout. |

## Slots hors service

Le fichier `~/printer_data/config/qc_bench_slots.json` permet de désactiver des
positions (ex. `{"disabled": [2, 11]}`). Ces positions sont :

- comptées comme `skipped` dans le rapport de session ;
- exclues de l'allocation (`count = 12 - len(disabled)`) ;
- non associées à un code, donc aucun rapport/étiquette n'est émis.

## Sélecteur LIGHT / PRO

Au lancement d'une séquence YMS, un dialogue demande le modèle. Toute la
série utilise le même modèle (pas de mixte). La valeur par défaut est
**LIGHT**. Le modèle choisi est propagé à :

- le champ `model` de l'allocation serveur ;
- le champ `qc_model` du rapport boîtier ;
- le champ `device=` de `yumi_config` ;
- le texte de l'étiquette TSPL.

## Re-test unitaire

Sur l'écran résumé, toucher une ligne `e<n>_head` en FAIL propose de re-tester
ce boîtier seul. Le système :

1. alloue un nouveau code (count=1, même modèle) ;
2. exécute uniquement `e<n>_head` ;
3. envoie le rapport et imprime l'étiquette comme en séquence.

Le code précédent (échec) reste brûlé, conformément au contrat serveur.

## Étiquette TSPL

Impression sur POS80L (`/dev/usb/lp0`) uniquement en cas de PASS. Le TSPL
contient un QR code vers `https://qc.yumi-lab.com/report/<printer_id>`.

## Déploiement

```bash
# Sur le pad usine
bash ~/yumi-config/qc/install_qc_station.sh [TOKEN_QC]

# Régénérer la cfg YMS12 après modification de qc_printer_C235.cfg
cd ~/yumi-config/qc && python3 generate_yms12_cfg.py

# Synchroniser les cfg vers le pad
bash ~/yumi-config/qc/sync_qc_cfgs.sh
```

## Dépannage — pannes banc vécues

- **Phase A TMC** : erreur `DRV_STATUS` sur un extruder_stepper → vérifier
  câblage moteur, courant TMC (`run_current: 1.2`), température driver.
- **Entrée capteur morte** : `sensor_mute` répété sur un slot → contrôler le
  câble du motion sensor et le connecteur du slot HyperDrive.
- **Thermistances** : le banc n'a pas de chauffe ; la cfg force
  `min_extrude_temp: -100` pour qu'une tête débranchée ne bloque pas le feed.

## Contrat serveur

Voir `docs/FORMAT-YMS.md` (v1.1) dans `Yumi-Lab/yumi-qc-counter` :

- `POST /api/qc/yms/allocate` `{"model","count"}` → `{"status":"ok","yms_ids":[...]}` ;
- `POST /api/qc/report` avec un rapport par boîtier, `device=YMS-LIGHT|YMS-PRO`,
  `machine_uid:""`, champs `bench_position/slot/session/total` ;
- `measures{}` est la source primaire avec `fail_reason` normé.
