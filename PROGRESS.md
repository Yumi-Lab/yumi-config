# PROGRESS — Banc QC YMS (branche yms-dev)

Un lot = une itération = un commit vert. Cocher UNIQUEMENT après verify.sh
vert + contrôle réel du lot. Cycle : AUDIT → PLAN → CODE → TEST → IMPROVE → GATE.

## Lots

- [ ] **L1 — Socle de test.** Créer `verify.sh` (py_compile qc/*.py +
  `python3 generate_yms12_cfg.py` depuis qc/ + parse configparser de
  qc_printer_YMS12.cfg + `python3 -m unittest discover -s qc/tests`) et
  `qc/tests/__init__.py` + un premier test trivial vert (import qc_engine,
  QC_TESTS_YMS12 = 13 tests, macros TOOL=1..12). verify.sh vert.
- [ ] **L2 — Module pur `qc/qc_yms.py` (measures).** Y déplacer
  `_extract_measures` (fonction module `extract_measures(logs, passed)`),
  qc_wizard.py l'importe (avec fallback sys.path comme pour qc_engine).
  Tests : les 6 cas réels ci-dessous (section Références) reproduits tels
  quels + cas stress-perdu (`PERDU le suivi au segment` → sensor_lost_stress).
- [ ] **L3 — Module pur : rapport boîtier.** Déplacer la construction du
  rapport (`build_box_report(...)` avec paramètres explicites, plus de self)
  dans qc_yms.py ; qc_wizard._build_box_report devient un adaptateur qui
  collecte l'état (engine, ids, session) et appelle le module. Tests de
  conformité v1.1 : clés exactes, mapping e5_head→position 6→slot
  hyperdrive_uart:4, PASS et FAIL (fail_reason), durée.
- [ ] **L4 — Module pur : client allocation.** `allocate_yms_codes(url,
  token, count, model, timeout)` dans qc_yms.py ; wizard = adaptateur.
  Tests avec http.server local éphémère : 200 ok (count codes), yms_id
  unitaire, HTTP 500, JSON invalide, count mismatch, timeout court.
- [ ] **L5 — Module pur : étiquette TSPL.** `build_label_tspl(report)` →
  bytes CRLF (SIZE/GAP/PEEL/TEXT/QRCODE/PRINT) ; wizard n'écrit plus que
  le device. Tests : contenu, CRLF, QR = https://qc.yumi-lab.com/report/<id>,
  ascii-safe.
- [ ] **L6 — Slots hors service.** `load_disabled_positions(path)` dans
  qc_yms.py (JSON `{"disabled":[...]}`, absent/illisible → []). Intégration
  wizard : positions désactivées → tests marqués SKIPPED (détail « slot banc
  hors service »), allocation count réduit, mapping position→code qui saute
  les désactivées, aucun rapport/étiquette pour elles. Tests unitaires du
  mapping + du chargement. (UI non testable : garder la logique 100% module.)
- [ ] **L7 — Sélecteur PRO/LIGHT.** Dialogue KlipperScreen 2 boutons avant
  allocation (défaut LIGHT), propagé à allocation model + qc_model +
  device= + étiquette. Logique de propagation dans qc_yms.py (constantes
  MODELS = {"light": ("YMS-LIGHT", ...), "pro": ...}) testée ; l'UI reste
  mince.
- [ ] **L8 — Re-test unitaire.** Depuis l'écran résumé : toucher une ligne
  e<n>_head FAIL → confirmation → allocation count=1 (même modèle que la
  séquence) → exécution du seul test via engine (séquence à 1 test + mcu
  déjà connu) → rapport + étiquette standard. Logique de re-test dans
  qc_yms/engine testée (construction de la mini-séquence), UI mince.
- [ ] **L9 — Doc `qc/README-YMS.md`** (architecture, mapping, protocole,
  critères, fail_reason, pannes banc connues, déploiement, contrat serveur).
- [ ] **L10 — GATE banc réel.** Écrire `.gate-handoff` : checklist de
  validation sur pad 192.168.100.110 (séquence YMS12 écran complet avec
  allocation serveur live, étiquettes PASS, slots 2/11 désactivés via
  qc_bench_slots.json, re-test unitaire d'un FAIL). STOP — le superviseur
  fait le gate et merge yms-dev → main.

## Références (cas réels mesurés sur le banc, à reprendre dans les tests)

PASS (YMS-6) : logs
`QC E5_HEAD: motion sensor YMS-6 a change d'etat (mouvement detecte)` ·
`QC E5_HEAD: filament a la tete apres 625mm + motion sensor YMS-6 OK -> stress aller-retour` ·
`QC: YMS-6 decrochage encodeur E=370.5` ·
`QC E5_HEAD: stress 16/16 detected=True` ·
`QC E5_HEAD: stress OK — 16 segments ±100mm (10→100→10mm/s), suivi capteur permanent`
→ measures : feed_mm=625, head_reached=True, motion_first_detect=True,
dropouts_e=[370.5], stress 16/16, retract_mm=625, fail_reason=None.

FAIL capteur muet (YMS-11) :
`QC E10_HEAD: filament a la tete mais motion sensor YMS-11 n'a PAS change d'etat (cablage / capteur HS)`
→ fail_reason=sensor_mute, motion_first_detect=False.

FAIL sabotage feed (YMS-6) : décrochages E=370.5, E=384.8 puis
`... a CESSE de suivre pendant le feed (a 250mm)` → fail_reason=
sensor_lost_feed, feed_dropout=True, feed_mm=250, dropout_count=2.

FAIL TMC (YMS-2) :
`TMC 'extruder_stepper extruder1' reports error: DRV_STATUS: 00150050 s2vsa=1(ShortToSupply_A!) ola=1(OpenLoad_A!) cs_actual=21`
→ fail_reason=tmc_error.

FAIL 900mm : `QC E5_HEAD: filament pas a la tete apres 900mm (chemin bouche / moteur / capteur HS)`
→ fail_reason=head_not_reached, feed_mm=900.

FAIL sans signature (macro morte) → fail_reason=timeout.

Mapping positions : 1..12 → ["main:E0","main:E1","hyperdrive_uart:1..5",
"hyperdrive_usb:1..5"] ; YMS-n = e(n-1)_head = extruder(n-1) = TOOL=n.

## Journal
