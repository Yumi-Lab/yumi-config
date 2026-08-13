# AUDIT-MESURES — ce que les rapports QC machine contiennent DÉJÀ

Date : 13/08/2026. Sources auditées : `qc/qc_engine.py` (capture `_test_log`,
`generate_report`), `qc/qc_macros.cfg` (identique aux macros inline des
`qc_printer_C235/C335/C435.cfg` générées par `qc/generate_qc_cfg.py`),
`klipper/klippy/extras/yumi_z_tap.py`, `yumi_sensorless_homing.py`,
`mcu_uid.py`, et le style measures-first de référence `qc/qc_yms.py`
(`extract_measures`).

Mécanisme de capture : `QCEngine.process_gcode_response` range dans
`self._test_log[test_id]` toute ligne `// ...` (respond_info) ou `!! ...`
(respond_error) reçue pendant le test courant (max 40 lignes, dédupliquées).
Ce `log` part déjà dans chaque entrée `tests[]` du rapport. Les mesures
structurées `measures{}` seront donc extraites **depuis ces logs existants**
(module pur, style `qc_yms.extract_measures`) — aucun changement de macro
n'est requis pour les mesures listées « OK ». Les mesures listées
« À instrumenter » demandent un ajout additif de log (macro ou engine).

## mcu_check (auto)

Log réel (QC_MCU_CHECK + QUERY_MCU_UID) :

```
[mcu] version: v0.12.0-159-gabcd1234
[mcu] board=CR-FDM-v2.5.s1 device=YUMI-C235 lot=2026-08 ... uid=ABC123
[mcu] comment: ...
MCU_UID=2D0046000D51353234323830
FAIL: "QC: aucune identite YUMI gravee sur les MCU (firmware non grave...)"
```

Extractible maintenant : `mcu_uid` (MCU_UID=), `mcu_count` (lignes `[<name>]
version:`), `firmware_versions` {mcu: version}, `yumi_config` (déjà en racine),
`yumi_config_found` (bool). fail_reason : `no_yumi_config` (ligne "aucune
identite YUMI"), `mcu_uid_error` (ligne `MCU_UID_ERROR:`), sinon `timeout`.

## fan_motherboard / fan_part / fan_hotend (visuels)

Log : aucun (macro = START + VISUAL, validation opérateur). Extractible :
`visual_ack` (bool, du résultat visuel). RPM non disponible (pas de tachymètre
câblé en mode QC). fail_reason : `visual_reject`.

## heat_extruder (auto)

Log : aucun (M104 + TEMPERATURE_WAIT, puis PASS direct). Extractible côté
engine : `target_c=220` (constante macro), `ramp_s` = durée mesurée du test
(engine connaît start/timestamp). `reached_c` / `stable` non observables sans
log. **À instrumenter** (additif) : un `action_respond_info` avec la temp
atteinte, ex. `HEAT_OK extruder t=%.1f apres %ds`. fail_reason :
`thermal_timeout` (timeout engine), `thermal_runaway` (ligne `!! ` Klipper
verify_heater si capturée).

## heat_bed (auto)

Idem heat_extruder : `target_c=60` (params.TEMP), `ramp_s` mesurable par
l'engine. `reached_c` à instrumenter (même log HEAT_OK). fail_reason :
`thermal_timeout`, `thermal_runaway`.

## cutter (visuel, feed YMS-1 + extrusion + coupe)

Log réel (boucle `_QC_HEAD_FEED` mode cutter) :

```
QC CUTTER: motion sensor YMS-1 a change d'etat (mouvement detecte)
QC CUTTER: extrude 60 + refroidit poop 5s + coupe + retracte 120
FAIL possibles: "filament pas a la tete apres 800mm (chemin bouche...)"
               "filament a la tete mais motion sensor YMS-1 n'a PAS change d'etat"
               "filament deja a la tete avant feed" (mode feed seul)
```

Extractible : `motion_first_detect` (bool), `feed_mm` = distance au capteur
tête (en mode cutter la ligne "apres Nmm" n'est PAS émise — le `pushed`
interne n'est pas loggé ; **à instrumenter** si voulu : ajouter la distance
dans la ligne « extrude 60 »), `cut_ok` = visual_ack opérateur.
fail_reason : `visual_reject`, `head_not_reached`, `sensor_mute`,
`already_at_head`, `timeout`.

## e1_head (auto, feed YMS-2) — e0_head hors séquence

Log réel (boucle `_QC_HEAD_FEED` mode feed, mêmes lignes que le banc YMS) :

```
QC E1_HEAD: motion sensor YMS-2 a change d'etat (mouvement detecte)
QC E1_HEAD: filament a la tete apres 425mm + motion sensor YMS-2 OK
FAIL: "filament pas a la tete apres 800mm (chemin bouche / moteur / capteur HS)"
      "filament a la tete mais motion sensor YMS-2 n'a PAS change d'etat (cablage / capteur HS)"
      "filament deja a la tete avant feed (pas retire ?)"
```

Extractible — **mêmes clés que le banc YMS** (`qc_yms.extract_measures` est
réutilisable tel quel sur ce test) : `feed_mm`, `feed_budget_mm` (800 ici,
900 banc), `head_reached`, `motion_first_detect`, fail_reason
`head_not_reached` / `sensor_mute` / `already_at_head` / `timeout`.
(Pas de stress segments ni dropouts sur la machine : séquence feed simple.)

## home_x / home_y (auto, sensorless 2 phases)

G28 → homing_override → `YUMI_SENSORLESS_HOME AXIS=X|Y`. Log réel :

```
YUMI_SENSORLESS_HOME X: home base... (sgthrs=63)
tap 1: pos=0.0000 gap=4.5210 (1/3)
tap 3: pos=0.0000 gap=4.5130 fenetre=0.0080/0.0500
tap 1 rejete: <raison>
YUMI_SENSORLESS_HOME X OK: 3 taps valides (1 rejetes) -> moyenne=0.0000
  spread=0.0080mm (tol=0.0500). Zero pose en butee=0.0000
FAIL (erreur franche): "repetabilite NON etablie (1 taps valides / 3 requis,
  2 rejetes) -> referentiel non fiable, home avorte"
  "spread=0.2100mm sur 3 taps (tol=0.0500) -> butee non repetable, home avorte"
```

Extractible : `sg_thrs` (ligne home base), `taps_valides`, `taps_rejetes`,
`spread_mm`, `tolerance_mm`, `zero_pos_mm` (butee), `duration_s` (engine).
fail_reason : `endstop_not_triggered` (« repetabilite NON etablie »),
`spread_too_wide` (« butee non repetable »), `tmc_error` (ligne DRV_STATUS),
`timeout`.

## z_tap_home (visuel, home complet + montée Zmax)

G28 → homing_override Z → `YUMI_Z_TAP SAVE=0` (1er tap = home Z). Log réel :

```
VALIDATED: trigger_z=486.1075 -> Z=0 pose 0.5500 au-dessus du tap
  (compression=0.5500 - z_offset=0.0000)
```

Extractible : `tap_z_mm` (trigger_z du 1er tap), `visual_ack`. `z_max_mm` :
connu de la macro (stepper_z.position_max) mais NON loggé — **à instrumenter**
(un `action_respond_info("ZMAX=%.1f")` additif). fail_reason :
`visual_reject`, `tap_not_converging` (erreur YUMI_Z_TAP non stabilisée),
`timeout`.

## z_tap_calib (auto, 15 taps)

Le module klippy logge par tap `VALIDATED: trigger_z=X.XXXX` (capturé dans le
log) ET l'engine calcule déjà le verdict dans `details` :

```
OK: 3 taps convergents spread=0.0000mm (tol=0.0500) sur 15 taps
  | taps=486.1075, 486.1025, ...
FAIL: "aucun groupe de 3 taps <= tol: meilleur=0.0700mm > 0.0500 sur 15 taps
  | taps=..."
FAIL: "Z tap calib: 2 tap(s), il en faut au moins 3"
```

Extractible (re-parse du details ou re-calcul depuis `taps=`):
`taps_mm[]`, `spread_mm` (meilleure fenêtre de 3, triée — outliers écartés),
`tolerance_mm=0.05`, `n_taps`, `converged_n=3`. fail_reason :
`tap_not_converging`, `too_few_taps`, `timeout`.

## screws_tilt (auto)

`SCREWS_TILT_CALCULATE` (Klipper standard, 4 vis CCW-M3 sur C235) logge :

```
front left screw (base) : x=49.5, y=175.5, z=0.00000
rear right screw : x=224.0, y=2.0, z=0.04123 : adjust CW 0:19
...
```

(plus lignes "Retrying..." / "Probed points range: ..." selon retries).
Extractible : `corrections` {nom_vis: tours} (parse "adjust CW/CCW H:MM" →
tours décimaux), `max_deviation_mm` (range des z sondés si loggé, sinon
calculé des corrections × pas 0.5mm), `n_retries`. fail_reason :
`screws_tilt_aborted` (erreur sondage), `not_homed` (« machine non homee »),
`timeout`.

## bed_mesh — HORS SÉQUENCE machine (retiré de `_QC_ORDER`, cf. qc_engine.py)
Pas de measures à produire : non exécuté, absent du rapport.

---

## fail_reason proposés (norme pad, à figer avec le serveur)

Réutilisés du banc YMS (`qc_yms.py`) quand identiques :
`head_not_reached`, `sensor_mute`, `already_at_head`, `tmc_error`, `timeout`.
Nouveaux machines : `tap_not_converging`, `too_few_taps`, `endstop_not_triggered`,
`spread_too_wide`, `thermal_timeout`, `thermal_runaway`, `visual_reject`,
`no_yumi_config`, `mcu_uid_error`, `not_homed`, `screws_tilt_aborted`.
Fallback engine : test FAIL sans signature reconnue → `timeout` si timeout,
sinon `unknown_fail` (mieux que la chaîne libre "Automated check failed").

## Synthèse extraction

| test | measures extractibles des logs ACTUELS | à instrumenter (additif) |
|---|---|---|
| mcu_check | mcu_uid, mcu_count, firmware_versions, yumi_config_found | — |
| fan_* | visual_ack | (rpm : pas de tachymètre) |
| heat_extruder | target_c, ramp_s (engine) | reached_c, stable |
| heat_bed | target_c, ramp_s (engine) | reached_c, stable |
| cutter | motion_first_detect, cut_ok (visuel) | feed_mm en mode cutter |
| e1_head | feed_mm, feed_budget_mm, head_reached, motion_first_detect | — |
| home_x/y | sg_thrs, taps valides/rejetés, spread_mm, tolerance_mm, duration_s (engine) | — |
| z_tap_home | tap_z_mm, visual_ack | z_max_mm |
| z_tap_calib | taps_mm[], spread_mm, tolerance_mm, n_taps, converged_n | — |
| screws_tilt | corrections par vis, max_deviation_mm, n_retries | — |
