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
- [x] **L2 — technician retire + harnais sandbox.** Supprimer technician du
  rapport (engine, save, upload — champ absent, pas vide) ; script
  scripts/sandbox_machine_test.py : poste un rapport machine REALISTE avec
  "sandbox": true sur https://qc.yumi-lab.com/api/qc/report (token lu du pad ou
  d'un fichier local .env) et verifie l'ack. Tests unitaires du rapport sans
  technician.
- [x] **L3 — Module pur qc_machine_measures.py (3 tests pilotes).** Extraction
  measures + fail_reason pour z_tap_calib (taps_mm[], spread_mm, tolerance_mm,
  n_taps, converged_n), heat_bed (target_c, reached_c, ramp_s, stable) et
  home_x/home_y (retries, duration_s si mesurable) depuis les logs captures par
  l'engine. fail_reason normes proposes : tap_not_converging, thermal_timeout,
  endstop_not_triggered, tmc_error, timeout, visual_reject, sensor_mute,
  head_not_reached (reutiliser ceux du banc YMS quand identiques). Fixtures =
  logs REELS de docs/AUDIT-MESURES.md. Tests exhaustifs.
- [x] **L4 — Engine : bloc measures par entree tests[].** generate_report
  attache measures + fail_reason quand l'extracteur du test existe (absent
  sinon — additif). details/log inchanges. Tests de conformite (cles exactes,
  rapport actuel intact champ par champ hors ajouts).
- [x] **L5 — Extension a tous les tests.** mcu_check (mcu_uid, mcu_count,
  firmware versions par mcu), fan_* (visual_ack), heat_extruder, cutter
  (feed_mm, cut_ok visuel), e1_head (memes cles que le banc : reutiliser
  qc/qc_yms.extract_measures), z_tap_home (z_max_mm, tap_z_mm), screws_tilt
  (corrections par vis en tours + max_deviation). Tests par extracteur.
- [x] **L6 — Versions logicielles.** Bloc racine software_versions :
  klipper_version (printer info), firmware_version par mcu (mcu_version),
  image_version (fichier release YumiOS si present), qc_cfg_version (marqueur
  _QC_MODE ou hash de la cfg). Additif, tolerant a l'absence. Tests.
- [x] **L7 — retest.** Champs retest: true + retest_reason envoyes quand un QC
  est relance sur une machine deja passee au QC sur CE pad (heuristique locale
  documentee : rapport precedent du meme machine_uid dans qc_reports/) ; jamais
  envoyes sinon. Tests.
- [x] **L8 — docs/REPONSES-SERVEUR.md.** Reponses completes aux questions du
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

- **L2 (13/08) — FAIT.** `technician` retiré du rapport machine :
  `qc_engine.generate_report()` ne l'émet plus (champ ABSENT, pas vide) et le
  paramètre `start(technician=...)` inutilisé est supprimé (aucun appelant ne
  le passait ; l'attribut `engine.technician` reste pour le flux YMS, contrat
  v1.x figé, wizard:1054). Harnais `scripts/sandbox_machine_test.py` : construit
  un rapport machine réaliste via le VRAI QCEngine (session C235 simulée,
  13 tests PASS, YUMI_CONFIG + MCU_UID), ajoute `"sandbox": true`, POST sur
  /api/qc/report (token : env QC_TOKEN > qc_token du pad > .env racine,
  déjà git-ignoré), vérifie l'ack HTTP 200. Aucun token sur cette machine ->
  le POST réel prod est le lot L9 ; l'ack est validé ici contre un mock local
  (même chemin de code). Tests : +TestMachineReport (technician absent, clés
  racine intactes, identité UID, forme tests[]) + test_sandbox_machine.py
  (POST mock 200 + payload sans technician, HTTPError 422 → code+corps,
  précédence token env).
  PROOF:
  - cmd: `./verify.sh 2>&1 | tail -8`
  - sortie (dernières lignes): `test_post_ack_200_and_payload ... ok` /
    `Ran 43 tests in 7.555s` / `OK` / `verify.sh: PASS`
  - critère numérique: 43/43 tests verts (35 avant L2, +8 nouveaux), 4/4
    étapes verify.sh OK.
  - attribution: python3 local macOS, branche qc-machines-dev @ f91e962+.
    VARIED: qc/qc_engine.py (rapport sans technician), qc/tests/
    test_qc_engine.py (+TestMachineReport), qc/tests/test_sandbox_machine.py
    (nouveau), scripts/sandbox_machine_test.py (nouveau) / HELD FIXED:
    qc_yms.py, qc_wizard.py, macros, klippy extras, verify.sh.
  - WHAT THIS DOES NOT SAY: ne prouve PAS que la prod accepte "sandbox": true
    — validé contre un mock local seulement (aucun token ici) ; l'E2E prod est
    le lot L9. Ne prouve rien sur un pad réel.
  Prochain lot : L3 (module pur qc_machine_measures.py, 3 tests pilotes).

- **L2 — FIX REVIEW (13/08).** Blocker du verdict CHANGES_REQUESTED (head
  d39c25e) : test flaky `test_http_error_returns_code_and_body` (3 échecs/5
  runs isolés, échec systématique en run complet). Root cause confirmée :
  `Boom.do_POST` envoyait la 422 SANS lire le corps du POST -> fermeture
  HTTP/1.0 avec body en buffer -> RST TCP -> `e.read()` levait parfois
  ConnectionResetError(54). Fix : première ligne de `Boom.do_POST` =
  `self.rfile.read(int(self.headers.get("Content-Length", 0)))` (miroir de
  `AckHandler.do_POST`), commentaire justifiant. AUCUN code produit touché
  (harnais de test uniquement).
  PROOF:
  - cmd1: `for i in 1 2 3 4 5; do python3 -m unittest qc.tests.test_sandbox_machine; done`
    -> 5 runs consecutifs `Ran 3 tests in ~2.0s` / `OK` (5/5).
  - cmd2: `for i in 1 2 3 4 5; do ./verify.sh; done` -> 5 runs consecutifs
    `Ran 43 tests in ~7.0-7.6s` / `OK` / `verify.sh: PASS` (5/5).
  - critère numérique: 43/43 tests verts sur 5 runs complets consécutifs, 0
    échec (avant : 1 échec systématique par run complet).
  - attribution: python3 local macOS, même HEAD d39c25e + ce fix seul.
    VARIED: qc/tests/test_sandbox_machine.py (Boom.do_POST lit le body) /
    HELD FIXED: qc/*.py, scripts/sandbox_machine_test.py, verify.sh, macros.
  - WHAT THIS DOES NOT SAY: 5 runs ne garantissent pas l'absence absolue de
    flake ; le mécanisme RST est toutefois supprimé à la source (body toujours
    consommé avant réponse), pas masqué par un retry. Ne prouve rien sur la
    prod ni sur un pad réel.
  Prochain lot : L3 (module pur qc_machine_measures.py, 3 tests pilotes).

- **L3 (13/08) — FAIT.** Nouveau module pur `qc/qc_machine_measures.py`
  (aucun import GTK, style `qc_yms.extract_measures`) :
  `extract_measures(test_id, logs, passed, details, duration_s, timed_out)`
  → dict measures (fail_reason inclus, style YMS) ou `None` si le test n'a
  pas d'extracteur (additif : rapport inchangé pour ce test). Extracteurs :
  - `z_tap_calib` : relit le verdict déjà calculé par l'engine dans `details`
    (taps_mm[], spread_mm, tolerance_mm, n_taps, converged_n) ; repli =
    re-calcul fenêtré depuis les `VALIDATED: trigger_z=` du log
    (`_best_window_spread`, même algorithme cluster trié que qc_engine) ;
    fail_reason `tap_not_converging` / `too_few_taps`.
  - `home_x`/`home_y` : parse des lignes réelles yumi_sensorless_homing.py
    (sg_thrs, taps valides/rejetés, spread_mm, tolerance_mm, zero_pos_mm,
    duration_s engine) ; fail_reason `endstop_not_triggered` (répétabilité
    NON établie / aucun contact), `spread_too_wide`, `tmc_error`.
  - `heat_bed` : target_c=60 (défaut macro QC_HEAT_BED), ramp_s=durée engine,
    reached_c/stable=None tant que l'instrumentation `HEAT_OK` n'existe pas
    (parse forward-compatible déjà en place) ; fail_reason `thermal_runaway`
    (ligne verify_heater) / `thermal_timeout` (défaut).
  - Commun : signature DRV_STATUS → `tmc_error` ; FAIL sans signature →
    `timeout` (ou `unknown_fail` si l'engine sait que ce n'est PAS un
    timeout). Constantes window/tol Z NON importées de qc_engine (import
    circulaire à venir en L4) : relues depuis `details`, défauts documentés.
  Tests : `qc/tests/test_qc_machine_measures.py`, 22 tests, fixtures = logs
  RÉELS de docs/AUDIT-MESURES.md (verdicts engine, lignes klippy verbatim).
  PROOF:
  - cmd: `./verify.sh 2>&1 | tail -8`
  - sortie (dernières lignes): `Ran 65 tests in 7.578s` / `OK` /
    `verify.sh: PASS`
  - critère numérique: 65/65 tests unittest verts (43 avant L3, +22
    nouveaux), 4/4 étapes verify.sh OK.
  - attribution: python3 local macOS, branche qc-machines-dev @ 6e1a225+.
    VARIED: qc/qc_machine_measures.py (nouveau),
    qc/tests/test_qc_machine_measures.py (nouveau) / HELD FIXED:
    qc_engine.py, qc_yms.py, qc_wizard.py, macros, klippy extras, verify.sh.
  - WHAT THIS DOES NOT SAY: extraction pure sur logs simulés en fixtures —
    ne prouve PAS que l'engine attache measures au rapport (lot L4) ni que
    la prod les accepte (lot L9). Ne prouve rien sur un pad réel.
  Prochain lot : L4 (engine : bloc measures par entrée tests[]).

- **L4 (13/08) — FAIT.** `generate_report` attache `measures` (fail_reason
  inclus, style YMS) à chaque entrée tests[] qui a un extracteur ET a été
  exécutée (pass/fail) — jamais sur pending/skipped, jamais sans extracteur
  (additif : rapport inchangé pour ces tests). Chaîne complète :
  - `qc_engine._record_result` mesure désormais `duration_s` (début = envoi
    macro via `next_test`) et stocke `timed_out` ; `fail_current_test` gagne
    le paramètre `timed_out` (défaut False → fallback `unknown_fail`).
  - `qc_wizard._on_test_timeout` passe `timed_out=True` → fallback normé
    (`timeout` / `thermal_timeout`) au lieu d'`unknown_fail`.
  - Import de `qc_machine_measures` triple chemin (package qc/ repo, symlink
    ks_includes/ pad — miroir du pattern qc_wizard —, qc/ seul en dev) ;
    `install_qc_station.sh` sylinke le module dans ks_includes (sinon ImportError
    sur pad à la prochaine install).
  Tests : +TestMachineReportMeasures (10 tests) — clés exactes par extracteur,
  valeurs relues du details engine, ramp_s=duration_s mesurée, timeout vs
  unknown_fail, signature log battant le fallback, skipped sans measures,
  entrée strictement additive (8 clés), gate charge réelle = rapport sandbox
  complet via le VRAI engine (measures sur les 4 extracteurs, JSON
  sérialisable).
  PROOF:
  - cmd1: `./verify.sh 2>&1 | tail -8`
  - sortie: `Ran 75 tests in 7.595s` / `OK` / `verify.sh: PASS`
  - critère numérique: 75/75 tests unittest verts (65 avant L4, +10
    nouveaux), 4/4 étapes verify.sh OK, shlint install_qc_station.sh propre.
  - cmd2 (gate E2E): `python3 -c "... sandbox_machine_test.build_report() ..."`
    -> entrée z_tap_calib réelle : `"measures": {"taps_mm": [],
    "spread_mm": 0.0312, "tolerance_mm": 0.05, "n_taps": 0,
    "converged_n": 3, "fail_reason": null}` ; `entrees avec measures:
    ['home_x', 'home_y', 'heat_bed', 'z_tap_calib']` ; `overall: PASS |
    technician present: False`.
  - attribution: python3 local macOS, branche qc-machines-dev @ 7b0e1e5+.
    VARIED: qc/qc_engine.py (import + duration/timed_out + measures au
    rapport), qc/qc_wizard.py (timed_out=True au timeout),
    qc/install_qc_station.sh (symlink), qc/tests/test_qc_engine.py
    (+TestMachineReportMeasures) / HELD FIXED: qc_machine_measures.py,
    qc_yms.py, macros, klippy extras, verify.sh, scripts/sandbox_machine_test.py.
  - WHAT THIS DOES NOT SAY: ne prouve PAS que la prod accepte les measures
    (E2E prod = L9, aucun token ici) ni qu'un pad réel remonte les durées —
    sessions simulées uniquement. Les extracteurs au-delà des 4 pilotes sont
    le lot L5.
  Prochain lot : L5 (extension à tous les tests : mcu_check, fan_*,
  heat_extruder, cutter, e1_head, z_tap_home, screws_tilt).

- **L5 (13/08) — FAIT.** Extracteurs pour les 9 tests restants : les 13 tests
  de la séquence machine ont TOUS measures au rapport (bed_mesh/e0_head, hors
  `_QC_ORDER`, restent sans extracteur -> None).
  - `mcu_check` : mcu_uid (MCU_UID=), mcu_count + firmware_versions {mcu:
    version} (lignes `[<name>] version:`), yumi_config_found (device=) ;
    fail_reason no_yumi_config / mcu_uid_error / timeout.
  - `fan_*` : extracteur visuel partagé, visual_ack=verdict opérateur ;
    FAIL hors timeout -> visual_reject (vs timeout).
  - `heat_extruder` : factory `_extract_heat(target_c)` partagée avec
    heat_bed (target 220 = M104 S220 de QC_HEAT_EXTRUDER), ramp_s=duration_s,
    HEAT_OK forward-compatible ; thermal_runaway / thermal_timeout.
  - `cutter` : motion_first_detect, feed_mm (ligne FAIL "pas a la tete apres
    Nmm" seulement — non loggé en PASS mode cutter), cut_ok opérateur ;
    head_not_reached / sensor_mute / already_at_head / visual_reject.
  - `e1_head` : RÉUTILISE `qc_yms.extract_measures` (mêmes lignes de log),
    clés banc-only élaguées (stress/dropouts/retract), feed_budget_mm=800
    (_QC_HEAD_FEED variable_maxd) ; fallback timeout -> unknown_fail quand
    timed_out=False. Import qc_yms double chemin (package qc/ ou symlink
    ks_includes — qc_yms.py déjà symlinks par install_qc_station.sh:34).
  - `z_tap_home` : tap_z_mm (1er VALIDATED trigger_z), z_max_mm (parse
    ZMAX= forward-compatible, None aujourd'hui), visual_ack ;
    tap_not_converging ("Pressure probe failed") / not_homed / visual_reject.
  - `screws_tilt` : corrections {vis: tours signés CW+/CCW-} (parse
    "adjust CW 00:19", format upstream screws_tilt_adjust.py vérifié),
    max_deviation_mm (écart z sondés), n_retries ; not_homed /
    screws_tilt_aborted ("bed level exceeds" / "triggered prior").
  - Signature interne des extracteurs : + timed_out (5e param) pour
    distinguer rejet opérateur (visual_reject) d'un vrai timeout engine.
  - Advisory reviewer L4 traité : défaut `timed_out=True` -> False (un
    appelant qui omet l'argument obtient unknown_fail, plus sûr ; l'engine
    passe toujours la valeur explicitement — 3 tests mis à jour).
  Tests : +TestMcuCheck/FansVisual/HeatExtruder/Cutter/E1Head/ZTapHome/
  ScrewsTilt (31 nouveaux), test_no_extractor -> test_all_executed_tests_
  have_measures (13/13), fixtures = logs réels AUDIT-MESURES.md/macros.
  PROOF:
  - cmd1: `./verify.sh 2>&1 | tail -8`
  - sortie: `Ran 106 tests in 7.091s` / `OK` / `verify.sh: PASS`
  - critère numérique: 106/106 tests unittest verts (75 avant L5, +31),
    4/4 étapes verify.sh OK. Aucun fichier shell touché (shlint sans objet).
  - cmd2 (gate E2E): `python3 - <<'PY' ... sandbox_machine_test.build_report() ... PY`
    -> `entrees avec measures (13/13): ['mcu_check', 'home_x', 'home_y',
    'fan_motherboard', 'fan_part', 'fan_hotend', 'heat_extruder', 'heat_bed',
    'cutter', 'e1_head', 'z_tap_home', 'z_tap_calib', 'screws_tilt']` ;
    `overall: PASS | technician present: False` ; mcu_check ->
    `{"mcu_uid": "2D0046000D51353234323830", "mcu_count": 1,
    "firmware_versions": {"mcu": "v0.12.0-159-gabcd1234"},
    "yumi_config_found": true, "fail_reason": null}` ; e1_head sans clés
    stress/dropouts, budget 800 ; JSON sérialisable.
  - attribution: python3 3.14.6 local macOS, branche qc-machines-dev @
    7edb4f0+. VARIED: qc/qc_machine_measures.py (9 extracteurs + factory
    heat + défaut timed_out), qc/tests/test_qc_machine_measures.py (+31),
    qc/tests/test_qc_engine.py (2 tests L5), scripts/sandbox_machine_test.py
    (log mcu_check + ligne version) / HELD FIXED: qc_engine.py, qc_yms.py,
    qc_wizard.py, macros, klippy extras, verify.sh.
  - WHAT THIS DOES NOT SAY: ne prouve PAS que la prod accepte ces measures
    (E2E prod = L9, aucun token ici) ni qu'un pad réel émet ces logs —
    fixtures issues de l'audit statique des macros/klippy. feed_mm cutter en
    PASS et z_max_mm restent None tant que l'instrumentation additive
    (documentée AUDIT-MESURES.md) n'est pas déployée.
  Prochain lot : L6 (versions logicielles au rapport).

- **L6 (13/08) — FAIT.** Bloc racine `software_versions` (contrat §3.2),
  additif et tolérant : chaque clé absente de sa source est omise, le bloc
  entier est omis si rien n'est disponible (rapport strictement identique à
  l'existant dans ce cas — test dédié sur le set de clés racine).
  - `klipper_version` : relue de la ligne MCU hôte DÉJÀ émise par
    QC_MCU_CHECK (`[mcu SmartPiOne] version: X (host ...)`, fallback
    `[mcu rpi]`) — la mcu_version du MCU linux EST la version Klipper du
    'printer info'. AUCUNE instrumentation macro nécessaire (les cfg
    générées ne sont pas régénérables ici : backups de prod absents du
    repo) — compatible avec les cfgs déjà déployées.
  - `firmware_version` : {mcu: version} depuis les lignes `[<mcu>] version:`,
    MCU hôte EXCLU (c'est le process Klipper, pas un firmware flashé).
    Parsing factorisé : `firmware_versions_from_log` réutilisé par
    `_extract_mcu_check` (DRY).
  - `image_version` : 1re ligne du 1er fichier présent parmi
    `/etc/yumi-image-version`, `/etc/yumi-release` (constante
    IMAGE_VERSION_FILES, convention best-effort documentée — à confirmer
    avec l'image YumiOS, réponse serveur lot L8).
  - `qc_cfg_version` : `sha256:<12 hex>` de
    `~/printer_data/config/qc_printer_<MODEL>.cfg` (cfg modèle déployée par
    sync_qc_cfgs.sh) — le modèle est désormais mémorisé par engine.start()
    (`self.model`, attribut nouveau, défaut ""). Choix du hash plutôt que
    du marqueur _QC_MODE : fonctionne avec les cfgs déployées sans régén.
  Helpers purs dans qc_machine_measures.py (image_version_from_files,
  qc_cfg_hash, klipper_version_from_log, software_versions) — l'engine
  reste un adaptateur mince.
  Tests : +TestSoftwareVersions (12, helpers : parsing multi-MCU, suffixe
  host élagué, fallback rpi, fichiers tmp, hash sha256 court, tolérance
  partielle/vide) + TestReportSoftwareVersions (5, engine : bloc complet
  avec HOME tmp + hash relu du fichier, bloc partiel, bloc omis + set de
  clés racine inchangé, gate sandbox).
  PROOF:
  - cmd1: `./verify.sh 2>&1 | tail -8`
  - sortie (dernières lignes): `test_post_ack_200_and_payload ... ok` /
    `Ran 121 tests in 7.061s` / `OK` / `verify.sh: PASS`
  - critère numérique: 121/121 tests unittest verts (106 avant L6, +15
    nouveaux — 1 fix test : machine_uid_missing ajouté au set attendu du
    test "bloc omis", garde-fou identité existant L2), 4/4 étapes verify.sh
    OK. Aucun fichier shell touché (shlint sans objet).
  - cmd2 (gate E2E): `python3 - <<'PY' ... sandbox_machine_test.build_report() ... PY`
    -> `software_versions: {"klipper_version": "v0.12.0-159-gabcd1234",
    "firmware_version": {"mcu": "v0.12.0-159-gabcd1234"}}` ;
    `sandbox: True | overall: PASS | technician present: False` ;
    mcu_check measures `mcu_count: 2` (hôte inclus dans measures, exclu du
    bloc racine) ; `JSON serializable OK`.
  - attribution: python3 3.14.6 local macOS, branche qc-machines-dev @
    7b308f7+. VARIED: qc/qc_machine_measures.py (helpers versions +
    factorisation firmware_versions_from_log), qc/qc_engine.py (self.model
    + bloc racine + _qc_cfg_version), qc/tests/test_qc_machine_measures.py
    (+12), qc/tests/test_qc_engine.py (+5), scripts/sandbox_machine_test.py
    (ligne hôte simulée) / HELD FIXED: qc_yms.py, qc_wizard.py, macros,
    klippy extras, verify.sh, cfgs générées.
  - WHAT THIS DOES NOT SAY: ne prouve PAS que la prod accepte le bloc
    (E2E prod = L9, aucun token ici) ni qu'un pad réel émet la ligne hôte
    sur toutes les cfgs (le `[mcu SmartPiOne]` est conditionnel dans la
    macro) ni que l'image YumiOS porte un fichier release — les deux clés
    concernées sont omises proprement dans ce cas (tolérance testée).
  Prochain lot : L7 (retest: true + retest_reason).

- **L7 (13/08) — FAIT.** `retest: true` + `retest_reason` (contrat §3.3)
  envoyés UNIQUEMENT quand un QC est relancé sur une machine déjà passée au
  QC sur CE pad — jamais `retest: false` (additif : rapport inchangé sinon).
  - Heuristique locale documentée : un rapport précédent portant le MÊME
    `machine_uid` (UID STM32, identité machine fiable) existe dans
    `~/printer_data/config/qc_reports/` (là où `save_report` écrit). Helper
    pur `qc_machine_measures.previous_qc_overall(report_dir, machine_uid,
    exclude_date)` → overall_result du rapport précédent le plus récent,
    None sinon. Tolérant : répertoire absent, JSON illisible, clés
    manquantes, marqueurs `.json.sent` (store-and-forward) ignorés ;
    comparaison UID insensible à la casse ; `exclude_date` = date du run
    courant (un double `generate_report` dans la même session n'est PAS un
    retest).
  - `retest_reason` normé (constante `RETEST_REASONS`, à figer avec le
    serveur en L8) : `previous_report_pass` / `previous_report_fail` /
    `previous_report_partial`, repli `previous_report` si overall inconnu.
  - `machine_uid` absent (garde-fou identité L2) → jamais de retest : pas
    d'identité machine fiable pour comparer.
  - DRY : chemin qc_reports factorisé en constante `QC_REPORT_DIR`
    (qc_engine) partagée par `save_report` et la détection retest.
  Tests : +TestPreviousQcOverall (9, helper pur : plus récent gagne, autre
  machine ignorée, casse, même run exclu, JSON cassé/.sent ignorés, couverture
  RETEST_REASONS) + TestReportRetest (6, engine via HOME tmp : absent sans
  rapport précédent + set de clés racine INCHANGÉ, raison miroir du verdict
  précédent, autre machine, sans UID, gate JSON sérialisable).
  PROOF:
  - cmd1: `./verify.sh 2>&1 | tail -6`
  - sortie (dernières lignes): `Ran 136 tests in 7.575s` / `OK` /
    `verify.sh: PASS`
  - critère numérique: 136/136 tests unittest verts (121 avant L7, +15
    nouveaux), 4/4 étapes verify.sh OK. Aucun fichier shell touché (shlint
    sans objet).
  - cmd2 (gate E2E charge réelle): `python3 - <<'PY' ...
    sandbox_machine_test.build_report() ×2, HOME tmp, 1er rapport sauvegardé
    comme le fait le wizard ... PY`
  - sortie: `1er QC — retest present: False | overall: PASS | uid:
    2D0046000D51353234323830` ; `2e QC — retest: True | retest_reason:
    previous_report_pass` ; `JSON serializable OK`.
  - attribution: python3 3.14.6 local macOS, branche qc-machines-dev @
    0732df4+. VARIED: qc/qc_machine_measures.py (RETEST_REASONS +
    previous_qc_overall, +json/os), qc/qc_engine.py (bloc retest +
    QC_REPORT_DIR), qc/tests/test_qc_machine_measures.py (+9),
    qc/tests/test_qc_engine.py (+6) / HELD FIXED: qc_yms.py, qc_wizard.py,
    macros, klippy extras, verify.sh, scripts/sandbox_machine_test.py.
  - WHAT THIS DOES NOT SAY: ne prouve PAS que la prod accepte les champs
    retest (E2E prod = L9, aucun token ici) ni qu'un pad réel accumule des
    rapports dans qc_reports/ comme simulé — la détection est prouvée sur la
    structure de fichiers réelle écrite par save_report, rejouée en tmp.
    La liste retest_reason reste à figer avec le serveur (lot L8).
  Prochain lot : L8 (docs/REPONSES-SERVEUR.md).

- **L8 (13/08) — FAIT.** `docs/REPONSES-SERVEUR.md` : réponses complètes aux 4
  questions du serveur (CDC §7), fidèles au code livré L1→L7 :
  - §1 inventaire des measures par test (13 tests, clés exactes, source de
    chaque mesure, nulls actuels assumés : reached_c/stable heat_*, feed_mm
    cutter en PASS, z_max_mm — parses forward-compatibles déjà en place) ;
  - §2 liste fail_reason DÉFINITIVE par test (6 réutilisés du banc YMS + 12
    nouveaux machines, sémantique des fallbacks timeout/unknown_fail/
    visual_reject) — proposée à figer ensemble ;
  - §3 versions remontables : klipper_version (MCU hôte), firmware_version
    {mcu} (hôte exclu), image_version (convention /etc/yumi-image-version —
    question en retour posée au serveur), qc_cfg_version (sha256:12hex) ;
  - §4 retest/retest_reason (3 raisons miroir du verdict précédent + repli
    previous_report, heuristique locale documentée) ;
  - §5 ids inconnus du serveur : AUCUN — la séquence n'émet que les 13 ids
    reconnus (bed_mesh/e0_head hors séquence, jamais envoyés) ;
  - §6 sandbox : harnais scripts/sandbox_machine_test.py, boucle E2E = L9.
  README qc/ : `qc/README-MACHINES.md` créé (architecture séquence 13 tests,
  rapport measures-first additif, sandbox, déploiement) — miroir de
  README-YMS.md, qui reste le doc du banc.
  Garde anti-dérive : `qc/tests/test_reponses_serveur.py` (5 tests) — chaque
  fail_reason émis par les extracteurs (assignations + défauts _EXTRACTORS),
  chaque test id instrumenté (13/13), chaque retest_reason et chaque clé
  software_versions DOIVENT figurer dans REPONSES-SERVEUR.md.
  PROOF:
  - cmd1: `python3 -m unittest qc.tests.test_reponses_serveur -v`
  - sortie: 5 tests `... ok` / `Ran 5 tests in 0.001s` / `OK`
  - cmd2: `./verify.sh 2>&1 | tail -8`
  - sortie (dernières lignes): `test_post_ack_200_and_payload ... ok` /
    `Ran 141 tests in 7.608s` / `OK` / `verify.sh: PASS`
  - critère numérique: 141/141 tests unittest verts (136 avant L8, +5
    nouveaux), 4/4 étapes verify.sh OK. Aucun fichier shell touché (shlint
    sans objet).
  - attribution: python3 3.14.6 local macOS, branche qc-machines-dev @
    482554f+. VARIED: docs/REPONSES-SERVEUR.md (nouveau),
    qc/README-MACHINES.md (nouveau), qc/tests/test_reponses_serveur.py
    (nouveau) / HELD FIXED: qc/*.py (code), macros, klippy extras, verify.sh,
    scripts/sandbox_machine_test.py.
  - WHAT THIS DOES NOT SAY: le contenu des réponses est cohérent avec le CODE
    (prouvé par le test de cohérence) mais pas encore validé par le SERVEUR —
    la liste fail_reason est une proposition à figer (réponse serveur
    attendue), l'acceptation prod des champs est le lot L9, le QC pilote réel
    le lot L10.
  Prochain lot : L9 (validation sandbox E2E prod).
