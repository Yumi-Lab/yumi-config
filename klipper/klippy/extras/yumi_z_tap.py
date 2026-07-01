import logging


class ZTap:
    def __init__(self, config):
        self.printer = config.get_printer()
        self.name = config.get_name()

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
        # Nb max de dwell pour attendre que le switch repasse OUVERT avant de
        # relancer une descente (anti "Probe triggered prior to movement").
        self.settle_retries = config.getint("settle_retries", 10)
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
        try:
            self._probe_with_pressure_switch(gcmd)
        finally:
            probe_pressure.speed = saved_speed
            probe_pressure.sample_count = saved_samples

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
        # Lance un palpage. Si le switch est encore declenche au depart
        # (vibration du chassis pas retombee, ou maintien manuel), probing_move
        # ne bouge pas et leve "Probe triggered prior to movement"
        # (homing.py: check_no_movement). On attend (dwell) et on reessaie,
        # jusqu'a settle_retries fois, le temps que le switch se rouvre, au
        # lieu de planter. La lecture query_endstop du capteur de pression
        # n'est pas fiable -> on reagit a l'echec reel du palpage.
        for i in range(self.settle_retries + 1):
            try:
                return probe_pressure.run_probe(gcmd)
            except self.printer.command_error as e:
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
        for attempt in range(self.max_probe_times):
            # Palpage tolerant : attend que le switch se rouvre s'il est encore
            # declenche (vibration/maintien), au lieu de planter en
            # "Probe triggered prior to movement".
            zcur = self._run_probe_settled(gcmd, probe_pressure, gcode)

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
                # Lift to safe_z above Z=0
                toolhead.manual_move([None, None, self.safe_z],
                                     self.lift_speed)
                gcode.run_script_from_command("M400")
                return

            # z_hop inter-tap, toujours clampe : ne peut jamais depasser la course
            if self.z_hop:
                pos = toolhead.get_position()
                lift_z = min(pos[2] + self.z_hop, self.z_max)
                toolhead.manual_move([None, None, lift_z], self.lift_speed)

        raise gcmd.error(
            "Pressure probe failed: %d/%d stable after %d taps "
            "(%d rejets vibration)"
            % (stable_count, self.samples, self.max_probe_times, spurious))


def load_config(config):
    return ZTap(config)
