# GOAL — Banc QC YMS : durcissement logiciel côté pad (repo yumi-config, branche yms-dev)

## Contexte
Le banc QC usine teste 12 boîtiers YMS sur une C235 + 2 HyperDrive 3P2L.
Tout le code vit dans `qc/` : cfg banc générée (`generate_yms12_cfg.py` →
`qc_printer_YMS12.cfg`), engine (`qc_engine.py`), panel KlipperScreen
(`qc_wizard.py`, symlinké sur les pads). Le contrat serveur est
`docs/FORMAT-YMS.md` du repo `Yumi-Lab/yumi-qc-counter` (v1.1) : allocation
groupée `POST /api/qc/yms/allocate` `{"model","count"}` → `{"status":"ok",
"yms_ids":[...]}`, puis UN rapport par boîtier sur `POST /api/qc/report`
(printer_id = code alloué, `machine_uid:""`, `device=YMS-LIGHT|YMS-PRO`,
`bench_position/slot/session/total`, `measures{}` source primaire avec
`fail_reason` normé : sensor_mute | sensor_lost_feed | sensor_lost_stress |
head_not_reached | tmc_error | already_at_head | timeout). Étiquette TSPL
sur PASS uniquement (POS80L, /dev/usb/lp0). Le flux marche déjà de bout en
bout ; ta mission est de le RENDRE TESTÉ, MODULAIRE et COMPLET.

## Definition of Done (tout coché dans PROGRESS.md + verify.sh vert)
1. **Logique pure extraite** de `qc_wizard.py` vers un module `qc/qc_yms.py`
   importable SANS gi/GTK : extraction des measures, construction du rapport
   par boîtier, client d'allocation, génération du TSPL de l'étiquette,
   mapping positions/slots. `qc_wizard.py` devient une couche UI mince qui
   importe ce module (compatibilité : le symlink KlipperScreen ne charge que
   qc_wizard.py + qc_engine.py depuis `qc/`, le nouveau module doit être
   importable par chemin relatif comme le fallback existant).
2. **Harnais de tests** `qc/tests/` en unittest STDLIB (pas de pytest, pas de
   dépendance) couvrant : extract_measures (les 6 cas réels du banc déjà
   validés — les reprendre de PROGRESS.md), build du rapport boîtier
   (structure conforme au contrat v1.1), client d'allocation (mock
   http.server local : ok, HTTP 500, réponse invalide, count≠12), engine
   YMS12 (séquence 13 tests, signaux QC:E*_HEAD, PARTIAL/PASS/FAIL),
   génération TSPL (contenu, CRLF, QR = report_url).
3. **Slots hors service** : fichier `~/printer_data/config/qc_bench_slots.json`
   (liste de positions 1..12 désactivées, ex. `{"disabled": [2, 11]}`) lu au
   démarrage de séquence → les positions désactivées sont SKIPPED (résultat
   `skipped`, détail "slot banc hors service"), AUCUN code alloué pour elles
   (l'allocation demande count = 12 - len(disabled) et le mapping
   position→code saute les positions désactivées), aucun rapport envoyé.
   Fichier absent = 12 positions actives (comportement actuel intact).
4. **Sélecteur PRO/LIGHT** au lancement d'une séquence YMS : dialogue 2 gros
   boutons (办 LIGHT / PRO) avant l'allocation → `model` de l'allocation et
   `qc_model`/`device=` des rapports suivent (YMS-PRO ↔ codes YMSP-).
   Session entière du même modèle (pas de mixte en v1). Défaut = LIGHT.
5. **Re-test unitaire** : sur l'écran résumé, toucher une ligne YMS en FAIL
   propose de re-tester CE boîtier seul : allocation unitaire (count=1) d'un
   NOUVEAU code, exécution du seul test e<n>_head, rapport + étiquette comme
   en séquence. (Le code précédent reste brûlé, conforme contrat.)
6. **Doc** `qc/README-YMS.md` : architecture du banc (mapping YMS↔extruder↔
   slots, macros, phases feed/stress, critères PASS/FAIL, fail_reason),
   déploiement (install_qc_station.sh, sync_qc_cfgs.sh), lien contrat
   FORMAT-YMS.md, dépannage (pannes banc vécues : phase A TMC, entrée
   capteur morte, thermistances).
7. `verify.sh` vert : py_compile de tous les qc/*.py + régénération de la cfg
   (`generate_yms12_cfg.py` → parse configparser OK) + `python3 -m unittest
   discover qc/tests` tout vert.

## Règles ABSOLUES
- **Ne JAMAIS toucher** au protocole C-series : liste `QC_TESTS` de
  qc_engine.py, `qc_macros.cfg`, `qc_printer_C235/C335/C435.cfg`, macros du
  générateur (generate_yms12_cfg.py ne se modifie que pour des besoins YMS).
  Les pads usine machines en dépendent.
- **Ne toucher à RIEN hors de `qc/`** dans ce repo (il contient des configs
  de prod d'autres sous-systèmes).
- **Python stdlib uniquement** (les pads n'ont pas de pip fiable). Cible
  Python 3.11+ (Debian Trixie).
- **Compatibilité KlipperScreen** : qc_wizard.py doit rester importable dans
  KlipperScreen (classe Panel(ScreenPanel), mêmes callbacks). Aucun import
  gi dans qc_yms.py et les tests.
- Le contrat v1.1 (endpoints, champs, fail_reason normés) est FIGÉ : ne pas
  le modifier, ne pas inventer de champ obligatoire côté serveur.
- Commits : messages style repo (`qc: ...`), AUCUNE mention d'outil IA.
  Travailler sur la branche `yms-dev` UNIQUEMENT — ne jamais pousser ni
  merger vers main (le merge est fait par le superviseur après gate banc).
  `git push origin yms-dev` autorisé.
- Toute validation nécessitant le PAD/BANC réel (KlipperScreen visuel,
  imprimante POS80L, moteurs) ou le serveur qc.yumi-lab.com réel → NE PAS
  simuler un PASS : écrire `.gate-handoff` avec la checklist et STOP.

## Quand TOUT est coché ET verify.sh vert → créer `.done`
