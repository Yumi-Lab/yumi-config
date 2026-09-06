# Filament at the head — homing-style load onto the head sensor, checked unload
#
# The Yumi heads carry a filament switch at the inlet (PA8). Feeding is blind otherwise: a
# print started with an empty head, or a colour change that never reached the nozzle, prints
# air. This module owns that pin as an ENDSTOP and moves the extruder with a homing move onto
# it: the feed is one continuous move and the MCU stops the steppers the instant the contact
# flips — no step-and-check, no overshoot beyond the trigger, synchronous for the caller
# (a tool macro or a start g-code blocks until the filament is really there).
#
#   YUMI_LOAD_TO_HEAD  [SPEED=] [MAX=] [HEAD_TO_NOZZLE=]
#       homing move of the extruder queue (extruder + synced feeders) until the sensor
#       triggers, MAX mm at most (error otherwise), then `head_to_nozzle` mm more to bring
#       the tip to the nozzle (hot end above min_temp for that part only).
#   YUMI_UNLOAD_CHECK  [MAX_EXTRA=]
#       after a tip-shaping unload: the sensor must have released; if it still sees filament,
#       homing move backwards until it releases (MAX_EXTRA mm at most, error otherwise).
#   SET_HEAD_SENSOR_BYPASS ENABLE=0|1
#       a broken sensor must not stop production: bypassed, the LOAD STILL RUNS — blind, over
#       `blind_load` mm (0 = the tip-shaping unload total read from the _YUMI_TIP macro) plus
#       head_to_nozzle — only the detection is skipped, and so is the unload check, with a
#       warning every time. Persisted in save_variables (head_sensor_bypass) so it survives
#       restarts; set from the Printer Config panel, YUMI_SETUP or the console.
#   QUERY_HEAD_SENSOR
#
# Options ([yumi_filament_head], values in the printer.cfg generator's catalog):
#   pin               the head switch, wired as an endstop pin (e.g. !PA8)
#   speed             mm/s of the continuous load move (default 40)
#   max_load          mm fed at most before "no filament at the head" is raised (default 800)
#   head_to_nozzle    mm from the switch to the nozzle, fed after the trigger (default 0)
#   nozzle_speed      mm/s of that last stretch into the melt zone (default 5)
#   max_unload_extra  mm pulled at most by YUMI_UNLOAD_CHECK while the switch still sees filament (default 200)
#   blind_load        mm fed when the sensor is bypassed; 0 = the unload total of _YUMI_TIP (default 0)
#   min_temp          degC the hotend must have reached before head_to_nozzle is fed (default 170)
#   bypass_variable   save_variables key that persists the bypass (default head_sensor_bypass)
# Status (printer.yumi_filament_head): loaded_mm, present (None = unknown or bypassed), bypass.
import logging
from . import homing


class YumiFilamentHead:
    def __init__(self, config):
        self.printer = config.get_printer()
        self.gcode = self.printer.lookup_object('gcode')
        ppins = self.printer.lookup_object('pins')
        self.mcu_endstop = ppins.setup_pin('endstop', config.get('pin'))
        self.speed = config.getfloat('speed', 40., above=0.)                 # mm/s, continuous feed
        self.max_load = config.getfloat('max_load', 800., above=0.)
        self.head_to_nozzle = config.getfloat('head_to_nozzle', 0., minval=0.)
        self.nozzle_speed = config.getfloat('nozzle_speed', 5., above=0.)    # mm/s, into the melt zone
        self.max_unload_extra = config.getfloat('max_unload_extra', 200., minval=0.)
        self.blind_load = config.getfloat('blind_load', 0., minval=0.)
        self.min_temp = config.getfloat('min_temp', 170., minval=0.)
        self.bypass_variable = config.get('bypass_variable', 'head_sensor_bypass')
        self.bypass = False
        self.last = {"loaded_mm": 0., "present": None}
        self.printer.register_event_handler("klippy:ready", self._handle_ready)
        for name, func in (("YUMI_LOAD_TO_HEAD", self.cmd_LOAD), ("YUMI_UNLOAD_CHECK", self.cmd_UNLOAD_CHECK),
                           ("SET_HEAD_SENSOR_BYPASS", self.cmd_BYPASS), ("QUERY_HEAD_SENSOR", self.cmd_QUERY)):
            self.gcode.register_command(name, func, desc=func.__doc__)

    # ── state ──────────────────────────────────────────────────────────
    def _handle_ready(self):
        sv = self.printer.lookup_object('save_variables', None)
        if sv is not None:
            self.bypass = bool(sv.allVariables.get(self.bypass_variable, False))
        if self.bypass:
            logging.warning("yumi_filament_head: head sensor BYPASSED (saved variable %s)", self.bypass_variable)

    def _present(self):
        toolhead = self.printer.lookup_object('toolhead')
        return bool(self.mcu_endstop.query_endstop(toolhead.get_last_move_time()))

    def get_status(self, eventtime):
        # NEVER query the MCU here: get_status runs in the reactor's main context (Moonraker,
        # KlipperScreen polls) where waiting for an MCU response is not allowed — the answer
        # arriving asynchronously took Klipper down ("'NoneType' object has no attribute
        # 'timer_is_running'"). The presence is the one seen by the last command.
        st = dict(self.last)
        st["bypass"] = self.bypass
        return st

    # ── moves ──────────────────────────────────────────────────────────
    def _extruder_steppers(self):
        """The active extruder's stepper and every feeder synced to it (their motion queue)."""
        toolhead = self.printer.lookup_object('toolhead')
        extruder = toolhead.get_extruder()
        steppers = []
        es = getattr(extruder, 'extruder_stepper', None)
        if es is not None:
            steppers.append(es.stepper)
        for _name, obj in self.printer.lookup_objects('extruder_stepper'):
            real = getattr(obj, 'extruder_stepper', obj)   # lookup_objects returns the wrapper
            if getattr(real, 'motion_queue', None) == extruder.get_name() and real.stepper not in steppers:
                steppers.append(real.stepper)
        return extruder, steppers

    def _homing_feed(self, distance, speed, triggered, what):
        """One continuous extruder move stopped by the head sensor (triggered=True: until it
        sees filament; False: until it releases). Returns the distance actually fed (signed)."""
        toolhead = self.printer.lookup_object('toolhead')
        toolhead.wait_moves()
        extruder, steppers = self._extruder_steppers()
        if not steppers:
            raise self.printer.command_error("YUMI head: no extruder stepper on the active extruder")
        for s in steppers:
            self.mcu_endstop.add_stepper(s)
        start = toolhead.get_position()
        movepos = list(start)
        movepos[3] += distance
        hmove = homing.HomingMove(self.printer, [(self.mcu_endstop, 'head_sensor')])
        try:
            hmove.homing_move(movepos, speed, probe_pos=True, triggered=triggered, check_triggered=True)
        except self.printer.command_error as e:
            self._resync_extruder(toolhead, extruder, steppers)
            raise self.printer.command_error("%s: %s" % (what, str(e)))
        fed = self._resync_extruder(toolhead, extruder, steppers) - start[3]
        return fed

    def _resync_extruder(self, toolhead, extruder, steppers):
        """homing_move only puts the XYZ kinematics back in place; the extruder axis is ours:
        the steppers stopped at the trigger, so the toolhead, the extruder and the g-code layer
        must all take that halt position — otherwise the next extrusion would jump by the
        distance that was never travelled."""
        toolhead.flush_step_generation()
        halt = steppers[0].get_commanded_position()
        for s in steppers[1:]:
            s.set_position([halt, 0., 0.])
        extruder.last_position = halt
        toolhead.set_extruder(extruder, halt)
        self.printer.lookup_object('gcode_move').reset_last_position()
        return halt

    def _move_e(self, distance, speed):
        self.gcode.run_script_from_command("SAVE_GCODE_STATE NAME=_yumi_head")
        self.gcode.run_script_from_command("M83")
        self.gcode.run_script_from_command("G1 E%.3f F%d" % (distance, int(speed * 60)))
        self.gcode.run_script_from_command("M400")
        self.gcode.run_script_from_command("RESTORE_GCODE_STATE NAME=_yumi_head")

    def _blind_load_len(self):
        """Blind load length: blind_load, or the tip-shaping unload total (single source)."""
        if self.blind_load > 0:
            return self.blind_load
        tip = self.printer.lookup_object('gcode_macro _YUMI_TIP', None)
        if tip is None:
            return 0.
        v = tip.variables
        return sum(float(v.get(k, 0)) for k in ('first_len', 'cut_len', 'slow_len', 'pull_len'))

    def _hotend_temp(self):
        try:
            heater = self.printer.lookup_object('extruder').get_heater()
            return heater.get_status(self.printer.get_reactor().monotonic())['temperature']
        except Exception:
            return None

    # ── commands ───────────────────────────────────────────────────────
    def cmd_LOAD(self, gcmd):
        """Feed filament in one move until the head sensor sees it, then head_to_nozzle mm more"""
        speed = gcmd.get_float('SPEED', self.speed, above=0.)
        max_load = gcmd.get_float('MAX', self.max_load, above=0.)
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
            self.last["present"] = True
        else:
            try:
                fed = self._homing_feed(max_load, speed, True, "YUMI_LOAD_TO_HEAD")
            except self.printer.command_error as e:
                if "No trigger" in str(e):
                    raise gcmd.error("No filament at the head after %.0f mm: check the spool and the feeder "
                                     "(SET_HEAD_SENSOR_BYPASS ENABLE=1 if the sensor is broken)" % max_load)
                raise
            gcmd.respond_info("filament reached the head after %.0f mm" % fed)
            self.last["present"] = True
        if to_nozzle > 0:
            temp = self._hotend_temp()
            if temp is not None and temp < self.min_temp:
                raise gcmd.error("Hotend at %.0fC: heat above %.0fC before loading to the nozzle" % (temp, self.min_temp))
            self._move_e(to_nozzle, self.nozzle_speed)
            fed += to_nozzle
        self.last = {"loaded_mm": fed, "present": None if self.bypass else True}
        self.gcode.run_script_from_command("M117 Filament loaded")

    def cmd_UNLOAD_CHECK(self, gcmd):
        """After an unload: the head sensor must have released, pull more (one move) if not"""
        max_extra = gcmd.get_float('MAX_EXTRA', self.max_unload_extra, minval=0.)
        if self.bypass:
            gcmd.respond_info("head sensor BYPASSED: unload not checked")
            return
        if not self._present():
            gcmd.respond_info("filament out of the head")
            self.last = {"loaded_mm": 0., "present": False}
            return
        try:
            fed = self._homing_feed(-max_extra, self.speed, False, "YUMI_UNLOAD_CHECK")
        except self.printer.command_error as e:
            if "No trigger" in str(e):
                raise gcmd.error("Filament still at the head after %.0f mm more of retraction: unload failed" % max_extra)
            raise
        self.last = {"loaded_mm": fed, "present": False}
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
        self.last["present"] = present
        gcmd.respond_info("head sensor: %s%s" % ("filament present" if present else "no filament",
                                                 " (BYPASSED)" if self.bypass else ""))


def load_config(config):
    return YumiFilamentHead(config)
