#!/usr/bin/env python3
"""
Genere qc_printer_YMS12.cfg (banc QC YMS 12 boitiers) depuis qc_printer_C235.cfg.

Banc = une C235 de test (capteur tete !PA8, YMS-1/YMS-2 sur la carte
principale) + 2 HYPERDRIVE 3P2L (carte SMART MAKER 1.X sans moteurs XYZ) :
  - hyperdrive_uart (/dev/ttyS2)   -> extruder2..extruder6  = YMS-3..YMS-7
  - hyperdrive_usb  (/dev/ttyACM0) -> extruder7..extruder11 = YMS-8..YMS-12

Pins des slots = celles validees en prod sur la C235 CHROMAX X12 7YMS
(backup 172.20.10.3) : dir/enable inverses, uart PB14/PA7/PB15/PC11/PC10,
capteurs PA13/PB9/PC13/PC14/PB7. L'hyperdrive USB est la MEME carte que
l'UART -> memes pins, seule la connexion (serial) change.

La cfg C235 reste la SOURCE UNIQUE : ce script ne fait qu'inserer les
sections hyperdrive et generaliser les macros feed a TOOL=1..12.

Usage :  python3 generate_yms12_cfg.py   (depuis yumi-config/qc/)
"""
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, "qc_printer_C235.cfg")
DST = os.path.join(HERE, "qc_printer_YMS12.cfg")

# Les 5 slots d'une HYPERDRIVE 3P2L (pins carte SMART MAKER 1.X, ordre
# physique slot 1..5). sensor = pin du yumi_motion_sensor du slot.
SLOTS = [
    dict(step="PB12", dir="PB10", en="PB13", uart="PB14", sensor="PA13"),
    dict(step="PA5",  dir="PA4",  en="PA6",  uart="PA7",  sensor="PB9"),
    dict(step="PB1",  dir="PB0",  en="PA15", uart="PB15", sensor="PC13"),
    dict(step="PA1",  dir="PA0",  en="PC5",  uart="PC11", sensor="PC14"),
    dict(step="PC3",  dir="PC2",  en="PC4",  uart="PC10", sensor="PB7"),
]

# (nom mcu, port serie, premier index extruder)
# ATTENTION -- hyperdrive_usb : /dev/ttyACM0 est un chemin RAW qui DERIVE
# (le kernel peut l'enumerer en ttyACM1 selon l'ordre d'attache USB au
# boot -- vecu plusieurs fois en 08/2026, Klipper refuse alors de demarrer :
# "Unable to open serial port... No such file or directory"). Un chemin
# stable existe (/dev/serial/by-id/usb-Klipper_stm32f401xc_<serial>-if00),
# mais <serial> est PROPRE A CHAQUE CARTE PHYSIQUE -> impossible a mettre
# ici (ce generateur produit UN SEUL cfg partage par tous les bancs). Apres
# TOUT deploiement qui ecrase printer.cfg en entier (pas seulement
# sync_qc_cfgs.sh, qui ne touche jamais printer.cfg), patcher a la main sur
# CHAQUE banc : `ls /dev/serial/by-id/` puis remplacer la ligne
# `serial: /dev/ttyACM0` sous [mcu hyperdrive_usb] par le chemin trouve,
# PUIS redemarrer klipper. Oublier cette etape = le prochain reboot qui
# fait deriver l'enumeration recree le meme plantage.
HYPERDRIVES = [
    ("hyperdrive_uart", "/dev/ttyS2", 2),
    ("hyperdrive_usb", "/dev/ttyACM0", 7),
]

HEADER = """\
#####################################################################
# YUMI QC — printer.cfg dédié BANC QC YMS (12 boîtiers)
# GÉNÉRÉ depuis qc_printer_C235.cfg par qc/generate_yms12_cfg.py
# — NE PAS éditer à la main.
#
# Banc = une C235 de test + 2 HYPERDRIVE 3P2L :
#   YMS-1/YMS-2   -> carte principale (extruder0/1)
#   YMS-3..YMS-7  -> hyperdrive_uart /dev/ttyS2   (extruder2..6)
#   YMS-8..YMS-12 -> hyperdrive_usb  /dev/ttyACM0 (extruder7..11)
# Test par boîtier : QC_HEAD_FEED TOOL=n (n=1..12) — feed jusqu'au
# capteur tête !PA8, valide le motion sensor, rétracte TOUT (le tube
# partagé doit être vide pour le YMS suivant). Pas de chauffe.
#
# MAPPING YMS ↔ extruder (TOOL=n → extruder(n-1) → signal E(n-1)_HEAD) :
#   YMS-1  = extruder0  (carte principale E0)   YMS-7  = extruder6  (UART slot 5)
#   YMS-2  = extruder1  (carte principale E1)   YMS-8  = extruder7  (USB  slot 1)
#   YMS-3  = extruder2  (UART slot 1)           YMS-9  = extruder8  (USB  slot 2)
#   YMS-4  = extruder3  (UART slot 2)           YMS-10 = extruder9  (USB  slot 3)
#   YMS-5  = extruder4  (UART slot 3)           YMS-11 = extruder10 (USB  slot 4)
#   YMS-6  = extruder5  (UART slot 4)           YMS-12 = extruder11 (USB  slot 5)
#####################################################################
"""


def replace(text, old, new, count):
    """Remplacement strict : le motif doit apparaitre exactement count fois."""
    found = text.count(old)
    if found != count:
        sys.exit("ERREUR: motif trouve %d fois (attendu %d):\n%s" % (found, count, old))
    return text.replace(old, new)


def mcu_sections():
    out = []
    for name, serial, first in HYPERDRIVES:
        n0, n1 = first + 1, first + len(SLOTS)
        out.append("""\
[mcu %s]
serial: %s            #HYPERDRIVE 3P2L (SMART MAKER 1.X) — YMS-%d..YMS-%d
restart_method: command
""" % (name, serial, n0, n1))
    return "\n".join(out)


def extruder_sections():
    out = []
    for name, _serial, first in HYPERDRIVES:
        for i, s in enumerate(SLOTS):
            e = first + i
            out.append("""\
# ── YMS-%(yms)d = extruder%(e)d — moteur feeder (%(m)s slot %(slot)d) ──
[extruder_stepper extruder%(e)d]
extruder:
step_pin: %(m)s:%(step)s
dir_pin: !%(m)s:%(dir)s
enable_pin: !%(m)s:%(en)s
microsteps: 16
rotation_distance: 22.6789511   #MK12 (BMG 50:17)
gear_ratio: 50:17
full_steps_per_rotation: 200
pressure_advance: 0.0

[tmc2209 extruder_stepper extruder%(e)d]
uart_pin: %(m)s:%(uart)s
interpolate: True
run_current: 1.2
hold_current: 0.300
stealthchop_threshold: 0
""" % dict(s, e=e, m=name, yms=e + 1, slot=i + 1))
    return "\n".join(out)


def sensor_sections():
    out = []
    for name, _serial, first in HYPERDRIVES:
        for i, s in enumerate(SLOTS):
            yms = first + i + 1
            out.append("""\
# ── YMS-%(y)d : capteur motion (feeder = extruder%(e)d, %(m)s slot %(slot)d) ──
# filament_yumi_smart_motion_sensor (PAS le filament_motion_sensor standard)
# mode=free (28/08, PAS hold) : mesure de pitch en STANDBY (cf. discussion --
# 2 plantages reels le meme jour). mode=hold reste risque MEME SANS
# pitch_view : sa branche RETRACTION fait un respond_info() a CHAQUE tick en
# arriere de facon INCONDITIONNELLE (pas de garde blockage_detection/
# pitch_view dessus) -- or le sweep stress recule sur 8 segments/16 (sens
# alterne) + le retrait final -300mm, ce qui a refait planter l'hote
# (Timer too close) meme apres avoir retire pitch_view. mode=free ne logge
# RIEN (juste le suivi filament present/absent) -- repasser en mode=hold
# seulement avec une vraie solution de batching (ticket pitch en attente).
[filament_yumi_smart_motion_sensor YMS-%(y)d]
switch_pin: %(m)s:%(p)s
detection_length: 10
pause_on_runout: False
extruder: extruder
event_delay: 0.5
mode: free
runout_gcode:
    {action_respond_info("QC: YMS-%(y)d encoder dropout E=%%.1f" %% printer.motion_report.live_position[3])}
""" % dict(y=yms, m=name, p=s["sensor"], e=yms - 1, slot=i + 1))
    return "\n".join(out)


# YMS Pro (plateau chauffant + sonde temperature integres) : seulement les
# 3 PREMIERS slots de chaque hyperdrive (YMS-3/4/5 et YMS-8/9/10) sont
# cables pour la chauffe -- slots 4/5 (YMS-6/7/11/12) n'ont pas cette
# option. Meme carte mere (SMART MAKER 1.X) que la principale, sur les 3
# canaux chauffe standard du board, heater+sensor du MEME canal ensemble
# (HE0+TH0, HBED+THB, HE1+TH1 -- validé sur banc réel le 23/08 : YMS-3
# HE0+TH0 nickel d'emblée, YMS-4/5 avaient le chauffage croisé jusqu'à
# ce fix) -- reassignes ici a un YMS chacun, jamais utilises par ailleurs
# sur les hyperdrives (la C235 de base n'utilise QUE HE0/TH0 pour son
# extruder et HBED/THB pour son lit, sur la carte PRINCIPALE -- ces
# canaux sont donc entierement libres sur hyperdrive_uart/hyperdrive_usb).
# sensor_type aligne sur l'usage connu du meme canal sur la carte
# principale (TH0 = 100K4190YUMI comme l'extruder, THB = 100K3950YUMI
# comme le heater_bed) ; TH1 n'a pas de precedent dans la cfg de base
# (jamais utilise) -- meme famille que TH0 par convention de nommage
# carte, confirme sur banc reel (voir commit 23/08). PID Kp/Ki/Kd =
# valeurs GENERIQUES de depart (non calibrees) -- PID_CALIBRATE a
# lancer une fois le materiel reel disponible.
# Valeurs corrigees A LA MAIN par Nicolas en direct sur le banc le 24-25/08
# (printer.cfg), recuperees ici comme source de verite -- PAS un simple
# echange de paire heater+sensor comme la 1ere hypothese : le cablage reel
# melange les canaux independamment (heater et sensor NE viennent PAS du
# meme "canal" carte pour les slots 2/3), et le ventilateur tourne sur les
# 3 slots (pas juste 2/3). Reconstituee en interrogeant configfile.settings
# sur le Klipper reellement en cours (pas juste relu le fichier).
HEAT_SLOTS = [
    dict(heater="PC8", sensor="PC1", sensor_type="100K4190YUMI", fan="PC6"),
    dict(heater="PC9", sensor="PA3", sensor_type="100K4190YUMI", fan="PC7"),
    dict(heater="PB6", sensor="PC0", sensor_type="100K3950YUMI", fan="PA2"),
]


def heater_sections():
    out = []
    for name, _serial, first in HYPERDRIVES:
        for i, h in enumerate(HEAT_SLOTS):
            yms = first + i + 1
            out.append("""\
# ── YMS-%(y)d : plateau chauffant + sonde (%(m)s slot %(slot)d, YMS Pro) ──
[heater_generic YMS-%(y)d-heater]
heater_pin: %(m)s:%(heater)s
sensor_type: %(sensor_type)s
sensor_pin: %(m)s:%(sensor)s
max_power: 1.0
control: pid
pid_Kp: 50
pid_Ki: 50
pid_Kd: 50
min_temp: -50
max_temp: 110

[heater_fan YMS-%(y)d-fan]
pin: %(m)s:%(fan)s
max_power: 1
off_below: 0.31
heater: YMS-%(y)d-heater
heater_temp: 25
shutdown_speed: 0

[verify_heater YMS-%(y)d-heater]
max_error: 180000
check_gain_time: 3000
hysteresis: 10
heating_gain: 2
""" % dict(h, y=yms, m=name, slot=i + 1))
    return "\n".join(out)


def autotune_sections():
    out = []
    for name, _serial, first in HYPERDRIVES:
        for i in range(len(SLOTS)):
            out.append("""\
[autotune_tmc extruder_stepper extruder%d]
motor: BJ42D07-06V02
tuning_goal: auto #silent
toff: 1
tbl: 1
voltage: 24
sg4_thrs: 150
extra_hysteresis: 2
pwm_freq_target: 60000
""" % (first + i))
    return "\n".join(out)


def main():
    with open(SRC) as f:
        cfg = f.read()

    # 1) En-tete + modele du marqueur QC
    cfg = re.sub(r"\A#{5,}\n.*?#{5,}\n", HEADER, cfg, count=1, flags=re.S)
    cfg = replace(cfg, 'variable_model: "C235"', 'variable_model: "YMS12"', 1)

    # 2) Autotune TMC des 10 extrudeurs hyperdrive (avant [idle_timeout])
    cfg = replace(cfg, "[idle_timeout]", autotune_sections() + "\n[idle_timeout]", 1)

    # 3) MCU hyperdrives (apres le host SmartPiOne)
    cfg = replace(
        cfg,
        "[mcu SmartPiOne]\nserial: /tmp/klipper_host_mcu\n",
        "[mcu SmartPiOne]\nserial: /tmp/klipper_host_mcu\n\n" + mcu_sections(),
        1)

    # 4) extruder2..11 + TMC (apres le bloc extruder1, avant [heater_bed])
    cfg = replace(cfg, "\n[heater_bed]", "\n" + extruder_sections() + "\n[heater_bed]", 1)

    # Liaison YMS <-> extruder en commentaire sur les sections de la base C235
    cfg = replace(cfg, "[extruder_stepper extruder0]",
                  "# ── YMS-1 = extruder0 — moteur feeder (carte principale E0) ──\n"
                  "[extruder_stepper extruder0]", 1)
    cfg = replace(cfg, "[extruder_stepper extruder1]",
                  "# ── YMS-2 = extruder1 — moteur feeder (carte principale E1) ──\n"
                  "[extruder_stepper extruder1]", 1)
    cfg = replace(cfg, "[filament_motion_sensor YMS-1]",
                  "# ── YMS-1 : capteur motion (feeder = extruder0, carte principale) ──\n"
                  "[filament_motion_sensor YMS-1]", 1)
    cfg = replace(cfg, "[filament_motion_sensor YMS-2]",
                  "# ── YMS-2 : capteur motion (feeder = extruder1, carte principale) ──\n"
                  "[filament_motion_sensor YMS-2]", 1)

    # 5) Capteurs YMS-3..YMS-12 (avant le capteur tete) + chauffe YMS Pro
    #    (YMS-3/4/5 et YMS-8/9/10 seulement -- slots 4/5 sans cette option)
    cfg = replace(
        cfg,
        "[filament_switch_sensor head_sensor]",
        sensor_sections() + "\n" + heater_sections()
        + "\n[filament_switch_sensor head_sensor]",
        1)

    # 6) QC_HEAD_FEED : selection d'outil generique TOOL=1..12
    cfg = replace(
        cfg,
        "description: QC - Feed YMS + valide motion sensor + capteur tete (TOOL=1 ou 2)",
        "description: QC - Feed YMS + valide motion sensor + capteur tete (TOOL=1..12)",
        1)
    cfg = replace(
        cfg,
        """    {% set tool = params.TOOL|default(1)|int %}
    {% if tool == 2 %}
        {% set id = "E1_HEAD" %}{% set feeder = "extruder1" %}{% set sensor = "YMS-2" %}
    {% else %}
        {% set id = "E0_HEAD" %}{% set feeder = "extruder0" %}{% set sensor = "YMS-1" %}
    {% endif %}""",
        """    {% set tool = params.TOOL|default(1)|int %}
    {% set id = "E" ~ (tool - 1) ~ "_HEAD" %}
    {% set feeder = "extruder" ~ (tool - 1) %}
    {% set sensor = "YMS-" ~ tool %}""",
        1)

    # Activation directe du feeder cible (T0 juste avant a tout desync)
    cfg = replace(
        cfg,
        "    {% if tool == 2 %}T2{% else %}T1{% endif %}\n",
        "    SYNC_EXTRUDER_MOTION EXTRUDER={feeder} MOTION_QUEUE=extruder\n",
        1)

    # Budget de feed reglable depuis le panel/console : QC_HEAD_FEED MAXD=...
    # (banc : tube YMS -> merger 12 entrees -> tete > 800mm, mesure ~900)
    cfg = replace(
        cfg,
        "    _QC_HEAD_FEED FEEDER={feeder} ID={id} SENSOR={sensor} MODE=feed\n",
        "    _QC_HEAD_FEED FEEDER={feeder} ID={id} SENSOR={sensor} MODE=feed "
        "MAXD={params.MAXD|default(900)|int}\n",
        1)

    # Banc = motion seulement, jamais de chauffe : une thermistance debranchee
    # (tete manipulee en permanence sur le banc) ne doit pas bloquer le feed.
    cfg = replace(cfg, "min_extrude_temp: 0\n", "min_extrude_temp: -100\n", 1)

    # Le pad du banc chauffe (usine, ete, KlipperScreen) : le capteur host a
    # 100C declenche des shutdowns en pleine sequence (vecu : 100.3C -> faux
    # FAIL thermique). L'Allwinner H3 throttle tout seul bien avant sa limite
    # de jonction -> 110C sur le banc, la vraie parade reste la ventilation.
    cfg = replace(
        cfg,
        "[temperature_sensor NanoPi]\nsensor_type: temperature_host\nmin_temp: 0\nmax_temp: 100\n",
        "[temperature_sensor NanoPi]\nsensor_type: temperature_host\nmin_temp: 0\nmax_temp: 110\n",
        1)

    # Micro-decrochages de l'encodeur (glissiere qui se repositionne a sa
    # butee a l'inversion de sens) : logges sur YMS-1/2 aussi (les capteurs
    # generes des hyperdrives l'ont deja). Logge, PAS eliminatoire — le FAIL
    # ne tombe que si le capteur est encore decroche en FIN de poussee.
    for yms, pin in ((1, "!PC14"), (2, "PC13")):
        old_block = ("[filament_motion_sensor YMS-%d]\nswitch_pin: %s\n"
                     "detection_length: 10\npause_on_runout: False\n"
                     "extruder: extruder\nevent_delay: 0.5\n" % (yms, pin))
        cfg = replace(
            cfg,
            old_block,
            old_block + "mode: free\n"
            "runout_gcode:\n"
            "    {action_respond_info(\"QC: YMS-%d decrochage encodeur E=%%.1f\""
            " %% printer.motion_report.live_position[3])}\n" % yms,
            1)

    # Isolation du capteur teste : pendant un run, TOUS les motion sensors
    # sont coupes puis seul celui du YMS teste est arme, APRES le reset
    # (l'extrusion a vide du reset ne genere ainsi aucun faux "decrochage").
    cfg = replace(
        cfg,
        """    T0
    SET_FILAMENT_SENSOR SENSOR={sensor} ENABLE=1
    M83
    G92 E0
    G1 E150 F6000
    M400
""",
        """    T0
    {% for name in printer %}
      {% if name.startswith("filament_motion_sensor ") %}
        SET_FILAMENT_SENSOR SENSOR={name.split(" ", 1)[1]} ENABLE=0
      {% endif %}
    {% endfor %}
    M83
    G92 E0
    G1 E150 F6000
    M400
    SET_FILAMENT_SENSOR SENSOR={sensor} ENABLE=1
""",
        1)

    # ---- PHASE STRESS (banc uniquement) -------------------------------------
    # Apres la detection tete : recul 150mm puis allers-retours ±20mm a
    # vitesse croissante puis decroissante (accel/decel reelles). A CHAQUE
    # segment on verifie que le motion sensor suit (filament_detected reste
    # vrai apres un push) -> valide l'encodeur en conditions dynamiques,
    # pas seulement au chargement. Chaque segment est logge (repris dans le
    # rapport QC par l'engine).
    cfg = replace(
        cfg,
        'variable_mode: "feed"\n',
        'variable_mode: "feed"\nvariable_phase: "feed"\nvariable_seg: 0\n',
        1)
    cfg = replace(
        cfg,
        "    SET_GCODE_VARIABLE MACRO=_QC_HEAD_FEED VARIABLE=yms_seen VALUE=0\n",
        "    SET_GCODE_VARIABLE MACRO=_QC_HEAD_FEED VARIABLE=yms_seen VALUE=0\n"
        "    SET_GCODE_VARIABLE MACRO=_QC_HEAD_FEED VARIABLE=phase VALUE=\"'feed'\"\n"
        "    SET_GCODE_VARIABLE MACRO=_QC_HEAD_FEED VARIABLE=seg VALUE=0\n",
        1)

    STRESS = """    {% if v.phase == "stress" %}
        {% set seg = v.seg|int %}
        {% set speeds = [600, 2400, 4800] %}
        {% set nseg = speeds|length * 2 %}
        {% if seg > 0 %}
            {action_respond_info("QC %s: stress %d/%d detected=%s" % (id, seg, nseg, yms))}
        {% endif %}
        {% if seg > 0 and (seg % 2) == 1 and not yms %}
            RESPOND TYPE=error MSG="QC {id}: motion sensor {sensor} a PERDU le suivi au segment {seg}/{nseg}"
            M83
            SYNC_EXTRUDER_MOTION EXTRUDER={feeder} MOTION_QUEUE=extruder
            G1 E-{[pushed - 150, 0]|max} F1200
            M400
            SYNC_EXTRUDER_MOTION EXTRUDER={feeder} MOTION_QUEUE=
            RESPOND MSG="QC:{id}:FAIL"
        {% elif seg >= nseg %}
            {action_respond_info("QC %s: stress OK — %d segments ±20mm (10→40→80mm/s), suivi capteur permanent" % (id, nseg))}
            M83
            SYNC_EXTRUDER_MOTION EXTRUDER={feeder} MOTION_QUEUE=extruder
            G1 E-{[pushed - 150, 0]|max} F1200
            M400
            SYNC_EXTRUDER_MOTION EXTRUDER={feeder} MOTION_QUEUE=
            RESPOND MSG="QC:{id}:PASS"
        {% else %}
            {% set spd = speeds[(seg // 2) % speeds|length] %}
            {% set dist = 20 if seg % 2 == 0 else -20 %}
            M83
            SYNC_EXTRUDER_MOTION EXTRUDER={feeder} MOTION_QUEUE=extruder
            G1 E{dist} F{spd}
            M400
            SET_GCODE_VARIABLE MACRO=_QC_HEAD_FEED VARIABLE=seg VALUE={seg + 1}
            UPDATE_DELAYED_GCODE ID=_qc_head_step DURATION=0.3
        {% endif %}
    {% elif head %}
"""
    cfg = replace(cfg, "    {% if head %}\n", STRESS, 1)

    # Detection tete (banc) : on passe en phase stress au lieu de conclure
    cfg = replace(
        cfg,
        """        {% elif v.yms_seen|int == 1 %}
            {action_respond_info("QC %s: filament a la tete apres %dmm + motion sensor %s OK" % (id, pushed, sensor))}
            # Une fois detecte, 20cm de retraction suffit (pas besoin de tout sortir)
            M83
            G1 E-{[pushed, 200]|min} F1200
            M400
            SYNC_EXTRUDER_MOTION EXTRUDER={feeder} MOTION_QUEUE=
            RESPOND MSG="QC:{id}:PASS"
""",
        """        {% elif v.yms_seen|int == 1 %}
            {% if bench %}
                {action_respond_info("QC %s: filament a la tete apres %dmm + motion sensor %s OK -> stress aller-retour" % (id, pushed, sensor))}
                M83
                SYNC_EXTRUDER_MOTION EXTRUDER={feeder} MOTION_QUEUE=extruder
                G1 E-250 F1200
                M400
                SET_GCODE_VARIABLE MACRO=_QC_HEAD_FEED VARIABLE=phase VALUE="'stress'"
                SET_GCODE_VARIABLE MACRO=_QC_HEAD_FEED VARIABLE=seg VALUE=0
                UPDATE_DELAYED_GCODE ID=_qc_head_step DURATION=0.3
            {% else %}
                {action_respond_info("QC %s: filament a la tete apres %dmm + motion sensor %s OK" % (id, pushed, sensor))}
                M83
                G1 E-{[pushed, 200]|min} F1200
                M400
                SYNC_EXTRUDER_MOTION EXTRUDER={feeder} MOTION_QUEUE=
                RESPOND MSG="QC:{id}:PASS"
            {% endif %}
""",
        1)

    # 7) T0 : desync TOUS les extruder_steppers (dynamique — la casse des noms
    #    n'est fiable qu'en iterant printer, pas configfile.settings)
    cfg = replace(
        cfg,
        """gcode:
    SYNC_EXTRUDER_MOTION EXTRUDER=extruder0 MOTION_QUEUE=
    SYNC_EXTRUDER_MOTION EXTRUDER=extruder1 MOTION_QUEUE=

[gcode_macro T1]""",
        """gcode:
    {% for name in printer %}
      {% if name.startswith("extruder_stepper ") %}
        SYNC_EXTRUDER_MOTION EXTRUDER={name.split(" ")[1]} MOTION_QUEUE=
      {% endif %}
    {% endfor %}

[gcode_macro T1]""",
        1)

    # 8) QC_CLEANUP : coupe tous les capteurs + desync tous les feeders
    cfg = replace(
        cfg,
        """    SET_FILAMENT_SENSOR SENSOR=YMS-1 ENABLE=0
    SET_FILAMENT_SENSOR SENSOR=YMS-2 ENABLE=0
    SYNC_EXTRUDER_MOTION EXTRUDER=extruder0 MOTION_QUEUE=
    SYNC_EXTRUDER_MOTION EXTRUDER=extruder1 MOTION_QUEUE=
""",
        """    {% for name in printer %}
      {% if name.startswith("filament_motion_sensor ") %}
        SET_FILAMENT_SENSOR SENSOR={name.split(" ", 1)[1]} ENABLE=0
      {% elif name.startswith("extruder_stepper ") %}
        SYNC_EXTRUDER_MOTION EXTRUDER={name.split(" ")[1]} MOTION_QUEUE=
      {% endif %}
    {% endfor %}
""",
        1)

    # 9) Retract : sur le banc (modele YMS*) on retracte TOUT le feed pour
    #    vider le tube partage avant le YMS suivant (les 12 convergent dans
    #    le meme merger). Les cfg C-series gardent min(pushed, 200).
    cfg = replace(
        cfg,
        '    {% set pushed = v.pushed|int %}\n',
        '    {% set pushed = v.pushed|int %}\n'
        '    {% set bench = (printer["gcode_macro _QC_MODE"].model|default("")|string).startswith("YMS") %}\n',
        1)
    cfg = replace(
        cfg,
        "            G1 E-{[pushed, 200]|min} F1200\n",
        "            G1 E-{pushed if bench else ([pushed, 200]|min)} F1200\n",
        2)

    # Regle feed durcie (banc) : le feed est UNIDIRECTIONNEL -> une fois la
    # glissiere plaquee (1ere detection), le capteur ne doit PLUS decrocher.
    # Un decrochage en cours de feed = capteur qui s'arrete = FAIL immediat.
    cfg = replace(
        cfg,
        """    {% else %}
        M83
        SYNC_EXTRUDER_MOTION EXTRUDER={feeder} MOTION_QUEUE=extruder
        G1 E{v.chunk|int} F600
""",
        """    {% elif v.yms_seen|int == 1 and not yms %}
        RESPOND TYPE=error MSG="QC {id}: motion sensor {sensor} a CESSE de suivre pendant le feed (a {pushed}mm)"
        M83
        SYNC_EXTRUDER_MOTION EXTRUDER={feeder} MOTION_QUEUE=extruder
        G1 E-{pushed if bench else ([pushed, 200]|min)} F1200
        M400
        SYNC_EXTRUDER_MOTION EXTRUDER={feeder} MOTION_QUEUE=
        RESPOND MSG="QC:{id}:FAIL"
    {% else %}
        M83
        SYNC_EXTRUDER_MOTION EXTRUDER={feeder} MOTION_QUEUE=extruder
        G1 E{v.chunk|int} F600
""",
        1)

    cfg += "\n" + parallel_stress_macros()

    # Bascule TOUS les capteurs YMS (1..12, y compris les 2 herites de la
    # base C235 + le lookup generique de qc_macros.cfg inline) sur le VRAI
    # module Xtrack33 (Nicolas 28/08) -- filament_motion_sensor standard
    # n'a aucune notion de pitch. "filament_motion_sensor" n'apparait dans
    # ce fichier QUE pour ces sensors YMS (le capteur tete est un
    # filament_switch_sensor, nom distinct) -- remplacement global sans
    # ambiguite, y compris les 2 boucles `name.startswith(...)`
    # (isolation avant test + QC_CLEANUP) qui doivent suivre le nouveau nom.
    cfg = cfg.replace("filament_motion_sensor", "filament_yumi_smart_motion_sensor")

    with open(DST, "w") as f:
        f.write(cfg)
    print("OK ->", DST)


def parallel_stress_macros():
    """PHASE 1 (QC_HEAD_FEED_REACH, par YMS, séquentiel — le capteur tête !PA8
    est UNIQUE et partagé par les 12, impossible d'attribuer une détection
    tête à la bonne position si 2+ poussent en même temps) + PHASE 2
    (QC_STRESS_ALL, TOUS les YMS déjà en tête ensemble — chaque motion sensor
    YMS-n a sa PROPRE pin, donc son suivi reste correctement attribué même
    en mouvement groupé) : diminue le temps total (~31min séquentiel pur
    aujourd'hui -> ~15-17min, la sweep stress ne coûte plus qu'1x au lieu
    de 12x) SANS changer le protocole de test (mêmes vitesses/segments,
    mêmes seuils de fail) — demande du 22/08.

    N'existait pas dans la cfg C235 de base : macros ENTIÈREMENT NOUVELLES
    (pas de replace() sur le texte source). QC_HEAD_FEED/_QC_HEAD_FEED
    d'origine restent INTACTES (utilisées par le re-test unitaire d'un seul
    boîtier, qui n'a aucune raison d'être parallélisé)."""
    return """\
# =====================================================================
# BANC YMS — SWEEP STRESS PARALLÉLISÉE (v2, 22/08) — voir generate_yms12_cfg.py
# =====================================================================

[gcode_macro _QC_YMS_STATE]
description: QC banc - Etat persistant phase1->phase2 (distance poussee + eligibilite stress par position). Remis a zero par CHAQUE position au debut de SA PROPRE phase 1 -> pas de residu inter-run possible pour une position qui tourne vraiment.
""" + "".join(
        "variable_pushed_%d: 0\nvariable_ok_%d: 0\n" % (n, n) for n in range(1, 13)
    ) + """\
gcode:
    # macro porte-etat, jamais appelee directement

[gcode_macro TALL]
description: QC banc — PRATIQUE (test manuel console) : synchronise les 12 extruder_steppers EN MEME TEMPS sur la queue E (preuve directe que SYNC_EXTRUDER_MOTION accepte plusieurs steppers a la fois -- validee le 22/08). Aucun garde-fou ok_n : usage MANUEL uniquement, jamais appelee par le wizard.
gcode:
""" + "".join(
        "    SYNC_EXTRUDER_MOTION EXTRUDER=extruder%d MOTION_QUEUE=extruder\n" % n
        for n in range(12)
    ) + """\

[gcode_macro TNONE]
description: QC banc — PRATIQUE (test manuel console) : desynchronise les 12 extruder_steppers (contrepartie de TALL).
gcode:
""" + "".join(
        "    SYNC_EXTRUDER_MOTION EXTRUDER=extruder%d MOTION_QUEUE=\n" % n
        for n in range(12)
    ) + """\

[gcode_macro QC_LOAD_ALL]
description: QC banc — CHARGE tous les TOOL= EN MEME TEMPS (synchronises), d'une distance FIXE (defaut 300mm), SANS viser le capteur tete (retire du protocole banc le 23/08 -- ne validait que le chemin tube du banc, pas le YMS lui-meme). Valide juste que chaque feeder + son motion sensor bougent. TOOLS=1,2,3,... DIST=300 (mm, optionnel)
gcode:
    {% set tools = params.TOOLS.split(",")|map("int")|list %}
    RESPOND MSG="QC:LOAD_ALL:START"
    _QC_BOARD_FAN_ON
    T0
    {% for t in tools %}
        SET_GCODE_VARIABLE MACRO=_QC_YMS_STATE VARIABLE=ok_{t} VALUE=0
        SET_GCODE_VARIABLE MACRO=_QC_YMS_STATE VARIABLE=pushed_{t} VALUE=0
        SET_FILAMENT_SENSOR SENSOR=YMS-{t} ENABLE=1
        SYNC_EXTRUDER_MOTION EXTRUDER=extruder{t - 1} MOTION_QUEUE=extruder
    {% endfor %}
    SET_GCODE_VARIABLE MACRO=_QC_LOAD_ALL_STEP VARIABLE=tools VALUE="{tools}"
    SET_GCODE_VARIABLE MACRO=_QC_LOAD_ALL_STEP VARIABLE=pushed VALUE=0
    SET_GCODE_VARIABLE MACRO=_QC_LOAD_ALL_STEP VARIABLE=dist VALUE={params.DIST|default(300)|int}
    UPDATE_DELAYED_GCODE ID=_qc_load_all_step DURATION=0.3

[gcode_macro _QC_LOAD_ALL_STEP]
description: QC banc - Etat de la boucle chargement groupe
variable_tools: []
variable_pushed: 0
variable_dist: 30
variable_chunk: 10
gcode:
    # macro porte-etat, jamais appelee directement

# Boucle chargement groupe : pousse TOUS les extruder_steppers synchronises
# (un G1 E deplace tout le lot en meme temps), par paliers de 10mm (pas 50 --
# a DIST=30mm par defaut un palier de 50 depasserait la cible des le 1er
# coup), jusqu'a DIST. A chaque palier, verifie le motion sensor de CHAQUE
# position (pin independante) -> une position qui n'a JAMAIS bouge une fois DIST atteint
# est marquee FAIL (feeder ou capteur HS), les autres PASS -- sans capteur
# tete, plus besoin de sequentiel : tout le lot avance ensemble d'un coup.
[delayed_gcode _qc_load_all_step]
gcode:
    {% set v = printer["gcode_macro _QC_LOAD_ALL_STEP"] %}
    {% set st = printer["gcode_macro _QC_YMS_STATE"] %}
    {% set tools = v.tools %}
    {% set pushed = v.pushed|int %}
    {% set dist = v.dist|int %}
    {% for t in tools %}
        {% set yms = printer["filament_motion_sensor YMS-" ~ t].filament_detected %}
        {% if yms and st["ok_" ~ t]|int == 0 %}
            SET_GCODE_VARIABLE MACRO=_QC_YMS_STATE VARIABLE=ok_{t} VALUE=1
            {action_respond_info("QC E%d_HEAD: motion sensor YMS-%d state changed (motion detected)" % (t - 1, t))}
        {% endif %}
    {% endfor %}
    {% if pushed >= dist %}
        {% for t in tools %}
            SYNC_EXTRUDER_MOTION EXTRUDER=extruder{t - 1} MOTION_QUEUE=
            {% if st["ok_" ~ t]|int == 1 %}
                SET_GCODE_VARIABLE MACRO=_QC_YMS_STATE VARIABLE=pushed_{t} VALUE={pushed}
                {action_respond_info("QC E%d_HEAD: loaded %dmm, motion sensor OK -> ready for group stress" % (t - 1, pushed))}
            {% else %}
                RESPOND TYPE=error MSG="QC E{t - 1}_HEAD: no motion detected over {pushed}mm (feeder or sensor faulty)"
            {% endif %}
        {% endfor %}
        RESPOND MSG="QC:LOAD_ALL:PASS"
    {% else %}
        M83
        G1 E{v.chunk|int} F1200
        M400
        SET_GCODE_VARIABLE MACRO=_QC_LOAD_ALL_STEP VARIABLE=pushed VALUE={pushed + v.chunk|int}
        UPDATE_DELAYED_GCODE ID=_qc_load_all_step DURATION=0.3
    {% endif %}

[gcode_macro QC_STRESS_ALL]
description: QC banc — PHASE 2 (parallele) : rampe accel/decel ±70mm 10→30→50→80→80→50→30→10mm/s (16 segments, seuls les 8 du plateau 50-80mm/s comptent -- les 4 premiers/derniers ne sont qu'une rampe mecanique douce, cf. _qc_stress_all_step) sur TOUS les TOOL= a la fois (extruder_steppers synchronises ENSEMBLE sur la meme queue E -> un seul G1 E les bouge tous en lockstep), chaque filament_motion_sensor YMS-n lu INDEPENDAMMENT (pin propre par position) -> attribution correcte par YMS meme en mouvement groupe. TOOLS=1,3,4,... (positions ayant deja passe la phase 1, calcule cote panel).
gcode:
    {% set tools = params.TOOLS.split(",")|map("int")|list %}
    {% set st = printer["gcode_macro _QC_YMS_STATE"] %}
    RESPOND MSG="QC:STRESS_ALL:START"
    _QC_BOARD_FAN_ON
    {% for t in tools %}
        {% if st["ok_" ~ t]|int == 1 %}
            SET_FILAMENT_SENSOR SENSOR=YMS-{t} ENABLE=1
            SYNC_EXTRUDER_MOTION EXTRUDER=extruder{t - 1} MOTION_QUEUE=extruder
        {% endif %}
    {% endfor %}
    SET_GCODE_VARIABLE MACRO=_QC_STRESS_ALL_STEP VARIABLE=tools VALUE="{tools}"
    SET_GCODE_VARIABLE MACRO=_QC_STRESS_ALL_STEP VARIABLE=seg VALUE=0
    UPDATE_DELAYED_GCODE ID=_qc_stress_all_step DURATION=0.3

[gcode_macro _QC_STRESS_ALL_STEP]
description: QC banc - Etat de la boucle stress groupee
variable_tools: []
variable_seg: 0
gcode:
    # macro porte-etat, jamais appelee directement

# Boucle PHASE 2 — rampe accel/decel 16 segments (Nicolas 26/08 : 8 paliers
# de vitesse 10->30->50->80->80->50->30->10mm/s, chacun tenu 2 segments),
# appliquee UNE SEULE FOIS a tous les extruder_steppers synchronises
# ensemble : un G1 E{dist} deplace tout le lot en meme temps. Les 4 premiers
# et 4 derniers segments (le tiers bas de la rampe, 10-30mm/s) ne servent
# qu'a demarrer/arreter en douceur -- SEULS les 8 segments du plateau
# 50-80mm/s (5..12) comptent dans le verdict et le total affiche ("mesure
# propre", hors transitoire d'acceleration). Verifie CHAQUE motion sensor a
# CHAQUE segment (pin independante par YMS) -> une position qui decroche
# SUR UN SEGMENT COMPTE est marquee FAIL + desynchronisee immediatement
# (elle arrete de bouger avec le groupe) SANS interrompre les autres ; un
# decrochage pendant la rampe (non compte) est journalise mais ignore.
[delayed_gcode _qc_stress_all_step]
gcode:
    {% set v = printer["gcode_macro _QC_STRESS_ALL_STEP"] %}
    {% set st = printer["gcode_macro _QC_YMS_STATE"] %}
    {% set tools = v.tools %}
    {% set seg = v.seg|int %}
    {% set speeds = [600, 1800, 3000, 4800, 4800, 3000, 1800, 600] %}
    {% set nseg = speeds|length * 2 %}
    {% if seg > 0 %}
        {% set seg_speed = speeds[((seg - 1) // 2) % speeds|length] // 60 %}
        {% set counted = seg > 4 and seg <= (nseg - 4) %}
        {% for t in tools %}
            {% if st["ok_" ~ t]|int == 1 %}
                {% set yms = printer["filament_motion_sensor YMS-" ~ t].filament_detected %}
                {% if (seg % 2) == 1 and not yms %}
                    SET_GCODE_VARIABLE MACRO=_QC_YMS_STATE VARIABLE=ok_{t} VALUE=0
                    SYNC_EXTRUDER_MOTION EXTRUDER=extruder{t - 1} MOTION_QUEUE=
                    {% if counted %}
                        RESPOND TYPE=error MSG="QC E{t - 1}_HEAD: motion sensor YMS-{t} LOST tracking at segment {seg}/{nseg}"
                    {% else %}
                        {action_respond_info("QC E%d_HEAD: motion sensor YMS-%d lost tracking during ramp segment %d/%d (ignored, not counted)" % (t - 1, t, seg, nseg))}
                    {% endif %}
                {% else %}
                    {action_respond_info("QC E%d_HEAD: stress %d/%d speed=%dmm/s detected=%s counted=%s" % (t - 1, seg, nseg, seg_speed, yms, counted))}
                {% endif %}
            {% endif %}
        {% endfor %}
    {% endif %}
    {% if seg >= nseg %}
        {% for t in tools %}
            {% if st["ok_" ~ t]|int == 1 %}
                {action_respond_info("QC E%d_HEAD: stress OK — %d segments ±70mm (10→30→50→80→80→50→30→10mm/s), sensor tracked throughout" % (t - 1, nseg - 8))}
            {% endif %}
        {% endfor %}
        {% set ns = namespace(maxpush=0) %}
        {% for t in tools %}
            {% set pushed = st["pushed_" ~ t]|int %}
            {% if pushed > ns.maxpush %}{% set ns.maxpush = pushed %}{% endif %}
            SYNC_EXTRUDER_MOTION EXTRUDER=extruder{t - 1} MOTION_QUEUE=extruder
        {% endfor %}
        {% if ns.maxpush > 0 %}
            # Retrait final 300mm (Nicolas 26/08) : PAS juste annuler le
            # chargement (ns.maxpush ~30mm), on va bien plus loin en arriere
            # pour degager le filament HORS de l'extrudeur -- l'operateur n'a
            # plus besoin de basculer l'extrudeur a la main pour le retirer,
            # les 12 (synchronises) reculent ensemble en une seule passe.
            M83
            G1 E-300 F1200
            M400
        {% endif %}
        {% for t in tools %}
            SYNC_EXTRUDER_MOTION EXTRUDER=extruder{t - 1} MOTION_QUEUE=
        {% endfor %}
        RESPOND MSG="QC:STRESS_ALL:PASS"
    {% else %}
        {% set spd = speeds[(seg // 2) % speeds|length] %}
        {% set dist = 70 if seg % 2 == 0 else -70 %}
        M83
        G1 E{dist} F{spd}
        M400
        SET_GCODE_VARIABLE MACRO=_QC_STRESS_ALL_STEP VARIABLE=seg VALUE={seg + 1}
        UPDATE_DELAYED_GCODE ID=_qc_stress_all_step DURATION=0.3
    {% endif %}

[gcode_macro QC_HEAT_START]
description: QC banc — YMS Pro : DEMARRE la chauffe de TOUS les TOOL= (plateau intégré) EN MEME TEMPS et retourne IMMEDIATEMENT (non-bloquant) -- la montée continue en tâche de fond PENDANT le reste du protocole (load_all/stress_all), qui n'attend plus la chauffe pour démarrer. TOOLS=3,4,5,... TARGET=85 (optionnel) — à faire suivre plus tard de QC_HEAT_WAIT avec les MÊMES TOOLS pour valider/couper.
gcode:
    {% set tools = params.TOOLS.split(",")|map("int")|list %}
    {% set target = params.TARGET|default(85)|int %}
    RESPOND MSG="QC:HEAT_START:START"
    {% for t in tools %}
        SET_HEATER_TEMPERATURE HEATER=YMS-{t}-heater TARGET={target}
    {% endfor %}
    RESPOND MSG="QC:HEAT_START:PASS"

[gcode_macro QC_HEAT_WAIT]
description: QC banc — YMS Pro : ATTEND que TOUS les TOOL= (chauffe déjà lancée via QC_HEAT_START) atteignent TARGET (tolérance 2C) ou TIMEOUT secondes (défaut 300, décompté depuis CET appel -- le temps déjà chauffé pendant load_all/stress_all est donc "gratuit"). Une position en timeout est marquée FAIL sans bloquer les autres. TOOLS=3,4,5,... TARGET=85 (optionnel, doit matcher QC_HEAT_START)
gcode:
    {% set tools = params.TOOLS.split(",")|map("int")|list %}
    {% set target = params.TARGET|default(85)|int %}
    RESPOND MSG="QC:HEAT_WAIT:START"
    SET_GCODE_VARIABLE MACRO=_QC_HEAT_ALL_STEP VARIABLE=tools VALUE="{tools}"
    SET_GCODE_VARIABLE MACRO=_QC_HEAT_ALL_STEP VARIABLE=target VALUE={target}
    SET_GCODE_VARIABLE MACRO=_QC_HEAT_ALL_STEP VARIABLE=elapsed VALUE=0
    SET_GCODE_VARIABLE MACRO=_QC_HEAT_ALL_STEP VARIABLE=timeout VALUE={params.TIMEOUT|default(300)|int}
    UPDATE_DELAYED_GCODE ID=_qc_heat_all_step DURATION=1.0

[gcode_macro _QC_HEAT_ALL_STEP]
description: QC banc - Etat de la boucle chauffe groupee (YMS Pro)
variable_tools: []
variable_target: 85
variable_elapsed: 0
variable_timeout: 300
gcode:
    # macro porte-etat, jamais appelee directement

# Boucle chauffe groupee — poll 1x/s la temperature de CHAQUE heater_generic
# (pin independante par position, meme principe que les motion sensors) ;
# des que TOUS ont atteint TARGET-2C -> PASS pour tous, coupe les heaters.
# A TIMEOUT, tranche position par position (celles qui ont atteint la cible
# entre-temps -> OK, les autres -> FAIL) -- une position lente ne bloque
# jamais les autres indefiniment.
#
# Point de mesure toutes les 10s (Nicolas 25/08) : une ligne par position,
# taguee "QC E<n>_HEAD:" -> routee par qc_engine dans le buffer DEDIE de
# cette position (cf. v5 25/08), donc jamais en concurrence avec les 5 autres
# positions chauffantes. A TIMEOUT=300s par defaut : ~30 points + la ligne
# finale = 31 lignes, large marge sous le plafond de 40/position. Sert a
# reconstruire la courbe de chauffe cote rapport (extract_measures ->
# measures.heat_curve).
[delayed_gcode _qc_heat_all_step]
gcode:
    {% set v = printer["gcode_macro _QC_HEAT_ALL_STEP"] %}
    {% set tools = v.tools %}
    {% set target = v.target|int %}
    {% set elapsed = v.elapsed|int %}
    {% set ns = namespace(alldone=true) %}
    {% if elapsed % 10 == 0 %}
        {% for t in tools %}
            {% set h = printer["heater_generic YMS-" ~ t ~ "-heater"] %}
            {action_respond_info("QC E%d_HEAD: heat %ds %.1fC" % (t - 1, elapsed, h.temperature))}
        {% endfor %}
    {% endif %}
    {% for t in tools %}
        {% set h = printer["heater_generic YMS-" ~ t ~ "-heater"] %}
        {% if h.temperature < target - 2 %}
            {% set ns.alldone = false %}
        {% endif %}
    {% endfor %}
    {% if ns.alldone or elapsed >= v.timeout|int %}
        {% for t in tools %}
            {% set h = printer["heater_generic YMS-" ~ t ~ "-heater"] %}
            {% if h.temperature < target - 2 %}
                RESPOND TYPE=error MSG="QC E{t - 1}_HEAD: heat timeout, {"%.1f" % h.temperature}C after {elapsed}s (target {target}C)"
            {% else %}
                {action_respond_info("QC E%d_HEAD: heat OK, %.1fC reached (target %dC)" % (t - 1, h.temperature, target))}
            {% endif %}
            SET_HEATER_TEMPERATURE HEATER=YMS-{t}-heater TARGET=0
        {% endfor %}
        RESPOND MSG="QC:HEAT_WAIT:PASS"
    {% else %}
        SET_GCODE_VARIABLE MACRO=_QC_HEAT_ALL_STEP VARIABLE=elapsed VALUE={elapsed + 1}
        UPDATE_DELAYED_GCODE ID=_qc_heat_all_step DURATION=1.0
    {% endif %}
"""


if __name__ == "__main__":
    main()
