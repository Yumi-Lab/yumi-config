# YUMI sensorless homing
#
# Home X ou Y en sensorless (StallGuard) de facon fiable et SURE :
#   - neutralise CoolStep/autotune pour ouvrir la fenetre StallGuard,
#   - pose le courant de "coarse",
#   - lance le home natif Klipper (G28 AXIS) = 2 passes (homing_speed puis
#     second_homing_speed), eprouve, qui pose le zero a position_endstop,
#   - restaure le courant de run + l'autotune.
# A appeler depuis le homing_override : YUMI_SENSORLESS_HOME AXIS=Y.
#
# IMPORTANT - pourquoi PAS de "multi-tap de precision" sur X/Y :
# Contre une butee SENSORLESS il n'existe PAS de reference rigide. StallGuard
# declenche sur une MONTEE DE CHARGE dont la position depend de la vitesse et
# du courant. "Re-taper" pour mesurer une repetabilite donne donc :
#   - soit un arret deterministe sur la coordonnee commandee (faux spread=0),
#   - soit, si StallGuard ne declenche pas (courant fin trop bas), une
#     surcourse au-dela de la limite -> le chariot "stoppe dans le vide".
# La validation multi-tap n'a de sens que pour le Z (nozzle tap sur le lit =
# vraie reference rigide, via probe_pressure / yumi_z_tap), pas ici.
import logging

AXIS_INDEX = {'X': 0, 'Y': 1, 'Z': 2}


class YumiSensorless:
    def __init__(self, config):
        self.printer = config.get_printer()
        # Courant pendant le home (0 = ne pas changer) et courant de run.
        self.coarse_current = {
            'X': config.getfloat('coarse_current_x', 0.0, minval=0.),
            'Y': config.getfloat('coarse_current_y', 0.0, minval=0.),
        }
        self.run_current = {
            'X': config.getfloat('run_current_x', 1.2, above=0.),
            'Y': config.getfloat('run_current_y', 1.2, above=0.),
        }
        # sgthrs a restaurer apres le home (0 = garde la valeur config).
        self.run_sgthrs = {
            'X': config.getint('run_sgthrs_x', 0),
            'Y': config.getint('run_sgthrs_y', 0),
        }
        # Restaurer l'autotune apres le home (re-applique coolstep silencieux).
        self.restore_autotune = config.getboolean('restore_autotune', True)
        # Parametres conserves pour compat (catalogue/printer.cfg) mais inutiles
        # ici : le home de precision n'est pas realisable en sensorless.
        config.getint('samples', 3, minval=1)
        config.getfloat('samples_tolerance', 0.05, above=0.)
        config.getint('max_taps', 10, minval=1)
        config.getfloat('fine_speed', 20.0, above=0.)
        config.getfloat('fine_accel', 1000.0, above=0.)
        config.getfloat('travel_speed', 40.0, above=0.)
        config.getfloat('retract', 5.0, above=0.)
        config.getfloat('overshoot', 2.0, minval=0.)
        config.getfloat('fine_current_x', 0.0, minval=0.)
        config.getfloat('fine_current_y', 0.0, minval=0.)
        config.getint('fine_sgthrs_x', 0)
        config.getint('fine_sgthrs_y', 0)
        gcode = self.printer.lookup_object('gcode')
        gcode.register_command('YUMI_SENSORLESS_HOME', self.cmd_home,
                               desc="Home sensorless natif (X ou Y)")

    def cmd_home(self, gcmd):
        axis = gcmd.get('AXIS', 'Y').upper()
        if axis not in ('X', 'Y'):
            raise gcmd.error("YUMI_SENSORLESS_HOME: AXIS doit etre X ou Y")
        st = axis.lower()
        gcode = self.printer.lookup_object('gcode')

        # Ouvre la fenetre StallGuard : CoolStep desactive pour le home.
        gcode.run_script_from_command(
            "SET_TMC_FIELD STEPPER=stepper_%s FIELD=tcoolthrs VALUE=0" % st)

        # Courant de home (si configure).
        cc = self.coarse_current[axis]
        if cc > 0:
            gcode.run_script_from_command(
                "SET_TMC_CURRENT STEPPER=stepper_%s CURRENT=%.3f" % (st, cc))

        try:
            gcmd.respond_info("YUMI_SENSORLESS_HOME %s: home..." % axis)
            # Home natif Klipper : 2 passes sensorless, pose le zero a
            # position_endstop. C'est la seule reference fiable disponible.
            gcode.run_script_from_command("G28 %s" % axis)
        finally:
            # Restaure courant de run + sgthrs + autotune quoi qu'il arrive.
            gcode.run_script_from_command(
                "SET_TMC_CURRENT STEPPER=stepper_%s CURRENT=%.3f"
                % (st, self.run_current[axis]))
            rs = self.run_sgthrs[axis]
            if rs > 0:
                gcode.run_script_from_command(
                    "SET_TMC_FIELD STEPPER=stepper_%s FIELD=sgthrs VALUE=%d"
                    % (st, rs))
            if self.restore_autotune:
                try:
                    gcode.run_script_from_command(
                        "AUTOTUNE_TMC STEPPER=stepper_%s" % st)
                except Exception as e:
                    logging.info("YUMI_SENSORLESS_HOME restore autotune: %s", e)

        gcmd.respond_info("YUMI_SENSORLESS_HOME %s OK" % axis)


def load_config(config):
    return YumiSensorless(config)
