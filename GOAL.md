# GOAL — QC machines C-series : measures-first (cahier des charges serveur du 13/08)

## But
Migrer le QC des imprimantes C235/C335/C435 (panel pad : qc/qc_engine.py,
qc/qc_wizard.py, qc/qc_macros.cfg + cfg generees par qc/generate_qc_cfg.py)
vers le contrat additif demande par le serveur qc.yumi-lab.com :
1. NE PLUS envoyer `technician` (abandonne cote serveur).
2. Bloc `measures` STRUCTURE dans chaque entree de tests[] du rapport machine
   (le pad calcule, details/log restent en repli humain) + `fail_reason` norme
   par test. Reference : docs/FORMAT-YMS.md du repo yumi-qc-counter (les YMS
   sont deja measures-first, module qc/qc_yms.py = exemple du style attendu).
3. Versions logicielles au rapport : firmware_version (mcu), klipper_version,
   image_version, qc_cfg_version.
4. `retest: true` + `retest_reason` quand un QC est relance sur une machine.
5. Scripts de test SANDBOX (`"sandbox": true` accepte par le serveur PROD sur
   /api/qc/report : valide sans rien ecrire) — boucler les tests dessus.
6. docs/REPONSES-SERVEUR.md : reponses aux questions du serveur (liste
   exhaustive des mesures deja calculees, liste fail_reason par test, versions
   remontables, ids de tests non connus du serveur).

## Invariants ABSOLUS (contrat serveur, ne jamais casser)
- TOUT est ADDITIF : un rapport actuel reste valide ; aucun champ nouveau
  obligatoire ; le format existant (printer_id, machine_uid=UID STM32 jamais
  vide/jamais MAC, overall_result PASS seulement si TOUS pass, date naive
  locale, yumi_config complet, pad_mac, tests[] id/name/result/timestamp/
  details/log) est INTOUCHABLE.
- Idempotence (printer_id, date) preservee ; store-and-forward .sent inchange.
- Ne PAS toucher au flux YMS (qc_yms.py, chemins YMS du wizard) sauf partage
  de helpers purs. Ne pas casser les 35+ tests existants.
- ids de tests machines : mcu_check home_x home_y fan_motherboard fan_part
  fan_hotend heat_extruder heat_bed cutter e1_head z_tap_home z_tap_calib
  screws_tilt (id inconnu tolere serveur mais rester sur ceux-la).
- Pas d'identifiant machine genere cote pad. Pas de gestion de fuseau attendue
  du serveur.
- Logique pure = module sans GTK (testable) ; qc_wizard/qc_engine = adaptateurs.
- AUCUNE mention d'outil IA dans les commits. Pas de deploy prod.

## Definition of Done
Tous les lots de PROGRESS.md coches, ./verify.sh vert (py_compile + generateurs
+ TOUS les tests unittest), reponses serveur ecrites, validation sandbox contre
la prod OK (script rejouable), gate-handoff final pour le QC pilote sur pad
reel — PUIS creer .done.
