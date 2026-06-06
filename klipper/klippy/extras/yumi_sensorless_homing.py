# YUMI sensorless homing — home contre hard-stop + check de repetabilite
#
# Modele : "home sensorless repete contre une butee MECANIQUE RIGIDE", PAS un
# probe multi-tap de precision facon Z (StallGuard = detection de charge moteur,
# pas une switch de contact). On obtient une repetabilite GROSSIERE (0.05-0.20mm
# realiste), suffisante pour poser le zero qui est de toute facon en butee.
#
# Etapes (cf. avis Codex 2026-06-06) :
#   A) Home natif (G28 AXIS) pour localiser le mur. En sensorless
#      homing_retract_dist=0 -> PAS de 2e passe fiable, c'est normal.
#   B) N taps, conditions STRICTEMENT constantes (courant FRANC proche du run,
#      sgthrs, vitesse, accel) : reculer, dwell (vider le flag StallGuard),
#      taper avec un overshoot tres limite via offset cinematique.
#      PREUVE DE CONTACT obligatoire : le trigger doit tomber AVANT la cible
#      commandee (= il a bute le mur). Si le move atteint la cible sans trigger
#      precoce -> tap REJETE (sinon faux positif spread=0, "tap dans le vide").
#   C) Rejet des outliers, moyenne des taps retenus, report du spread, puis
#      SET_KINEMATIC_POSITION AXIS=position_endstop (le zero est le hard-stop).
#
# A appeler depuis le homing_override : YUMI_SENSORLESS_HOME AXIS=Y.
import logging

AXIS_INDEX = {'X': 0, 'Y': 1, 'Z': 2}


class YumiSensorless:
    def __init__(self, config):
        self.printer = config.get_printer()
        self.samples = config.getint('samples', 5, minval=2)
        # Taps de chauffe ignores avant de mesurer : le 1er contact "tasse"
        # systematiquement (jeu mecanique), il fausse le spread.
        self.warmup_taps = config.getint('warmup_taps', 1, minval=0)
        self.samples_tolerance = config.getfloat('samples_tolerance', 0.10,
                                                  above=0.)
        self.max_taps = config.getint('max_taps', 15, minval=2)
        self.tap_speed = config.getfloat('fine_speed', 20.0, above=0.)
        self.tap_accel = config.getfloat('fine_accel', 1000.0, above=0.)
        self.travel_speed = config.getfloat('travel_speed', 40.0, above=0.)
        self.retract = config.getfloat('retract', 5.0, above=0.)
        # Overshoot physique dans le mur (offset cinematique). Tres limite.
        self.overshoot = config.getfloat('overshoot', 1.0, minval=0.3, maxval=3.)
        # Dwell entre taps pour vider le flag StallGuard / stabiliser le moteur.
        self.dwell_ms = config.getint('dwell_ms', 1500, minval=0)
        # Marge de rejet des outliers autour de la mediane.
        self.outlier_margin = config.getfloat('outlier_margin', 0.20, above=0.)
        # Courant pendant le home grossier (G28) ; 0 = ne pas changer.
        self.coarse_current = {
            'X': config.getfloat('coarse_current_x', 0.0, minval=0.),
            'Y': config.getfloat('coarse_current_y', 0.0, minval=0.),
        }
        # Courant pendant les TAPS : doit etre FRANC (proche du run) sinon
        # StallGuard ne sent pas le mur et le chariot file. 0 = utiliser run.
        self.tap_current = {
            'X': config.getfloat('tap_current_x', 0.0, minval=0.),
            'Y': config.getfloat('tap_current_y', 0.0, minval=0.),
        }
        self.run_current = {
            'X': config.getfloat('run_current_x', 1.2, above=0.),
            'Y': config.getfloat('run_current_y', 1.2, above=0.),
        }
        # sgthrs pendant les taps / a restaurer apres (0 = garder la valeur cfg).
        self.tap_sgthrs = {
            'X': config.getint('tap_sgthrs_x', 0),
            'Y': config.getint('tap_sgthrs_y', 0),
        }
        self.run_sgthrs = {
            'X': config.getint('run_sgthrs_x', 0),
            'Y': config.getint('run_sgthrs_y', 0),
        }
        self.restore_autotune = config.getboolean('restore_autotune', True)
        # Nb de re-homes de recuperation si aucun contact n'est trouve dans la
        # course attendue (= home de base foireux / depart mal positionne).
        self.max_rehomes = config.getint('max_rehomes', 2, minval=0)
        # Parametres obsoletes conserves pour compat printer.cfg (ignores).
        config.getfloat('fine_current_x', 0.0, minval=0.)
        config.getfloat('fine_current_y', 0.0, minval=0.)
        config.getint('fine_sgthrs_x', 0)
        config.getint('fine_sgthrs_y', 0)
        gcode = self.printer.lookup_object('gcode')
        gcode.register_command('YUMI_SENSORLESS_HOME', self.cmd_home,
                               desc="Home sensorless contre hard-stop + check")

    def _rail(self, ai):
        kin = self.printer.lookup_object('toolhead').get_kinematics()
        return kin.rails[ai]

    def _set_kin(self, axis, value):
        self.printer.lookup_object('gcode').run_script_from_command(
            "SET_KINEMATIC_POSITION %s=%.4f" % (axis, value))

    def cmd_home(self, gcmd):
        axis = gcmd.get('AXIS', 'Y').upper()
        if axis not in ('X', 'Y'):
            raise gcmd.error("YUMI_SENSORLESS_HOME: AXIS doit etre X ou Y")
        ai = AXIS_INDEX[axis]
        st = axis.lower()
        samples = gcmd.get_int('SAMPLES', self.samples, minval=2)
        warmup = gcmd.get_int('WARMUP', self.warmup_taps, minval=0)
        tol = gcmd.get_float('TOLERANCE', self.samples_tolerance, above=0.)
        max_taps = gcmd.get_int('MAX_TAPS', self.max_taps,
                                minval=samples + warmup)
        speed = gcmd.get_float('SPEED', self.tap_speed, above=0.)
        skip_base = gcmd.get_int('SKIP_BASE', 0)

        toolhead = self.printer.lookup_object('toolhead')
        gcode = self.printer.lookup_object('gcode')
        phoming = self.printer.lookup_object('homing')
        rail = self._rail(ai)
        mcu_endstop = rail.get_endstops()[0][0]
        hi = rail.get_homing_info()
        pos_endstop = hi.position_endstop
        # away = direction qui s'eloigne de l'endstop
        away = -1.0 if hi.positive_dir else 1.0
        # Cible physique du tap : overshoot mm AU-DELA du mur, dans la direction
        # de home. Atteinte via offset cinematique (cible logique = pos_endstop,
        # donc <= position_max ; le chariot surcourse physiquement).
        target_phys = pos_endstop - away * self.overshoot

        # Ouvre la fenetre StallGuard pour tout le home.
        gcode.run_script_from_command(
            "SET_TMC_FIELD STEPPER=stepper_%s FIELD=tcoolthrs VALUE=0" % st)

        # --- A) Home natif : localise le mur ---
        if not skip_base:
            cc = self.coarse_current[axis]
            if cc > 0:
                gcode.run_script_from_command(
                    "SET_TMC_CURRENT STEPPER=stepper_%s CURRENT=%.3f" % (st, cc))
            gcmd.respond_info("YUMI_SENSORLESS_HOME %s: home base..." % axis)
            gcode.run_script_from_command("G28 %s" % axis)

        # --- B) Prep taps : courant FRANC + sgthrs + accel constants ---
        gcode.run_script_from_command(
            "SET_TMC_FIELD STEPPER=stepper_%s FIELD=tcoolthrs VALUE=0" % st)
        tap_cur = self.tap_current[axis] or self.run_current[axis]
        gcode.run_script_from_command(
            "SET_TMC_CURRENT STEPPER=stepper_%s CURRENT=%.3f" % (st, tap_cur))
        tap_sg = self.tap_sgthrs[axis]
        if tap_sg > 0:
            gcode.run_script_from_command(
                "SET_TMC_FIELD STEPPER=stepper_%s FIELD=sgthrs VALUE=%d"
                % (st, tap_sg))
        eventtime = self.printer.get_reactor().monotonic()
        saved_accel = toolhead.get_status(eventtime).get('max_accel', 1000.)
        gcode.run_script_from_command(
            "SET_VELOCITY_LIMIT ACCEL=%.0f" % self.tap_accel)

        triggers = []
        rejects = 0
        valid_count = 0  # taps valides (chauffe inclus)
        no_contact = 0   # rejets consecutifs "aucun contact" (home suspect)
        rehomes = 0
        validated = False
        try:
            for attempt in range(1, max_taps + 1):
                if len(triggers) >= samples:
                    break
                # Recule du mur (referentiel reel), puis dwell.
                cur = toolhead.get_position()
                cur[ai] = pos_endstop + away * self.retract
                toolhead.manual_move(cur, self.travel_speed)
                toolhead.wait_moves()
                if self.dwell_ms > 0:
                    gcode.run_script_from_command("G4 P%d" % self.dwell_ms)
                # Offset cinematique : on fait croire au planner qu'on demarre
                # overshoot mm plus loin -> cible logique = pos_endstop (dans la
                # plage), surcourse physique = overshoot dans le mur.
                self._set_kin(axis, pos_endstop
                              + away * (self.retract + self.overshoot))
                target = list(toolhead.get_position())
                target[ai] = pos_endstop
                reason = None
                try:
                    epos = phoming.probing_move(mcu_endstop, target, speed,
                                                check_movement=True)
                except self.printer.command_error as e:
                    # Aucun trigger sur toute la course (retract + overshoot) :
                    # le chariot a parcouru PLUS que la distance de retraction
                    # sans buter -> le home de base a mal localise le mur.
                    self._set_kin(axis, pos_endstop)
                    reason = "aucun contact sur %.1fmm (%s)" % (
                        self.retract + self.overshoot, str(e).split('\n')[0])
                else:
                    # PREUVE DE CONTACT : le trigger doit tomber AVANT la cible
                    # commandee. gap = distance trigger->cible (coord logiques).
                    # Vrai mur -> gap ~ overshoot ; faux (arrive a la cible sans
                    # buter) -> gap ~ 0.
                    gap = abs(epos[ai] - pos_endstop)
                    trig = epos[ai] - away * self.overshoot  # mur reel
                    self._set_kin(axis, pos_endstop)  # recale au mur
                    if gap < self.overshoot * 0.5:
                        reason = "cible atteinte sans buter (gap=%.4f)" % gap

                if reason is not None:
                    rejects += 1
                    no_contact += 1
                    gcmd.respond_info("tap %d rejete: %s" % (attempt, reason))
                    # Pas de contact 2x de suite = home de base foireux / depart
                    # mal positionne -> re-home de recuperation (borne), sinon
                    # erreur franche (jamais de faux zero).
                    if no_contact >= 2:
                        if rehomes >= self.max_rehomes:
                            raise gcmd.error(
                                "YUMI_SENSORLESS_HOME %s: aucun contact apres "
                                "%d re-home(s) -> position de depart ou butee "
                                "non fiable, home avorte" % (axis, rehomes))
                        gcmd.respond_info(
                            "YUMI_SENSORLESS_HOME %s: home de base suspect "
                            "(pas de contact) -> re-home de recuperation %d/%d"
                            % (axis, rehomes + 1, self.max_rehomes))
                        cc = self.coarse_current[axis]
                        if cc > 0:
                            gcode.run_script_from_command(
                                "SET_TMC_CURRENT STEPPER=stepper_%s "
                                "CURRENT=%.3f" % (st, cc))
                        gcode.run_script_from_command("G28 %s" % axis)
                        gcode.run_script_from_command(
                            "SET_TMC_CURRENT STEPPER=stepper_%s CURRENT=%.3f"
                            % (st, tap_cur))
                        rehomes += 1
                        no_contact = 0
                    continue

                no_contact = 0
                valid_count += 1
                if valid_count <= warmup:
                    gcmd.respond_info("tap %d: pos=%.4f gap=%.4f (chauffe, ignore)"
                                      % (attempt, trig, gap))
                    continue
                triggers.append(trig)
                # Fenetre glissante : on valide des que les `samples` derniers
                # taps concordent dans `tol` (chariot stabilise contre le mur).
                # Les taps de tassement initiaux ne forment pas de fenetre
                # stable -> ecartes automatiquement, sans nombre fixe de chauffe.
                if len(triggers) >= samples:
                    wspread = max(triggers[-samples:]) - min(triggers[-samples:])
                    gcmd.respond_info(
                        "tap %d: pos=%.4f gap=%.4f fenetre=%.4f/%.4f"
                        % (attempt, trig, gap, wspread, tol))
                    if wspread <= tol:
                        validated = True
                        break
                else:
                    gcmd.respond_info("tap %d: pos=%.4f gap=%.4f (%d/%d)"
                                      % (attempt, trig, gap,
                                         len(triggers), samples))
        finally:
            gcode.run_script_from_command(
                "SET_VELOCITY_LIMIT ACCEL=%.0f" % saved_accel)
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
                    logging.info("YUMI_SENSORLESS_HOME autotune: %s", e)

        # --- C) Decision : le zero est le hard-stop (pos_endstop). Les taps
        # servent a VERIFIER la repetabilite, pas a calculer le zero. ---
        self._set_kin(axis, pos_endstop)
        cur = toolhead.get_position()
        cur[ai] = pos_endstop + away * self.retract
        toolhead.manual_move(cur, self.travel_speed)
        toolhead.wait_moves()

        if len(triggers) < samples:
            gcmd.respond_info(
                "YUMI_SENSORLESS_HOME %s: repetabilite NON etablie "
                "(%d taps valides / %d, %d rejetes) -> home natif conserve, "
                "zero pose en butee" % (axis, len(triggers), samples, rejects))
            return

        # Fenetre finale = les `samples` derniers taps (stabilises si validated).
        window = triggers[-samples:]
        spread = max(window) - min(window)
        mean = sum(window) / len(window)
        ok = validated and spread <= tol
        gcmd.respond_info(
            "YUMI_SENSORLESS_HOME %s %s: %d taps valides (%d rejetes) -> "
            "moyenne=%.4f spread=%.4fmm (tol=%.4f). Zero pose en butee=%.4f"
            % (axis, "OK" if ok else "IMPRECIS", len(triggers), rejects,
               mean, spread, tol, pos_endstop))


def load_config(config):
    return YumiSensorless(config)
