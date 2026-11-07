import logging


class ZTap:
    # Extra descent allowed once the declared travel is used up without a contact: covers
    # the slack between position_max and the real top of the Z axis, bounded so a dead
    # switch never drives the nozzle far into the bed.
    SEARCH_EXTENSION_MM = 20.0
    # stealthChop at every speed during the tap descents (en_spreadcycle off, no TPWMTHRS ceiling)
    TAP_MODE_FIELDS = {"en_spreadcycle": 0, "tpwmthrs": 0}

    def __init__(self, config):
        self.printer = config.get_printer()
        self.name = config.get_name()
        self._sampler = None
        self._normal_current = None
        self._normal_mode = None

        # Read parameters from config file
        # (pressure_switch_x/y sont lus plus bas, UNIQUEMENT en mode switch :
        # en mode mesh-zero ils sont inutiles -> non lus -> Klipper interdit
        # leur declaration.)
        self.compression_offset = config.getfloat('compression_offset', 0.2)
        self.max_probe_times = config.getint('max_probe_times', 6)
        self.z_hop = config.getfloat("z_hop", 10.0)
        self.samples_tolerance = config.getfloat("samples_tolerance", 0.02)
        self.samples = config.getint("samples", 2)
        self.probe_delay = config.getfloat("probe_delay", 0.5)  # seconds
        self.safe_z = config.getfloat("safe_z", 10.0)
        self.travel_speed = config.getfloat("travel_speed", 30.0)
        # Acceleration Z dediee a la descente de tap (mm/s^2), scopee
        # strictement a cette phase via save/restore autour du probe -- ne
        # touche jamais le max_z_accel des impressions/autres mouvements.
        # 0 = ne rien changer (herite le max_z_accel global de la kinematique).
        self.tap_accel = config.getfloat("tap_accel", 0., minval=0.)
        # Nb max de dwell pour attendre que le switch repasse OUVERT avant de
        # relancer une descente (anti "Probe triggered prior to movement").
        self.settle_retries = config.getint("settle_retries", 10)
        # Z run current during the whole tap (the Z position is unknown until the contact):
        # a dead pressure switch then makes the motor give way instead of pressing the
        # nozzle into the bed. 0 = keep the [tmc2209 stepper_z] current.
        self.tap_current = config.getfloat("tap_current", 0., minval=0.)
        # Tolerance de validation Z=0/maillage : a la fin du tap, on verifie
        # que le maillage charge vaut ~0 a la coordonnee du tap (sinon Z=0 et
        # le mesh sont decales). 0 = desactive la verif.
        self.mesh_zero_tol = config.getfloat("mesh_zero_tol", 0.05, minval=0.)
        # Tolerance XY (mm) pour comparer le point de tap au
        # zero_reference_position du [bed_mesh] (verif statique, sans mesh charge).
        self.mesh_zero_xy_tol = config.getfloat("mesh_zero_xy_tol", 1.0,
                                                minval=0.)
        # Si True (defaut), le point de tap par defaut = [bed_mesh]
        # zero_reference_position (source unique, Z=0 pile sur le zero du mesh).
        # Mettre False sur les machines a switch/fixture dedie hors plateau
        # (revient a pressure_switch_x/y).
        self.tap_at_bed_mesh_zero_position = config.getboolean(
            "tap_at_bed_mesh_zero_position", True)
        # True si l'option est declaree explicitement (vs defaut).
        self.tap_at_mesh_zero_explicit = (
            config.get('tap_at_bed_mesh_zero_position', None) is not None)

        # Y a-t-il un [bed_mesh] zero_reference_position ? (config brute, dispo
        # au demarrage quel que soit l'ordre de chargement des modules)
        has_zrp = (config.has_section('bed_mesh')
                   and config.getsection('bed_mesh').get(
                       'zero_reference_position', None) is not None)

        # Mode mesh-zero : le tap suit zero_reference_position. Dans ce mode
        # pressure_switch_x/y sont INUTILES -> on ne les lit PAS, donc Klipper
        # interdit leur declaration (Option not valid au boot). Ils ne sont
        # lus/declarables qu'en mode switch.
        self.mesh_zero_mode = self.tap_at_bed_mesh_zero_position and has_zrp
        if self.mesh_zero_mode:
            self.pressure_switch_x = None
            self.pressure_switch_y = None
        elif (self.tap_at_bed_mesh_zero_position
              and self.tap_at_mesh_zero_explicit and not has_zrp):
            # Opt-in explicite mais pas de zero_reference_position : erreur dure
            # des le boot -> force la correction du printer.cfg.
            raise config.error(
                "yumi_z_tap: tap_at_bed_mesh_zero_position=True mais [bed_mesh] "
                "zero_reference_position est absent. Definis-le, ou mets "
                "tap_at_bed_mesh_zero_position: False.")
        else:
            # Mode switch (flag False, ou defaut sans zero_reference_position)
            # -> pressure_switch_x/y sont utilises (et donc declarables).
            self.pressure_switch_x = config.getfloat('pressure_switch_x', 30.0)
            self.pressure_switch_y = config.getfloat('pressure_switch_y', 200.0)

        # Register gcode command
        gcode = self.printer.lookup_object('gcode')
        gcode.register_command('YUMI_Z_TAP', self.cmd_Z_TAP,
                              desc="Set Z=0 at nozzle contact via pressure switch")
        gcode.register_command('YUMI_Z_TRACE', self.cmd_Z_TRACE,
                              desc="Record the Z driver load outside a tap: ACTION=START [PATH=] | MARK TEXT= | STOP")

    def _check_homed(self, gcmd):
        toolhead = self.printer.lookup_object('toolhead')
        if 'xy' not in toolhead.get_status(0).get('homed_axes', ''):
            raise gcmd.error("ZTap: X and Y must be homed first")

    def cmd_Z_TAP(self, gcmd):
        logging.info("[ZTap] Starting Z=0 calibration")
        self._check_homed(gcmd)

        # Per-call tap target. Defaults to the dedicated pressure switch.
        # X/Y allow tapping any reachable rigid point (e.g. metal reference);
        # COMPRESSION overrides the trigger->surface offset for that point.
        x_param = gcmd.get_float('X', None)
        y_param = gcmd.get_float('Y', None)
        self.active_compression = gcmd.get_float('COMPRESSION',
                                                 self.compression_offset)
        # TOLERANCE overrides the tap-to-tap stability threshold for this call
        # (e.g. a wider value for a noisier/farther point like a metal ref).
        self.active_tolerance = gcmd.get_float('TOLERANCE',
                                              self.samples_tolerance, above=0.)
        # SPEED overrides the probe descent speed (mm/s) for this call.
        self.active_speed = gcmd.get_float('SPEED', None, above=0.)
        # ACCEL overrides tap_accel (mm/s^2) for this call. 0 = tap_accel
        # config value (0 = ne rien changer, herite le max_z_accel global).
        self.active_accel = gcmd.get_float('ACCEL', self.tap_accel, minval=0.)
        # Point de tap par defaut :
        #  - tap_at_bed_mesh_zero_position=True -> [bed_mesh]
        #    zero_reference_position (source unique, Z=0 pile sur le zero du
        #    mesh, pas de doublon),
        #  - sinon -> pressure_switch_x/y (comportement historique).
        # Un X/Y explicite a l'appel surcharge toujours.
        # Point de tap par defaut selon le mode decide au demarrage :
        #  - mesh_zero_mode -> [bed_mesh] zero_reference_position,
        #  - sinon (mode switch) -> pressure_switch_x/y.
        # Un X/Y explicite a l'appel surcharge toujours.
        if self.mesh_zero_mode:
            zrp = self._get_mesh_zero_ref()
            if zrp is None:
                raise gcmd.error(
                    "Z_TAP: [bed_mesh] zero_reference_position devenu "
                    "illisible a l'execution.")
            default_x, default_y = zrp
        else:
            default_x, default_y = self.pressure_switch_x, self.pressure_switch_y
        self.tap_x = x_param if x_param is not None else default_x
        self.tap_y = y_param if y_param is not None else default_y

        gcode = self.printer.lookup_object('gcode')
        toolhead = self.printer.lookup_object('toolhead')
        probe_pressure = self.printer.lookup_object('probe_pressure')
        self.lift_speed = probe_pressure.get_lift_speed()
        # travel_speed from config (set in __init__)

        # Declare a high Z so the probe has room to descend, but leave
        # headroom above for the inter-tap z_hop so lifts never exceed
        # position_max (a tall tap point triggers near the top, and
        # pos[2] + z_hop would otherwise go out of range).
        settings = self.printer.lookup_object('configfile').get_status(0)['settings']
        z_max = settings['stepper_z']['position_max']
        self.z_max = z_max
        # z_offset du palpeur de reference [probe], SOUSTRAIT au calcul du Z=0
        # (convention Klipper standard, celle qu'ecrit Z_OFFSET_APPLY_PROBE :
        # z_offset POSITIF = buse plus PROCHE du plateau). Ainsi le workflow
        # client babystep + "Save Z offset" s'applique dans le bon sens.
        # IMPORTANT : on le recupere via l'OBJET probe de Klipper
        # (probe.get_offsets()), qui contient la valeur EFFECTIVE incluant le
        # bloc SAVE_CONFIG (#*# [probe] z_offset = ...). Lire le texte du
        # printer.cfg donnerait la valeur de base (souvent 0), pas la sauvegarde.
        # Surchargeable par Z_OFFSET= a l'appel (meme convention).
        probe = self.printer.lookup_object('probe', None)
        if probe is not None:
            cfg_z_offset = probe.get_offsets()[2]
        else:
            cfg_z_offset = settings.get('probe', {}).get('z_offset', 0.0)
        self.active_z_offset = gcmd.get_float('Z_OFFSET', cfg_z_offset)
        if abs(self.active_z_offset) > 0.5:
            gcmd.respond_info(
                "ATTENTION: z_offset=%.3f anormalement grand (>0.5mm) -> "
                "Z=0 sera decale d'autant. Verifie #*# [probe] z_offset "
                "dans printer.cfg." % self.active_z_offset)
        z_start = z_max - self.z_hop - 1.0
        gcode.run_script_from_command("SET_KINEMATIC_POSITION Z=%.1f" % z_start)

        # Position over the tap point, then tap
        gcmd.respond_info("Moving to tap point X=%.1f Y=%.1f..."
                          % (self.tap_x, self.tap_y))
        self._move_to_tap_point()
        gcode.run_script_from_command("M400")
        gcode.run_script_from_command("G4 P%d" % int(self.probe_delay * 1000))

        # Probe with pressure switch (nozzle touches physically)
        gcmd.respond_info("Probing with pressure switch...")
        # SPEED overrides the descent speed (else [probe_pressure] speed).
        saved_speed = probe_pressure.speed
        saved_samples = probe_pressure.sample_count
        if self.active_speed is not None:
            probe_pressure.speed = self.active_speed
        # yumi_z_tap fait son propre multi-tap (boucle stable_count). On force
        # un seul echantillon brut par run_probe pour que probe_pressure ne
        # fasse jamais son retract inter-echantillon (non clampe), qui peut
        # depasser position_max sur un faux trigger haut.
        probe_pressure.sample_count = 1
        # tap_accel/ACCEL= : ralentit uniquement la RAMPE d'acceleration de
        # la descente de tap (le probing_move de probe_pressure passe par le
        # move planner standard, qui applique max_z_accel de la kinematique
        # a tout mouvement Z pur -- pas de parametre d'accel dedie cote
        # probing_move, donc on le fait ici en encadrant l'appel).
        kin = toolhead.get_kinematics()
        saved_max_z_accel = kin.max_z_accel
        if self.active_accel > 0:
            kin.max_z_accel = self.active_accel
        tap_current = gcmd.get_float('CURRENT', self.tap_current, minval=0.)
        tmc_z = self.printer.lookup_object('tmc2209 stepper_z', None)
        self._normal_current = None
        self._normal_mode = None
        if tap_current > 0 and tmc_z is not None:
            st = tmc_z.get_status(self.printer.get_reactor().monotonic())
            self._normal_current = (st['run_current'], st['hold_current'])
            gcode.run_script_from_command(
                "SET_TMC_CURRENT STEPPER=stepper_z CURRENT=%.3f HOLDCURRENT=%.3f"
                % (tap_current, tap_current))
        if tmc_z is not None:
            # StallGuard4 (SG_RESULT) only exists in stealthChop: Z runs spreadCycle for its
            # torque at speed (C335 07/11), so the slow tap descents switch to stealthChop at
            # every speed and the normal mode comes back with the normal current.
            self._normal_mode = {f: tmc_z.fields.get_field(f) for f in self.TAP_MODE_FIELDS}
            for field, value in self.TAP_MODE_FIELDS.items():
                gcode.run_script_from_command(
                    "SET_TMC_FIELD STEPPER=stepper_z FIELD=%s VALUE=%d" % (field, value))
        trace = gcmd.get('TRACE', '')
        self._sampler_start(self.TRACE_DEFAULT_PATH if trace == '1' else trace, gcmd)
        try:
            self._probe_with_pressure_switch(gcmd)
        finally:
            self._sampler_stop(gcmd)
            probe_pressure.speed = saved_speed
            probe_pressure.sample_count = saved_samples
            kin.max_z_accel = saved_max_z_accel
            self._restore_z_current()

        # Validation : le maillage charge doit valoir ~0 a la coordonnee du tap
        self._validate_mesh_zero(gcmd)

        gcmd.respond_info("Z calibration complete!")
        logging.info("[ZTap] Z=0 set at pressure switch contact")

    def _get_mesh_zero_ref(self):
        # Retourne (x, y) de [bed_mesh] zero_reference_position, ou None si
        # absent / illisible. Lu via la config Klipper (inclut l'autosave).
        try:
            settings = self.printer.lookup_object(
                'configfile').get_status(0)['settings']
            zrp = settings.get('bed_mesh', {}).get(
                'zero_reference_position', None)
            if zrp is None:
                return None
            return float(zrp[0]), float(zrp[1])
        except (TypeError, ValueError, IndexError, KeyError):
            return None

    def _validate_mesh_zero(self, gcmd):
        # Valide que le point de tap (ou on pose Z=0) coincide avec le zero du
        # maillage. mesh_zero_tol == 0 -> tout est desactive.
        #
        # 1) Verif CONFIG (statique, marche meme sans mesh charge, donc valable
        #    quand BED_MESH_PROFILE LOAD est appele APRES YUMI_Z_TAP) :
        #    [bed_mesh] zero_reference_position doit == (tap_x, tap_y).
        # 2) Bonus : si un mesh est deja charge, on mesure calc_z au point de
        #    tap (doit etre ~0). Absence de mesh charge = normal -> on se tait.
        # Avertissements uniquement, ne bloque jamais le homing.
        if not self.mesh_zero_tol:
            return

        # --- 1) Verif statique sur zero_reference_position ---
        try:
            settings = self.printer.lookup_object(
                'configfile').get_status(0)['settings']
            zrp = settings.get('bed_mesh', {}).get(
                'zero_reference_position', None)
        except Exception as e:
            gcmd.respond_info("Maillage: lecture config impossible (%s)" % e)
            zrp = None

        if zrp is None:
            gcmd.respond_info(
                "ATTENTION maillage: pas de zero_reference_position dans "
                "[bed_mesh] -> Z=0 (tap X%.1f Y%.1f) et mesh potentiellement "
                "decales" % (self.tap_x, self.tap_y))
        else:
            try:
                zx, zy = float(zrp[0]), float(zrp[1])
            except (TypeError, ValueError, IndexError):
                gcmd.respond_info(
                    "Maillage: zero_reference_position illisible (%r)" % (zrp,))
                zx = zy = None
            if zx is not None:
                if (abs(zx - self.tap_x) <= self.mesh_zero_xy_tol
                        and abs(zy - self.tap_y) <= self.mesh_zero_xy_tol):
                    gcmd.respond_info(
                        "Maillage OK (config): zero_reference_position "
                        "(%.1f, %.1f) ~ point de tap (%.1f, %.1f)"
                        % (zx, zy, self.tap_x, self.tap_y))
                else:
                    gcmd.respond_info(
                        "ATTENTION maillage: zero_reference_position "
                        "(%.1f, %.1f) != point de tap (%.1f, %.1f) -> Z=0 et "
                        "mesh DECALES. Mets [bed_mesh] zero_reference_position: "
                        "%.1f, %.1f puis BED_MESH_CALIBRATE + sauvegarde du "
                        "profil."
                        % (zx, zy, self.tap_x, self.tap_y,
                           self.tap_x, self.tap_y))

        # --- 2) Bonus : verif sur le mesh reellement charge (s'il y en a un) ---
        bed_mesh = self.printer.lookup_object('bed_mesh', None)
        if bed_mesh is None:
            return
        z_mesh = bed_mesh.get_mesh()
        if z_mesh is None:
            return  # pas de mesh charge = normal dans la sequence -> silence
        try:
            mesh_z = z_mesh.calc_z(self.tap_x, self.tap_y)
        except Exception as e:
            gcmd.respond_info(
                "Maillage: calc_z impossible au point de tap (%s)" % e)
            return
        if abs(mesh_z) <= self.mesh_zero_tol:
            gcmd.respond_info(
                "Maillage OK (charge): mesh=%.4f au point de tap X%.1f Y%.1f "
                "(<= %.3f)" % (mesh_z, self.tap_x, self.tap_y,
                               self.mesh_zero_tol))
        else:
            gcmd.respond_info(
                "ATTENTION maillage: mesh=%.4f au point de tap X%.1f Y%.1f "
                "(> %.3f) -> Z=0 et maillage charge DECALES de %.4f mm"
                % (mesh_z, self.tap_x, self.tap_y, self.mesh_zero_tol, mesh_z))

    def _move_to_tap_point(self):
        toolhead = self.printer.lookup_object('toolhead')
        # Move XY only — Z stays where it is, probe will descend
        toolhead.manual_move([self.tap_x, self.tap_y, None],
                             self.travel_speed)
        logging.info("[ZTap] Moved to tap point: X=%.1f, Y=%.1f",
                     self.tap_x, self.tap_y)

    def _run_probe_settled(self, gcmd, probe_pressure, gcode):
        try:
            return self._run_probe_settled_inner(gcmd, probe_pressure, gcode)
        finally:
            self._arm_stall(False)

    def _run_probe_settled_inner(self, gcmd, probe_pressure, gcode):
        # Lance un palpage. Si le switch est encore declenche au depart
        # (vibration du chassis pas retombee, ou maintien manuel), probing_move
        # ne bouge pas et leve "Probe triggered prior to movement"
        # (homing.py: check_no_movement). On attend (dwell) et on reessaie,
        # jusqu'a settle_retries fois, le temps que le switch se rouvre, au
        # lieu de planter. La lecture query_endstop du capteur de pression
        # n'est pas fiable -> on reagit a l'echec reel du palpage.
        for i in range(self.settle_retries + 1):
            self._arm_stall(True)
            try:
                return probe_pressure.run_probe(gcmd)
            except self.printer.command_error as e:
                if self._stalled():
                    # the host ended the descent: the probe only says 'no trigger'
                    self._lift_and_unhome_z()
                    raise gcmd.error(
                        "ZTap: Z motor stalled during the descent (driver load under %d %d times "
                        "in %.1f s) -> stopped at once, nozzle lifted %.0f mm, Z unhomed. Check "
                        "what blocks Z and the Z current (tap_current)"
                        % (self.STALL_SG, self.STALL_SAMPLES, self.STALL_WINDOW_S, self.safe_z))
                if "prior to movement" not in str(e):
                    raise
                # Nettoie un eventuel etat multi-probe laisse en suspens
                try:
                    probe_pressure.multi_probe_end()
                except Exception:
                    pass
                if i >= self.settle_retries:
                    raise gcmd.error(
                        "Switch pression encore declenche apres %d attentes "
                        "(vibration permanente / switch maintenu / coince)"
                        % self.settle_retries)
                gcmd.respond_info(
                    "Switch encore declenche (vibration) -> attente %.1fs "
                    "puis nouvelle tentative (%d/%d)"
                    % (self.probe_delay, i + 1, self.settle_retries))
                gcode.run_script_from_command("M400")
                gcode.run_script_from_command(
                    "G4 P%d" % int(self.probe_delay * 1000))

    def _probe_with_pressure_switch(self, gcmd):
        toolhead = self.printer.lookup_object('toolhead')
        gcode = self.printer.lookup_object('gcode')
        probe_pressure = self.printer.lookup_object('probe_pressure')

        # Un trigger dont la remontee depasserait position_max s'est declenche
        # tres haut dans la descente : c'est un faux positif (vibration du
        # chassis), pas un vrai contact. Interdit de remonter (ca sortirait de
        # la course) : on saute le z_hop et on re-palpe, ce qui poursuit la
        # descente vers le vrai point.
        def _is_false_positive(z):
            return (z + self.z_hop) > self.z_max

        stable_count = 0
        zendstop_p = None
        spurious = 0
        extended = False
        for attempt in range(self.max_probe_times):
            # Palpage tolerant : attend que le switch se rouvre s'il est encore
            # declenche (vibration/maintien), au lieu de planter en
            # "Probe triggered prior to movement".
            try:
                self._trace_mark("descent")
                zcur = self._run_probe_settled(gcmd, probe_pressure, gcode)
                self._trace_mark("contact z=%.4f" % zcur[2])
            except self.printer.command_error as e:
                if "No trigger" not in str(e):
                    raise
                if extended:
                    raise gcmd.error(
                        "ZTap: pressure switch never triggered over the whole Z travel + "
                        "%.0f mm -> check the pressure sensor (wiring, [probe_pressure] pin)"
                        % self.SEARCH_EXTENSION_MM)
                # The search starts from a declared Z (z_max - z_hop - 1): a head parked at
                # the very top is farther than that from the bed (C335 2026-11-07: 400 mm
                # down, stopped just above the plate). Go on for a bounded extra distance.
                extended = True
                stable_count = 0
                zendstop_p = None
                z_min = self.printer.lookup_object('configfile').get_status(0)[
                    'settings']['stepper_z']['position_min']
                gcode.run_script_from_command(
                    "SET_KINEMATIC_POSITION Z=%.3f SET_HOMED=Z"
                    % (z_min + self.SEARCH_EXTENSION_MM))
                gcmd.respond_info(
                    "ZTap: no contact over the declared travel -> searching %.0f mm further"
                    % self.SEARCH_EXTENSION_MM)
                continue

            if _is_false_positive(zcur[2]):
                spurious += 1
                stable_count = 0
                zendstop_p = None
                gcmd.respond_info(
                    "Tap rejete (vibration chassis, z=%.4f > max-z_hop) "
                    "-> pas de remontee, on poursuit la descente (%d)"
                    % (zcur[2], spurious))
                # Pas de remontee : la descente reprend au prochain run_probe,
                # une fois le switch revenu ouvert (verifie en tete de boucle).
                continue

            # Stabilite sur taps valides consecutifs
            if zendstop_p is not None:
                diff_z = abs(zcur[2] - zendstop_p[2])
                if diff_z <= self.active_tolerance:
                    stable_count += 1
                    gcmd.respond_info("Tap OK (diff=%.4fmm, stable %d/%d)"
                                      % (diff_z, stable_count, self.samples))
                else:
                    stable_count = 1
                    gcmd.respond_info("Tap drift (diff=%.4fmm) — reset"
                                      % diff_z)
            else:
                stable_count = 1
            zendstop_p = zcur

            if stable_count >= self.samples:
                # VALIDE
                trigger_z = zendstop_p[2]
                # Le vrai Z=0 (surface libre) est `lift` AU-DESSUS du contact :
                #  - compression : le nez s'est enfonce dans le switch -> on
                #    detecte plus bas que la surface reelle -> compense vers
                #    le haut,
                #  - z_offset [probe] : convention Klipper standard (celle
                #    qu'ecrit Z_OFFSET_APPLY_PROBE apres un babystep) ->
                #    POSITIF = buse plus PROCHE -> se SOUSTRAIT.
                # On pose le zero arithmetiquement (pas de mouvement) : le
                # point de contact vaut -lift. Un lift negatif (z_offset >
                # compression) marche donc sans jamais presser la buse dans
                # le plateau.
                lift = self.active_compression - self.active_z_offset
                gcode.run_script_from_command(
                    "SET_KINEMATIC_POSITION Z=%.6f" % -lift)
                gcmd.respond_info(
                    "VALIDATED: trigger_z=%.4f -> Z=0 pose %.4f au-dessus du tap "
                    "(compression=%.4f - z_offset=%.4f)"
                    % (trigger_z, lift, self.active_compression,
                       self.active_z_offset))
                # Lift to safe_z above Z=0, at the normal current: the moves that follow a tap
                # (a 100 mm mesh lift) stalled on the C335 at the reduced one (07/11)
                self._restore_z_current()
                toolhead.manual_move([None, None, self.safe_z],
                                     self.lift_speed)
                gcode.run_script_from_command("M400")
                return

            # z_hop inter-tap, toujours clampe : ne peut jamais depasser la course
            if self.z_hop:
                pos = toolhead.get_position()
                lift_z = min(pos[2] + self.z_hop, self.z_max)
                self._trace_mark("lift")
                toolhead.manual_move([None, None, lift_z], self.lift_speed)

        # No stable contact within max_probe_times: the measured signature of a Z stalling
        # against the bed (C335 07/11: 32 triggers in 30 s, 1.2 mm apart). A single false
        # trigger (vibration, someone touching the printer) only resets the stable count above.
        self._lift_and_unhome_z()
        raise gcmd.error(
            "ZTap: no stable contact: %d/%d stable after %d taps (%d rejected as vibration) "
            "-> Z stalls against the bed or the pressure sensor is unreliable; nozzle lifted "
            "%.0f mm, Z unhomed. Check the Z current (tap_current) and the sensor"
            % (stable_count, self.samples, self.max_probe_times, spurious, self.safe_z))

    # ── Z driver sampler: stall detection during the tap descents, optional TRACE recording ──
    # One reader for both, so the TMC UART is never asked twice (two readers at 2 ms starved
    # Klipper's own TMC checks: 'Timeout on wait for tmcuart_response', C335 08/11).
    # Stall (C335 08/11, Z driven into its end stop, 6 mm/s, 0.7 A, stealthChop): the load
    # (SG_RESULT) of a free descent stayed 212-244; at the stop it fell under 15 within 0.3 s and
    # kept swinging 2-250. spreadCycle shows nothing (SG_RESULT 0, cs_actual fixed), which is
    # why the descents run in stealthChop (TAP_MODE_FIELDS).
    STALL_SG = 50               # 4x under the lowest free-descent reading
    STALL_SAMPLES = 3           # readings under STALL_SG while Z goes down...
    STALL_WINDOW_S = 0.3        # ...within this window: a single low reading never stops a tap
    SAMPLE_PERIOD_S = 0.025
    TRACE_DEFAULT_PATH = "/tmp/yumi_z_tap_trace.csv"

    def cmd_Z_TRACE(self, gcmd):
        """Same recording as YUMI_Z_TAP TRACE, driven by hand: a stall provoked on purpose
        (Z carriage driven into its end stop, nozzle off the bed) to learn its pattern."""
        action = gcmd.get('ACTION').upper()
        if action == 'START':
            if self._sampler is not None:
                raise gcmd.error("YUMI_Z_TRACE: a recording is already running")
            self._sampler_start(gcmd.get('PATH', self.TRACE_DEFAULT_PATH), gcmd)
        elif action == 'MARK':
            self._trace_mark(gcmd.get('TEXT'))
        elif action == 'STOP':
            self._sampler_stop(gcmd)
        else:
            raise gcmd.error("YUMI_Z_TRACE: ACTION=%s unknown, START / MARK / STOP" % action)

    def _sampler_start(self, path, gcmd):
        """path: also record every sample (TRACE), else stall detection only."""
        reactor = self.printer.get_reactor()
        kin = self.printer.lookup_object('toolhead').get_kinematics()
        stepper = kin.rails[2].get_steppers()[0]
        sp = {"t0": reactor.monotonic(), "tmc": self.printer.lookup_object('tmc2209 stepper_z'),
              "stepper": stepper, "mcu": stepper.get_mcu(), "f": None, "path": path, "rows": 0,
              "flushed": 0., "prev_z": None, "low": [], "armed": False, "stalled": False}
        if path:
            sp["f"] = open(path, "w")
            sp["f"].write("t,sg_result,z,event,drv_status\n")
            gcmd.respond_info("ZTap: TRACE -> %s" % path)
        self._sampler = sp
        sp["timer"] = reactor.register_timer(self._sample, reactor.NOW)

    def _sample(self, eventtime):
        sp = self._sampler
        reactor = self.printer.get_reactor()
        if sp is None:
            return reactor.NEVER
        try:
            tmc = sp["tmc"]
            sg = tmc.fields.get_field('sg_result', tmc.mcu_tmc.get_register('SG_RESULT'), 'SG_RESULT')
            drv = tmc.mcu_tmc.get_register('DRV_STATUS') if sp["f"] else 0
            st = sp["stepper"]
            z = st.mcu_to_commanded_position(
                st.get_past_mcu_position(sp["mcu"].estimated_print_time(eventtime)))
        except Exception as e:
            logging.info("ZTap sampler: read failed: %s", e)
            return eventtime + 0.05
        if self._sampler is not sp:
            # the tap ended while this UART read was in flight (writing to the closed trace
            # file raised in a reactor timer and shut Klipper down, C335 08/11)
            return reactor.NEVER
        going_down = sp["prev_z"] is not None and z < sp["prev_z"] - 1e-4
        sp["prev_z"] = z
        if sp["armed"] and going_down and sg < self.STALL_SG and not sp["stalled"]:
            sp["low"] = [t for t in sp["low"] if eventtime - t <= self.STALL_WINDOW_S] + [eventtime]
            if len(sp["low"]) >= self.STALL_SAMPLES:
                sp["stalled"] = True
                self._stop_descent()
        if sp["f"]:
            sp["f"].write("%.4f,%d,%.4f,,%08x\n" % (eventtime - sp["t0"], sg, z, drv))
            sp["rows"] += 1
            if eventtime - sp["flushed"] > 0.5:
                sp["f"].flush()  # an emergency stop must not lose the samples
                sp["flushed"] = eventtime
        return eventtime + self.SAMPLE_PERIOD_S

    def _stop_descent(self):
        """End the running probing move from the host, as Klipper does for a homing that times
        out: the MCU stops the Z steppers at once and the probe reports no trigger."""
        trsync = self.printer.lookup_object('probe_pressure').mcu_probe.mcu_endstop._dispatch._trsyncs[0]
        trsync._trsync_trigger_cmd.send([trsync.get_oid(), trsync.REASON_HOST_REQUEST])
        logging.info("[ZTap] Z stall detected, descent stopped from the host")
        self._trace_mark("stall")

    def _arm_stall(self, armed):
        sp = self._sampler
        if sp is not None:
            sp["armed"] = armed
            sp["low"] = []
            sp["prev_z"] = None

    def _stalled(self):
        return self._sampler is not None and self._sampler["stalled"]

    def _trace_mark(self, event):
        sp = self._sampler
        if sp is not None and sp["f"]:
            t = self.printer.get_reactor().monotonic() - sp["t0"]
            sp["f"].write("%.4f,,,%s,\n" % (t, event))

    def _sampler_stop(self, gcmd):
        sp, self._sampler = self._sampler, None
        if sp is None:
            return
        self.printer.get_reactor().unregister_timer(sp["timer"])
        if sp["f"]:
            sp["f"].close()
            gcmd.respond_info("ZTap: TRACE %d samples in %.1f s -> %s" % (
                sp["rows"], self.printer.get_reactor().monotonic() - sp["t0"], sp["path"]))

    def _restore_z_current(self):
        """Back to the [tmc2209 stepper_z] current and chopper mode once the descents are over.
        Runs before the final lift and again in the finally: idempotent."""
        gcode = self.printer.lookup_object('gcode')
        if self._normal_current is not None:
            gcode.run_script_from_command(
                "SET_TMC_CURRENT STEPPER=stepper_z CURRENT=%.3f HOLDCURRENT=%.3f"
                % self._normal_current)
            self._normal_current = None
        if self._normal_mode is not None:
            for field, value in self._normal_mode.items():
                gcode.run_script_from_command(
                    "SET_TMC_FIELD STEPPER=stepper_z FIELD=%s VALUE=%d" % (field, value))
            self._normal_mode = None

    def _lift_and_unhome_z(self):
        """Lift away first (a hot nozzle must not stay on the bed), from where the nozzle really
        is: after a stall the Z position cannot be trusted. Then forget Z."""
        toolhead = self.printer.lookup_object('toolhead')
        self._restore_z_current()
        self.printer.lookup_object('gcode').run_script_from_command(
            "SET_KINEMATIC_POSITION Z=0 SET_HOMED=Z")
        toolhead.manual_move([None, None, self.safe_z], self.lift_speed)
        toolhead.wait_moves()
        toolhead.get_kinematics().clear_homing_state("z")


def load_config(config):
    return ZTap(config)
