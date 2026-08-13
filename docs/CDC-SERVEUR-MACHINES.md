# Cahier des charges — QC des imprimantes 3D (C235/C335/C435) côté pad
(Émis par le serveur qc.yumi-lab.com le 13/08/2026. RIEN NE PRESSE ET RIEN NE
CASSE : serveur 100 % tolérant au format actuel, aucun champ obligatoire,
migration pad par pad.)

## 1. Ce que le serveur exploite aujourd'hui
printer_id (clé + idempotence avec date) · machine_uid = identité machine,
dédup « 1 machine = 1 UID » · overall_result → compteur et taux · date →
ventilation par jour (chaîne opaque, jamais parsée en fuseau) · yumi_config →
ventilations device=/lot=/mains=/bedw=/ssr= · pad_mac (affiché « QC pad ») ·
date_end/duration_seconds · tests[] (id,name,result,timestamp,details,log),
ids connus traduits 13 langues. Archivés bruts : version, qc_model,
failed_tests, skipped_tests, type.
technician : ABANDONNÉ, ne plus l'envoyer.
ids reconnus : mcu_check, home_x, home_y, fan_motherboard, fan_part,
fan_hotend, heat_extruder, heat_bed, cutter, e1_head, z_tap_home, z_tap_calib,
screws_tilt (id inconnu passe quand même).

## 2. Invariants (le vrai contrat)
(a) machine_uid = UID STM32, jamais MAC, jamais vide sur rapport final.
(b) Pas de faux PASS : PASS seulement si TOUS les tests pass.
(c) Idempotence : retry = même (printer_id, date) ; re-test réparation =
nouvelle date = nouvelle ligne.
(d) Store-and-forward : .sent seulement sur 200 {"status":"ok"} ; vérifier
qc-upload.timer installé ET enabled sur TOUS les pads.
(e) Dates ISO locales naïves acceptées telles quelles (offset absent/Z/+08:00
tolérés) ; aucune conversion de fuseau côté serveur.
(f) yumi_config = chaîne firmware complète, device= vrai modèle.

## 3. Additif souhaité
3.1 measures structuré PAR TEST (demande principale) + fail_reason normé.
Exemple cible :
{"id": "z_tap_calib", "result": "pass",
 "measures": {"taps_mm": [486.1075, 486.1025], "spread_mm": 0.0,
              "tolerance_mm": 0.05, "n_taps": 15, "converged_n": 3},
 "details": "OK: 3 taps convergents…", "log": ["…"]}
Souhaité : mcu_check (mcu_uid, mcu_count, firmware/klipper_version) ·
home_x/home_y (retries, duration_s, sg_thrs, current_a) · fan_* (rpm sinon
visual_ack) · heat_* (target_c, reached_c, ramp_s, stable) · cutter (feed_mm,
cut_ok) · e1_head (clés du banc YMS) · z_tap_home (z_max_mm, tap_z_mm) ·
z_tap_calib (taps_mm[], spread_mm, tolerance_mm, n_taps, converged_n) ·
screws_tilt (corrections par vis, max_deviation_mm). fail_reason normé par
test (thermal_runaway, endstop_not_triggered, tap_not_converging, tmc_error,
timeout…) : proposer la liste exhaustive, on la fige ensemble.
Note : measures machines pas encore parsées serveur (archivées intégralement
dès réception) — envoyer d'abord, afficher ensuite.
3.2 Versions logicielles : firmware_version, klipper_version, image_version,
qc_cfg_version.
3.3 retest: true + retest_reason.

## 4. Sandbox
"sandbox": true dans le corps (rapport machine/YMS, et /api/qc/yms/allocate) :
validation et réponses identiques au réel, rien n'est écrit, réponse marquée
"sandbox": true. Boucler les tests dessus, y compris contre la prod.

## 5. À ne pas faire
technician · printer_id = MAC · PASS avec tests pending · identifiant machine
généré côté pad · gestion de fuseau attendue du serveur.

## 6. Ordre de bascule conseillé
1) Retirer technician, tester sandbox. 2) measures + fail_reason sur
z_tap_calib/heat_bed/home_x, sandbox puis QC pilote, vérif /report/<UID>
ensemble. 3) Étendre tous tests + versions. 4) Généraliser. 5) Vérifier
qc-upload.timer sur chaque pad touché.

## 7. Questions du serveur (à répondre dans docs/REPONSES-SERVEUR.md)
Liste exhaustive des mesures déjà calculées · liste fail_reason par test ·
versions logicielles remontables · ids de tests machines inconnus du serveur ?
