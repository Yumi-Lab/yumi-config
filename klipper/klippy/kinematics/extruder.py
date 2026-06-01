# Code for handling printer nozzle extruders
#
# Copyright (C) 2016-2025  Kevin O'Connor <kevin@koconnor.net>
#
# This file may be distributed under the terms of the GNU GPLv3 license.
import math, logging
import stepper, chelper

class ExtruderStepper:
    def __init__(self, config):
        self.printer = config.get_printer()
        self.name = config.get_name().split()[-1]
        self.pressure_advance = self.pressure_advance_smooth_time = 0.
        # YUMI: smooth_time actually loaded in the kinematics (may be active
        # because of lead_time even when pressure_advance == 0).
        self._applied_smooth = 0.
        self.config_pa = config.getfloat('pressure_advance', 0., minval=0.)
        self.config_smooth_time = config.getfloat(
                'pressure_advance_smooth_time', 0.040, above=0., maxval=.200)
        # Setup stepper
        self.stepper = stepper.PrinterStepper(config)
        ffi_main, ffi_lib = chelper.get_ffi()
        self.sk_extruder = ffi_main.gc(ffi_lib.extruder_stepper_alloc(),
                                       ffi_lib.extruder_stepper_free)
        self.stepper.set_stepper_kinematics(self.sk_extruder)
        self.motion_queue = None
        # Register commands
        self.printer.register_event_handler("klippy:connect",
                                            self._handle_connect)
        gcode = self.printer.lookup_object('gcode')
        if self.name == 'extruder':
            gcode.register_mux_command("SET_PRESSURE_ADVANCE", "EXTRUDER", None,
                                       self.cmd_default_SET_PRESSURE_ADVANCE,
                                       desc=self.cmd_SET_PRESSURE_ADVANCE_help)
        gcode.register_mux_command("SET_PRESSURE_ADVANCE", "EXTRUDER",
                                   self.name, self.cmd_SET_PRESSURE_ADVANCE,
                                   desc=self.cmd_SET_PRESSURE_ADVANCE_help)
        gcode.register_mux_command("SET_EXTRUDER_ROTATION_DISTANCE", "EXTRUDER",
                                   self.name, self.cmd_SET_E_ROTATION_DISTANCE,
                                   desc=self.cmd_SET_E_ROTATION_DISTANCE_help)
        gcode.register_mux_command("SYNC_EXTRUDER_MOTION", "EXTRUDER",
                                   self.name, self.cmd_SYNC_EXTRUDER_MOTION,
                                   desc=self.cmd_SYNC_EXTRUDER_MOTION_help)
    def _handle_connect(self):
        self._set_pressure_advance(self.config_pa, self.config_smooth_time)
    def get_status(self, eventtime):
        motion_queuing = self.printer.lookup_object('motion_queuing')
        lead_time = motion_queuing.get_trapq_lead(self.stepper.get_trapq())
        return {'pressure_advance': self.pressure_advance,
                'smooth_time': self.pressure_advance_smooth_time,
                'lead_time': lead_time,
                'motion_queue': self.motion_queue}
    def _cur_lead(self):
        mq = self.printer.lookup_object('motion_queuing')
        return mq.get_trapq_lead(self.stepper.get_trapq())
    def _set_lead_time(self, gcmd, lead_time):
        # YUMI: lead belongs to the motion queue (trapq); apply it to whatever
        # trapq this stepper is currently bound to. Synced feeders share it.
        strapq = self.stepper.get_trapq()
        if strapq is None:
            gcmd.respond_info("Extruder stepper '%s' not bound to a motion "
                              "queue; LEAD_TIME ignored" % (self.name,))
            return
        toolhead = self.printer.lookup_object('toolhead')
        toolhead.flush_step_generation()
        motion_queuing = self.printer.lookup_object('motion_queuing')
        motion_queuing.set_trapq_lead(strapq, lead_time)
        # YUMI: a lead can enable (or disable) smoothing even when PA==0, so the
        # smooth_time works WITH the lead without needing a fake tiny PA.
        new_smooth = (self.pressure_advance_smooth_time
                      if (self.pressure_advance or lead_time) else 0.)
        if new_smooth != self._applied_smooth:
            ffi_main, ffi_lib = chelper.get_ffi()
            ffi_lib.extruder_set_pressure_advance(
                    self.sk_extruder, 0., self.pressure_advance, new_smooth)
            self._applied_smooth = new_smooth
        motion_queuing.check_step_generation_scan_windows()
    def find_past_position(self, print_time):
        mcu_pos = self.stepper.get_past_mcu_position(print_time)
        return self.stepper.mcu_to_commanded_position(mcu_pos)
    def sync_to_extruder(self, extruder_name):
        toolhead = self.printer.lookup_object('toolhead')
        toolhead.flush_step_generation()
        motion_queuing = self.printer.lookup_object('motion_queuing')
        if not extruder_name:
            self.stepper.set_trapq(None)
            self.motion_queue = None
            motion_queuing.check_step_generation_scan_windows()
            return
        extruder = self.printer.lookup_object(extruder_name, None)
        if extruder is None or not isinstance(extruder, PrinterExtruder):
            raise self.printer.command_error("'%s' is not a valid extruder."
                                             % (extruder_name,))
        self.stepper.set_position([extruder.last_position, 0., 0.])
        self.stepper.set_trapq(extruder.get_trapq())
        self.motion_queue = extruder_name
        motion_queuing.check_step_generation_scan_windows()
    def _set_pressure_advance(self, pressure_advance, smooth_time):
        # YUMI: smoothing is active when PA>0 OR a lead is set on this trapq, so
        # smooth_time works at PA=0 alongside lead_time (no fake tiny PA needed).
        lead = self._cur_lead()
        new_smooth_time = smooth_time if (pressure_advance or lead) else 0.
        toolhead = self.printer.lookup_object("toolhead")
        ffi_main, ffi_lib = chelper.get_ffi()
        espa = ffi_lib.extruder_set_pressure_advance
        if new_smooth_time != self._applied_smooth:
            # Need full kinematic flush to change the smooth time
            toolhead.flush_step_generation()
            espa(self.sk_extruder, 0., pressure_advance, new_smooth_time)
            self._applied_smooth = new_smooth_time
            motion_queuing = self.printer.lookup_object('motion_queuing')
            motion_queuing.check_step_generation_scan_windows()
        else:
            toolhead.register_lookahead_callback(
                lambda print_time: espa(self.sk_extruder, print_time,
                                        pressure_advance, new_smooth_time))
        self.pressure_advance = pressure_advance
        self.pressure_advance_smooth_time = smooth_time
    cmd_SET_PRESSURE_ADVANCE_help = "Set pressure advance parameters"
    def cmd_default_SET_PRESSURE_ADVANCE(self, gcmd):
        extruder = self.printer.lookup_object('toolhead').get_extruder()
        if extruder.extruder_stepper is None:
            raise gcmd.error("Active extruder does not have a stepper")
        strapq = extruder.extruder_stepper.stepper.get_trapq()
        if strapq is not extruder.get_trapq():
            raise gcmd.error("Unable to infer active extruder stepper")
        extruder.extruder_stepper.cmd_SET_PRESSURE_ADVANCE(gcmd)
    def cmd_SET_PRESSURE_ADVANCE(self, gcmd):
        pressure_advance = gcmd.get_float('ADVANCE', self.pressure_advance,
                                          minval=0.)
        smooth_time = gcmd.get_float('SMOOTH_TIME',
                                     self.pressure_advance_smooth_time,
                                     minval=0., maxval=.200)
        self._set_pressure_advance(pressure_advance, smooth_time)
        lead_time = gcmd.get_float('LEAD_TIME', None, minval=0., maxval=0.5)
        if lead_time is not None:
            self._set_lead_time(gcmd, lead_time)
        motion_queuing = self.printer.lookup_object('motion_queuing')
        cur_lead = motion_queuing.get_trapq_lead(self.stepper.get_trapq())
        msg = ("pressure_advance: %.6f\n"
               "pressure_advance_smooth_time: %.6f\n"
               "lead_time: %.6f"
               % (pressure_advance, smooth_time, cur_lead))
        self.printer.set_rollover_info(self.name, "%s: %s" % (self.name, msg))
        gcmd.respond_info(msg, log=False)
    cmd_SET_E_ROTATION_DISTANCE_help = "Set extruder rotation distance"
    def cmd_SET_E_ROTATION_DISTANCE(self, gcmd):
        rotation_dist = gcmd.get_float('DISTANCE', None)
        if rotation_dist is not None:
            if not rotation_dist:
                raise gcmd.error("Rotation distance can not be zero")
            invert_dir, orig_invert_dir = self.stepper.get_dir_inverted()
            next_invert_dir = orig_invert_dir
            if rotation_dist < 0.:
                next_invert_dir = not orig_invert_dir
                rotation_dist = -rotation_dist
            toolhead = self.printer.lookup_object('toolhead')
            toolhead.flush_step_generation()
            self.stepper.set_rotation_distance(rotation_dist)
            self.stepper.set_dir_inverted(next_invert_dir)
        else:
            rotation_dist, spr = self.stepper.get_rotation_distance()
        invert_dir, orig_invert_dir = self.stepper.get_dir_inverted()
        if invert_dir != orig_invert_dir:
            rotation_dist = -rotation_dist
        gcmd.respond_info("Extruder '%s' rotation distance set to %0.6f"
                          % (self.name, rotation_dist))
    cmd_SYNC_EXTRUDER_MOTION_help = "Set extruder stepper motion queue"
    def cmd_SYNC_EXTRUDER_MOTION(self, gcmd):
        ename = gcmd.get('MOTION_QUEUE')
        self.sync_to_extruder(ename)
        gcmd.respond_info("Extruder '%s' now syncing with '%s'"
                          % (self.name, ename))

# Tracking for hotend heater, extrusion motion queue, and extruder stepper
class PrinterExtruder:
    def __init__(self, config, extruder_num):
        self.printer = config.get_printer()
        self.name = config.get_name()
        self.last_position = 0.
        # Setup hotend heater
        pheaters = self.printer.load_object(config, 'heaters')
        gcode_id = 'T%d' % (extruder_num,)
        self.heater = pheaters.setup_heater(config, gcode_id)
        # Setup kinematic checks
        self.nozzle_diameter = config.getfloat('nozzle_diameter', above=0.)
        filament_diameter = config.getfloat(
            'filament_diameter', minval=self.nozzle_diameter)
        self.filament_area = math.pi * (filament_diameter * .5)**2
        def_max_cross_section = 4. * self.nozzle_diameter**2
        def_max_extrude_ratio = def_max_cross_section / self.filament_area
        max_cross_section = config.getfloat(
            'max_extrude_cross_section', def_max_cross_section, above=0.)
        self.max_extrude_ratio = max_cross_section / self.filament_area
        logging.info("Extruder max_extrude_ratio=%.6f", self.max_extrude_ratio)
        toolhead = self.printer.lookup_object('toolhead')
        max_velocity, max_accel = toolhead.get_max_velocity()
        self.max_e_velocity = config.getfloat(
            'max_extrude_only_velocity', max_velocity * def_max_extrude_ratio
            , above=0.)
        self.max_e_accel = config.getfloat(
            'max_extrude_only_accel', max_accel * def_max_extrude_ratio
            , above=0.)
        self.max_e_dist = config.getfloat(
            'max_extrude_only_distance', 50., minval=0.)
        self.instant_corner_v = config.getfloat(
            'instantaneous_corner_velocity', 1., minval=0.)
        # Setup extruder trapq (trapezoidal motion queue)
        self.motion_queuing = self.printer.load_object(config, 'motion_queuing')
        self.trapq = self.motion_queuing.allocate_trapq()
        self.trapq_append = self.motion_queuing.lookup_trapq_append()
        # YUMI: extruder lead time (pure anticipation / coast). The lead shifts
        # the trapq content earlier in time at planner level (see process_move);
        # it is NOT a kin_extruder.c / calc_position change. Cache the trapq
        # address so process_move reads the live lead with one dict lookup.
        self._trapq_addr = self.motion_queuing.trapq_addr(self.trapq)
        config_lead = config.getfloat('lead_time', 0., minval=0., maxval=0.5)
        if config_lead:
            self.motion_queuing.set_trapq_lead(self.trapq, config_lead)
            # Refresh step-generation scan windows once syncemitters exist
            self.printer.register_event_handler(
                "klippy:connect",
                lambda: self.motion_queuing.check_step_generation_scan_windows())
        # Setup extruder stepper
        self.extruder_stepper = None
        if (config.get('step_pin', None) is not None
            or config.get('dir_pin', None) is not None
            or config.get('rotation_distance', None) is not None):
            self.extruder_stepper = ExtruderStepper(config)
            self.extruder_stepper.stepper.set_trapq(self.trapq)
        # Register commands
        gcode = self.printer.lookup_object('gcode')
        if self.name == 'extruder':
            toolhead.set_extruder(self, 0.)
            gcode.register_command("M104", self.cmd_M104)
            gcode.register_command("M109", self.cmd_M109)
        gcode.register_mux_command("ACTIVATE_EXTRUDER", "EXTRUDER",
                                   self.name, self.cmd_ACTIVATE_EXTRUDER,
                                   desc=self.cmd_ACTIVATE_EXTRUDER_help)
    def get_status(self, eventtime):
        sts = self.heater.get_status(eventtime)
        sts['can_extrude'] = self.heater.can_extrude
        if self.extruder_stepper is not None:
            sts.update(self.extruder_stepper.get_status(eventtime))
        return sts
    def get_name(self):
        return self.name
    def get_heater(self):
        return self.heater
    def get_trapq(self):
        return self.trapq
    def get_axis_gcode_id(self):
        return 'E'
    def stats(self, eventtime):
        return self.heater.stats(eventtime)
    def check_move(self, move, ea_index):
        if not self.heater.can_extrude:
            raise self.printer.command_error(
                "Extrude below minimum temp\n"
                "See the 'min_extrude_temp' config option for details")
        axis_r = move.axes_r[ea_index]
        axis_d = move.axes_d[ea_index]
        if (not move.axes_d[0] and not move.axes_d[1]) or axis_r < 0.:
            # Extrude only move (or retraction move) - limit accel and velocity
            if abs(axis_d) > self.max_e_dist:
                raise self.printer.command_error(
                    "Extrude only move too long (%.3fmm vs %.3fmm)\n"
                    "See the 'max_extrude_only_distance' config"
                    " option for details" % (axis_d, self.max_e_dist))
            inv_extrude_r = 1. / abs(axis_r)
            move.limit_speed(self.max_e_velocity * inv_extrude_r,
                             self.max_e_accel * inv_extrude_r)
        elif axis_r > self.max_extrude_ratio:
            if axis_d <= self.nozzle_diameter * self.max_extrude_ratio:
                # Permit extrusion if amount extruded is tiny
                return
            area = axis_r * self.filament_area
            logging.debug("Overextrude: %s vs %s (area=%.3f dist=%.3f)",
                          axis_r, self.max_extrude_ratio, area, move.move_d)
            raise self.printer.command_error(
                "Move exceeds maximum extrusion (%.3fmm^2 vs %.3fmm^2)\n"
                "See the 'max_extrude_cross_section' config option for details"
                % (area, self.max_extrude_ratio * self.filament_area))
    def calc_junction(self, prev_move, move, ea_index):
        diff_r = move.axes_r[ea_index] - prev_move.axes_r[ea_index]
        if diff_r:
            return (self.instant_corner_v / abs(diff_r))**2
        return move.max_cruise_v2
    def process_move(self, print_time, move, ea_index):
        axis_r = move.axes_r[ea_index]
        accel = move.accel * axis_r
        start_v = move.start_v * axis_r
        cruise_v = move.cruise_v * axis_r
        can_pressure_advance = False
        if axis_r > 0. and (move.axes_d[0] or move.axes_d[1]):
            can_pressure_advance = True
        # YUMI: shift the extruder trapq content earlier by the lead time so the
        # extruder finishes pushing `lead` before the toolhead stops (coast).
        # Positions are untouched; only the time base of the E trapq moves.
        lead = self.motion_queuing.trapq_leads.get(self._trapq_addr, 0.)
        # Queue movement (x is extruder movement, y is pressure advance flag)
        self.trapq_append(self.trapq, print_time - lead,
                          move.accel_t, move.cruise_t, move.decel_t,
                          move.start_pos[ea_index], 0., 0.,
                          1., can_pressure_advance, 0.,
                          start_v, cruise_v, accel)
        self.last_position = move.end_pos[ea_index]
    def find_past_position(self, print_time):
        if self.extruder_stepper is None:
            return 0.
        return self.extruder_stepper.find_past_position(print_time)
    def cmd_M104(self, gcmd, wait=False):
        # Set Extruder Temperature
        temp = gcmd.get_float('S', 0.)
        index = gcmd.get_int('T', None, minval=0)
        if index is not None:
            section = 'extruder'
            if index:
                section = 'extruder%d' % (index,)
            extruder = self.printer.lookup_object(section, None)
            if extruder is None:
                if temp <= 0.:
                    return
                raise gcmd.error("Extruder not configured")
        else:
            extruder = self.printer.lookup_object('toolhead').get_extruder()
        pheaters = self.printer.lookup_object('heaters')
        pheaters.set_temperature(extruder.get_heater(), temp, wait)
    def cmd_M109(self, gcmd):
        # Set Extruder Temperature and Wait
        self.cmd_M104(gcmd, wait=True)
    cmd_ACTIVATE_EXTRUDER_help = "Change the active extruder"
    def cmd_ACTIVATE_EXTRUDER(self, gcmd):
        toolhead = self.printer.lookup_object('toolhead')
        if toolhead.get_extruder() is self:
            gcmd.respond_info("Extruder %s already active" % (self.name,))
            return
        gcmd.respond_info("Activating extruder %s" % (self.name,))
        toolhead.flush_step_generation()
        toolhead.set_extruder(self, self.last_position)
        self.printer.send_event("extruder:activate_extruder")

# Dummy extruder class used when a printer has no extruder at all
class DummyExtruder:
    def __init__(self, printer):
        self.printer = printer
    def check_move(self, move, ea_index):
        raise move.move_error("Extrude when no extruder present")
    def find_past_position(self, print_time):
        return 0.
    def calc_junction(self, prev_move, move, ea_index):
        return move.max_cruise_v2
    def get_name(self):
        return ""
    def get_heater(self):
        raise self.printer.command_error("Extruder not configured")
    def get_trapq(self):
        return None
    def get_axis_gcode_id(self):
        return 'E'

def add_printer_objects(config):
    printer = config.get_printer()
    for i in range(99):
        section = 'extruder'
        if i:
            section = 'extruder%d' % (i,)
        if not config.has_section(section):
            break
        pe = PrinterExtruder(config.getsection(section), i)
        printer.add_object(section, pe)
