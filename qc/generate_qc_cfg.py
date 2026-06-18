#!/usr/bin/env python3
"""
Génère qc_printer.cfg à partir d'un backup printer.cfg de production.

Principe : on EXTRAIT les blocs matériels EXACTS du printer.cfg de référence
(garantit la géométrie/pins/courants réels de la machine) et on assemble un
cfg QC autonome :
  - sans hyperdrive (mcu hyperdrive_uart + extruder2-6 + YMS-3..7 retirés)
  - sans dépendance aux includes prod (plr/mainsail/anc/timelapse)
  - YMS-1 / YMS-2 réécrits en capteurs standard avec runout -> QC:Ex_ROTATION:FAIL
  - min_extrude_temp 0, PID/z_offset inline (pas de bloc SAVE_CONFIG -> pas de restart)
  - marqueur _QC_MODE, [force_move], [respond] ajoutés

Usage : python3 generate_qc_cfg.py <ref_printer.cfg> <sortie_qc.cfg>
"""
import os
import re
import sys


def parse_sections(text):
    """Retourne une liste (header, body_text) en respectant l'ordre.
    Un header = ligne commençant par '[' en colonne 0. Le body va jusqu'au
    prochain header (col 0)."""
    lines = text.splitlines(keepends=True)
    sections = []
    cur_header = None
    cur_buf = []
    preamble = []
    hdr_re = re.compile(r'^\[([^\]]+)\]')
    for ln in lines:
        m = hdr_re.match(ln)
        if m:
            if cur_header is None:
                preamble = cur_buf
            else:
                sections.append((cur_header, ''.join(cur_buf)))
            cur_header = m.group(1).strip()
            cur_buf = [ln]
        else:
            cur_buf.append(ln)
    if cur_header is not None:
        sections.append((cur_header, ''.join(cur_buf)))
    return sections


def main():
    ref_path, out_path = sys.argv[1], sys.argv[2]
    # Taille machine déduite du nom de sortie (qc_printer_<TAILLE>.cfg) — gravée
    # dans le marqueur _QC_MODE pour que le panel détecte le modèle chargé.
    _b = out_path.rsplit("/", 1)[-1]
    qc_model = _b[11:-4] if _b.startswith("qc_printer_") and _b.endswith(".cfg") else ""
    with open(ref_path) as f:
        text = f.read()
    sections = parse_sections(text)
    by_header = {}
    for h, body in sections:
        by_header.setdefault(h, body)  # première occurrence

    def grab(header):
        if header not in by_header:
            raise SystemExit(f"Section manquante dans la référence : [{header}]")
        body_lines = by_header[header].splitlines()
        # retire les lignes de fin purement commentaires/vides (séparateurs
        # orphelins laissés par la suppression de la section suivante)
        while body_lines and (not body_lines[-1].strip()
                              or body_lines[-1].lstrip().startswith("#")):
            body_lines.pop()
        return "\n".join(body_lines) + "\n"

    # ── Ordre des blocs matériels EXTRAITS verbatim du backup .3 ──
    keep_order = [
        # modules custom requis par le homing 2-phase sensorless + Z tap
        "probe_pressure",
        "yumi_sensorless_homing",
        "yumi_z_tap",
        "motor_constants BJ42D29-28V31",
        "motor_constants BJ42D29-28V03",
        "motor_constants BJ42D07-06V02",
        "motor_constants BJ42D07-03V05",
        "autotune_tmc stepper_y",
        "autotune_tmc stepper_x",
        "autotune_tmc stepper_z",
        "autotune_tmc extruder_stepper extruder0",
        "autotune_tmc extruder_stepper extruder1",
        # cinématique / MCU
        "idle_timeout",
        "mcu",
        "mcu rpi",
        "printer",
        "adxl345",
        "resonance_tester",
        "input_shaper",
        "stepper_x",
        "stepper_y",
        "tmc2209 stepper_x",
        "tmc2209 stepper_y",
        "stepper_z",
        "tmc2209 stepper_z",
        # thermique / extrusion
        "thermistor 100K4190YUMI",
        "thermistor 100K3950YUMI",
        "extruder",            # PID inline ajouté plus bas
        "extruder_stepper extruder0",
        "tmc2209 extruder_stepper extruder0",
        "extruder_stepper extruder1",
        "tmc2209 extruder_stepper extruder1",
        "heater_bed",          # PID inline ajouté plus bas
        # fans
        "fan",
        "heater_fan hotend_fan",
        "controller_fan Motherboard_Fan",
        "fan_generic Aux_Fan",
        "temperature_sensor NanoPi",
        "verify_heater extruder",
        "verify_heater heater_bed",
        # probe / nivellement
        "screws_tilt_adjust",
        "probe",               # z_offset inline ajouté plus bas
        "bed_mesh",
        # homing réel (sensorless 2-phase + Z tap)
        "homing_override",
        # cutter (profil de coupe spécifique machine, appelé par QC_CUTTER)
        "gcode_macro CUT_FILAMENT",
    ]

    # PID/z_offset repris du bloc SAVE_CONFIG de la référence
    EXTRUDER_PID = ("control: pid\n"
                    "pid_kp: 22.214\n"
                    "pid_ki: 1.452\n"
                    "pid_kd: 84.968\n")
    BED_PID = ("control: pid\n"
               "pid_Kp: 28.566\n"
               "pid_Ki: 0.281\n"
               "pid_Kd: 725.210\n")
    PROBE_ZOFFSET = "z_offset: 0.100\n"

    def inject_after_header(block, extra):
        """Insère `extra` juste après la ligne header du bloc."""
        nl = block.find("\n")
        return block[:nl + 1] + extra + block[nl + 1:]

    out = []
    out.append(
        "#####################################################################\n"
        "# YUMI QC — printer.cfg dédié contrôle qualité usine\n"
        "# GÉNÉRÉ depuis le backup de production 172.20.10.3 (machine 7-YMS\n"
        "# hyperdrive) par qc/generate_qc_cfg.py — NE PAS éditer à la main.\n"
        "#\n"
        "# Géométrie/pins/courants = EXACTEMENT ceux de la machine de réf.\n"
        "# Hyperdrive retiré : QC sur carte principale seule (E0/E1).\n"
        "# Klipper démarre sans la box (pas de MCU /dev/ttyS2).\n"
        "#\n"
        "# Déploiement :\n"
        "#   cp qc_printer.cfg ~/printer_data/config/qc_printer.cfg\n"
        "# Le panel QC du SmartPad gère backup/swap/restart/restore.\n"
        "#####################################################################\n\n")

    out.append(
        "# Marqueur lu par le panel QC pour détecter que la cfg QC est active.\n"
        "# variable_model = taille machine (sélecteur du panel), gravée ici.\n"
        "[gcode_macro _QC_MODE]\n"
        "description: Marqueur mode QC actif\n"
        "variable_active: 1\n"
        f'variable_model: "{qc_model}"\n'
        "gcode:\n"
        '    RESPOND MSG="QC mode actif"\n\n')

    # DEVICE — identite YUMI multi-MCU (en prod fournie par yumi-device.cfg ;
    # la cfg QC est standalone donc on l'embarque ici).
    out.append(
        "[gcode_macro DEVICE]\n"
        "description: Identite YUMI de toutes les cartes connectees (multi-MCU)\n"
        "gcode:\n"
        "    {% set ns = namespace(found=false) %}\n"
        "    {% for name in printer.configfile.settings %}\n"
        '      {% if name == "mcu" or name.startswith("mcu ") %}\n'
        "        {% set c = printer[name].mcu_constants if printer[name] is defined else {} %}\n"
        "        {% if c.YUMI_CONFIG is defined and c.YUMI_CONFIG %}\n"
        "          {% set ns.found = true %}\n"
        '          {action_respond_info("[" ~ name ~ "] " ~ c.YUMI_CONFIG)}\n'
        "          {% if c.YUMI_COMMENT is defined and c.YUMI_COMMENT %}\n"
        '            {action_respond_info("[" ~ name ~ "] comment: " ~ c.YUMI_COMMENT)}\n'
        "          {% endif %}\n"
        "        {% endif %}\n"
        "      {% endif %}\n"
        "    {% endfor %}\n"
        '    {% if not ns.found %}{action_respond_info("Aucune identite YUMI trouvee sur les MCU")}{% endif %}\n\n')

    # Sections utilitaires fournies en prod par plr.cfg / mainsail.cfg
    out.append("[respond]\n\n")
    out.append("[force_move]\nenable_force_move: True\n\n")
    out.append("[virtual_sdcard]\npath: ~/printer_data/gcodes/\n\n")
    out.append("[pause_resume]\n\n")
    out.append("[display_status]\n\n")
    out.append("[exclude_object]\n\n")
    out.append("[gcode_arcs]\nresolution: 0.1\n\n")
    out.append("[save_variables]\nfilename: ~/printer_data/config/variables.cfg\n\n")

    for h in keep_order:
        block = grab(h)
        if h == "extruder":
            block = inject_after_header(block, EXTRUDER_PID)
        elif h == "heater_bed":
            block = inject_after_header(block, BED_PID)
        elif h == "probe":
            block = inject_after_header(block, PROBE_ZOFFSET)
        elif h == "mcu rpi":
            # SmartPi One, pas un Raspberry Pi -> nom de MCU explicite.
            block = block.replace("[mcu rpi]", "[mcu SmartPiOne]", 1)
        elif h == "adxl345":
            # suit le renommage du MCU hôte (cs_pin: rpi: -> SmartPiOne:)
            block = block.replace("rpi:", "SmartPiOne:")
        elif h == "controller_fan Motherboard_Fan":
            # QC : ventilo carte mère converti en fan_generic pilotable
            # directement (SET_FAN_SPEED), pour un test de contrôle franc à
            # 100% comme le cooling fan. La prod garde son controller_fan.
            mpin = re.search(r'(?m)^pin:\s*(\S+)', block)
            pin = mpin.group(1) if mpin else 'PB8'
            block = ("[fan_generic Motherboard_Fan]\n"
                     f"pin: {pin}\n"
                     "max_power: 1.0\n"
                     "kick_start_time: 0.05\n"
                     "off_below: 0.1\n")
        elif h == "heater_fan hotend_fan":
            # QC : ventilo hotend converti en fan_generic pilotable
            # directement -> testable sans chauffer la buse. La macro
            # QC_HEAT_EXTRUDER le force ON pendant la chauffe 220C (anti
            # heat-creep). La prod garde son heater_fan auto.
            mpin = re.search(r'(?m)^pin:\s*(\S+)', block)
            pin = mpin.group(1) if mpin else 'PC6'
            block = ("[fan_generic hotend_fan]\n"
                     f"pin: {pin}\n"
                     "max_power: 1.0\n"
                     "kick_start_time: 0.05\n"
                     "shutdown_speed: 1.0\n")
        out.append(block + "\n")

    # ── YMS-1 / YMS-2 : capteurs standard, runout -> signal QC ──
    out.append(
        "#####################################################################\n"
        "# Motion sensors YMS E0 (PC14) / E1 (PC13) — encodeurs de mouvement.\n"
        "# Lecture seule pour le QC : on lit filament_detected (= mouvement\n"
        "# detecte). Pas de runout_gcode (le reset T0 + extrude a vide les\n"
        "# passe en non-detecte sans declencher d'action). Valide le cablage\n"
        "# du capteur jusqu'a la carte mere quand le filament avance.\n"
        "#####################################################################\n\n")
    out.append(
        "[filament_motion_sensor YMS-1]\n"
        "switch_pin: !PC14\n"
        "detection_length: 10\n"
        "pause_on_runout: False\n"
        "extruder: extruder\n"
        "event_delay: 0.5\n\n")
    out.append(
        "[filament_motion_sensor YMS-2]\n"
        "switch_pin: PC13\n"
        "detection_length: 10\n"
        "pause_on_runout: False\n"
        "extruder: extruder\n"
        "event_delay: 0.5\n\n")

    # Capteur d'arrivee filament a la TETE (switch, !PA8). Sert au test
    # feed E0/E1 : on lit filament_detected pour savoir si le filament est
    # arrive a la tete. pause_on_runout False + pas de runout_gcode : lecture
    # seule, aucun effet de bord.
    out.append(
        "[filament_switch_sensor head_sensor]\n"
        "switch_pin: !PA8\n"
        "pause_on_runout: False\n\n")

    # ── UID matériel unique de la carte mère (STM32 96 bits) ──
    # Lu via debug_read sur la connexion MCU principale (AUCUN second MCU, AUCUNE
    # modif firmware). QUERY_MCU_UID -> "MCU_UID=<24 hex>". C'est l'identité
    # UNIQUE par imprimante utilisée par le QC (printer_id côté compteur).
    out.append(
        "#####################################################################\n"
        "# UID STM32 unique (debug_read sur le MCU principal) -> QUERY_MCU_UID\n"
        "#####################################################################\n"
        "[mcu_uid]\n"
        "# mcu: mcu          # MCU interrogé (défaut: mcu principal RJ11)\n"
        "# addr: 0x1FFF7A10  # base UID STM32F4 (défaut)\n"
        "# words: 3          # 96 bits = 3 mots de 32 bits\n")

    # ── Macros QC inlinées (cfg MONOLITHIQUE : aucun [include]) ──
    # Source de vérité = qc_macros.cfg (éditable) ; on l'embarque ici pour
    # que le swap QC ne dépende d'aucun autre fichier.
    script_dir = os.path.dirname(os.path.abspath(__file__))
    macros_path = os.path.join(script_dir, "qc_macros.cfg")
    with open(macros_path) as f:
        qc_macros = f.read()
    out.append(
        "#####################################################################\n"
        "# MACROS QC (inlinées depuis qc_macros.cfg — cfg monolithique)\n"
        "#####################################################################\n\n")
    out.append(qc_macros.rstrip() + "\n")

    text = "".join(out)
    # Point de tap QC (zero_reference_position du bed_mesh) déplacé en Y212
    # (était Y202 dans le backup) — le Z tap se fait à X31 Y212.
    text = re.sub(r"(?m)^zero_reference_position:.*$",
                  "zero_reference_position: 31, 212", text)
    # Garde-fou : un cfg QC monolithique ne doit contenir AUCUN [include].
    includes = re.findall(r'(?m)^\[include .*\]', text)
    if includes:
        raise SystemExit("cfg QC non monolithique, includes restants : %s" % includes)

    with open(out_path, "w") as f:
        f.write(text)
    print(f"Écrit : {out_path} (monolithique, {text.count(chr(10))} lignes)")


if __name__ == "__main__":
    main()
