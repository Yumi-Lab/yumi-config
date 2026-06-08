# Printer CFG Generator — Specification

## Principes

- Chaque composant physique = un objet avec ses enfants (cfg + macros)
- Les schemas de connexion sont generes dynamiquement depuis le JSON produit
- Detection automatique du hardware au firstboot via scan MCU + drivers TMC
- Configuration progressive : scan → cfg minimal → test → cfg final

---

## Architecture MCU

```
Carte Mere (OBLIGATOIRE)
├── Serial : /dev/ttyS1 (RJ11)
├── Drivers : X, Y, Z, E0, E1
└── Sorties extrudeur : E0 + E1 (TOUJOURS presentes)

Smartbox 1 (OPTIONNEL — si YMS > 2)
├── Serial : /dev/ttyS2 (UART2 GPIO)
├── Drivers : E2, E3, E4, E5, E6
└── Max 5 extrudeurs

Smartbox 2 (OPTIONNEL — si YMS > 7)
├── Serial : /dev/ttyUSB0 (USB)
├── Drivers : E7, E8, E9, E10, E11
└── Max 5 extrudeurs

RPi Host MCU (OPTIONNEL)
├── Serial : /tmp/klipper_host_mcu
└── Usage : ADXL345
```

### Regles de placement extrudeurs

| YMS count | Carte Mere | Smartbox 1 (UART) | Smartbox 2 (USB) |
|-----------|-----------|-------------------|------------------|
| 2 | E0, E1 | — | — |
| 3-7 | E0, E1 | E2..E6 | — |
| 8-12 | E0, E1 | E2..E6 | E7..E11 |

---

## Sequence de detection (firstboot)

### Phase 1 : Scan MCU

```
1. Scanner les ports serie :
   - /dev/ttyS1 → tenter communication Klipper → Carte Mere
   - /dev/ttyS2 → tenter communication Klipper → Smartbox 1
   - /dev/ttyUSB* → tenter communication Klipper → Smartbox 2
   - /tmp/klipper_host_mcu → RPi MCU (ADXL)

2. Pour chaque MCU detectee :
   - Lire chip ID / version firmware
   - Stocker dans variable d'environnement ou fichier
```

### Phase 2 : CFG minimal pour test

Generer un `printer.cfg` minimal avec UNIQUEMENT les MCU detectees :

```ini
# printer.cfg minimal — phase detection
[mcu]
serial: /dev/ttyS1
restart_method: command

[mcu smartbox]          # seulement si detecte
serial: /dev/ttyS2
restart_method: command

[mcu smartbox2]         # seulement si detecte
serial: /dev/ttyUSB0
restart_method: command

[printer]
kinematics: none        # pas de mouvement, juste lecture registres
```

### Phase 3 : Scan drivers TMC (via Klipper)

Pour chaque driver UART connu sur chaque MCU :

```
Lire registre DRV_STATUS :
├── ola=0, olb=0 → MOTEUR BRANCHE
├── ola=1, olb=1 → PAS DE MOTEUR (port vide)
├── s2ga=1 ou s2gb=1 → COURT-CIRCUIT (erreur cablage)
└── Pas de reponse UART → driver absent ou defectueux

Si moteur detecte, mesurer R et L via TMC :
├── R≈1.4Ω, L≈2.6mH → BJ42D29-28V03 (stepper XY)
├── R≈1.4Ω, L≈2.2mH → BJ42D07-06V02 (stepper Z / extrudeur)
├── R≈4.3Ω, L≈5.8mH → BJ42D07-03V05 (ancien extrudeur 1gen)
└── Autre → INCONNU (demander confirmation utilisateur)
```

### Phase 4 : Scan capteurs et peripheriques

```
Pour chaque pin capteur filament connu :
├── Lire etat digital → capteur present / absent

Pour le pin probe :
├── Tester reponse → probe presente
└── Type non identifiable electriquement → DEMANDER A L'UTILISATEUR

Pour les heaters :
├── Lire thermistance → si valeur coherente = branche
└── Lecture aberrante (open circuit) = pas branche
```

### Phase 5 : Confirmation utilisateur (KlipperScreen)

Certains composants ne sont **pas identifiables** par analyse electrique seule :

| Composant | Identifiable auto ? | Si non → |
|-----------|:-------------------:|----------|
| Moteur X/Y/Z | OUI (R/L match) | — |
| Moteur extrudeur | OUI (R/L match) | — |
| Type de probe | NON | Demander : pressure / bltouch / inductive |
| Taille du bed | NON | Demander : C235 / C435 / C520 |
| Nozzle diameter | NON | Demander : 0.4 / 0.6 / 0.8 |
| Nombre de YMS voulu | PARTIEL (on detecte les branches) | Confirmer |
| Dryer present | OUI (thermistance sur pin) | — |

Ecran KlipperScreen :

```
┌─────────────────────────────────────────────┐
│  CONFIGURATION DETECTEE         Scan OK     │
├─────────────────────────────────────────────┤
│                                             │
│  MCU principale    : ✓ /dev/ttyS1           │
│  Smartbox 1        : ✓ /dev/ttyS2           │
│  Smartbox 2        : ✗ non detectee         │
│  RPi MCU (ADXL)   : ✓                      │
│                                             │
│  Moteurs detectes :                         │
│    X: BJ42D29-28V03  Y: BJ42D29-28V03      │
│    Z: BJ42D07-06V02                         │
│    E0: BJ42D07-06V02  E1: BJ42D07-06V02    │
│    E2: BJ42D07-06V02  E3: BJ42D07-06V02    │
│    E4: ✗ vide  E5: ✗ vide  E6: ✗ vide     │
│                                             │
│  YMS detectes : 4                           │
│  Dryer : ✓ (thermistance smartbox PC1)      │
│                                             │
│  ⚠ Confirmation requise :                  │
│  ┌─────────────────────────────────────┐    │
│  │ Type de probe ?                     │    │
│  │  ○ Probe Pressure                   │    │
│  │  ● BLTouch                          │    │
│  │  ○ Inductive                        │    │
│  ├─────────────────────────────────────┤    │
│  │ Taille bed ?                        │    │
│  │  ● C235 (235x239mm)                 │    │
│  │  ○ C435 (435x435mm)                 │    │
│  │  ○ Custom...                        │    │
│  └─────────────────────────────────────┘    │
│                                             │
│  [Generer configuration]                    │
└─────────────────────────────────────────────┘
```

### Phase 6 : Generation CFG final

```
Inputs :
├── Resultats scan MCU (auto)
├── Resultats scan drivers (auto)
├── Resultats scan capteurs (auto)
├── Reponses utilisateur (probe, bed size, nozzle)
└── JSON produit de base (template par modele)

Output :
├── ~/printer_data/config/printer.cfg (complet, fonctionnel)
└── ~/printer_data/config/.detected_hardware.json (sauvegarde du scan)
```

---

## Arbre composants avec parentage

```
Imprimante
│
├── [OBLIGATOIRE] Carte Mere
│   ├── [OBLIGATOIRE] stepper_x + tmc2209
│   ├── [OBLIGATOIRE] stepper_y + tmc2209
│   ├── [OBLIGATOIRE] stepper_z + tmc2209
│   ├── [OBLIGATOIRE] extruder E0 (hotend + YMS-1) + tmc2209
│   ├── [OBLIGATOIRE] extruder E1 (YMS-2) + tmc2209
│   ├── [OBLIGATOIRE] heater_bed
│   ├── [OBLIGATOIRE] fan_part + fan_hotend
│   ├── [OPTIONNEL]  fan_board
│   ├── [OPTIONNEL]  fan_aux
│   └── [OBLIGATOIRE] sensors (filament YMS-1, YMS-2)
│
├── [OPTIONNEL] Smartbox 1 (UART — si YMS > 2)
│   ├── [VARIABLE] extruder E2..E6 + tmc2209
│   ├── [VARIABLE] sensors filament YMS-3..YMS-7
│   ├── [OPTIONNEL] dryer (heater + fan + verify)
│   └── [OPTIONNEL] fan_board
│
├── [OPTIONNEL] Smartbox 2 (USB — si YMS > 7)
│   ├── [VARIABLE] extruder E7..E11 + tmc2209
│   ├── [VARIABLE] sensors filament YMS-8..YMS-12
│   └── [OPTIONNEL] fan_board
│
├── [OBLIGATOIRE] YMS System
│   │   count: 2 (min) a 12 (max)
│   │   E0+E1 → Carte Mere (toujours)
│   │   E2-E6 → Smartbox 1
│   │   E7-E11 → Smartbox 2
│   ├── [OBLIGATOIRE] macros/
│   │   ├── T0..T{N} (tool select)
│   │   ├── TOFF
│   │   ├── LOAD_YMS
│   │   ├── INIT_YMS
│   │   ├── MOTION_SENSOR_INIT
│   │   ├── CURRENT_UNLOAD
│   │   ├── SET_PRESSURE_ADVANCE (override N extrudeurs)
│   │   └── CUT_FILAMENT
│   └── [OPTIONNEL] Dryer
│       ├── heater_generic
│       ├── verify_heater
│       ├── heater_fan
│       └── SET_HEATER_TEMPERATURE (override limite temp)
│
├── [OBLIGATOIRE] Probe
│   │   Type auto-detecte: NON → demander utilisateur
│   ├── [OBLIGATOIRE] probe section
│   ├── [OBLIGATOIRE] bed_mesh
│   ├── [OBLIGATOIRE] screws_tilt_adjust
│   ├── [OBLIGATOIRE] macros/ (PROBE_CALIBRATE, BED_MESH_CALIBRATE, SCREWS_TILT)
│   └── [SI type=pressure]
│       ├── probe_pressure
│       ├── yumi_z_tap
│       ├── BED_DETECTION
│       └── Z_TAP
│
├── [OPTIONNEL] ADXL (auto-detecte via RPi MCU)
│   ├── mcu rpi
│   ├── adxl345 + resonance_tester + input_shaper
│   └── macros/ (ADXL_AXE_X, ADXL_AXE_Y)
│
├── [OPTIONNEL] TMC Autotune (active par defaut si moteurs identifies)
│   ├── motor_constants (par modele detecte)
│   └── autotune_tmc × (X + Y + Z + N_extrudeurs)
│
├── [OBLIGATOIRE] Macros Globales
│   ├── pause_resume_cancel
│   ├── print_start_end + all_fan_off
│   ├── homing_override
│   ├── set_gcode_offset (persistent)
│   ├── load_unload_filament
│   ├── pid_bed + pid_hotend
│   ├── marlin_compat (M201, M203, M205, M900)
│   ├── m106_m107_override
│   ├── purge (SET_EXTRA_FLUSH, EXTRA_FLUSH)
│   └── saveconfig
│
├── [OBLIGATOIRE] Includes
│   ├── plr.cfg
│   ├── timelapse.cfg
│   └── [OPTIONNEL] moonraker_obico_macros.cfg
│
└── [OBLIGATOIRE] KlipperScreen UI
    ├── Panneau detection hardware (firstboot)
    ├── Panneau confirmation utilisateur
    ├── Schema connexion (genere depuis JSON)
    └── Status live des composants detectes
```

---

## KlipperScreen — Schemas generes depuis JSON

Les schemas de connexion ne sont pas des images statiques.
Ils sont **rendus dynamiquement** par KlipperScreen a partir du JSON produit.

### Donnees necessaires dans le JSON pour le rendu

```json
{
  "wiring_diagram": {
    "carte_mere": {
      "board_image": "nano_v3.1",
      "connectors": {
        "E0": {"pin_step": "PB12", "pin_dir": "PB10", "label": "YMS-1", "color": "#FF0000"},
        "E1": {"pin_step": "PA5", "pin_dir": "PA4", "label": "YMS-2", "color": "#00FF00"},
        "X":  {"pin_step": "PB1", "pin_dir": "PB0", "label": "Stepper X"},
        "Y":  {"pin_step": "PA1", "pin_dir": "PA0", "label": "Stepper Y"},
        "Z":  {"pin_step": "PC3", "pin_dir": "PC2", "label": "Stepper Z"},
        "PROBE": {"pin": "PA14", "label": "Probe"},
        "BED": {"pin_heater": "PC9", "pin_sensor": "PC0"},
        "HOTEND": {"pin_heater": "PC8", "pin_sensor": "PC1"}
      }
    },
    "smartbox_1": {
      "board_image": "smart_maker_1.1",
      "connection": {"type": "UART", "port": "/dev/ttyS2"},
      "connectors": {
        "E2": {"pin_step": "PB12", "pin_dir": "PB10", "label": "YMS-3", "color": "#0000FF"},
        "E3": {"pin_step": "PA5", "pin_dir": "PA4", "label": "YMS-4", "color": "#FFFF00"},
        "E4": {"pin_step": "PB1", "pin_dir": "PB0", "label": "YMS-5", "color": "#FF00FF"},
        "E5": {"pin_step": "PA1", "pin_dir": "PA0", "label": "YMS-6", "color": "#00FFFF"},
        "E6": {"pin_step": "PC3", "pin_dir": "PC2", "label": "YMS-7", "color": "#FFA500"}
      }
    }
  }
}
```

### Rendu dans KlipperScreen

KlipperScreen utilise GTK/Cairo → on peut dessiner des schemas vectoriels :
- Rectangle = board
- Cercles = connecteurs
- Lignes colorees = cables vers moteurs/capteurs
- Icones status : ✓ (vert) / ✗ (rouge) / ⚠ (orange)
- Overlay live : etat detection en temps reel

---

## Produits connus (a definir en JSON)

| Modele | Bed | YMS | Smartbox 1 | Smartbox 2 | Probe |
|--------|-----|-----|:----------:|:----------:|-------|
| C235 2YMS | 235x239 | 2 | — | — | variable |
| C235 4YMS | 235x239 | 4 | UART | — | variable |
| C235 7YMS | 235x239 | 7 | UART | — | variable |
| C235 12YMS | 235x239 | 12 | UART | USB | variable |
| C435 2YMS | 435x435 | 2 | — | — | variable |
| C435 7YMS | 435x435 | 7 | UART | — | variable |
| S235 (simple) | 235x235 | 0 | — | — | bltouch |

---

## Fichiers a creer dans le repo

```
src/modules/printer-cfg-generator/
├── generator.py                 # Assembleur principal
├── scanner.py                   # Detection hardware (MCU, TMC, sensors)
├── renderers/                   # 1 renderer par composant
│   ├── mcu.py
│   ├── steppers.py
│   ├── extruder.py
│   ├── yms.py
│   ├── probe.py
│   ├── fans.py
│   ├── macros_core.py
│   ├── macros_yms.py
│   ├── tmc_autotune.py
│   └── includes.py
├── products/                    # 1 JSON par produit
│   ├── C235_2YMS.json
│   ├── C235_4YMS.json
│   ├── C235_7YMS.json
│   ├── C235_12YMS.json
│   └── C435_7YMS.json
├── boards/                      # Pinout par revision de board
│   ├── nano_v3.1.json
│   ├── nano_v3.2.json
│   └── smart_maker_1.1.json
└── tests/
    └── golden/
        └── C235_CHROMAX_X12_7YMS.cfg
```

---

## Systeme de Maintenance Preventive et Curative

Integre a KlipperScreen, le systeme surveille l'usure des composants et guide l'utilisateur
pour les operations de maintenance — comme BambuLab mais adapte aux machines Yumi.

### Donnees de suivi (compteurs automatiques)

| Compteur | Source | Stockage |
|----------|--------|----------|
| Heures d'impression totales | print_stats.total_duration | save_variables |
| Distance extrusion totale (m) | extruder.total_extrusion | save_variables |
| Distance deplacement XY (km) | calcul via position tracking | save_variables |
| Nombre de changements outil | T0-T12 call count | save_variables |
| Cycles chauffe hotend | heater on/off count | save_variables |
| Cycles chauffe bed | heater_bed on/off count | save_variables |
| Heures dryer | heater_generic total_on_time | save_variables |
| Nombre de coupes filament | CUT_FILAMENT count | save_variables |
| Nombre de homing | G28 count | save_variables |

### Seuils de maintenance preventive

| Composant | Declencheur | Action | Priorite |
|-----------|-------------|--------|----------|
| Courroies X/Y | 500h impression OU input_shaper freq change >10% | Verifier tension | MOYENNE |
| Roulements lineaires | 300h OU bruit detecte (ADXL anomalie) | Lubrifier | MOYENNE |
| Hotend (nozzle) | 200h OU pression_advance derive >20% | Nettoyer / remplacer | HAUTE |
| Hotend (heatbreak) | 500h | Inspecter + nettoyer | MOYENNE |
| Lame de coupe YMS | 5000 coupes | Remplacer | HAUTE |
| Engrenages extrudeur | 1000h extrusion | Nettoyer debris | BASSE |
| Courroie bed (axe Y) | 500h | Verifier tension | MOYENNE |
| Vis Z (leadscrew) | 200h | Lubrifier | BASSE |
| Ventilateurs | 2000h | Verifier bruit / remplacer | BASSE |
| Bed (surface) | 100 prints | Nettoyer IPA | BASSE |
| Capteurs filament | 10000 evenements | Nettoyer encodeur | BASSE |
| Dryer (dessicant) | 200h dryer actif | Remplacer dessicant | MOYENNE |
| TMC drivers | derive SG_RESULT >30% baseline | Verifier moteur/meca | HAUTE |

### Maintenance curative (guides de depannage)

| Probleme detecte | Detection automatique | Guide |
|-----------------|:---------------------:|-------|
| Clog hotend | extrusion stall (filament sensor no motion + extruder moving) | Nettoyage cold-pull |
| Blob hotend | thermistance lecture aberrante apres crash | Nettoyage blob |
| Sous-extrusion | pitch filament sensor < min_pitch repetitif | Verifier gear/tension |
| Courroie lache | input_shaper freq baisse >15% vs baseline | Procedure tension |
| Bed pas level | bed_mesh ecart max > 1mm | Procedure tramming |
| Bruit anormal | ADXL amplitude pic hors freq resonance | Lubrifier / verifier fixation |
| Moteur rate des pas | SG_RESULT saturation + position error | Verifier meca + courant |
| Filament casse dans YMS | sensor runout sans insert apres 30s | Guide extraction |
| Dryer inefficace | humidite ne baisse pas (si capteur) | Remplacer dessicant |
| WiFi instable | deconnexions frequentes moonraker | Verifier dongle / position |

### Architecture KlipperScreen — Panneau Maintenance

```
┌─────────────────────────────────────────────────┐
│  MAINTENANCE              C235 CHROMAX - 847h   │
├─────────────────────────────────────────────────┤
│                                                 │
│  ⚠ ACTIONS REQUISES (2)                        │
│  ┌─────────────────────────────────────────┐   │
│  │ ● Nozzle : 210h / 200h → Nettoyer      │   │
│  │   [Voir guide]  [Reporter 50h]  [Fait]  │   │
│  ├─────────────────────────────────────────┤   │
│  │ ● Lame coupe : 4800 / 5000 → Bientot   │   │
│  │   [Commander piece]  [Reporter]  [Fait]  │   │
│  └─────────────────────────────────────────┘   │
│                                                 │
│  ETAT DES COMPOSANTS                           │
│  ┌─────────────────────────────────────────┐   │
│  │ Courroies X/Y    ████████░░  78%  420h  │   │
│  │ Hotend           ██████████  100% ALERTE│   │
│  │ Roulements       ██████░░░░  55%  165h  │   │
│  │ Lame coupe       █████████░  96%  4800  │   │
│  │ Vis Z            ████░░░░░░  38%  76h   │   │
│  │ Ventilateurs     ███░░░░░░░  28%  560h  │   │
│  │ Surface bed      ██████░░░░  62 prints  │   │
│  └─────────────────────────────────────────┘   │
│                                                 │
│  [Historique]  [Guides]  [Reset compteur]      │
└─────────────────────────────────────────────────┘
```

### Guide interactif (exemple : nettoyage hotend)

```
┌─────────────────────────────────────────────────┐
│  GUIDE : Nettoyage Hotend (Cold Pull)    1/5   │
├─────────────────────────────────────────────────┤
│                                                 │
│  ┌─────────────────────────────────────────┐   │
│  │                                         │   │
│  │         [SVG ANIME]                     │   │
│  │    Schema hotend avec fleche             │   │
│  │    montrant ou inserer filament          │   │
│  │                                         │   │
│  └─────────────────────────────────────────┘   │
│                                                 │
│  Etape 1 : Chauffer la buse a 250C             │
│                                                 │
│  Temperature actuelle : 23C → chauffage...     │
│  ████░░░░░░░░░░░░░░░░  23C / 250C             │
│                                                 │
│  ⓘ Attendez que la temperature soit atteinte   │
│    avant de passer a l'etape suivante.          │
│                                                 │
│  [< Retour]                    [Suivant >]     │
└─────────────────────────────────────────────────┘
```

### Detection anomalies via donnees machine

Le systeme exploite les donnees deja presentes dans Klipper :

```python
# Detecteurs automatiques (module Klipper custom)
class MaintenanceMonitor:
    
    def check_belt_tension(self):
        """Compare input_shaper freq actuelle vs baseline"""
        current_freq_x = self.printer.lookup_object('input_shaper').freq_x
        baseline_x = self.saved_vars.get('baseline_freq_x')
        if baseline_x and abs(current_freq_x - baseline_x) / baseline_x > 0.15:
            self.trigger_alert('belt_tension', axis='X')
    
    def check_extrusion_health(self):
        """Detecte sous-extrusion via filament sensor pitch"""
        # Si motion sensor detecte pitch < min repetitivement
        # → probable usure gear ou clog partiel
        
    def check_motor_health(self):
        """Detecte derive StallGuard vs baseline"""
        # SG_RESULT moyen en mouvement vs premiere calibration
        # Derive > 30% → probleme mecanique
    
    def check_nozzle_wear(self):
        """Detecte usure nozzle via pressure_advance"""
        # Si PA optimal augmente significativement
        # → nozzle usee (diametre agrandi)
    
    def update_counters(self):
        """Appele a chaque fin d'impression"""
        # Incrementer tous les compteurs
        # Verifier seuils
        # Generer alertes si necessaire
```

### Stockage des donnees maintenance

```ini
# Dans variables.cfg (save_variables)
[Variables]
maintenance_total_hours = 847.3
maintenance_extrusion_m = 12450.8
maintenance_xy_km = 234.5
maintenance_tool_changes = 18420
maintenance_cuts = 4800
maintenance_homing_count = 2340
maintenance_bed_heater_cycles = 890
maintenance_hotend_heater_cycles = 1240
maintenance_dryer_hours = 156.2
maintenance_baseline_freq_x = 80.4
maintenance_baseline_freq_y = 35.4
maintenance_baseline_sg_x = 120
maintenance_baseline_sg_y = 95
maintenance_last_belt_check = "2026-04-15"
maintenance_last_nozzle_clean = "2026-05-01"
maintenance_last_lube_z = "2026-03-20"
maintenance_prints_since_bed_clean = 62
```

### Guides de maintenance (contenu)

Chaque guide est un fichier JSON avec etapes + SVG + actions automatiques :

```json
{
  "id": "hotend_cold_pull",
  "title": "Nettoyage Hotend — Cold Pull",
  "trigger": "nozzle_hours > 200 OR extrusion_anomaly",
  "difficulty": "facile",
  "duration_min": 10,
  "tools_needed": ["filament nylon ou cleaning filament"],
  "steps": [
    {
      "instruction": "Chauffer la buse a 250C",
      "auto_action": "M104 S250",
      "wait_condition": "extruder.temperature > 245",
      "svg": "hotend_heating.svg",
      "svg_highlight": ["nozzle", "heater_block"]
    },
    {
      "instruction": "Inserer le filament de nettoyage jusqu'a ce qu'il sorte par la buse",
      "svg": "hotend_insert_filament.svg",
      "svg_highlight": ["filament_path"]
    },
    {
      "instruction": "Refroidir a 90C (ne pas tirer avant)",
      "auto_action": "M104 S90",
      "wait_condition": "extruder.temperature < 95",
      "svg": "hotend_cooling.svg"
    },
    {
      "instruction": "Tirer le filament d'un coup sec vers le haut",
      "svg": "hotend_pull.svg",
      "svg_animate": "pull_arrow"
    },
    {
      "instruction": "Verifier l'extremite : propre = OK, noir/debris = repeter",
      "svg": "filament_tip_check.svg",
      "user_confirm": true,
      "options": ["Propre → Terminer", "Sale → Repeter depuis etape 1"]
    }
  ],
  "on_complete": {
    "reset_counter": "maintenance_last_nozzle_clean",
    "reset_hours": "maintenance_nozzle_hours"
  }
}
```

### Structure fichiers maintenance

```
src/modules/printer-cfg-generator/
├── maintenance/
│   ├── monitor.py               # Module Klipper : compteurs + detection
│   ├── alerts.py                # Gestion seuils + notifications
│   ├── guides/
│   │   ├── hotend_cold_pull.json
│   │   ├── hotend_blob_cleanup.json
│   │   ├── belt_tension_xy.json
│   │   ├── lubricate_z_axis.json
│   │   ├── lubricate_linear_rails.json
│   │   ├── clean_bed_surface.json
│   │   ├── replace_nozzle.json
│   │   ├── replace_cut_blade.json
│   │   ├── clean_extruder_gears.json
│   │   ├── clean_filament_sensor.json
│   │   ├── replace_dryer_desiccant.json
│   │   ├── yms_filament_extraction.json
│   │   └── check_fan_noise.json
│   ├── svg/
│   │   ├── hotend_assembly.svg
│   │   ├── hotend_heating.svg
│   │   ├── hotend_insert_filament.svg
│   │   ├── hotend_cooling.svg
│   │   ├── hotend_pull.svg
│   │   ├── filament_tip_check.svg
│   │   ├── belt_tension_x.svg
│   │   ├── belt_tension_y.svg
│   │   ├── z_axis_lube.svg
│   │   ├── extruder_gears.svg
│   │   ├── yms_path.svg
│   │   └── cut_blade.svg
│   └── thresholds.json          # Seuils configurables par modele
```

### Seuils par modele (thresholds.json)

```json
{
  "default": {
    "nozzle_hours": 200,
    "belt_hours": 500,
    "linear_bearings_hours": 300,
    "cut_blade_count": 5000,
    "z_lube_hours": 200,
    "fan_hours": 2000,
    "bed_clean_prints": 100,
    "dryer_desiccant_hours": 200,
    "freq_drift_percent": 15,
    "sg_drift_percent": 30
  },
  "C235_CHROMAX": {
    "nozzle_hours": 150,
    "cut_blade_count": 4000
  },
  "C435": {
    "belt_hours": 400,
    "linear_bearings_hours": 250
  }
}
```

---

---

## Compteur Heures Machine — Anti-triche via YUMI_SYNC

### Principe

Le temps d'impression reel est la **propriete du serveur Yumi-ID**, pas de la machine locale.
La machine envoie ses compteurs. Le serveur est l'autorite. Si la valeur locale est inferieure
a la valeur serveur → triche detectee → on ecrase par la valeur serveur.

### YUMI_SYNC actuel (ce qui existe)

```
yumi_sync.py :
- Identifie la machine par MAC address (end0) → hexid
- Surveille printer.cfg (hash MD5), envoie au serveur si change
- Endpoint : http://yumi-id.yumi-lab.com/upload
- Poll toutes les 30s
- Gere first boot QC + client first boot (>15 jours apres QC)
```

### Extension : ajout du compteur heures

Le compteur est un **triplet** :
- `total_print_hours` — temps cumule ou l'imprimante etait en etat "printing"
- `total_power_hours` — temps total allumee
- `total_extrusion_m` — metres de filament extrudes

#### Cote machine (yumi_sync.py)

```python
# Nouvelles fonctions dans yumi_sync.py

COUNTERS_FILE = '/home/pi/printer_data/config/.yumi_counters.json'
COUNTERS_ENDPOINT = "http://yumi-id.yumi-lab.com/counters"

def read_local_counters():
    """Lit les compteurs locaux"""
    try:
        with open(COUNTERS_FILE, 'r') as f:
            return json.load(f)
    except:
        return {"print_hours": 0, "power_hours": 0, "extrusion_m": 0, "signature": ""}

def sync_counters(hexid):
    """
    Envoie compteurs au serveur.
    Le serveur repond avec la valeur autorite.
    Si serveur > local → on ecrase local (triche detectee).
    """
    local = read_local_counters()
    
    payload = {
        "hexid": hexid,
        "print_hours": local["print_hours"],
        "power_hours": local["power_hours"],
        "extrusion_m": local["extrusion_m"],
        "timestamp": datetime.now().isoformat(),
        "signature": local["signature"]  # HMAC pour verifier integrite
    }
    
    response = requests.post(COUNTERS_ENDPOINT, json=payload, timeout=15)
    server_data = response.json()
    
    # Le serveur repond avec sa valeur autorite
    if server_data["status"] == "ok":
        server_counters = server_data["counters"]
        
        # REGLE ANTI-TRICHE : le compteur ne peut JAMAIS baisser
        final = {
            "print_hours": max(local["print_hours"], server_counters["print_hours"]),
            "power_hours": max(local["power_hours"], server_counters["power_hours"]),
            "extrusion_m": max(local["extrusion_m"], server_counters["extrusion_m"]),
            "last_sync": datetime.now().isoformat(),
            "signature": server_data["signature"]  # Signe par le serveur
        }
        
        write_local_counters(final)
        
        if local["print_hours"] < server_counters["print_hours"]:
            logging.warning("TAMPER DETECTED: local print_hours=%s < server=%s. Restored.",
                          local["print_hours"], server_counters["print_hours"])

def write_local_counters(counters):
    """Ecrit les compteurs avec signature HMAC"""
    with open(COUNTERS_FILE, 'w') as f:
        json.dump(counters, f)
    # Aussi ecrire dans save_variables pour que Klipper/KlipperScreen puisse lire
    # (mais save_variables n'est PAS l'autorite)
```

#### Cote serveur (yumi-id.yumi-lab.com)

```python
# Endpoint /counters (a ajouter au serveur yumi-id)

@app.route('/counters', methods=['POST'])
def handle_counters():
    data = request.json
    hexid = data['hexid']
    
    # Recuperer les compteurs serveur pour cette machine
    db_counters = db.get_counters(hexid)
    
    if not db_counters:
        # Premiere fois : initialiser
        db_counters = {"print_hours": 0, "power_hours": 0, "extrusion_m": 0}
    
    # REGLE : on prend toujours le MAX (jamais de recul)
    new_counters = {
        "print_hours": max(data["print_hours"], db_counters["print_hours"]),
        "power_hours": max(data["power_hours"], db_counters["power_hours"]),
        "extrusion_m": max(data["extrusion_m"], db_counters["extrusion_m"]),
    }
    
    # Sauvegarder en base
    db.update_counters(hexid, new_counters)
    
    # Signer la reponse (HMAC-SHA256 avec cle serveur)
    signature = hmac.new(SERVER_SECRET, json.dumps(new_counters).encode(), 'sha256').hexdigest()
    
    # Detecter triche
    if data["print_hours"] < db_counters["print_hours"] - 0.1:
        db.log_tamper_event(hexid, data, db_counters)
        logging.warning("TAMPER: %s tried to reduce hours from %s to %s",
                       hexid, db_counters["print_hours"], data["print_hours"])
    
    return jsonify({
        "status": "ok",
        "counters": new_counters,
        "signature": signature
    })
```

### Protection anti-triche (couches multiples)

| Couche | Mecanisme | Contre quoi |
|--------|-----------|-------------|
| 1. Serveur = autorite | `max(local, serveur)` toujours | Reset fichier local |
| 2. Signature HMAC | Compteurs signes par le serveur | Edition manuelle du JSON |
| 3. Sync frequente | Toutes les 5 min en impression | Modifier entre 2 syncs |
| 4. Log tamper events | Serveur enregistre toute tentative de recul | Audit |
| 5. Compteur monotone | Jamais de decrement possible cote serveur | Replay ancien payload |
| 6. Fichier cache | `.yumi_counters.json` (dote, pas dans printer_data visible) | Utilisateur naif |
| 7. Lecture MCU | Heures depuis timer hardware MCU (optionnel, si MCU supporte) | Remplacement SD |

### Scenarios de triche bloques

| Attaque | Protection |
|---------|-----------|
| Supprimer `.yumi_counters.json` | Au prochain sync, serveur renvoie la vraie valeur |
| Editer le fichier avec valeur basse | Signature HMAC invalide → sync force → serveur gagne |
| Reflasher la SD card | MAC address identique → serveur reconnait la machine → restaure compteurs |
| Bloquer internet (jamais sync) | Compteur local continue de monter. Au retour internet, sync envoie la valeur accumulee |
| Changer la MAC address | Perd l'identite machine → nouvelle machine aux yeux du serveur (pas de gain) |
| Modifier yumi_sync.py | Service redemarre avec le binaire original au reboot (systemd + watchdog) |

### Integration dans la boucle YUMI_SYNC

```python
# Main loop modifie
COUNTER_SYNC_INTERVAL = 300  # 5 min

def main():
    state = load_state()
    previous_hash = state.get('last_hash')
    last_counter_sync = 0
    
    while True:
        current_time = time.time()
        
        # === Sync printer.cfg (existant) ===
        current_hash = calculate_file_hash(file_to_monitor)
        if current_hash and current_hash != previous_hash:
            send_file_to_server(file_to_monitor, mac_address)
            state['last_hash'] = current_hash
            save_state(state)
            previous_hash = current_hash
        
        # === Sync compteurs (NOUVEAU) ===
        if current_time - last_counter_sync > COUNTER_SYNC_INTERVAL:
            update_local_counters_from_klipper()  # Lire print_stats via moonraker API
            sync_counters(hexid)
            last_counter_sync = current_time
        
        time.sleep(POLL_INTERVAL)

def update_local_counters_from_klipper():
    """Lit les stats Klipper via l'API Moonraker locale"""
    try:
        r = requests.get("http://localhost:7125/printer/objects/query?print_stats", timeout=5)
        data = r.json()["result"]["status"]["print_stats"]
        
        counters = read_local_counters()
        
        # Incrementer (jamais decrémenter)
        if data["state"] == "printing":
            counters["print_hours"] += COUNTER_SYNC_INTERVAL / 3600.0
        counters["power_hours"] += COUNTER_SYNC_INTERVAL / 3600.0
        
        # Extrusion totale via extruder
        r2 = requests.get("http://localhost:7125/printer/objects/query?extruder", timeout=5)
        ext_data = r2.json()["result"]["status"]["extruder"]
        # Note: on track la position totale, pas le delta (Klipper track deja)
        
        write_local_counters(counters)
    except Exception as e:
        logging.error("Failed to read Klipper stats: %s", e)
```

### Ce que le serveur Yumi-ID stocke par machine

```json
{
  "hexid": "AABBCCDDEEFF",
  "mac": "aa:bb:cc:dd:ee:ff",
  "model": "C235_CHROMAX_X12_7YMS",
  "registered_at": "2026-01-15T10:30:00",
  "client_boot_at": "2026-02-01T08:00:00",
  "counters": {
    "print_hours": 847.3,
    "power_hours": 1204.5,
    "extrusion_m": 12450.8,
    "tool_changes": 18420,
    "cuts": 4800
  },
  "last_sync": "2026-05-17T14:22:00",
  "tamper_events": [],
  "maintenance_log": [
    {"date": "2026-04-15", "action": "belt_check", "hours_at": 720},
    {"date": "2026-05-01", "action": "nozzle_clean", "hours_at": 810}
  ],
  "firmware_version": "YumiOS-V2.1.0",
  "printer_cfg_hash": "a1b2c3d4e5f6..."
}
```

### KlipperScreen — Affichage compteur

```
┌─────────────────────────────────────────────┐
│  MACHINE INFO          C235 CHROMAX         │
��─────────────────────────────────────────────┤
│                                             │
│  Heures impression : 847h 18min   ✓ Synced │
│  Heures allumee    : 1204h 30min  ✓ Synced │
│  Filament total    : 12.4 km      ✓ Synced │
│  Changements outil : 18 420       ✓ Synced │
│  Coupes filament   : 4 800        ✓ Synced │
│                                             │
│  Derniere sync     : il y a 3 min          │
│  ID Machine        : AABBCCDDEEFF          │
│                                             │
│  ⓘ Ces compteurs sont proteges par le      │
│    serveur Yumi Lab et ne peuvent pas       │
│    etre reinitialises.                      │
│                                             │
│  [Historique maintenance]  [Infos garantie] │
���─────────────────────────────────────────────┘
```

### Impact garantie / SAV

Le compteur sert aussi a :
- **Garantie** : verifier les heures reelles si reclamation SAV
- **Revente** : acheteur peut verifier les heures via Yumi-ID (comme un compteur voiture)
- **Maintenance contractuelle** : alertes automatiques pour les clients pro
- **Analytics** : Yumi Lab sait combien ses machines tournent en moyenne

---

## Next steps

1. Definir le JSON schema formel (boards + products)
2. Coder le scanner MCU/TMC (Phase detection)
3. Coder les renderers par composant
4. Creer le panneau KlipperScreen pour le firstboot
5. Creer le module maintenance (compteurs + alertes)
6. Designer les SVG des boards et guides maintenance
7. Etendre yumi_sync.py avec le systeme compteurs
8. Ajouter endpoint /counters au serveur yumi-id
9. Valider avec le golden file C235 CHROMAX X12 7 YMS
