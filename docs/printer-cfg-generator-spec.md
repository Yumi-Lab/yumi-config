# Yumi C-Series — Générateur dynamique de printer.cfg

## Contexte

Yumi Lab fabrique les imprimantes 3D modulaires **Yumi C-Series** tournant sous **YumiOS** (Armbian + Klipper + Moonraker + Mainsail + KlipperScreen). Le firmware est 100% vanilla Klipper.

Le projet est géré dans le repo **`Yumi-Lab/yumi-config`** (GitHub, branche `main`), cloné sur chaque pad dans `~/yumi-config`. Ce repo gère déjà l'installation des configs Klipper, le QC factory, et les mises à jour via Moonraker update manager.

**Fichier de référence** : `C235 CHROMAX X12 7 YMS.cfg` — printer.cfg actuel pour la configuration C235 + Chroma X12 + HyperDrive 3P2L + 7 YMS.

---

## Problème

Les imprimantes C-Series sont modulaires :
- La **tête d'impression** est interchangeable via un connecteur DB9 qui remappe les I/O de la carte mère
- Le nombre de **YMS (Yumi Material System)** est variable (1 à 12), avec deux types (PRO et LITE)
- Plusieurs **MCU** peuvent être connectés simultanément (UART et/ou USB)
- Les versions de **carte mère** et de **SmartPad** peuvent différer
- De nouveaux modules hardware seront ajoutés à l'avenir

Le `printer.cfg` doit s'adapter automatiquement à la configuration hardware. Il doit rester un fichier **monolithique** (pas de `[include]` pour les parties générées).

---

## Architecture hardware

### SmartPad (Contrôleur — racine de l'arbre)
- **SmartPad V1** : SmartPi ONE (AllWinner H3, armhf, sun8i-h3)
- Futur : SmartPad V2 avec plus de ports/IO
- Détection automatique via CPU/board (`/etc/armbian-release`, `/proc/cpuinfo`)
- Variable d'environnement créée au build pour identifier le type
- Connecté aux MCU via UART et/ou USB

### Smart Maker Board (Carte mère imprimante, MCU principal UART0)
- **Versions** : 1.1, 1.2 (pinout et drivers TMC peuvent différer)
- Axes : X, Y, Z
- Extrudeurs intégrés : E0, E1
- **2 slots YMS LITE max** (pas assez d'I/O pour PRO)
- Connecteur DB9 : remappe des pins pour la tête d'impression
- Drivers TMC : dépendent de la version de carte

### HyperDrive 3P2L (Module d'extension extrudeurs)
- Même carte pour UART et USB
- **3 slots PRO** (entrées 1-3) : supportent YMS PRO et LITE
- **2 slots LITE** (entrées 4-5) : supportent uniquement YMS LITE
- Connexion au SmartPad : UART (bus partagé) ou USB (serial dédié)
- Plusieurs HyperDrive possibles par imprimante

### Têtes d'impression (via DB9)
- **Chroma X12** : tête multi-couleur (mélange de filaments, 12 entrées)
- **Direct Drive** : tête directe classique
- **Combo (futur)** : Chroma X12 + Direct Drive combinés
- Chaque tête a sa propre config : fans, thermistances, heater, nozzle
- Le DB9 remappe les I/O de la Smart Maker Board

### YMS — Yumi Material System
Boîtiers individuels de poussée filament, 1 par matériau/couleur.

**YMS PRO :**
- Moteur poussée filament (extrudeur Klipper)
- Plateau chauffant 24V (séchage filament)
- Sonde de température
- Ventilateur air chaud
- Se branche sur les slots PRO (1-3) d'un HyperDrive

**YMS LITE :**
- Moteur poussée filament uniquement
- Pas de système de chauffe
- Se branche sur n'importe quel slot (PRO ou LITE) d'un HyperDrive, ou sur la Smart Maker Board (max 2)

---

## Système de parentage bidirectionnel

Chaque produit dans le catalogue déclare :
- **`parent_types`** : à quoi il peut se connecter (vers le haut)
- **`child_types`** : ce qu'on peut lui brancher (vers le bas)
- **`connection_type`** : UART, USB, DB9, slot_pro, slot_lite

Cela permet de :
1. **Valider** la config utilisateur (empêcher un YMS PRO sur un slot LITE)
2. **Générer le plan d'architecture** visuel de l'imprimante
3. **Ajouter de nouveaux modules** sans modifier le code (juste le catalogue)
4. **Afficher uniquement les options possibles** dans l'UI KlipperScreen

### Arbre de connexion type (C235 + Chroma X12 + 7 YMS)

```
SmartPad V1 (SmartPi ONE)
├── Smart Maker Board 1.2 (UART0)
│   ├── Axe X, Y, Z
│   ├── E0, E1
│   ├── Tête Chroma X12 (DB9)
│   ├── YMS LITE #1 (slot intégré)
│   └── YMS LITE #2 (slot intégré)
└── HyperDrive #1 3P2L (UART)
    ├── Slot 1 (PRO) → YMS PRO #3
    ├── Slot 2 (PRO) → YMS PRO #4
    ├── Slot 3 (PRO) → YMS PRO #5
    ├── Slot 4 (LITE) → YMS LITE #6
    └── Slot 5 (LITE) → YMS LITE #7
```

---

## YUMI-LAB_product-catalog.json

Fichier monolithique unique contenant **tous les produits Yumi Lab** avec leurs attributs, variantes, et règles de parentage. Un seul endroit à maintenir.

### Structure

```json
{
  "version": "1.0.0",
  "products": {

    "smartpad": {
      "name": "SmartPad",
      "category": "controller",
      "is_root": true,
      "variants": {
        "v1": {
          "name": "SmartPad V1 (SmartPi ONE)",
          "detection": {
            "cpu": "sun8i-h3",
            "board": "smartpi1",
            "env_var": "SMARTPAD_VERSION"
          },
          "child_types": ["smart_maker", "hyperdrive"],
          "child_connections": {
            "smart_maker": {"type": "uart", "max": 1},
            "hyperdrive": {"type": ["uart", "usb"], "max": 2}
          }
        }
      },
      "parent_types": []
    },

    "smart_maker": {
      "name": "Smart Maker Board",
      "category": "mainboard",
      "variants": {
        "v1.1": {
          "name": "Smart Maker Board 1.1",
          "drivers": {"x": "tmc2209", "y": "tmc2209", "z": "tmc2209", "e0": "tmc2209", "e1": "tmc2209"},
          "pins": {
            "x_step": "PB13", "x_dir": "PB12", "x_enable": "PB14",
            "comment": "... pinout complet à remplir depuis le schéma"
          }
        },
        "v1.2": {
          "name": "Smart Maker Board 1.2",
          "drivers": {"x": "tmc2209", "y": "tmc2209", "z": "tmc2209", "e0": "tmc2209", "e1": "tmc2209"},
          "pins": {
            "comment": "... pinout complet — peut différer de v1.1"
          }
        }
      },
      "parent_types": ["smartpad"],
      "parent_connections": {"type": "uart"},
      "child_types": ["print_head", "yms"],
      "child_connections": {
        "print_head": {"type": "db9", "max": 1},
        "yms": {"type": "slot_lite", "max": 2, "accepts_variants": ["lite"]}
      },
      "klipper_config": {
        "axes": ["x", "y", "z"],
        "extruders": ["extruder", "extruder1"],
        "mcu": "mcu"
      }
    },

    "hyperdrive": {
      "name": "HyperDrive",
      "category": "extension",
      "variants": {
        "3p2l": {
          "name": "HyperDrive 3P2L",
          "slots": [
            {"id": 1, "type": "pro", "accepts": ["yms:pro", "yms:lite"]},
            {"id": 2, "type": "pro", "accepts": ["yms:pro", "yms:lite"]},
            {"id": 3, "type": "pro", "accepts": ["yms:pro", "yms:lite"]},
            {"id": 4, "type": "lite", "accepts": ["yms:lite"]},
            {"id": 5, "type": "lite", "accepts": ["yms:lite"]}
          ]
        }
      },
      "parent_types": ["smartpad"],
      "parent_connections": {"type": ["uart", "usb"]},
      "child_types": ["yms", "neopixel"],
      "klipper_config": {
        "mcu_prefix": "hyperdrive",
        "extruder_start_index": 2
      }
    },

    "print_head": {
      "name": "Tête d'impression",
      "category": "toolhead",
      "variants": {
        "chroma_x12": {
          "name": "Chroma X12",
          "description": "Tête multi-couleur 12 entrées",
          "klipper_config": {
            "heater": true,
            "fan_part": true,
            "fan_hotend": true,
            "thermistor": "ATC Semitec 104NT-4-R025H42G",
            "nozzle_sizes": [0.4, 0.6, 0.8],
            "max_temp": 300
          }
        },
        "direct_drive": {
          "name": "Direct Drive",
          "description": "Tête directe classique",
          "klipper_config": {
            "heater": true,
            "fan_part": true,
            "fan_hotend": true,
            "thermistor": "ATC Semitec 104NT-4-R025H42G",
            "nozzle_sizes": [0.4, 0.6, 0.8, 1.0],
            "max_temp": 300
          }
        },
        "combo": {
          "name": "Combo Chroma + Direct Drive",
          "description": "Futur — combine les deux systèmes",
          "status": "planned",
          "klipper_config": {}
        }
      },
      "parent_types": ["smart_maker"],
      "parent_connections": {"type": "db9"},
      "child_types": []
    },

    "yms": {
      "name": "YMS — Yumi Material System",
      "category": "feeder",
      "variants": {
        "pro": {
          "name": "YMS PRO",
          "description": "Poussée filament + plateau chauffant 24V + sonde température + ventilateur",
          "parent_slots": ["pro"],
          "klipper_config": {
            "extruder": true,
            "heater": {
              "type": "heater_generic",
              "max_temp": 80,
              "control": "pid"
            },
            "temperature_sensor": true,
            "fan": {
              "type": "heater_fan",
              "heater_temp": 40.0
            }
          }
        },
        "lite": {
          "name": "YMS LITE",
          "description": "Poussée filament uniquement",
          "parent_slots": ["pro", "lite"],
          "klipper_config": {
            "extruder": true
          }
        }
      },
      "parent_types": ["hyperdrive", "smart_maker"],
      "child_types": []
    },

    "neopixel": {
      "name": "NeoPixel LED Strip",
      "category": "accessory",
      "variants": {
        "ws2812": {
          "name": "WS2812 LED Strip",
          "klipper_config": {
            "type": "neopixel",
            "chain_count": 30,
            "color_order": "GRB"
          }
        }
      },
      "parent_types": ["hyperdrive", "smart_maker"],
      "child_types": []
    }
  }
}
```

---

## Config utilisateur

Fichier `yumi-printer-config.yaml` dans `~/printer_data/config/` :

```yaml
# Yumi C-Series Printer Configuration
printer_model: C235

smartpad:
  variant: v1

smart_maker:
  variant: v1.2
  connection: uart
  yms_slots:
    - {slot: 1, type: lite}
    - {slot: 2, type: lite}

print_head:
  variant: chroma_x12
  nozzle_size: 0.4
  nozzle_type: standard

hyperdrives:
  - name: hyperdrive1
    variant: 3p2l
    connection: uart
    serial: /dev/ttyS1
    slots:
      - {slot: 1, yms: pro}
      - {slot: 2, yms: pro}
      - {slot: 3, yms: pro}
      - {slot: 4, yms: lite}
      - {slot: 5, yms: lite}
```

---

## Script générateur

### `generate_printer_cfg.py`

```
~/yumi-config/
├── YUMI-LAB_product-catalog.json     # Catalogue produits (1 seul fichier)
├── templates/                         # Templates Jinja2 pour printer.cfg
│   ├── base.cfg.j2                   # Header, settings généraux
│   ├── mcu.cfg.j2                    # Déclaration MCU
│   ├── axes.cfg.j2                   # Steppers X/Y/Z
│   ├── extruder.cfg.j2              # Template par extrudeur
│   ├── yms_pro.cfg.j2              # Config YMS PRO (heater + fan + sensor)
│   ├── yms_lite.cfg.j2             # Config YMS LITE (extrudeur only)
│   ├── head_chroma_x12.cfg.j2     # Config tête Chroma X12
│   ├── head_direct_drive.cfg.j2   # Config tête Direct Drive
│   ├── macros.cfg.j2               # Macros (T0-T12, etc.)
│   └── bed.cfg.j2                  # Bed mesh, heater bed
├── generate_printer_cfg.py           # Script générateur
└── docs/
    └── printer-cfg-generator-spec.md  # Ce document
```

### Flow de génération

```
1. Lire YUMI-LAB_product-catalog.json (définitions produits)
2. Lire yumi-printer-config.yaml (config utilisateur)
3. Valider la config :
   - Chaque module a un parent valide
   - Les slots respectent les contraintes PRO/LITE
   - Pas de connexion impossible
4. Construire l'arbre de modules
5. Pour chaque module dans l'arbre :
   - Charger le template Jinja2 correspondant
   - Remplir avec les données du catalogue + config utilisateur
   - Numéroter les extrudeurs séquentiellement (E0, E1, E2...)
6. Assembler le printer.cfg monolithique :
   - Header + settings
   - MCU(s)
   - Axes
   - Tête d'impression
   - Extrudeurs (Smart Maker + HyperDrive + YMS)
   - Heaters/fans YMS PRO
   - Macros (T0-TN selon nombre de YMS)
7. Écrire ~/printer_data/config/printer.cfg
8. Optionnel : backup de l'ancien printer.cfg
```

### Validation des connexions

Le générateur DOIT refuser les configs invalides :
- YMS PRO sur un slot LITE → erreur
- YMS sur Smart Maker au-delà de 2 → erreur
- Plus de 2 HyperDrive sur un SmartPad V1 → erreur
- Tête combo sur un Smart Maker V1.1 (si pas supporté) → erreur
- HyperDrive sans SmartPad → erreur

### Panel KlipperScreen

- Même pattern que QC Wizard (`qc_wizard.py` / `qc_engine.py`)
- L'arbre se construit dynamiquement depuis le catalogue
- Les options impossibles sont grisées (parentage invalide)
- Preview du printer.cfg avant application
- Bouton "Generate & Apply" → génère + FIRMWARE_RESTART

---

## Extensibilité

Pour ajouter un nouveau produit (ex: SmartPad V2, nouveau HyperDrive 5P3L) :

1. Ajouter l'entrée dans `YUMI-LAB_product-catalog.json`
2. Créer le template Jinja2 si nécessaire
3. C'est tout — le générateur et l'UI s'adaptent automatiquement

Pour ajouter une nouvelle variante d'un produit existant (ex: YMS Ultra) :

1. Ajouter la variante dans la section `variants` du produit dans le catalogue
2. Définir `parent_slots`, `klipper_config`
3. Créer le template si la config Klipper est différente

---

## Contraintes techniques

- Fichier `printer.cfg` monolithique (pas de `[include]` pour les parties générées)
- 100% compatible Klipper vanilla (pas de fork)
- Tourne sur SmartPi ONE (armhf, 1GB RAM)
- Python 3.11+ (Bookworm) ou 3.13+ (Trixie)
- Dépendances : `jinja2`, `pyyaml` (à installer dans un venv ou system)
- Géré dans `Yumi-Lab/yumi-config`, déployé via Moonraker update manager
