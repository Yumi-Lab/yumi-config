# WATCHDOG — À LIRE EN PREMIER (avant GOAL.md)

> Copie ce gabarit en `WATCHDOG.md` à la racine, adapte la **Mission**. La boucle le lit à l'**étape 0**
> (loop.sh l'injecte en tête du prompt codeuse quand le fichier existe). But : que l'agent comprenne le
> DISPOSITIF dans lequel il tourne et ne le casse pas.

## Le dispositif (comprends-le avant d'agir)
- Une **BOUCLE** (`loop.sh`, ou un fork `run-vN.sh`) te relance en **sessions fraîches**, **lot par lot**,
  jusqu'au fichier sentinelle **`.done`**. Ton état vit dans `GOAL.md` (la DoD) + `PROGRESS.md` (les cases).
- Un **WATCHDOG** (`watchdog.sh`, cron toutes les 5 min) te **RELANCE tout seul** si la boucle tombe
  (crash, incident modèle, `max_iters`). Journal : `.monitor/watchdog.log`.
- Un **VERROU** `.monitor/.lock` garantit **une seule boucle** à la fois. Le dashboard lit `.monitor/*.jsonl`.
- Mode contrôlé (optionnel) : **CODEUSE** + **CONTRÔLEUSE** (revue read-only → verdict), **un modèle par
  instance** (ex. Opus / Kimi / Grok / Mistral) — cf. `loop.conf`.

## Ne casse RIEN de ce dispositif
- **NE touche pas** à `watchdog.sh`, `.monitor/.lock`, ni au cron. **NE lance PAS une 2ᵉ boucle** (le verrou
  la refuserait, mais n'essaie pas de le contourner).
- Ne modifie `loop.sh` / `loop.conf` que si nécessaire, et alors **copie + `mv` atomique** (jamais in-place
  pendant qu'une boucle tourne).
- **Staging CIBLÉ** : `git add` fichier par fichier, **jamais `git add -A`**. Aucune mention d'outil IA dans les commits.

## Comment ça s'ARRÊTE proprement
- Crée **`.done`** UNIQUEMENT quand **TOUTE** la DoD de `GOAL.md` est atteinte **ET vérifiée** (tests verts +
  contrôle réel). `.done` arrête proprement **la boucle ET le watchdog**. Tant que ce n'est pas fini :
  **commit + STOP**, jamais `.done`.
- Un gate **visuel/device** hors de ta portée (pas de Chrome MCP, appareil…) → écris **`.gate-handoff`**
  (URL + checklist) + **STOP** : tu cèdes la main à l'humain (le watchdog NE relance PAS tant que ce fichier existe).

## Garde-fous à respecter
- **Preuve avant de cocher** : une case ne se coche qu'avec un **bloc PROOF** au Journal (commande exacte
  + vraie sortie + critère numérique + attribution `VARIED: … / HELD FIXED: …` +
  `WHAT THIS DOES NOT SAY: …` ; échec = causes éliminées avec mesure).
- Zéro hardcodage / DRY ; réutilise l'existant ; non-régression ; secrets jamais committés.

## MISSION (à personnaliser — sois précis, c'est ce qui cadre l'agent)
> Remplace ce bloc par la vraie mission. Sois EXPLICITE sur ce qui est attendu **et** ce qui ne l'est pas.
>
> Exemple : « Construire une **VRAIE application fonctionnelle** X (backend + frontend qui marchent, testés
> de bout en bout), **pas** une maquette ni un thème CSS. Chaque lot doit livrer une fonctionnalité réelle
> et démontrable. »
