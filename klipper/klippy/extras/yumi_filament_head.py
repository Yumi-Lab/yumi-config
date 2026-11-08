# Filament at the head — step-and-check load onto the head sensor, checked unload
#
# The Yumi heads carry a filament switch at the inlet (PA8). Feeding is blind otherwise: a
# print started with an empty head, or a colour change that never reached the nozzle, prints
# air. This module reads that pin through Klipper's buttons module (debounced, live state) and
# feeds the extruder queue (extruder + synced feeders) in short steps queued straight into the
# toolhead (manual_move, no g-code round trip), reading the switch after each one — the factory
# QC procedure made quasi-continuous: at 16.7 mm/s a 5 mm step lasts 0.3 s, the stop after
# detection is under one step. Synchronous for the caller: a tool macro or a start g-code blocks
# until the filament is really there.
# Why not a homing move (MCU trsync on the switch): Klipper's drip_move drops the E axis
# (`newpos = newpos[:3] + commanded_pos[3:]`), so an extruder "homing" is a zero-length move —
# the 4/4 "No trigger after full movement" of 2026-09-06 never moved a millimetre of filament.
#
#   YUMI_LOAD_TO_HEAD  [SENSOR=] [PRELOAD=] [SPEED=] [MAX=] [STEP=] [HEAD_TO_NOZZLE=]
#                      [CURRENT=]  overrides load_current for THIS call only, nothing is
#                      remembered: finding the lowest current that still pulls a full tube is a
#                      sweep, and a config option would cost a restart per value.
#       feed `step` mm at `speed`, read the switch, again until it sees filament, MAX mm at
#       most (error otherwise), then stop: what follows (prime to the nozzle, purge) is the
#       slicer's G-code. PRELOAD= feeds that many mm first in ONE move without reading the
#       switch — when the distance is known (a colour change pulled the filament back by a known
#       length), the steps only cover the uncertainty. STEP= of the call sets a longer step for
#       that call only (Nicolas: declared = different, not declared = the default; nothing
#       stored); default = the `step` option of the config.
#       SENSOR= names the motion sensor of the YMS that feeds (YMS-3): while feeding, that
#       encoder must tick; once the feed has covered `feeder_check` mm and the sensor still
#       reports no filament, the load stops with "No filament coming from YMS-3" — an empty YMS
#       fails in seconds instead of feeding max_load mm of nothing (a T3 launched by mistake on
#       an empty YMS fed 2 x 2500 mm, 10 minutes, on 07/09). The generated T<n> pass it; without
#       SENSOR= only the head switch is watched.
#       HEAD_TO_NOZZLE= can add mm after the trigger (hot end above min_temp for that part),
#       0 by default and by decision. T<n> forwards its parameters: `T1 PRELOAD=100`.
#   YUMI_UNLOAD_CHECK  [MAX_EXTRA=] [STEP=]
#       after a tip-shaping unload: the switch must have released; if it still sees filament,
#       pull `step` mm at a time until it releases (MAX_EXTRA mm at most, error otherwise).
#   SET_HEAD_SENSOR_BYPASS ENABLE=0|1
#       a broken sensor must not stop production: bypassed, the LOAD STILL RUNS — blind, over
#       `blind_load` mm (0 = the tip-shaping unload total read from the _YUMI_TIP macro) plus
#       head_to_nozzle — only the detection is skipped, and so is the unload check, with a
#       warning every time. Persisted in save_variables (head_sensor_bypass) so it survives
#       restarts; set from the Printer Config panel, YUMI_SETUP or the console.
#   QUERY_HEAD_SENSOR
#
# Options ([yumi_filament_head], values in the printer.cfg generator's catalog):
#   pin               the head switch (^!PA8: pull-up, the line floats otherwise; ! = filament pulls it low)
#   speed             mm/s of the load steps (default 16.7, the QC feed rate)
#   step              mm fed between two readings of the switch = maximum overshoot (default 5),
#                     the default when the call carries no STEP=
#   settle            s left after each step for the switch state to reach the host (default 0.02)
#   max_load          mm fed at most before "no filament at the head" is raised (default 800)
#   feeder_check      mm of feed after which the SENSOR= motion sensor must report filament, else the
#                     load aborts; 0 (default) = that sensor's detection_length plus two steps, i.e.
#                     the earliest point where "no tick" is a verdict and not a lag
#   head_to_nozzle    mm fed after the trigger, towards the nozzle. 0 by decision: the load stops at the
#                     switch and the slicer's G-code drives the rest (prime, purge), tunable in Orca
#   nozzle_speed      mm/s of that last stretch into the melt zone (default 5)
#   max_unload_extra  mm pulled at most by YUMI_UNLOAD_CHECK while the switch still sees filament (default 200)
#   blind_load        mm fed when the sensor is bypassed; 0 = the unload total of _YUMI_TIP (default 0)
#   min_temp          degC the hotend must have reached before head_to_nozzle is fed (default 170)
#   load_current      A on the pusher driver(s) WHILE LOADING, restored after (0 = leave alone).
#                     Loading needs almost no force -- it pushes filament down a tube -- while the
#                     configured run current is sized for retractions and for pushing melt through
#                     the nozzle. Nicolas 09/11: "il n'y a pas besoin d'avoir de force. Si il y a
#                     une contrainte, il ne faut pas qu'on bouffe le filament", and earlier: "un
#                     pas moteur rate c'est pas tres grave sur la pression alors que bouffer le
#                     filament ca arrete tout court". A pusher that cannot win against an
#                     obstruction skips and the load reports a failure; one that can wins by
#                     grinding a flat into the filament, and from then on NOTHING feeds -- the
#                     failure Nicolas has been chasing. So the load deliberately runs too weak to
#                     chew. It covers every path through this module (T<n>, the runout recovery,
#                     the tip test); the slicer's own prime and LOAD_FILAMENT do not come through
#                     here, and neither does the head_to_nozzle prime, which pushes melt through
#                     the nozzle and needs the real current. UNLOADING keeps the full current too:
#                     pulling a cold tip out of the die needs it.
#   bypass_variable   save_variables key that persists the bypass (default head_sensor_bypass)
# Status (printer.yumi_filament_head): present (live switch state, usable in macros: e.g. cut and
# pull the previous filament before loading another tool), loaded_mm (last load), bypass,
# yms_connected (last YMS_SCAN), pending (the command RESUME will run again).
#
#   YMS_SCAN [REQUIRE=<n>]
#       turns each YMS motor 0.1 mm back then forward (slowly, low current) and reads its
#       driver: plugged or not, saved in save_variables `yms_connected` as [1, 0, ...] (YMS-1
#       first, -1 = driver not readable). Run at boot and by PRINT_START. REQUIRE=n: YMS-n must
#       be plugged.
#   YUMI_RETRY_PENDING
#       first line of RESUME: runs again the load / check that paused the print.
# During a print a failed load or a required YMS unplugged no longer stops the print: alert,
# PAUSE, and RESUME retries it (Nicolas 08/11: the YMS cable was unplugged, the print was lost).
import contextlib
import json
import logging
import re


class YumiFilamentHead:
    def __init__(self, config):
        self.printer = config.get_printer()
        self.gcode = self.printer.lookup_object('gcode')
        buttons = self.printer.load_object(config, 'buttons')
        buttons.register_buttons([config.get('pin')], self._button_handler)
        self.present = False
        self.speed = config.getfloat('speed', 16.7, above=0.)               # mm/s, feed steps
        self.step = config.getfloat('step', 5., above=0.)                   # mm between two readings
        self.settle = config.getfloat('settle', 0.02, minval=0.)            # s, switch state latency
        self.max_load = config.getfloat('max_load', 800., above=0.)
        self.feeder_check = config.getfloat('feeder_check', 0., minval=0.)   # mm, 0 = from the sensor
        self.head_to_nozzle = config.getfloat('head_to_nozzle', 0., minval=0.)
        self.nozzle_speed = config.getfloat('nozzle_speed', 5., above=0.)    # mm/s, into the melt zone
        self.max_unload_extra = config.getfloat('max_unload_extra', 200., minval=0.)
        self.load_current = config.getfloat('load_current', 0., minval=0.)
        self.blind_load = config.getfloat('blind_load', 0., minval=0.)
        self.min_temp = config.getfloat('min_temp', 170., minval=0.)
        self.bypass_variable = config.get('bypass_variable', 'head_sensor_bypass')
        self.bypass = False
        self.loaded_mm = 0.
        self.yms_connected = []
        self.pending = None
        self.printer.register_event_handler("klippy:ready", self._handle_ready)
        for name, func in (("YUMI_LOAD_TO_HEAD", self.cmd_LOAD), ("YUMI_UNLOAD_CHECK", self.cmd_UNLOAD_CHECK),
                           ("SET_HEAD_SENSOR_BYPASS", self.cmd_BYPASS), ("QUERY_HEAD_SENSOR", self.cmd_QUERY),
                           ("YMS_SCAN", self.cmd_YMS_SCAN), ("YUMI_RETRY_PENDING", self.cmd_RETRY_PENDING)):
            self.gcode.register_command(name, func, desc=func.__doc__)

    # ── state ──────────────────────────────────────────────────────────
    def _handle_ready(self):
        sv = self.printer.lookup_object('save_variables', None)
        if sv is not None:
            self.bypass = bool(sv.allVariables.get(self.bypass_variable, False))
        if self.bypass:
            logging.warning("yumi_filament_head: head sensor BYPASSED (saved variable %s)", self.bypass_variable)

    def _button_handler(self, eventtime, state):
        self.present = bool(state)

    def _present(self):
        """The switch state once the queued moves are done and the MCU had time to report it.
        No MCU query from here (get_status is polled from the reactor's main context, where an
        awaited MCU answer took Klipper down on 06/09): the buttons module pushes the state."""
        self.printer.lookup_object('toolhead').wait_moves()
        reactor = self.printer.get_reactor()
        reactor.pause(reactor.monotonic() + self.settle)
        return self.present

    def get_status(self, eventtime):
        # `pusher` = what an E move will really turn, for the macros that drive E themselves;
        # empty means an E move would move nothing at all. Attribute reads only, no MCU I/O: this
        # runs in the reactor's main context, where an MCU query shuts Klipper down.
        return {"present": self.present, "loaded_mm": self.loaded_mm, "bypass": self.bypass,
                "yms_connected": list(self.yms_connected), "pending": self.pending is not None,
                "pusher": ",".join(self._synced_pushers())}

    # ── the feeding YMS ────────────────────────────────────────────────
    def _feeder(self, gcmd, step):
        """(name, sensor object, check length) for SENSOR= of the call, None without it. The
        sensor is any filament motion sensor section carrying that name (stock
        filament_motion_sensor or the smart one): both publish filament_detected, which their
        own timer drops once the extruder travelled detection_length mm without a tick."""
        name = gcmd.get('SENSOR', None)
        if not name:
            return None
        for obj_name, obj in self.printer.lookup_objects():
            if obj_name.endswith(" " + name) and hasattr(obj, 'get_status') \
                    and 'filament_detected' in obj.get_status(self.printer.get_reactor().monotonic()):
                check = self.feeder_check
                if check <= 0:
                    check = getattr(obj, 'detection_length', 50.) + 2 * step
                return (name, obj, check)
        raise gcmd.error("SENSOR=%s: no filament motion sensor of that name" % name)

    def _feeder_present(self, feeder):
        return bool(feeder[1].get_status(self.printer.get_reactor().monotonic()).get('filament_detected'))

    def _check_feeder(self, feeder, fed):
        """Past the check length, a feeder whose encoder never ticked has nothing to give."""
        if feeder is not None and fed >= feeder[2] and not self._feeder_present(feeder):
            raise self.printer.command_error(
                "No filament coming from %s: its motion sensor saw nothing over %.0f mm of feed "
                "(empty spool, or filament not inserted in that YMS)" % (feeder[0], fed))

    # ── the YMS motors: plugged or not ─────────────────────────────────
    # The TMC2209 flags an open coil (ola / olb) only while it drives the motor slowly (datasheet:
    # not valid at standstill nor in fast motion), and the flags stay latched until it drives
    # again. Measured on the C335 08/11, YMS-2 plugged and unplugged by hand: a 0.1 mm back and
    # forth at 0.05 mm/s and 0.3 A flagged each coil of the unplugged motor at least once (B after
    # the back move, A+B after the forward one) and none on the plugged one; 0.05 mm only ever
    # flagged coil A. 0.1 mm stays in the tube's slack: a filament stuck cold in the head is not
    # pulled, and the YMS ends where it started.
    SCAN_MM = 0.1
    SCAN_SPEED = 0.05
    SCAN_CURRENT = 0.3
    SCAN_VARIABLE = "yms_connected"

    def _yms_motors(self):
        """[(n, 'extruderK', extruder_stepper)] in YMS order: YMS-n is extruder{n-1} (E0 = YMS-1),
        the mapping the generated T<n> macros sync."""
        out = []
        for name, obj in self.printer.lookup_objects('extruder_stepper'):
            m = re.match(r"extruder_stepper (extruder(\d+))$", name)
            if m:
                out.append((int(m.group(2)) + 1, m.group(1), obj))
        return sorted(out, key=lambda t: t[0])

    def _yms_unplugged(self, short, obj):
        """True / False, None when the YMS driver is not on UART (nothing to read)."""
        tmc = self.printer.lookup_object('tmc2209 extruder_stepper ' + short, None)
        if tmc is None:
            return None
        es = obj.extruder_stepper
        stepper = es.stepper
        force_move = self.printer.lookup_object('force_move')
        enable = self.printer.lookup_object('stepper_enable')
        toolhead = self.printer.lookup_object('toolhead')
        cur = tmc.get_status(self.printer.get_reactor().monotonic())
        was_enabled = enable.lookup_enable(stepper.get_name()).is_motor_enabled()
        seen = set()
        self.gcode.run_script_from_command("SET_TMC_CURRENT STEPPER=%s CURRENT=%.3f HOLDCURRENT=%.3f"
                                           % (short, self.SCAN_CURRENT, self.SCAN_CURRENT))
        try:
            enable.set_motors_enable([stepper.get_name()], True)
            for dist in (-self.SCAN_MM, self.SCAN_MM):
                force_move.manual_move(stepper, dist, self.SCAN_SPEED)
                toolhead.wait_moves()
                drv = tmc.mcu_tmc.get_register('DRV_STATUS')
                seen.update(c for c in ('ola', 'olb') if tmc.fields.get_field(c, drv))
        finally:
            # force_move drove the stepper on its own position: back in step with the extruder
            if es.motion_queue:
                es.sync_to_extruder(es.motion_queue)
            self.gcode.run_script_from_command("SET_TMC_CURRENT STEPPER=%s CURRENT=%.3f HOLDCURRENT=%.3f"
                                               % (short, cur['run_current'], cur['hold_current']))
            if not was_enabled:
                enable.set_motors_enable([stepper.get_name()], False)
        return seen == {'ola', 'olb'}

    def _synced_pushers(self):
        """Names of the steppers an extruder move will ACTUALLY turn.

        On a ChromaX12 [extruder] is a fictitious motor on free pins; the real pushers are the YMS
        extruder_steppers, one metre of tube away, and T<n> is what syncs one of them. Outside a
        print T<n> deliberately syncs NONE (its else branch sets MOTION_QUEUE=""), so every E move
        sent by hand turns a motor connected to nothing -- silently, with the g-code E axis
        advancing exactly as asked. Measured on the C335 on 09/11: a whole unload sequence, 347 mm
        of commanded retraction and a blade cut, moved not one millimetre of filament, and the only
        symptom was a head sensor that would not release.

        On a direct drive there is no extruder_stepper at all and [extruder] is the real motor, so
        the list is never empty there and nothing is refused."""
        toolhead = self.printer.lookup_object('toolhead')
        active = toolhead.get_extruder().get_name()
        steppers = list(self.printer.lookup_objects('extruder_stepper'))
        if not steppers:
            return [active]
        out = []
        for name, obj in steppers:
            es = getattr(obj, 'extruder_stepper', obj)
            if getattr(es, 'motion_queue', None) == active:
                out.append(name.split()[-1])
        return out

    def _require_pusher(self, gcmd):
        if not self._synced_pushers():
            raise gcmd.error(
                "No pusher is synced to the active extruder '%s': every E move would turn a motor "
                "that is not connected to the filament, and nothing would move. Call T<n> first "
                "(T0 = YMS-1). Outside a print T<n> deliberately leaves them unsynced, so a "
                "sequence run by hand has to sync one itself: "
                "SYNC_EXTRUDER_MOTION EXTRUDER=extruder0 MOTION_QUEUE=extruder"
                % self.printer.lookup_object('toolhead').get_extruder().get_name())

    def _feeding_drivers(self):
        """[(mux name, tmc)] of every driver that will move on an extruder move: the active
        extruder's own stepper plus every extruder_stepper synced to it. SET_TMC_CURRENT keys on
        the LAST word of the driver section, which is what is returned. A ChromaX12's [extruder]
        is a fictitious motor on free pins with no driver at all, so it simply does not appear."""
        moving = set(self._synced_pushers())
        out = []
        for name, obj in self.printer.lookup_objects():
            if name.startswith('tmc') and ' ' in name and name.split()[-1] in moving:
                out.append((name.split()[-1], obj))
        return out

    @contextlib.contextmanager
    def _reduced_current(self, gcmd):
        """Run the body with load_current on the pusher(s), then put back what was there --
        including when the body raises, which is exactly when it matters. CURRENT= overrides the
        configured value for this call only."""
        wanted = gcmd.get_float('CURRENT', self.load_current, minval=0.)
        if wanted <= 0.:
            yield
            return
        eventtime = self.printer.get_reactor().monotonic()
        saved = [(short, tmc.get_status(eventtime)) for short, tmc in self._feeding_drivers()]
        saved = [(short, cur['run_current'], cur['hold_current'])
                 for short, cur in saved if cur.get('run_current') is not None]
        if not saved:
            # never silently load at full current when a weaker one was asked for
            gcmd.respond_info("load current of %.2fA asked for, but no adjustable driver "
                              "feeds the filament here: loading at the configured current"
                              % wanted)
            yield
            return
        for short, run, hold in saved:
            self.gcode.run_script_from_command(
                "SET_TMC_CURRENT STEPPER=%s CURRENT=%.3f HOLDCURRENT=%.3f"
                % (short, wanted, min(hold, wanted)))
        gcmd.respond_info("loading at %.2f A on %s (run %.2f A): too weak to chew the filament, "
                          "so an obstruction makes it skip instead"
                          % (wanted, ", ".join(s for s, _, _ in saved), saved[0][1]))
        try:
            yield
        finally:
            for short, run, hold in saved:
                self.gcode.run_script_from_command(
                    "SET_TMC_CURRENT STEPPER=%s CURRENT=%.3f HOLDCURRENT=%.3f" % (short, run, hold))

    def _unplugged_message(self, n):
        return ("YMS-%d motor not connected (its driver reads both coils open): plug the YMS-%d "
                "cable back in" % (n, n))

    # ── a print is paused, not lost ────────────────────────────────────
    def _printing(self):
        ps = self.printer.lookup_object('print_stats', None)
        return ps is not None and ps.get_status(self.printer.get_reactor().monotonic())['state'] == 'printing'

    def _fail_or_pause(self, gcmd, message):
        """During a print: alert, PAUSE, and keep this command for RESUME to run again. Outside a
        print -- and while RESUME retries it, the print being paused then -- the error itself."""
        if not self._printing():
            raise gcmd.error(message)
        self.pending = gcmd.get_commandline()
        self.gcode.respond_raw("!! %s -> print paused: fix it, then press Resume" % message)
        self.gcode.run_script_from_command("PAUSE")

    # ── moves ──────────────────────────────────────────────────────────
    def _stepped_feed(self, direction, max_mm, step, speed, want_present, feeder=None):
        """Feed `step` mm at a time (direction +1 load / -1 unload), queued straight into the
        toolhead, reading the switch after each step, until it reports `want_present` (None =
        feed the whole distance). Returns the signed distance fed; raises "No trigger" past
        max_mm, "No filament coming from" when the feeding YMS never ticks. The g-code layer is
        resynced at the end so the slicer's E bookkeeping continues from the real position."""
        toolhead = self.printer.lookup_object('toolhead')
        toolhead.wait_moves()
        fed = 0.
        try:
            while fed < max_mm:
                chunk = min(step, max_mm - fed)
                pos = toolhead.get_position()
                toolhead.manual_move([None, None, None, pos[3] + direction * chunk], speed)
                fed += chunk
                present = self._present()
                if want_present is not None and present == want_present:
                    return direction * fed
                self._check_feeder(feeder, fed)
            if want_present is None:
                return direction * fed
            raise self.printer.command_error("No trigger on head_sensor after %.0f mm" % max_mm)
        finally:
            self.printer.lookup_object('gcode_move').reset_last_position()

    def _move_e(self, distance, speed):
        """One relative extruder move queued straight into the toolhead, complete before
        returning; the g-code layer is resynced so the slicer's E bookkeeping goes on."""
        toolhead = self.printer.lookup_object('toolhead')
        toolhead.wait_moves()
        pos = toolhead.get_position()
        toolhead.manual_move([None, None, None, pos[3] + distance], speed)
        toolhead.wait_moves()
        self.printer.lookup_object('gcode_move').reset_last_position()

    def _blind_load_len(self):
        """Blind load length: blind_load, or the tip-shaping unload total (single source)."""
        if self.blind_load > 0:
            return self.blind_load
        tip = self.printer.lookup_object('gcode_macro _YUMI_TIP', None)
        if tip is None:
            return 0.
        v = tip.variables
        return sum(float(v.get(k, 0)) for k in ('first_len', 'cut_len', 'slow_len', 'pull_len'))

    def load_step(self, gcmd=None):
        """STEP= of the call for this call only, else the config default."""
        if gcmd is not None:
            explicit = gcmd.get_float('STEP', None, above=0.)
            if explicit is not None:
                return explicit
        return self.step

    def _hotend_temp(self):
        try:
            heater = self.printer.lookup_object('extruder').get_heater()
            return heater.get_status(self.printer.get_reactor().monotonic())['temperature']
        except Exception:
            return None

    # ── commands ───────────────────────────────────────────────────────
    def cmd_LOAD(self, gcmd):
        """Feed filament step by step until the head sensor sees it (PRELOAD= known mm first, in one move)"""
        try:
            self._load(gcmd)
        except self.printer.command_error as e:
            message = str(e)
            m = re.match(r"No filament coming from YMS-(\d+)", message)
            if m:
                # nothing came: name an unplugged motor rather than an empty spool
                n = int(m.group(1))
                motor = {t[0]: t for t in self._yms_motors()}.get(n)
                if motor is not None and self._yms_unplugged(motor[1], motor[2]):
                    message = self._unplugged_message(n)
            self._fail_or_pause(gcmd, message)

    def _load(self, gcmd):
        self._require_pusher(gcmd)
        # the weak current covers the descent down the tube ONLY. The prime into the nozzle after
        # it pushes melt through a 0.4 mm orifice and genuinely needs the configured current, so it
        # runs outside the window: a current chosen to be too weak to chew would simply stall there.
        with self._reduced_current(gcmd):
            fed = self._feed_to_switch(gcmd)
        to_nozzle = gcmd.get_float('HEAD_TO_NOZZLE', self.head_to_nozzle, minval=0.)
        if to_nozzle > 0:
            temp = self._hotend_temp()
            if temp is not None and temp < self.min_temp:
                raise gcmd.error("Hotend at %.0fC: heat above %.0fC before loading to the nozzle"
                                 % (temp, self.min_temp))
            self._move_e(to_nozzle, self.nozzle_speed)
            fed += to_nozzle
        self.loaded_mm = fed
        self.gcode.run_script_from_command("M117 Filament loaded")

    def _feed_to_switch(self, gcmd):
        """Down the tube until the head switch sees it; returns the mm fed."""
        speed = gcmd.get_float('SPEED', self.speed, above=0.)
        max_load = gcmd.get_float('MAX', self.max_load, above=0.)
        step = self.load_step(gcmd)
        preload = gcmd.get_float('PRELOAD', 0., minval=0.)
        feeder = self._feeder(gcmd, step)
        if self.bypass:
            blind = self._blind_load_len()
            gcmd.respond_info("head sensor BYPASSED: blind load of %.0f mm, not checked "
                              "(SET_HEAD_SENSOR_BYPASS ENABLE=0 to restore the check)" % blind)
            fed = 0.
            if blind > 0:
                # stepped only so that an empty feeder is still caught; the switch is never read
                fed = self._stepped_feed(+1, blind, step, speed, None, feeder)
        elif self._present():
            gcmd.respond_info("filament already at the head")
            fed = 0.
        else:
            fed = 0.
            if preload > 0:
                # the known part of the way, one move, no reading: the steps below cover the rest
                self._move_e(min(preload, max_load), speed)
                fed = min(preload, max_load)
                self._check_feeder(feeder, fed)
            if not self._present():
                try:
                    fed += self._stepped_feed(+1, max_load - fed, step, speed, True, feeder)
                except self.printer.command_error as e:
                    if "No trigger" in str(e):
                        raise gcmd.error("No filament at the head after %.0f mm: check the spool and the feeder "
                                         "(SET_HEAD_SENSOR_BYPASS ENABLE=1 if the sensor is broken)" % max_load)
                    raise
            gcmd.respond_info("filament reached the head after %.0f mm%s"
                              % (fed, (" (preload %.0f)" % preload) if preload > 0 else ""))
        return fed

    def cmd_YMS_SCAN(self, gcmd):
        """Which YMS are plugged (0.1 mm back and forth each), saved as yms_connected; REQUIRE=<n>: YMS-n must be"""
        flags, words = [], []
        for n, short, obj in self._yms_motors():
            unplugged = self._yms_unplugged(short, obj)
            flags.append(-1 if unplugged is None else 0 if unplugged else 1)
            words.append("YMS-%d %s" % (n, {1: "plugged", 0: "NOT plugged",
                                            -1: "unknown (driver not on UART)"}[flags[-1]]))
        self.yms_connected = flags
        if self.printer.lookup_object('save_variables', None) is not None:
            self.gcode.run_script_from_command("SAVE_VARIABLE VARIABLE=%s VALUE=%s"
                                               % (self.SCAN_VARIABLE, json.dumps(flags).replace(" ", "")))
        gcmd.respond_info("YMS scan: %s -> %s=%s" % (", ".join(words) or "no YMS declared",
                                                     self.SCAN_VARIABLE, flags))
        need = gcmd.get_int('REQUIRE', None, minval=1)
        if need is None or not flags:
            return  # no YMS declared: the head feeds itself, nothing to require
        if need > len(flags):
            self._fail_or_pause(gcmd, "YMS-%d required, this printer.cfg declares %d YMS (YMS-1..YMS-%d)"
                                % (need, len(flags), len(flags)))
        elif flags[need - 1] == 0:
            self._fail_or_pause(gcmd, self._unplugged_message(need))

    def cmd_RETRY_PENDING(self, gcmd):
        """First line of RESUME: run again the load / check that paused the print (an error keeps it paused)"""
        if self.pending is None:
            return
        gcmd.respond_info("retrying: %s" % self.pending)
        # paused here, so a new failure raises: RESUME stops, the print stays paused
        self.gcode.run_script_from_command(self.pending)
        self.pending = None

    def cmd_UNLOAD_CHECK(self, gcmd):
        self._require_pusher(gcmd)
        """After an unload: the head sensor must have released, pull more (one move) if not"""
        max_extra = gcmd.get_float('MAX_EXTRA', self.max_unload_extra, minval=0.)
        step = self.load_step(gcmd)
        if self.bypass:
            gcmd.respond_info("head sensor BYPASSED: unload not checked")
            return
        if not self._present():
            gcmd.respond_info("filament out of the head")
            self.loaded_mm = 0.
            return
        try:
            fed = self._stepped_feed(-1, max_extra, step, self.speed, False)
        except self.printer.command_error as e:
            if "No trigger" in str(e):
                raise gcmd.error("Filament still at the head after %.0f mm more of retraction: unload failed" % max_extra)
            raise
        self.loaded_mm = 0.
        gcmd.respond_info("filament out of the head after %.0f mm more" % -fed)

    def cmd_BYPASS(self, gcmd):
        """SET_HEAD_SENSOR_BYPASS ENABLE=1: loading still runs, blind; detection off (broken sensor), persisted"""
        enable = gcmd.get_int('ENABLE', 1, minval=0, maxval=1)
        self.bypass = bool(enable)
        if self.printer.lookup_object('save_variables', None) is not None:
            self.gcode.run_script_from_command("SAVE_VARIABLE VARIABLE=%s VALUE=%d" % (self.bypass_variable, enable))
        gcmd.respond_info("head sensor %s" % ("BYPASSED — loads run blind, no checks" if self.bypass else "checks enabled"))

    def cmd_QUERY(self, gcmd):
        """State of the head sensor"""
        present = self._present()
        gcmd.respond_info("head sensor: %s%s" % ("filament present" if present else "no filament",
                                                 " (BYPASSED)" if self.bypass else ""))


def load_config(config):
    return YumiFilamentHead(config)
