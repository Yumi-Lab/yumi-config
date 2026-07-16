# PROMPT — Dashboard QC Yumi Lab (qc.yumi-lab.com)

> Brief complet et autonome pour construire le backend + dashboard de contrôle
> qualité usine Yumi Lab. À donner tel quel à un dev ou un agent de code.

## 1. Objectif

Chaque imprimante Yumi passe un **contrôle qualité (QC) de 15 tests** sur le SmartPad
(panel KlipperScreen) avant expédition. À la fin, le pad génère un **rapport JSON** et
doit l'**envoyer à un serveur central**. Construire :

1. Une **API d'ingestion** qui reçoit et stocke ces rapports.
2. Un **dashboard web** accessible sur **`https://qc.yumi-lab.com`** affichant les runs
   QC, le détail de chaque machine, et des **compteurs de production** (total, par lot,
   par jour, par modèle), avec un taux de réussite et l'analyse des échecs.

## 2. Hébergement & stack (IMPÉRATIF : identique à config.yumi-lab.com)

Déployer sur le **même serveur** que `config.yumi-lab.com` (le serveur yumi-sync), en
sous-domaine **`qc.yumi-lab.com`**. Stack à reproduire :

- **Flask** (Python 3) servi par **Apache + `libapache2-mod-wsgi-py3`** (WSGI).
- Templates **Jinja2** (`templates/base.html` partagé, etc.), statics (logo Yumi).
- **HTTPS Let's Encrypt** via `certbot` + `python3-certbot-apache`.
- **SQLite** pour le stockage (simple, suffisant, fichier unique sauvegardable).
- Un **`install.sh`** idempotent qui : installe les paquets, crée le venv, le `.wsgi`,
  le vhost Apache `qc.yumi-lab.com.conf`, lance certbot, démarre le service.
- Structure repo : `opt/yumi-qc/app.py`, `opt/yumi-qc/templates/`, `opt/yumi-qc/static/`,
  `opt/yumi-qc/qc.db`, `install.sh`. (Calquer `yumi-config-server`.)

## 3. API d'ingestion (pad → serveur)

### `POST /api/qc/report`
- **Auth** : header `X-QC-Token: <token partagé>` (rejeter 401 sinon). Token en variable
  d'env / fichier de conf serveur, et déployé sur les pads par yumi-config.
- **Body** : le rapport JSON (schéma §5).
- **Comportement** : valider le JSON, en extraire l'identité (parse `yumi_config`, §6),
  insérer en base (idempotent sur `(printer_id, date)` — un re-POST du même run met à
  jour, ne duplique pas), renvoyer `200 {"status":"ok","id":<run_id>}`.
- **Robustesse** : accepter un rapport même partiel (PARTIAL/FAIL). Ne jamais perdre un
  rapport (contrainte clé : le WiFi des pads est instable).

### `GET /api/qc/stats` (JSON, pour le dashboard / usage externe)
Renvoie les compteurs agrégés (§7).

## 4. Côté pad (intégration — à fournir aussi)

Le panel a déjà `qc_engine.save_report()` (JSON local dans
`~/printer_data/config/qc_reports/`) et un `_on_upload_report()`. À adapter :

1. **Pointer l'upload vers `https://qc.yumi-lab.com/api/qc/report`** avec le header
   `X-QC-Token`.
2. **File d'attente offline + retry** : si l'upload échoue (pas de réseau — fréquent),
   le rapport reste dans `qc_reports/` avec un flag `pending`; un petit daemon (ou un
   hook yumi-sync au prochain réveil réseau) **re-tente l'envoi** jusqu'au `200`, puis
   marque `sent`. Aucun rapport ne doit être perdu.
3. L'upload se déclenche au clic **"Terminer"** du panel (fin de QC) + au retry.

## 5. Schéma du rapport JSON (exact, produit par le pad)

```json
{
  "version": "1.0",
  "printer_id": "AABBCCDDEEFF",          // YUMI ID = MAC ETH0, identifiant machine
  "technician": "",                       // opérateur (souvent vide pour l'instant)
  "date": "2026-06-15T09:21:58.559388",   // ISO début QC
  "date_end": "2026-06-15T09:25:10.000",  // ISO fin QC
  "duration_seconds": 192,
  "overall_result": "PASS",               // PASS | FAIL | PARTIAL
  "failed_tests": ["home_x"],             // ids des tests FAIL
  "skipped_tests": [],
  "yumi_config": "board=SMARTMAKER cpu=STM32 device=C235 lot=L2406 uid=ABCD ...",
  "tests": [
    {
      "id": "mcu_check",
      "name": "MCU + firmware (YUMI_CONFIG)",
      "type": "automated",                // automated | visual
      "result": "pass",                   // pass | fail | skipped | pending
      "timestamp": "2026-06-15T09:21:58",
      "details": "spread=0.0450mm ...",   // mesure clé (si l'engine l'a posée)
      "log": [                            // log riche capturé pendant le test
        "[mcu] version: v0.13.0-...",
        "YUMI_CONFIG: board=... device=C235 lot=L2406 uid=ABCD"
      ]
    }
    // ... 15 tests
  ]
}
```

### Les 15 tests (ordre) — `id` → libellé
`mcu_check`, `fan_motherboard`, `fan_part`, `fan_hotend`, `heat_extruder`, `heat_bed`,
`cutter`, `home_x`, `home_y`, `z_tap_home`, `z_tap_calib`, `screws_tilt`, `bed_mesh`,
`e0_head` (YMS-1 motion sensor + feed tête), `e1_head` (YMS-2 idem).

Données riches notables dans `log`/`details` : distance d'arrivée filament (ex
"filament a la tete apres 650mm"), spread Z (ex "spread=0.0450mm"), changement d'état
motion sensor, corrections de vis du screws tilt (ex "CW 02:08"), `YUMI_CONFIG`.

## 6. Parsing de l'identité (`yumi_config` + `printer_id`)

`yumi_config` est la constante gravée dans le MCU par le builder firmware Yumi, sous forme
`clé=valeur` séparées par des espaces. En extraire (tolérant aux clés absentes) :
- **`device` / `model`** → modèle machine (C235, C335, C435…) → segmentation "par modèle".
- **`lot`** → lot de fabrication → segmentation "par lot".
- **`uid`** → identifiant unique carte.
- **`board`, `cpu`, `drivers`, `motors`** → infos affichées dans le détail.
- `printer_id` (MAC) = identifiant pad/machine, clé d'affichage si `uid` absent.

## 7. Dashboard — pages & compteurs

Style : sobre, lisible, responsive, charte Yumi (logo, couleurs ; reprendre `base.html`
de config.yumi-lab.com). PASS = vert, FAIL = rouge, PARTIAL = orange.

### Page d'accueil `/` — vue d'ensemble + COMPTEURS
- **Compteur principal géant** : **nombre total de machines VALIDÉES** (overall PASS).
- Cartes compteurs : **aujourd'hui** (runs + validées), **taux de réussite global**
  (PASS / total %), **en échec à retraiter** (machines FAIL sans run PASS ultérieur).
- **Par lot** : tableau/bar-chart des validées par lot de fabrication.
- **Par jour** : courbe des validées par jour (30 derniers jours).
- **Par modèle** : répartition C235/C335/C435… (validées + taux).
- **Top des tests qui échouent** : classement des `id` de test les plus souvent FAIL
  (ex: `home_x` imprécis, `e0_head` motion sensor HS) → pilotage qualité prod.
- Liste des **derniers runs** (10), cliquables vers le détail.

### Page liste `/runs` — table filtrable
Colonnes : date, modèle, lot, machine (uid/MAC), durée, résultat global, nb FAIL.
Filtres : plage de dates, modèle, lot, résultat (PASS/FAIL/PARTIAL), recherche par
uid/MAC. Tri par colonnes. Pagination.

### Page détail `/run/<id>` — rapport complet d'une machine
- En-tête : machine (uid/MAC), modèle, lot, technicien, dates, durée, **résultat global**.
- `yumi_config` brut + parsé.
- **Tableau des 15 tests** : nom, type, résultat (badge couleur), `details`, et le **log
  riche déroulable** par test (distances, spread, corrections de vis, erreurs).
- Bouton "télécharger le JSON".

## 8. Stockage (SQLite — schéma indicatif)

- `runs(id, printer_id, uid, model, lot, board, cpu, technician, date_start, date_end,
  duration_s, overall, n_fail, n_skip, yumi_config, raw_json, created_at)`.
- `tests(id, run_id FK, test_id, name, type, result, timestamp, details, log_json)`.
- Index sur `model`, `lot`, `date_start`, `overall` pour les compteurs rapides.
- Une vue/agrégat "machines validées" = runs avec `overall='PASS'` (dédupliqué par
  uid si une machine repasse le QC : compter la machine, pas le run).

## 9. Sécurité

- POST protégé par `X-QC-Token` (token partagé pad↔serveur).
- HTTPS obligatoire (certbot). Rediriger http→https.
- Dashboard : auth simple (basic auth Apache ou login Flask) — accès interne usine/SAV.
- Pas de `localStorage`/`sessionStorage` côté front (règle Yumi) : état serveur/Jinja.
- Le `raw_json` est conservé tel quel (preuve/archive QC).

## 10. Critères d'acceptation

1. `install.sh` déploie tout depuis zéro sur le serveur → `https://qc.yumi-lab.com` répond
   en HTTPS valide.
2. Un `POST /api/qc/report` avec un rapport d'exemple (§5) + bon token → stocké, visible
   immédiatement dans le dashboard ; mauvais token → 401.
3. La page d'accueil affiche : total validées, aujourd'hui, taux de réussite, par
   lot/jour/modèle, top tests en échec.
4. La liste filtre par date/modèle/lot/résultat ; le détail montre les 15 tests + le log
   riche par test.
5. Un re-POST du même run (même printer_id+date) met à jour sans dupliquer.
6. Le compteur "machines validées" déduplique par `uid` (une machine = 1, même si
   re-QC).
7. Démo : injecter 3 rapports (1 PASS C235 lot L2406, 1 FAIL C335, 1 PASS C235) →
   compteurs et segmentations cohérents.

## 11. Livrables

- Repo `yumi-qc-dashboard` (calqué sur `yumi-config-server`) : `opt/yumi-qc/app.py`,
  templates, statics, `install.sh`, `README.md`.
- Patch côté pad : `qc_engine`/`qc_wizard` upload vers `qc.yumi-lab.com/api/qc/report`
  avec token + **file d'attente offline/retry**, déployé via `yumi-config`.
- Token QC provisionné sur les pads + le serveur.
