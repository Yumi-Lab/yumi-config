# PROGRESS — QC machines measures-first (branche qc-machines-dev)

Un lot = une iteration = un commit vert (verify.sh PASS). Cycle :
AUDIT → PLAN → CODE → TEST → IMPROVE → GATE. Gate avant de cocher.

## Lots

- [x] **L1 — Audit des mesures existantes.** Relire qc_engine.py + qc_macros.cfg
  (+ qc_printer_C235.cfg macros inline) et produire docs/AUDIT-MESURES.md :
  pour CHAQUE test machine, ce que les logs/details contiennent deja (format
  exact des lignes, ex. z_tap_calib "VALIDATED: trigger_z=..." / spread, feed
  "filament a la tete apres NNNmm", screws_tilt corrections par vis...), et la
  mesure structuree qu'on peut en tirer. verify.sh reste vert.
- [ ] **L2 — technician retire + harnais sandbox.** Supprimer technician du
  rapport (engine, save, upload — champ absent, pas vide) ; script
  scripts/sandbox_machine_test.py : poste un rapport machine REALISTE avec
  "sandbox": true sur https://qc.yumi-lab.com/api/qc/report (token lu du pad ou
  d'un fichier local .env) et verifie l'ack. Tests unitaires du rapport sans
  technician.
- [ ] **L3 — Module pur qc_machine_measures.py (3 tests pilotes).** Extraction
  measures + fail_reason pour z_tap_calib (taps_mm[], spread_mm, tolerance_mm,
  n_taps, converged_n), heat_bed (target_c, reached_c, ramp_s, stable) et
  home_x/home_y (retries, duration_s si mesurable) depuis les logs captures par
  l'engine. fail_reason normes proposes : tap_not_converging, thermal_timeout,
  endstop_not_triggered, tmc_error, timeout, visual_reject, sensor_mute,
  head_not_reached (reutiliser ceux du banc YMS quand identiques). Fixtures =
  logs REELS de docs/AUDIT-MESURES.md. Tests exhaustifs.
- [ ] **L4 — Engine : bloc measures par entree tests[].** generate_report
  attache measures + fail_reason quand l'extracteur du test existe (absent
  sinon — additif). details/log inchanges. Tests de conformite (cles exactes,
  rapport actuel intact champ par champ hors ajouts).
- [ ] **L5 — Extension a tous les tests.** mcu_check (mcu_uid, mcu_count,
  firmware versions par mcu), fan_* (visual_ack), heat_extruder, cutter
  (feed_mm, cut_ok visuel), e1_head (memes cles que le banc : reutiliser
  qc/qc_yms.extract_measures), z_tap_home (z_max_mm, tap_z_mm), screws_tilt
  (corrections par vis en tours + max_deviation). Tests par extracteur.
- [ ] **L6 — Versions logicielles.** Bloc racine software_versions :
  klipper_version (printer info), firmware_version par mcu (mcu_version),
  image_version (fichier release YumiOS si present), qc_cfg_version (marqueur
  _QC_MODE ou hash de la cfg). Additif, tolerant a l'absence. Tests.
- [ ] **L7 — retest.** Champs retest: true + retest_reason envoyes quand un QC
  est relance sur une machine deja passee au QC sur CE pad (heuristique locale
  documentee : rapport precedent du meme machine_uid dans qc_reports/) ; jamais
  envoyes sinon. Tests.
- [ ] **L8 — docs/REPONSES-SERVEUR.md.** Reponses completes aux questions du
  serveur : inventaire des mesures (issu de L1/L3/L5), liste fail_reason
  definitive par test, versions remontables, ids non connus du serveur
  eventuels. + section README qc/ mise a jour.
- [ ] **L9 — Validation sandbox E2E prod.** scripts/sandbox_machine_test.py
  etendu : rapport machine COMPLET (13 tests, measures partout, versions,
  retest) poste en sandbox sur la prod ; verifier ack + "sandbox": true ;
  documenter la sortie dans le Journal (PROOF).
- [ ] **L10 — GATE pad pilote.** Ecrire .gate-handoff : checklist pour un QC
  machine reel sur pad pilote (rapport recu, page /report/<UID> verifiee avec
  le serveur, aucun champ regresse) — STOP, le superviseur fait le gate et
  merge.

## References
- Cahier des charges serveur complet : docs/CDC-SERVEUR-MACHINES.md (fourni).
- Style measures-first de reference : qc/qc_yms.py + ses tests qc/tests/.
- Exemple details reel z_tap_calib : "OK: 3 taps convergents spread=0.0000mm
  (tol=0.0500) sur 15 taps | taps=19.4450, 486.1075, ...".

## Journal

- **L1 (13/08) — FAIT.** `docs/AUDIT-MESURES.md` produit : pour chacun des 13
  tests machine, format exact des logs déjà capturés (`_test_log`, lignes
  `//`/`!!`), mesures extractibles sans toucher aux macros, et mesures qui
  demandent une instrumentation additive (reached_c heat_*, z_max_mm, feed_mm
  cutter). `qc_yms.extract_measures` réutilisable tel quel pour e1_head.
  Liste fail_reason proposée (5 réutilisés du banc + 10 nouveaux + fallbacks).
  PROOF:
  - cmd: `./verify.sh 2>&1 | tail -15`
  - sortie (dernières lignes): `Ran 35 tests in 5.579s` / `OK` /
    `verify.sh: PASS` (py_compile qc/*.py OK, generate_yms12_cfg OK,
    configparser YMS12 OK)
  - critère numérique: 35/35 tests unittest verts, 4/4 étapes verify.sh OK.
  - attribution: python3 local macOS, repo commit de base 9b1f8b2,
    seul fichier ajouté = docs/AUDIT-MESURES.md (doc, aucun code touché).
    VARIED: docs/AUDIT-MESURES.md (nouveau) / HELD FIXED: qc/*.py, macros,
    klippy extras, tests.
  - WHAT THIS DOES NOT SAY: ne prouve rien sur la prod ni sur les pads —
    audit statique des formats de logs, pas une extraction implémentée.
  Prochain lot : L2 (technician retiré + harnais sandbox).
