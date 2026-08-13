# QC machines C-series — Documentation

> Ce document décrit le QC des imprimantes C235/C335/C435 (branche
> `qc-machines-dev`, dossier `qc/`). Le cahier des charges serveur est
> `docs/CDC-SERVEUR-MACHINES.md` ; les réponses au serveur (inventaire des
> mesures, fail_reason, versions, retest) sont dans
> `docs/REPONSES-SERVEUR.md`. Pour le banc YMS, voir `README-YMS.md`.

## Architecture

Le wizard GTK (`qc_wizard.py`) pilote une séquence de 13 tests via
`qc_engine.py` (macros Klipper de `qc_macros.cfg`, cfg modèle
`qc_printer_<MODEL>.cfg` générée par `generate_qc_cfg.py`) :

`mcu_check → home_x → home_y → fan_motherboard → fan_part → fan_hotend →
heat_extruder → heat_bed → cutter → e1_head → z_tap_home → z_tap_calib →
screws_tilt`

Le rapport est sauvegardé dans `~/printer_data/config/qc_reports/` puis
uploadé par `qc_upload_pending.py` (store-and-forward, marqueur `.sent`,
`qc-upload.timer`).

## Rapport measures-first (contrat additif serveur)

Le format historique est INTACT (printer_id, machine_uid, overall_result,
date, yumi_config, pad_mac, tests[] id/name/result/timestamp/details/log).
Ajouts, tous optionnels côté serveur :

- **`measures` + `fail_reason` par entrée tests[]** : extraits des logs déjà
  capturés par l'engine — module pur `qc_machine_measures.py` (aucun GTK,
  style `qc_yms.extract_measures`, réutilisé tel quel pour `e1_head`).
  Clés par test : voir `docs/REPONSES-SERVEUR.md` §1 et §2.
- **`software_versions`** (racine) : `klipper_version`, `firmware_version`
  {mcu: version}, `image_version`, `qc_cfg_version` (sha256 court de la cfg
  déployée). Chaque clé absente de sa source est omise (§3).
- **`retest: true` + `retest_reason`** : uniquement quand un rapport
  précédent du même `machine_uid` existe dans `qc_reports/` du pad (§4).
- `technician` n'est **plus envoyé** (abandonné côté serveur).

`bed_mesh` et `e0_head` restent dans le catalogue mais hors séquence machine
(pas de measures, jamais envoyés).

## Tests et sandbox

```bash
./verify.sh                                        # gate complet (py_compile + cfg + 136+ tests)
python3 scripts/sandbox_machine_test.py            # POST sandbox sur qc.yumi-lab.com (token requis)
```

Le script sandbox poste un rapport machine réaliste (construit par le vrai
`QCEngine`) avec `"sandbox": true` : validation serveur identique au réel,
rien n'est écrit. Token : env `QC_TOKEN` > `qc_token` du pad > `.env` racine.

## Déploiement

```bash
bash ~/yumi-config/qc/install_qc_station.sh [TOKEN_QC]   # symlinks ks_includes + timer
bash ~/yumi-config/qc/sync_qc_cfgs.sh                    # cfg modèle vers le pad
```
