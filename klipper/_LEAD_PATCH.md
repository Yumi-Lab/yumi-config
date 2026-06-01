# Patch « extruder lead_time » (anticipation / coast)

Injecté par **symlink** depuis `install.sh` dans l'arbre Klipper du pad.

## Fichiers patchés (core Klipper, symlinkés)

| Source yumi-config | Cible Klipper | Type |
|---|---|---|
| `klipper/klippy/extras/motion_queuing.py` | `~/klipper/klippy/extras/motion_queuing.py` | core patché |
| `klipper/klippy/kinematics/extruder.py` | `~/klipper/klippy/kinematics/extruder.py` | core patché |

`kin_extruder.c` reste **STOCK** (md5 `7923f1e2eba26f40e746184c19704571`). Le patch
est **100 % Python** : c'est ce qui le rend maintenable (pas de fork C à recompiler,
le `c_helper.so` stock fait l'affaire).

## Ce que fait le patch

- `lead_time` (config `[extruder]` ou `SET_PRESSURE_ADVANCE EXTRUDER=... LEAD_TIME=`)
  décale dans le temps le contenu du trapq extrudeur au niveau **planner**
  (`process_move` append à `print_time - lead`). Pur décalage temporel → total
  d'extrusion conservé, débit constant (pas de surépaisseur d'angle comme le PA).
- `motion_queuing.py` élargit la fenêtre de scan (`kin_flush_delay`) de `lead` pour
  rester sync-safe sur les feeders synchronisés (YMS). **Ne jamais** élargir
  `gen_steps_pre/post_active` → crash « Invalid sequence ».
- `smooth_time` découplé du PA : s'active si `pressure_advance > 0` **OU** `lead > 0`,
  donc le smooth lisse les rampes à PA=0 sans fake PA.

Config validée en réel : `pressure_advance: 0` + `pressure_advance_smooth_time: 0.04`
+ `lead_time: 0.03`.

## Baseline & discipline de rebase

Le patch a été dérivé du Klipper au commit **`85c79bc`** (juste avant les 4 commits
de debugging stepcompress upstream `cb54464`/`e5241f1`/`ed82be0`/`4bc5646`).

Comme on symlinke des fichiers **core**, ils **figent** ces 2 fichiers à ce baseline.
À chaque montée de version Klipper :

1. `git diff 85c79bc HEAD -- klippy/extras/motion_queuing.py klippy/kinematics/extruder.py`
   pour voir si upstream a touché ces fichiers.
2. Si oui, ré-appliquer les blocs marqués `# YUMI:` sur la nouvelle version, mettre
   à jour le baseline ci-dessus, re-tester (impression + 0 erreur sync watchdog).
3. Vérifier que `kin_extruder.c` est toujours stock (md5 ci-dessus).

Le diff YUMI est minimal : `trapq_leads` + `set/get_trapq_lead` + `kin_flush_delay`
(motion_queuing) ; `lead_time` config + `process_move` shift + découplage smooth/PA
+ commande `LEAD_TIME` (extruder).
