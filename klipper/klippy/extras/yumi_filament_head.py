# Filament at the head — step-and-check load onto the head sensor, checked unload
#
# The Yumi heads carry a filament switch at the inlet (PA8). Feeding is blind otherwise: a
# print started with an empty head, or a colour change that never reached the nozzle, prints
# air. This module owns that pin as an ENDSTOP (queried, with pull-up) and feeds the extruder
# queue (extruder + synced feeders) in short steps, reading the switch after each one — the
# factory QC procedure. Synchronous for the caller: a tool macro or a start g-code blocks until
# the filament is really there. A continuous homing move (MCU trsync on the switch) was tried
# first and failed 4/4 on the bench while the stepped feed detected at the first attempt; the
# overshoot past the switch is at most one `step`.
#
#   YUMI_LOAD_TO_HEAD  [SPEED=] [MAX=] [STEP=] [HEAD_TO_NOZZLE=]
#       feed `step` mm at `speed`, read the switch, again until it sees filament, MAX mm at
#       most (error otherwise), then `head_to_nozzle` mm more to bring the tip to the nozzle
#       (hot end above min_temp for that part only).
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
#   pin               the head switch, wired as an endstop pin (^!PA8: pull-up, the line floats otherwise)
#   speed             mm/s of the load steps (default 16.7, the QC feed rate)
#   step              mm fed between two readings of the switch = maximum overshoot (default 20)
#   max_load          mm fed at most before "no filament at the head" is raised (default 800)
#   head_to_nozzle    mm from the switch to the nozzle, fed after the trigger (default 0)
#   nozzle_speed      mm/s of that last stretch into the melt zone (default 5)
#   max_unload_extra  mm pulled at most by YUMI_UNLOAD_CHECK while the switch still sees filament (default 200)
#   blind_load        mm fed when the sensor is bypassed; 0 = the unload total of _YUMI_TIP (default 0)
#   min_temp          degC the hotend must have reached before head_to_nozzle is fed (default 170)
#   bypass_variable   save_variables key that persists the bypass (default head_sensor_bypass)
# Status (printer.yumi_filament_head): loaded_mm, present (None = unknown or bypassed), bypass.
import logging


class YumiFilamentHead:
    def __init__(self, config):
        self.printer = config.get_printer()
        self.gcode = self.printer.lookup_object('gcode')
        ppins = self.printer.lookup_object('pins')
        self.mcu_endstop = ppins.setup_pin('endstop', config.get('pin'))
        self.speed = config.getfloat('speed', 16.7, above=0.)               # mm/s, feed steps
        self.step = config.getfloat('step', 20., above=0.)                  # mm between two readings
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
    def _stepped_feed(self, direction, max_mm, step, speed, want_present):
        """Feed `step` mm at a time (direction +1 load / -1 unload), reading the switch after
        each step, until it reports `want_present`. Returns the signed distance fed; raises
        "No trigger" past max_mm. The switch is read at rest, after M400, like the QC loop."""
        fed = 0.
        while fed < max_mm:
            chunk = min(step, max_mm - fed)
            self._move_e(direction * chunk, speed)
            fed += chunk
            if self._present() == want_present:
                return direction * fed
        raise self.printer.command_error("No trigger on head_sensor after %.0f mm" % max_mm)

    def _move_e(self, distance, speed):
        """One relative extruder move, complete before returning (the switch is read at rest)."""
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
        step = gcmd.get_float('STEP', self.step, above=0.)
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
                fed = self._stepped_feed(+1, max_load, step, speed, True)
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
        step = gcmd.get_float('STEP', self.step, above=0.)
        if self.bypass:
            gcmd.respond_info("head sensor BYPASSED: unload not checked")
            return
        if not self._present():
            gcmd.respond_info("filament out of the head")
            self.last = {"loaded_mm": 0., "present": False}
            return
        try:
            fed = self._stepped_feed(-1, max_extra, step, self.speed, False)
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
