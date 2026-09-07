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
#   YUMI_LOAD_TO_HEAD  [PRELOAD=] [SPEED=] [MAX=] [STEP=] [HEAD_TO_NOZZLE=]
#       feed `step` mm at `speed`, read the switch, again until it sees filament, MAX mm at
#       most (error otherwise), then stop: what follows (prime to the nozzle, purge) is the
#       slicer's G-code. PRELOAD= feeds that many mm first in ONE move without reading the
#       switch — when the distance is known (a colour change pulled the filament back by a known
#       length), the steps only cover the uncertainty. The step is, in order: STEP= of the
#       call, else the saved variable `step_variable` (load_step, set live with SET_LOAD_STEP or
#       from variables.cfg), else the `step` option of the config (20).
#       HEAD_TO_NOZZLE= can add mm after the trigger (hot end above min_temp for that part),
#       0 by default and by decision. T<n> forwards its parameters: `T1 PRELOAD=100`.
#   YUMI_UNLOAD_CHECK  [MAX_EXTRA=] [STEP=]
#       after a tip-shaping unload: the switch must have released; if it still sees filament,
#       pull `step` mm at a time until it releases (MAX_EXTRA mm at most, error otherwise).
#   SET_LOAD_STEP STEP=<mm>
#       the machine's own load step, persisted in save_variables (load_step) without touching the
#       G-code; STEP=0 goes back to the config default.
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
#                     the default when neither STEP= nor the saved variable is set
#   settle            s left after each step for the switch state to reach the host (default 0.02)
#   step_variable     save_variables key of the machine's own step (default load_step, 0 = unset)
#   max_load          mm fed at most before "no filament at the head" is raised (default 800)
#   head_to_nozzle    mm fed after the trigger, towards the nozzle. 0 by decision: the load stops at the
#                     switch and the slicer's G-code drives the rest (prime, purge), tunable in Orca
#   nozzle_speed      mm/s of that last stretch into the melt zone (default 5)
#   max_unload_extra  mm pulled at most by YUMI_UNLOAD_CHECK while the switch still sees filament (default 200)
#   blind_load        mm fed when the sensor is bypassed; 0 = the unload total of _YUMI_TIP (default 0)
#   min_temp          degC the hotend must have reached before head_to_nozzle is fed (default 170)
#   bypass_variable   save_variables key that persists the bypass (default head_sensor_bypass)
# Status (printer.yumi_filament_head): present (live switch state, usable in macros: e.g. cut and
# pull the previous filament before loading another tool), loaded_mm (last load), bypass.
import logging


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
        self.head_to_nozzle = config.getfloat('head_to_nozzle', 0., minval=0.)
        self.nozzle_speed = config.getfloat('nozzle_speed', 5., above=0.)    # mm/s, into the melt zone
        self.max_unload_extra = config.getfloat('max_unload_extra', 200., minval=0.)
        self.blind_load = config.getfloat('blind_load', 0., minval=0.)
        self.min_temp = config.getfloat('min_temp', 170., minval=0.)
        self.bypass_variable = config.get('bypass_variable', 'head_sensor_bypass')
        self.step_variable = config.get('step_variable', 'load_step')
        self.bypass = False
        self.loaded_mm = 0.
        self.printer.register_event_handler("klippy:ready", self._handle_ready)
        for name, func in (("YUMI_LOAD_TO_HEAD", self.cmd_LOAD), ("YUMI_UNLOAD_CHECK", self.cmd_UNLOAD_CHECK),
                           ("SET_HEAD_SENSOR_BYPASS", self.cmd_BYPASS), ("QUERY_HEAD_SENSOR", self.cmd_QUERY),
                           ("SET_LOAD_STEP", self.cmd_SET_LOAD_STEP)):
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
        return {"present": self.present, "loaded_mm": self.loaded_mm, "bypass": self.bypass}

    # ── moves ──────────────────────────────────────────────────────────
    def _stepped_feed(self, direction, max_mm, step, speed, want_present):
        """Feed `step` mm at a time (direction +1 load / -1 unload), queued straight into the
        toolhead, reading the switch after each step, until it reports `want_present`. Returns
        the signed distance fed; raises "No trigger" past max_mm. The g-code layer is resynced
        at the end so the slicer's E bookkeeping continues from the real position."""
        toolhead = self.printer.lookup_object('toolhead')
        toolhead.wait_moves()
        fed = 0.
        try:
            while fed < max_mm:
                chunk = min(step, max_mm - fed)
                pos = toolhead.get_position()
                toolhead.manual_move([None, None, None, pos[3] + direction * chunk], speed)
                fed += chunk
                if self._present() == want_present:
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

    def _saved_step(self):
        """The machine's own load step from save_variables, or None when unset / 0 / invalid."""
        sv = self.printer.lookup_object('save_variables', None)
        if sv is None:
            return None
        try:
            value = float(sv.allVariables.get(self.step_variable, 0) or 0)
        except (TypeError, ValueError):
            return None
        return value if value > 0 else None

    def load_step(self, gcmd=None):
        """STEP= of the call, else the saved variable, else the config default."""
        if gcmd is not None:
            explicit = gcmd.get_float('STEP', None, above=0.)
            if explicit is not None:
                return explicit
        saved = self._saved_step()
        return saved if saved is not None else self.step

    def _hotend_temp(self):
        try:
            heater = self.printer.lookup_object('extruder').get_heater()
            return heater.get_status(self.printer.get_reactor().monotonic())['temperature']
        except Exception:
            return None

    # ── commands ───────────────────────────────────────────────────────
    def cmd_LOAD(self, gcmd):
        """Feed filament step by step until the head sensor sees it (PRELOAD= known mm first, in one move)"""
        speed = gcmd.get_float('SPEED', self.speed, above=0.)
        max_load = gcmd.get_float('MAX', self.max_load, above=0.)
        step = self.load_step(gcmd)
        preload = gcmd.get_float('PRELOAD', 0., minval=0.)
        to_nozzle = gcmd.get_float('HEAD_TO_NOZZLE', self.head_to_nozzle, minval=0.)
        if self.bypass:
            blind = self._blind_load_len()
            gcmd.respond_info("head sensor BYPASSED: blind load of %.0f mm, not checked "
                              "(SET_HEAD_SENSOR_BYPASS ENABLE=0 to restore the check)" % blind)
            if blind > 0:
                self._move_e(blind, speed)
            fed = blind
        elif self._present():
            gcmd.respond_info("filament already at the head")
            fed = 0.
        else:
            fed = 0.
            if preload > 0:
                # the known part of the way, one move, no reading: the steps below cover the rest
                self._move_e(min(preload, max_load), speed)
                fed = min(preload, max_load)
            if not self._present():
                try:
                    fed += self._stepped_feed(+1, max_load - fed, step, speed, True)
                except self.printer.command_error as e:
                    if "No trigger" in str(e):
                        raise gcmd.error("No filament at the head after %.0f mm: check the spool and the feeder "
                                         "(SET_HEAD_SENSOR_BYPASS ENABLE=1 if the sensor is broken)" % max_load)
                    raise
            gcmd.respond_info("filament reached the head after %.0f mm%s"
                              % (fed, (" (preload %.0f)" % preload) if preload > 0 else ""))
        if to_nozzle > 0:
            temp = self._hotend_temp()
            if temp is not None and temp < self.min_temp:
                raise gcmd.error("Hotend at %.0fC: heat above %.0fC before loading to the nozzle" % (temp, self.min_temp))
            self._move_e(to_nozzle, self.nozzle_speed)
            fed += to_nozzle
        self.loaded_mm = fed
        self.gcode.run_script_from_command("M117 Filament loaded")

    def cmd_UNLOAD_CHECK(self, gcmd):
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

    def cmd_SET_LOAD_STEP(self, gcmd):
        """SET_LOAD_STEP STEP=<mm>: the machine's own load step, persisted (0 = back to the config default)"""
        value = gcmd.get_float('STEP', minval=0.)
        if self.printer.lookup_object('save_variables', None) is None:
            raise gcmd.error("SET_LOAD_STEP needs [save_variables]")
        self.gcode.run_script_from_command("SAVE_VARIABLE VARIABLE=%s VALUE=%s" % (self.step_variable, value))
        gcmd.respond_info("load step: %s" % ("%.1f mm (saved)" % value if value > 0
                                             else "config default %.1f mm" % self.step))

    def cmd_QUERY(self, gcmd):
        """State of the head sensor"""
        present = self._present()
        gcmd.respond_info("head sensor: %s%s" % ("filament present" if present else "no filament",
                                                 " (BYPASSED)" if self.bypass else ""))


def load_config(config):
    return YumiFilamentHead(config)
