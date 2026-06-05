# YUMI sensorless homing with multi-tap precision validation
#
# Auto-suffisant : neutralise CoolStep/autotune, fait un coarse (G28 reel,
# courant bas, vitesse douce) pour localiser la butee, puis N taps fins via
# probing_move qui capturent la position REELLE du trigger et valident que
# `samples` taps consecutifs concordent dans `samples_tolerance`. A appeler
# depuis le homing_override : YUMI_SENSORLESS_HOME AXIS=Y.
#
# Klipper ne fait aucune verif de tolerance sur un endstop natif : ce module
# l'ajoute (equivalent samples_tolerance du probe, porte sur le sensorless).
import logging

AXIS_INDEX = {'X': 0, 'Y': 1, 'Z': 2}


class YumiSensorless:
    def __init__(self, config):
        self.printer = config.get_printer()
        self.samples = config.getint('samples', 3, minval=2)
        self.samples_tolerance = config.getfloat('samples_tolerance', 0.05,
                                                  above=0.)
        self.max_taps = config.getint('max_taps', 10, minval=1)
        self.fine_speed = config.getfloat('fine_speed', 20.0, above=0.)
        self.fine_accel = config.getfloat('fine_accel', 1000.0, above=0.)
        self.travel_speed = config.getfloat('travel_speed', 40.0, above=0.)
        self.retract = config.getfloat('retract', 5.0, above=0.)
        self.overshoot = config.getfloat('overshoot', 2.0, minval=0.)
        # Courant coarse (G28 de localisation) et fin (taps). 0 = ne pas changer
        self.coarse_current = {
            'X': config.getfloat('coarse_current_x', 0.0, minval=0.),
            'Y': config.getfloat('coarse_current_y', 0.0, minval=0.),
        }
        self.fine_current = {
            'X': config.getfloat('fine_current_x', 0.0, minval=0.),
            'Y': config.getfloat('fine_current_y', 0.0, minval=0.),
        }
        self.run_current = {
            'X': config.getfloat('run_current_x', 1.2, above=0.),
            'Y': config.getfloat('run_current_y', 1.2, above=0.),
        }
        # sgthrs eleve/bas pendant les taps (0 = garde la valeur config)
        self.fine_sgthrs = {
            'X': config.getint('fine_sgthrs_x', 0),
            'Y': config.getint('fine_sgthrs_y', 0),
        }
        self.run_sgthrs = {
            'X': config.getint('run_sgthrs_x', 150),
            'Y': config.getint('run_sgthrs_y', 150),
        }
        # Restaurer l'autotune apres le home (re-applique coolstep silencieux)
        self.restore_autotune = config.getboolean('restore_autotune', True)
        gcode = self.printer.lookup_object('gcode')
        gcode.register_command('YUMI_SENSORLESS_HOME', self.cmd_home,
                               desc="Sensorless home avec validation multi-tap")

    def _rail(self, ai):
        kin = self.printer.lookup_object('toolhead').get_kinematics()
        return kin.rails[ai]

    def cmd_home(self, gcmd):
        axis = gcmd.get('AXIS', 'Y').upper()
        if axis not in ('X', 'Y'):
            raise gcmd.error("YUMI_SENSORLESS_HOME: AXIS doit etre X ou Y")
        ai = AXIS_INDEX[axis]
        st = axis.lower()
        samples = gcmd.get_int('SAMPLES', self.samples, minval=2)
        tol = gcmd.get_float('TOLERANCE', self.samples_tolerance, above=0.)
        max_taps = gcmd.get_int('MAX_TAPS', self.max_taps, minval=samples)
        fine_speed = gcmd.get_float('SPEED', self.fine_speed, above=0.)
        skip_coarse = gcmd.get_int('SKIP_COARSE', 0)

        toolhead = self.printer.lookup_object('toolhead')
        gcode = self.printer.lookup_object('gcode')
        phoming = self.printer.lookup_object('homing')
        rail = self._rail(ai)
        mcu_endstop = rail.get_endstops()[0][0]
        hi = rail.get_homing_info()
        pos_endstop = hi.position_endstop
        pmin, pmax = rail.get_range()
        # away = direction qui s'eloigne de l'endstop
        away = -1.0 if hi.positive_dir else 1.0

        # Neutralise CoolStep/autotune pour TOUT le home (coarse + fin)
        gcode.run_script_from_command(
            "SET_TMC_FIELD STEPPER=stepper_%s FIELD=tcoolthrs VALUE=0" % st)

        # 1) Coarse: localise la butee (G28 reel, courant bas, vitesse douce)
        if not skip_coarse:
            cc = self.coarse_current[axis]
            if cc > 0:
                gcode.run_script_from_command(
                    "SET_TMC_CURRENT STEPPER=stepper_%s CURRENT=%.3f" % (st, cc))
            gcmd.respond_info("YUMI_SENSORLESS_HOME %s: coarse..." % axis)
            gcode.run_script_from_command("G28 %s" % axis)

        # 2) Prep taps fins. Re-neutralise tcoolthrs : si le coarse est passe
        # par un G28 override qui finit par AUTOTUNE_TMC, tcoolthrs a ete
        # re-pose non-nul -> il faut le remettre a 0 pour les taps.
        gcode.run_script_from_command(
            "SET_TMC_FIELD STEPPER=stepper_%s FIELD=tcoolthrs VALUE=0" % st)
        fine_cur = self.fine_current[axis]
        if fine_cur > 0:
            gcode.run_script_from_command(
                "SET_TMC_CURRENT STEPPER=stepper_%s CURRENT=%.3f" % (st, fine_cur))
        fine_sg = self.fine_sgthrs[axis]
        if fine_sg > 0:
            gcode.run_script_from_command(
                "SET_TMC_FIELD STEPPER=stepper_%s FIELD=sgthrs VALUE=%d"
                % (st, fine_sg))
        eventtime = self.printer.get_reactor().monotonic()
        saved_accel = toolhead.get_status(eventtime).get('max_accel', 1000.)
        gcode.run_script_from_command(
            "SET_VELOCITY_LIMIT ACCEL=%.0f" % self.fine_accel)

        triggers = []
        stable = 0
        validated = False
        try:
            for attempt in range(1, max_taps + 1):
                # Retract loin de l'endstop pour avoir de la course
                cur = toolhead.get_position()
                cur[ai] = pos_endstop + away * self.retract
                toolhead.manual_move(cur, self.travel_speed)
                toolhead.wait_moves()
                # Tap: commande AU-DELA du mur physique. raw_t inclut deja
                # l'overshoot dans la direction de home -> on vise 'overshoot'
                # mm au-dela de l'endstop. On NE clampe PAS sur la plage
                # cinematique : un homing move ignore les limites, et clamper a
                # pos_endstop ferait stopper le planner PILE sur la coordonnee
                # -> StallGuard declenche sur l'arret de fin de course (faux
                # tap deterministe, spread=0, "taps en l'air"). Cible au-dela =
                # seul un contact reel peut arreter le chariot -> vrai stall,
                # position mesuree avec son jitter naturel.
                # check_movement=True rejette un trigger sans mouvement (DIAG
                # deja arme) ; et une course libre sans contact leve une erreur
                # ("no trigger") au lieu de valider un faux zero.
                target = list(toolhead.get_position())
                target[ai] = pos_endstop - away * self.overshoot
                epos = phoming.probing_move(mcu_endstop, target, fine_speed,
                                            check_movement=True)
                trig = epos[ai]
                triggers.append(trig)
                if len(triggers) >= 2:
                    diff = abs(triggers[-1] - triggers[-2])
                    if diff <= tol:
                        stable += 1
                    else:
                        stable = 0  # il faut samples taps CONSECUTIFS
                    gcmd.respond_info(
                        "tap %d: pos=%.4f diff=%.4f stable=%d/%d"
                        % (attempt, trig, diff, stable, samples - 1))
                else:
                    gcmd.respond_info("tap %d: pos=%.4f (ref)" % (attempt, trig))
                if stable >= samples - 1:
                    validated = True
                    break
        finally:
            # Restaure accel + courant + sgthrs quoi qu'il arrive
            gcode.run_script_from_command(
                "SET_VELOCITY_LIMIT ACCEL=%.0f" % saved_accel)
            gcode.run_script_from_command(
                "SET_TMC_CURRENT STEPPER=stepper_%s CURRENT=%.3f"
                % (st, self.run_current[axis]))
            if fine_sg > 0:
                gcode.run_script_from_command(
                    "SET_TMC_FIELD STEPPER=stepper_%s FIELD=sgthrs VALUE=%d"
                    % (st, self.run_sgthrs[axis]))
            # Restaure le tuning autotune silencieux (re-pose coolstep/pwm)
            if self.restore_autotune:
                try:
                    gcode.run_script_from_command(
                        "AUTOTUNE_TMC STEPPER=stepper_%s" % st)
                except Exception:
                    pass

        if not validated:
            raise gcmd.error(
                "YUMI_SENSORLESS_HOME %s: non valide apres %d taps "
                "(spread=%.4f > tol=%.4f)"
                % (axis, len(triggers), self._spread(triggers), tol))

        used = triggers[-samples:]
        spread = self._spread(used)
        mean = sum(used) / len(used)
        # Pose le zero a l'endstop sur le dernier trigger (on y est)
        gcode.run_script_from_command(
            "SET_KINEMATIC_POSITION %s=%.4f" % (axis, pos_endstop))
        # Degage a une position sure
        cur = toolhead.get_position()
        cur[ai] = pos_endstop + away * self.retract
        toolhead.manual_move(cur, self.travel_speed)
        toolhead.wait_moves()
        gcmd.respond_info(
            "YUMI_SENSORLESS_HOME %s VALIDE: %d taps, derniers %d -> "
            "moyenne=%.4f spread=%.4fmm (tol=%.4f)"
            % (axis, len(triggers), samples, mean, spread, tol))

    def _spread(self, vals):
        return (max(vals) - min(vals)) if len(vals) >= 2 else 0.0


def load_config(config):
    return YumiSensorless(config)
