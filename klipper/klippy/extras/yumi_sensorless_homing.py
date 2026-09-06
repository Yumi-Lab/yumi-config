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
#      commandee (= il a bute le mur), hors zone de deceleration (= pas un
#      artefact d'arret), et pas trop avant (= pas un faux stall / obstacle).
#   C) Fenetre glissante de `samples` taps concordants, puis
#      SET_KINEMATIC_POSITION AXIS=position_endstop (le zero est le hard-stop).
#
# ATTENTION CoolStep/autotune (cause racine de l'echec "Y ne home plus a chaud"
# 2026-08-12) : pendant un homing move, le tmc.py de Klipper force StealthChop
# (2209/SG4) et TCOOLTHRS=0xFFFFF (si le champ vaut 0), ce qui arme CoolStep a
# TOUTES les vitesses si semin>0 -> courant effectif reduit jusqu'a IRUN/4
# (seimin=1). Il FAUT couper semin pendant le home (cf. _open_sg_window).
#
# Seuil StallGuard : la phase A et les taps n'heritent JAMAIS du registre tel
# quel (un home avorte, un SET_TMC_FIELD a la main, un run_sgthrs applique
# par un module tiers laissent une valeur imprevisible -> faux stall des le
# depart du tap, gap ~ retract, reproductible au micron). Sans coarse_sgthrs /
# tap_sgthrs explicites, la reference est le sg4_thrs de [autotune_tmc
# stepper_X] (celui que l'autotune repose au boot et apres chaque home),
# sinon la valeur lue a l'entree. Le marqueur "home base..." nomme la source.
#
# Repetabilite : StallGuard n'evalue le stall qu'une fois par PAS ENTIER, donc
# la position de trigger est quantifiee au pas entier (rotation_distance /
# full_steps_per_rotation, ex. 39/200 = 0.195 mm). La tolerance effective est
# planchee a un pas entier : en dessous, la fenetre ne peut converger que si
# tous les taps tombent sur le meme pas (convergence par chance).
#
# Options ([yumi_sensorless_homing], values in the generator's catalog):
#   samples               taps concordants requis dans la fenetre glissante (default 5)
#   warmup_taps           taps de chauffe ignores avant de mesurer (default 1)
#   samples_tolerance     spread max de la fenetre en mm, planche a 1 pas entier (default 0.10)
#   max_taps              budget total de taps par home (default 15)
#   fine_speed            vitesse des taps en mm/s (default 20)
#   fine_accel            acceleration des taps en mm/s^2, restauree apres (default 1000)
#   travel_speed          vitesse des reculs en mm/s (default 40)
#   retract               recul avant chaque tap en mm (default 5)
#   overshoot             surcourse commandee dans le mur en mm (default 1, 0.3..3)
#   dwell_ms              pause entre taps pour vider le flag StallGuard (default 1500)
#   outlier_margin        marge de rejet autour de la mediane en mm (default 0.20)
#   coarse_current_x, coarse_current_y   courant du home natif (G28) en A, 0 = inchange
#   tap_current_x, tap_current_y         courant des taps en A, 0 = run_current
#   run_current_x, run_current_y         courant restaure en sortie en A (default 1.2)
#   coarse_sgthrs_x, coarse_sgthrs_y     sgthrs du home natif, 0 = reference autotune
#   tap_sgthrs_x, tap_sgthrs_y           sgthrs des taps, 0 = reference autotune
#   run_sgthrs_x, run_sgthrs_y           sgthrs re-applique APRES un home reussi seulement,
#                                        0 = laisser l'autotune (recommande, cf. catalogue)
#   restore_autotune      AUTOTUNE_TMC en sortie de home (default True)
#   max_rehomes           re-homes de recuperation si aucun contact (default 2)
#   fine_current_x, fine_current_y, fine_sgthrs_x, fine_sgthrs_y   obsoletes, ignores
# Command: YUMI_SENSORLESS_HOME AXIS=X|Y [SAMPLES=] [WARMUP=] [TOLERANCE=]
#   [MAX_TAPS=] [SPEED=] [SKIP_BASE=1]  -- a appeler depuis le homing_override.
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
        # sgthrs pendant le home de base (G28). 0 = ne pas toucher au registre.
        # ATTENTION : "ne pas toucher" = heriter de la valeur posee par
        # l'autotune (sg4_thrs, ex 50), PAS du driver_SGTHRS du printer.cfg.
        self.coarse_sgthrs = {
            'X': config.getint('coarse_sgthrs_x', 0),
            'Y': config.getint('coarse_sgthrs_y', 0),
        }
        # sgthrs pendant les taps / a restaurer apres (0 = ne pas toucher).
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
        # Reference sgthrs par axe quand coarse_sgthrs/tap_sgthrs valent 0 :
        # le sg4_thrs de [autotune_tmc stepper_X], lu ici sans le marquer
        # (note_valid=False : il appartient a l'autotune). None si absent.
        # Pas entier par axe : full_steps_per_rotation du stepper (defaut
        # Klipper 200) ; la rotation_distance effective (gear_ratio inclus)
        # vient de l'objet stepper a l'execution.
        self.autotune_sgthrs = {}
        self.full_steps = {}
        for axis in ('X', 'Y'):
            st = axis.lower()
            sg = None
            sec = 'autotune_tmc stepper_%s' % st
            if config.has_section(sec):
                sg = config.getsection(sec).getint('sg4_thrs', None,
                                                   note_valid=False)
            self.autotune_sgthrs[axis] = sg
            fs = 200
            sec = 'stepper_%s' % st
            if config.has_section(sec):
                fs = config.getsection(sec).getint(
                    'full_steps_per_rotation', 200, minval=1,
                    note_valid=False)
            self.full_steps[axis] = fs
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

    def _unhome(self, ai):
        # Retire le statut "homed" de l'axe avant un abort : les _set_kin des
        # taps ont marque l'axe homed sur un referentiel possiblement FAUX.
        # API Python directe. Ne JAMAIS passer par la commande
        # SET_KINEMATIC_POSITION CLEAR=... : son SET_HOMED vaut 'xyz' par
        # defaut, elle force-home donc les DEUX AUTRES axes en effet de bord
        # (dont un Z jamais home), et un force_move ancien sans CLEAR=
        # force-home les trois sans rien effacer.
        kin = self.printer.lookup_object('toolhead').get_kinematics()
        clear = getattr(kin, 'clear_homing_state', None)
        if clear is None:
            # Vieux Klipper : pas d'API d'unhome propre -> on n'aggrave rien
            # (comportement d'origine), l'erreur franche reste le garde-fou.
            return
        # Signature reelle : clear_homing_state(clear_axes) teste "axis_name
        # in clear_axes" avec des LETTRES ('x'/'y'/'z') -> passer la lettre
        # (un tuple d'indices int serait silencieusement sans effet).
        clear("xyz"[ai])

    def _g28(self, axis):
        # G28 PRIMITIF meme hors homing_override : tape en console (bench),
        # un "G28 Y" nu relancerait l'override COMPLET (recursion X+Y+Z +
        # restore autotune qui refermerait la fenetre StallGuard de l'appel
        # en cours). On leve in_script comme l'override le fait lui-meme.
        gcode = self.printer.lookup_object('gcode')
        ho = self.printer.lookup_object('homing_override', None)
        if ho is not None and not getattr(ho, 'in_script', True):
            ho.in_script = True
            try:
                gcode.run_script_from_command("G28 %s" % axis)
            finally:
                ho.in_script = False
        else:
            gcode.run_script_from_command("G28 %s" % axis)

    def _open_sg_window(self, st):
        # Ouvre VRAIMENT la fenetre StallGuard pour les homing moves :
        # - tcoolthrs=0 : le tmc.py de Klipper force alors TCOOLTHRS=0xFFFFF
        #   pendant chaque homing move (fenetre pleine-gamme), puis restaure.
        # - semin=0 : coupe CoolStep. OBLIGATOIRE et pas optionnel : pendant un
        #   homing move Klipper met TCOOLTHRS=0xFFFFF, ce qui arme CoolStep a
        #   TOUTES les vitesses si semin>0 (l'autotune pose semin=2/seimin=1 au
        #   boot ET apres chaque home via le restore). CoolStep reduit alors le
        #   courant effectif jusqu'a IRUN/4 (seimin=1) a faible charge : un tap
        #   commande a 1.2A peut rouler vers ~0.3-0.6A = courant qui NE SENT
        #   PAS le mur (verifie live 2026-06), et un home de base a 0.55A tombe
        #   encore plus bas -> decrochage / broutage non senti moteur chaud.
        #   Cause racine de l'echec "Y ne home plus a chaud" (2026-08).
        gcode = self.printer.lookup_object('gcode')
        gcode.run_script_from_command(
            "SET_TMC_FIELD STEPPER=stepper_%s FIELD=tcoolthrs VALUE=0" % st)
        gcode.run_script_from_command(
            "SET_TMC_FIELD STEPPER=stepper_%s FIELD=semin VALUE=0" % st)

    def _read_field(self, st, field):
        # Valeur EFFECTIVE d'un champ TMC (celle qui compte : l'autotune
        # ecrase p.ex. driver_SGTHRS avec son sg4_thrs au boot et apres chaque
        # home, donc la valeur du printer.cfg peut etre lettre morte).
        obj = self.printer.lookup_object('tmc2209 stepper_%s' % st, None)
        if obj is None:
            return None
        try:
            return obj.fields.get_field(field)
        except Exception:
            return None

    def _full_step(self, axis, rail):
        # Taille d'un pas entier en mm : rotation_distance effective (gear_ratio
        # inclus, lue sur l'objet stepper) / full_steps_per_rotation. None si
        # l'API n'existe pas (vieux Klipper) -> pas de plancher, comportement
        # d'origine.
        steppers = rail.get_steppers()
        if not steppers:
            return None
        get_rd = getattr(steppers[0], 'get_rotation_distance', None)
        if get_rd is None:
            return None
        try:
            rotation_dist = get_rd()[0]
        except Exception:
            return None
        fs = self.full_steps.get(axis, 200)
        if not rotation_dist or fs <= 0:
            return None
        return abs(rotation_dist) / fs

    def _sgthrs_ref(self, axis, explicit, sg0):
        # Seuil a poser pour une phase : la valeur explicite de la config si
        # > 0, sinon le sg4_thrs de l'autotune, sinon la valeur lue a l'entree
        # (dernier recours : aucune reference plus fiable disponible). Retourne
        # (valeur ou None, nom de la source pour le marqueur).
        if explicit > 0:
            return explicit, "config"
        at = self.autotune_sgthrs.get(axis)
        if at is not None:
            return at, "autotune sg4_thrs"
        return sg0, "registre a l'entree"

    def _set_sgthrs(self, st, value, gcmd):
        # Ecrit sgthrs seulement si le driver l'a (2209) : sur un autre driver
        # SET_TMC_FIELD leverait "Unknown field name" et avorterait le home.
        if self._read_field(st, 'sgthrs') is None:
            gcmd.respond_info(
                "YUMI_SENSORLESS_HOME: pas de registre sgthrs sur stepper_%s "
                "(driver non 2209) -> reglage sgthrs ignore" % st)
            return
        self.printer.lookup_object('gcode').run_script_from_command(
            "SET_TMC_FIELD STEPPER=stepper_%s FIELD=sgthrs VALUE=%d"
            % (st, value))

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

        # Plancher de tolerance = 1 pas entier (+1 % pour le bruit flottant
        # d'une difference de positions). StallGuard evalue le stall une fois
        # par pas entier : deux taps sur des pas voisins ont EXACTEMENT ce
        # spread, ce n'est pas un defaut de butee. En dessous, la fenetre ne
        # converge que si tous les taps tombent sur le meme pas.
        full_step = self._full_step(axis, rail)
        if full_step is not None and tol < full_step * 1.01:
            gcmd.respond_info(
                "YUMI_SENSORLESS_HOME %s: tolerance %.4f < 1 pas entier "
                "%.4f (StallGuard evalue par pas entier) -> tolerance "
                "effective %.4f" % (axis, tol, full_step, full_step * 1.01))
            tol = full_step * 1.01

        # Le faux trigger d'arret tombe DANS la zone de deceleration du tap
        # (gap fantome ~ v_trigger^2/2a, observe 0.05-0.08 a 20mm/s/1000mm/s2).
        # La preuve de contact n'est discriminante que si l'overshoot depasse
        # largement cette zone — sinon un tap dans le vide serait ACCEPTE.
        decel_zone = speed * speed / (2. * self.tap_accel)
        if self.overshoot <= decel_zone * 2.:
            raise gcmd.error(
                "YUMI_SENSORLESS_HOME: overshoot %.2f <= 2x zone de decel "
                "%.2f (SPEED^2 / 2*fine_accel) -> preuve de contact non "
                "discriminante. Augmenter overshoot ou fine_accel, ou baisser "
                "SPEED." % (self.overshoot, decel_zone))
        min_gap = max(self.overshoot * 0.5, decel_zone * 1.5)

        # Etat TMC a restaurer en sortie (capture AVANT toute modification).
        semin0 = self._read_field(st, 'semin')
        tcool0 = self._read_field(st, 'tcoolthrs')
        sg0 = self._read_field(st, 'sgthrs')

        # Ouvre la fenetre StallGuard (tcoolthrs=0 + CoolStep coupe).
        self._open_sg_window(st)

        # Seuils de reference des deux phases, resolus AVANT de bouger : jamais
        # le registre tel quel (un home avorte y laisse ce que le dernier
        # restore a pose ; un seuil trop sensible herite ainsi -> faux stall
        # des le depart du tap, gap ~ retract, boucle auto-entretenue).
        coarse_sg, coarse_src = self._sgthrs_ref(
            axis, self.coarse_sgthrs[axis], sg0)
        tap_sg, tap_src = self._sgthrs_ref(axis, self.tap_sgthrs[axis], sg0)

        # --- A) Home natif : localise le mur ---
        if not skip_base:
            cc = self.coarse_current[axis]
            if cc > 0:
                gcode.run_script_from_command(
                    "SET_TMC_CURRENT STEPPER=stepper_%s CURRENT=%.3f" % (st, cc))
            if coarse_sg is not None:
                self._set_sgthrs(st, coarse_sg, gcmd)
            gcmd.respond_info(
                "YUMI_SENSORLESS_HOME %s: home base... (sgthrs=%s via %s)"
                % (axis, self._read_field(st, 'sgthrs'), coarse_src))
            self._g28(axis)

        # --- B) Prep taps : courant FRANC + sgthrs + accel constants ---
        self._open_sg_window(st)
        tap_cur = self.tap_current[axis] or self.run_current[axis]
        gcode.run_script_from_command(
            "SET_TMC_CURRENT STEPPER=stepper_%s CURRENT=%.3f" % (st, tap_cur))
        # Le seuil coarse etait pose pour le courant coarse : les taps a
        # courant franc reprennent leur propre reference.
        if tap_sg is not None:
            self._set_sgthrs(st, tap_sg, gcmd)
        gcmd.respond_info(
            "YUMI_SENSORLESS_HOME %s: taps a %.2fA (sgthrs=%s via %s)"
            % (axis, tap_cur, self._read_field(st, 'sgthrs'), tap_src))
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
                # NE PAS sortir des que len(triggers) >= samples : la fenetre
                # GLISSANTE doit pouvoir continuer a taper tant que les
                # `samples` derniers taps ne concordent pas (sinon elle ne
                # glisse jamais et max_taps ne sert a rien — bug historique).
                # Sorties de boucle : validated, anti-marathon, ou max_taps.
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
                    # Stock probing_move suffit : homing_move(probe_pos=True)
                    # leve un command_error "No trigger ... after full movement"
                    # s'il n'y a AUCUN contact (-> branche except), et retourne
                    # la position de trigger sinon (-> branche else, calcul gap).
                    # NE PAS ajouter de kwarg (ex: check_movement) : il n'existe
                    # pas dans le homing.py stock -> TypeError -> erreur interne
                    # -> shutdown Klipper sur un pad non patche.
                    epos = phoming.probing_move(mcu_endstop, target, speed)
                except self.printer.command_error as e:
                    # Pas de trigger valide. Re-ancre le referentiel depuis la
                    # position logique COURANTE moins l'offset forcepos : juste
                    # sur ce que l'on sait, quel que soit le sous-cas ("no
                    # trigger" = move complet -> chariot a overshoot au-dela du
                    # mur suppose ; "triggered prior to movement" = pas bouge
                    # -> chariot toujours au retract). Re-ancrer betement a
                    # pos_endstop fausserait le referentiel de retract mm.
                    cur_log = toolhead.get_position()[ai]
                    self._set_kin(axis, cur_log - away * self.overshoot)
                    reason = "aucun contact sur %.1fmm (%s)" % (
                        self.retract + self.overshoot, str(e).split('\n')[0])
                else:
                    # PREUVE DE CONTACT : le trigger doit tomber AVANT la cible
                    # commandee, HORS zone de decel, et pas trop avant le mur.
                    # gap = distance trigger->cible (coord logiques).
                    # Vrai mur -> gap ~ overshoot ; arret sans buter -> gap ~ 0
                    # (zone de decel) ; faux stall / obstacle -> gap >> overshoot.
                    gap = abs(epos[ai] - pos_endstop)
                    trig = epos[ai] - away * self.overshoot  # mur reel
                    self._set_kin(axis, pos_endstop)  # recale au mur
                    if gap < min_gap:
                        reason = "cible atteinte sans buter (gap=%.4f)" % gap
                    elif gap > self.overshoot + 1.5:
                        # Trigger bien AVANT le mur attendu : faux stall
                        # (moteur chaud / sgthrs trop sensible) ou obstacle
                        # sur la course. Ne JAMAIS valider un tel tap.
                        # Marge absolue 1.5mm : le tassement legitime du 1er
                        # contact mesure au pire ~0.8mm (Y), jamais 1.5.
                        reason = ("trigger avant le mur attendu (gap=%.4f) "
                                  "-> faux stall ou obstacle" % gap)

                if reason is not None:
                    rejects += 1
                    no_contact += 1
                    gcmd.respond_info("tap %d rejete: %s" % (attempt, reason))
                    # Pas de contact fiable 2x de suite = home de base foireux /
                    # depart mal positionne -> re-home de recuperation (borne),
                    # sinon erreur franche (jamais de faux zero).
                    if no_contact >= 2:
                        if rehomes >= self.max_rehomes or skip_base:
                            # L'axe a ete marque homed par les _set_kin des
                            # taps sur un referentiel non fiable -> unhome
                            # avant l'abort (jamais de faux zero). Avec
                            # SKIP_BASE=1 (bench, referentiel pose a la main)
                            # on n'impose JAMAIS un G28 natif surprise.
                            self._unhome(ai)
                            raise gcmd.error(
                                "YUMI_SENSORLESS_HOME %s: aucun contact apres "
                                "%d re-home(s) -> position de depart ou butee "
                                "non fiable, home avorte" % (axis, rehomes))
                        # Re-home de recuperation a courant FRANC (tap_cur,
                        # deja en place) : les conditions du home de base
                        # viennent de prouver qu'elles echouent (moteur chaud),
                        # les retenter a l'identique re-echoue a l'identique.
                        # A courant franc le faux trigger en course libre est
                        # quasi impossible et le mur rigide stalle net.
                        gcmd.respond_info(
                            "YUMI_SENSORLESS_HOME %s: home de base suspect "
                            "(pas de contact fiable) -> re-home de "
                            "recuperation a courant franc %d/%d"
                            % (axis, rehomes + 1, self.max_rehomes))
                        self._g28(axis)
                        # Nouveau referentiel -> repartir a zero : ne jamais
                        # melanger dans la fenetre des taps mesures avant et
                        # apres le re-home, et re-bruler la chauffe.
                        triggers = []
                        valid_count = 0
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
                    # Anti-marathon : la fenetre glissante sert a ecarter le
                    # tassement initial (1-2 taps), pas a taper des minutes
                    # (un axe degrade peut ne jamais converger et max_taps
                    # peut etre configure tres haut). Apres samples*3 taps
                    # valides sans stabilisation, inutile d'insister.
                    if len(triggers) >= samples * 3:
                        break
                else:
                    gcmd.respond_info("tap %d: pos=%.4f gap=%.4f (%d/%d)"
                                      % (attempt, trig, gap,
                                         len(triggers), samples))
        finally:
            gcode.run_script_from_command(
                "SET_VELOCITY_LIMIT ACCEL=%.0f" % saved_accel)
            # Restaure l'etat TMC d'entree (utile si pas d'autotune installe /
            # restore_autotune=False : sinon semin=0 tuerait CoolStep pour
            # toute la session). L'AUTOTUNE_TMC ci-dessous re-ecrase ensuite
            # avec le tuning silencieux quand il est present — c'est voulu.
            if semin0 is not None:
                gcode.run_script_from_command(
                    "SET_TMC_FIELD STEPPER=stepper_%s FIELD=semin VALUE=%d"
                    % (st, semin0))
            if tcool0 is not None:
                gcode.run_script_from_command(
                    "SET_TMC_FIELD STEPPER=stepper_%s FIELD=tcoolthrs VALUE=%d"
                    % (st, tcool0))
            gcode.run_script_from_command(
                "SET_TMC_CURRENT STEPPER=stepper_%s CURRENT=%.3f"
                % (st, self.run_current[axis]))
            if self.restore_autotune:
                try:
                    gcode.run_script_from_command(
                        "AUTOTUNE_TMC STEPPER=stepper_%s" % st)
                except Exception as e:
                    logging.info("YUMI_SENSORLESS_HOME autotune: %s", e)
            # run_sgthrs n'est PAS re-applique ici : ce finally tourne aussi
            # sur abort, et une valeur posee la serait heritee par le home
            # suivant. Il est applique tout en bas, apres validation.

        # --- C) Decision : le zero est le hard-stop (pos_endstop). Les taps
        # servent a VERIFIER la repetabilite, pas a calculer le zero. ---
        self._set_kin(axis, pos_endstop)
        cur = toolhead.get_position()
        cur[ai] = pos_endstop + away * self.retract
        toolhead.manual_move(cur, self.travel_speed)
        toolhead.wait_moves()

        if len(triggers) < samples:
            # Budget de taps epuise sans assez de contacts prouves : le
            # referentiel n'est pas fiable -> unhome + erreur franche (avant :
            # "home natif conserve" = zero potentiellement faux garde en douce).
            self._unhome(ai)
            raise gcmd.error(
                "YUMI_SENSORLESS_HOME %s: repetabilite NON etablie "
                "(%d taps valides / %d requis, %d rejetes) -> referentiel "
                "non fiable, home avorte" % (axis, len(triggers), samples,
                                             rejects))

        # Fenetre finale = les `samples` derniers taps (stabilises si validated).
        window = triggers[-samples:]
        spread = max(window) - min(window)
        mean = sum(window) / len(window)
        ok = validated and spread <= tol
        if not ok and spread > max(tol * 4., 0.2):
            # Contacts reels mais dispersion incompatible avec une butee
            # fiable (courroie/mecanique) : jamais de faux zero.
            self._unhome(ai)
            raise gcmd.error(
                "YUMI_SENSORLESS_HOME %s: spread=%.4fmm sur %d taps "
                "(tol=%.4f) -> butee non repetable, home avorte"
                % (axis, spread, len(window), tol))
        # run_sgthrs seulement ici, une fois le zero pose : APRES l'autotune du
        # finally (sinon re-ecrase par sg4_thrs) et JAMAIS sur un abort.
        rs = self.run_sgthrs[axis]
        if rs > 0:
            self._set_sgthrs(st, rs, gcmd)
        gcmd.respond_info(
            "YUMI_SENSORLESS_HOME %s %s: %d taps valides (%d rejetes) -> "
            "moyenne=%.4f spread=%.4fmm (tol=%.4f). Zero pose en butee=%.4f"
            % (axis, "OK" if ok else "IMPRECIS", len(triggers), rejects,
               mean, spread, tol, pos_endstop))


def load_config(config):
    return YumiSensorless(config)
