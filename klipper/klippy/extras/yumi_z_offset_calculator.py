import logging


class ZOffsetCalculator:
    def __init__(self, config):
        self.printer = config.get_printer()
        self.name = config.get_name()

        # Read parameters from config file
        self.pressure_switch_x = config.getfloat('pressure_switch_x', 30.0)
        self.pressure_switch_y = config.getfloat('pressure_switch_y', 200.0)
        self.compression_offset = config.getfloat('compression_offset', 0.2)
        self.max_probe_times = config.getint('max_probe_times', 6)
        self.z_hop = config.getfloat("z_hop", 10.0)
        self.samples_tolerance = config.getfloat("samples_tolerance", 0.02)
        self.samples = config.getint("samples", 2)
        self.probe_delay = config.getfloat("probe_delay", 1000.0)
        self.safe_z = config.getfloat("safe_z", 10.0)
        self.travel_speed = config.getfloat("travel_speed", 30.0)

        # Register gcode command
        gcode = self.printer.lookup_object('gcode')
        gcode.register_command('YUMI_CALCULATE_Z_OFFSET', self.cmd_CALCULATE_Z_OFFSET,
                              desc="Set Z=0 at nozzle contact via pressure switch")

    def _check_homed(self, gcmd):
        toolhead = self.printer.lookup_object('toolhead')
        if 'xy' not in toolhead.get_status(0).get('homed_axes', ''):
            raise gcmd.error("ZOffsetCalculator: X and Y must be homed first")

    def cmd_CALCULATE_Z_OFFSET(self, gcmd):
        logging.info("[ZOffsetCalculator] Starting Z=0 calibration")
        self._check_homed(gcmd)

        gcode = self.printer.lookup_object('gcode')
        toolhead = self.printer.lookup_object('toolhead')
        probe_pressure = self.printer.lookup_object('probe_pressure')
        self.lift_speed = probe_pressure.get_lift_speed()
        # travel_speed from config (set in __init__)

        # Set Z to max position to give full travel range for probe
        z_max = self.printer.lookup_object('configfile').get_status(0)['settings']['stepper_z']['position_max']
        gcode.run_script_from_command("SET_KINEMATIC_POSITION Z=%.1f" % z_max)

        # Move nozzle above pressure switch
        gcmd.respond_info("Moving to pressure switch position...")
        self._move_to_pressure_switch()
        gcode.run_script_from_command("M400")
        gcode.run_script_from_command("G4 P%d" % int(self.probe_delay))

        # Probe with pressure switch (nozzle touches physically)
        gcmd.respond_info("Probing with pressure switch...")
        self._probe_with_pressure_switch(gcmd)

        gcmd.respond_info("Z calibration complete!")
        logging.info("[ZOffsetCalculator] Z=0 set at pressure switch contact")

    def _move_to_pressure_switch(self):
        toolhead = self.printer.lookup_object('toolhead')
        # Move XY only — Z stays where it is, probe will descend
        toolhead.manual_move([self.pressure_switch_x, self.pressure_switch_y, None],
                             self.travel_speed)
        logging.info("[ZOffsetCalculator] Moved to pressure switch: X=%.1f, Y=%.1f",
                     self.pressure_switch_x, self.pressure_switch_y)

    def _probe_with_pressure_switch(self, gcmd):
        toolhead = self.printer.lookup_object('toolhead')
        gcode = self.printer.lookup_object('gcode')
        probe_pressure = self.printer.lookup_object('probe_pressure')

        zendstop_p = probe_pressure.run_probe(gcmd)
        stable_count = 1  # first probe counts

        for attempt in range(1, self.max_probe_times):
            # Z hop from first probe (or previous iteration lift already done)
            if attempt == 1 and self.z_hop:
                pos = toolhead.get_position()
                toolhead.manual_move([None, None, pos[2] + self.z_hop],
                                     self.lift_speed)
                gcode.run_script_from_command("M400")

            gcmd.respond_info("Tap %d/%d (stable: %d/%d)"
                              % (attempt + 1, self.max_probe_times,
                                 stable_count, self.samples))

            # Probe
            zendstop_p1 = probe_pressure.run_probe(gcmd)

            # Process result at trigger point (before any lift)
            diff_z = abs(zendstop_p1[2] - zendstop_p[2])
            zendstop_p = zendstop_p1

            if diff_z <= self.samples_tolerance:
                stable_count += 1
                gcmd.respond_info("Tap OK (diff=%.4fmm, stable %d/%d)"
                                  % (diff_z, stable_count, self.samples))
                if stable_count >= self.samples:
                    # VALIDATED
                    trigger_z = zendstop_p[2]
                    current_z = toolhead.get_position()[2]
                    gcmd.respond_info(
                        "VALIDATED: trigger_z=%.4f current_z=%.4f "
                        "diff=%.4f compression=%.2f"
                        % (trigger_z, current_z,
                           current_z - trigger_z, self.compression_offset))
                    # Z=0 = trigger + compression_offset above bed
                    # We are at trigger point → set Z = -compression_offset
                    gcode.run_script_from_command(
                        "SET_KINEMATIC_POSITION Z=%.4f"
                        % (-self.compression_offset))
                    gcmd.respond_info("SET_KINEMATIC_POSITION Z=%.4f"
                                      % (-self.compression_offset))
                    # Lift to safe_z above Z=0
                    toolhead.manual_move([None, None, self.safe_z],
                                         self.lift_speed)
                    gcode.run_script_from_command("M400")
                    return
            else:
                stable_count = 1
                gcmd.respond_info("Tap drift (diff=%.4fmm) — reset"
                                  % diff_z)

            # Not validated — z_hop for next attempt
            if self.z_hop:
                pos = toolhead.get_position()
                toolhead.manual_move([None, None, pos[2] + self.z_hop],
                                     self.lift_speed)
            gcode.run_script_from_command("M400")

        raise gcmd.error("Pressure probe failed: only %d/%d stable after %d taps"
                         % (stable_count, self.samples, self.max_probe_times))


def load_config(config):
    return ZOffsetCalculator(config)
