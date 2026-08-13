# Réponses au serveur QC — machines C-series (C235/C335/C435)

> Réponses aux questions de `docs/CDC-SERVEUR-MACHINES.md` §7 (émis le
> 13/08/2026). État du pad : branche `qc-machines-dev`, lots L1→L7 livrés.
> Tout ce qui suit est **ADDITIF** : un rapport sans ces champs reste valide.
> Implémentation de référence : `qc/qc_machine_measures.py` (module pur),
> rapport assemblé par `qc/qc_engine.py::generate_report`.

## 1. Inventaire exhaustif des mesures remontées (measures par test)

Chaque entrée `tests[]` porte un bloc `measures` (avec `fail_reason` inclus,
style YMS) dès que le test a été exécuté (pass/fail). `details` et `log`
restent inchangés (repli humain). `null` = mesure non disponible sur ce run
(instrumentation pas déployée ou ligne de log absente) — la clé est toujours
présente pour un test donné.

| Test id | Clés `measures` | Source |
|---------|-----------------|--------|
| `mcu_check` | `mcu_uid` (str), `mcu_count` (int), `firmware_versions` ({mcu: version}, MCU hôte inclus), `yumi_config_found` (bool) | lignes `[<mcu>] version:`, `MCU_UID=`, `device=` |
| `home_x` / `home_y` | `axis` ("X"/"Y"), `sg_thrs` (int), `taps_valides` (int), `taps_rejetes` (int), `spread_mm`, `tolerance_mm`, `zero_pos_mm`, `duration_s` | logs `yumi_sensorless_homing` + durée engine |
| `fan_motherboard` / `fan_part` / `fan_hotend` | `visual_ack` (bool) | verdict opérateur (pas de tachymètre câblé) |
| `heat_extruder` | `target_c` (220), `reached_c` (**null aujourd'hui**), `ramp_s` (durée mesurée), `stable` (**null aujourd'hui**) | cible macro `QC_HEAT_EXTRUDER` ; `reached_c`/`stable` remplis dès que l'instrumentation `HEAT_OK <sensor> t=…` sera déployée (parse déjà en place) |
| `heat_bed` | idem avec `target_c` (60) | macro `QC_HEAT_BED` |
| `cutter` | `motion_first_detect` (bool), `feed_mm` (**null en PASS**, = budget atteint en échec « pas à la tête »), `cut_ok` (bool, opérateur) | logs `_QC_HEAD_FEED` mode cutter |
| `e1_head` | `feed_mm`, `feed_budget_mm` (800), `head_reached` (bool), `motion_first_detect` (bool) | **mêmes clés que le banc YMS** (parseur `qc_yms.extract_measures` réutilisé), clés banc-only élguées (stress/dropouts/retract), budget recalé 800 vs 900 |
| `z_tap_home` | `tap_z_mm` (1er tap = home Z), `z_max_mm` (**null aujourd'hui**, parse `ZMAX=` forward-compatible), `visual_ack` (bool) | lignes `VALIDATED: trigger_z=` |
| `z_tap_calib` | `taps_mm` (liste), `spread_mm`, `tolerance_mm` (0.05), `n_taps` (int), `converged_n` (int) | verdict engine relu de `details`, repli = re-calcul fenêtré depuis le log |
| `screws_tilt` | `corrections` ({vis: tours signés, CW+, CCW-}, base exclue), `max_deviation_mm`, `n_retries` (int) | sortie `SCREWS_TILT_CALCULATE` Klipper standard |

Signature commune : toute ligne `DRV_STATUS` ajoute `tmc_error` (ligne brute,
≤200 chars) dans `measures` et force `fail_reason: "tmc_error"`, quel que soit
le test.

`bed_mesh` et `e0_head` existent dans le catalogue pad mais sont **hors
séquence machine** (non exécutés, jamais envoyés) : pas de measures.

## 2. Liste `fail_reason` définitive par test (à figer ensemble)

Valeurs **réutilisées du banc YMS** : `tmc_error`, `timeout`,
`head_not_reached`, `sensor_mute`, `already_at_head`, `sensor_lost_feed`.
Valeurs **nouvelles machines** : `tap_not_converging`, `too_few_taps`,
`endstop_not_triggered`, `spread_too_wide`, `thermal_runaway`,
`thermal_timeout`, `visual_reject`, `no_yumi_config`, `mcu_uid_error`,
`not_homed`, `screws_tilt_aborted`, `unknown_fail`.

| Test id | fail_reason possibles |
|---------|-----------------------|
| `mcu_check` | `no_yumi_config` (aucune identité YUMI gravée), `mcu_uid_error`, `tmc_error`, `timeout`, `unknown_fail` |
| `home_x` / `home_y` | `endstop_not_triggered` (répétabilité non établie / aucun contact), `spread_too_wide` (butée non répétable), `tmc_error`, `timeout`, `unknown_fail` |
| `fan_*` | `visual_reject` (rejet opérateur), `timeout`, `tmc_error` |
| `heat_extruder` / `heat_bed` | `thermal_runaway` (ligne verify_heater Klipper), `thermal_timeout` (cible non atteinte dans le budget), `tmc_error`, `unknown_fail` |
| `cutter` | `head_not_reached` (budget 800 mm atteint), `sensor_mute`, `already_at_head`, `visual_reject` (coupe rejetée), `timeout`, `tmc_error` |
| `e1_head` | `head_not_reached`, `sensor_mute`, `sensor_lost_feed` (décrochage en cours de feed), `already_at_head`, `tmc_error`, `timeout`, `unknown_fail` |
| `z_tap_home` | `tap_not_converging` (« Pressure probe failed »), `not_homed`, `visual_reject`, `timeout`, `tmc_error` |
| `z_tap_calib` | `tap_not_converging` (meilleure fenêtre > tolérance), `too_few_taps` (< 3 taps), `tmc_error`, `timeout`, `unknown_fail` |
| `screws_tilt` | `screws_tilt_aborted` (« bed level exceeds configured limits » / « probe triggered prior »), `not_homed`, `tmc_error`, `timeout`, `unknown_fail` |

Sémantique des fallbacks : `timeout` = l'échec vient d'un **timeout engine
avéré** (macro morte) ; `unknown_fail` = échec sans signature reconnue hors
timeout ; les tests à validation opérateur (fan_*, cutter coupe, z_tap_home)
émettent `visual_reject` au lieu d'`unknown_fail`. Sur PASS, `fail_reason`
est toujours `null`.

## 3. Versions logicielles remontables

Bloc racine `software_versions`, additif et tolérant : chaque clé dont la
source est absente est **omise** ; le bloc entier est omis si rien n'est
disponible (rapport alors identique à l'existant).

| Clé | Format | Source pad |
|-----|--------|------------|
| `klipper_version` | str, ex. `v0.12.0-159-gabcd1234` | ligne `[mcu SmartPiOne] version: … (host …)` (fallback `[mcu rpi]`) déjà émise par `QC_MCU_CHECK` — la mcu_version du MCU Linux = version Klipper de `printer info` |
| `firmware_version` | {mcu: version}, ex. `{"mcu": "v0.12.0-159-gabcd1234"}` | lignes `[<mcu>] version:` du même log, MCU hôte **exclu** (process Klipper, pas un firmware flashé) |
| `image_version` | str (1re ligne du fichier) | `/etc/yumi-image-version` sinon `/etc/yumi-release` — **convention à confirmer côté image YumiOS** : la clé n'apparaît que si l'un de ces fichiers existe |
| `qc_cfg_version` | `sha256:<12 hex>` | empreinte courte de `~/printer_data/config/qc_printer_<MODEL>.cfg` (cfg modèle déployée) — fonctionne avec les cfgs déjà déployées, sans régénération |

Question en retour : la convention `image_version` ci-dessus convient-elle,
ou l'image YumiOS porte-t-elle un autre fichier release à privilégier ?

## 4. `retest` / `retest_reason`

Champs racine envoyés **uniquement** quand un QC est relancé sur une machine
déjà passée au QC sur CE pad (jamais `retest: false` — rapport inchangé
sinon) :

- `retest: true`
- `retest_reason` ∈ `previous_report_pass` / `previous_report_fail` /
  `previous_report_partial` (miroir du `overall_result` du rapport précédent
  le plus récent portant le même `machine_uid`), repli `previous_report` si
  le verdict précédent est illisible.

Heuristique locale documentée : un rapport précédent du même `machine_uid`
(UID STM32, identité fiable) existe dans `~/printer_data/config/qc_reports/`
(là où `save_report` écrit). Un rapport de la MÊME session n'est pas un
retest ; sans `machine_uid`, jamais de retest. Le serveur reste libre de
recouper avec son historique (dédup « 1 machine = 1 UID »).

## 5. Ids de tests machines inconnus du serveur ?

**Aucun.** La séquence machine n'émet que les 13 ids déjà reconnus :
`mcu_check`, `home_x`, `home_y`, `fan_motherboard`, `fan_part`, `fan_hotend`,
`heat_extruder`, `heat_bed`, `cutter`, `e1_head`, `z_tap_home`, `z_tap_calib`,
`screws_tilt`. Le catalogue pad contient aussi `bed_mesh` et `e0_head`, mais
ils sont hors séquence (non exécutés, jamais envoyés dans un rapport machine).

## 6. Sandbox

Le harnais `scripts/sandbox_machine_test.py` construit un rapport machine
complet via le vrai `QCEngine` et le poste avec `"sandbox": true` sur
`https://qc.yumi-lab.com/api/qc/report`. Boucle de validation E2E contre la
prod : lot L9 (ack + marqueur `"sandbox": true` de la réponse documentés au
Journal de PROGRESS.md).
