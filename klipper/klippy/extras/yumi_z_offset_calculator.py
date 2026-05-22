import logging


class ZOffsetCalculator:
    def __init__(self, config):
        self.printer = config.get_printer()
        self.name = config.get_name()

        # Read parameters from config file
        self.pressure_switch_x = config.getfloat('pressure_switch_x', 30.0)
        self.pressure_switch_y = config.getfloat('pressure_switch_y', 200.0)
        self.compression_offset = config.getfloat('compression_offset', 0.2)
        self.approach_speed = config.getfloat('approach_speed', 5.0)
        self.retract_dist = config.getfloat('retract_dist', 5.0)
        self.max_probe_travel = config.getfloat('max_probe_travel', 20.0)
        self.max_probe_times = config.getint('max_probe_times', 6)
        self.z_hop = config.getfloat("z_hop", 10.0)
        self.samples_tolerance = config.getfloat("samples_tolerance", 0.02)
        self.probe_delay = config.getfloat("probe_delay", 1000.0)

        # Delayed initialization to ensure all objects are loaded
        self.printer.register_event_handler("klippy:connect", self._handle_connect)

        # Register gcode command
        gcode = self.printer.lookup_object('gcode')
        gcode.register_command('YUMI_CALCULATE_Z_OFFSET', self.cmd_CALCULATE_Z_OFFSET,
                              desc="Calculate probe z_offset using pressure switch")

    def _handle_connect(self):
        self.probe = self.printer.lookup_object('probe')
        self.probe_x_offset = self.probe.get_offsets()[0]
        self.probe_y_offset = self.probe.get_offsets()[1]
        logging.info("[ZOffsetCalculator] Probe offsets: X=%.2f, Y=%.2f",
                     self.probe_x_offset, self.probe_y_offset)

    def _check_homed(self, gcmd):
        toolhead = self.printer.lookup_object('toolhead')
        if 'xy' not in toolhead.get_status(0).get('homed_axes', ''):
            raise gcmd.error("ZOffsetCalculator: X and Y must be homed before running YUMI_CALCULATE_Z_OFFSET")

    def cmd_CALCULATE_Z_OFFSET(self, gcmd):
        logging.info("[ZOffsetCalculator] Starting z_offset calculation")
        self._check_homed(gcmd)

        save_config = gcmd.get_int('SAVE', 0, minval=0, maxval=1)
        gcode = self.printer.lookup_object('gcode')

        # Phase 1: Probe with proximity sensor at pressure switch location
        gcmd.respond_info("Phase 1: Probing with proximity sensor...")
        proximity_z = self._probe_with_proximity_sensor(gcmd)

        # Move to pressure switch position (nozzle directly above)
        gcmd.respond_info("Phase 2: Moving to pressure switch position...")
        self._move_to_pressure_switch()
        gcode.run_script_from_command("M400")
        gcode.run_script_from_command("G4 P%d" % int(self.probe_delay))

        # Phase 2: Probe with pressure switch (nozzle touches)
        gcmd.respond_info("Phase 2: Probing with pressure switch...")
        pressure_z = self._probe_with_pressure_switch(gcmd)

        # Calculate z_offset
        z_offset = proximity_z - pressure_z + self.compression_offset
        gcmd.respond_info("Calculated z_offset: %.4f" % z_offset)

        # Lift nozzle before saving
        toolhead = self.printer.lookup_object('toolhead')
        pos = toolhead.get_position()
        toolhead.manual_move([None, None, pos[2] + 10.0], self.approach_speed * 10)

        # Save z_offset
        self._save_z_offset(z_offset, save_config, gcmd)

        gcmd.respond_info("Z offset calculation complete!")
        logging.info("[ZOffsetCalculator] Completed z_offset calculation: %.4f", z_offset)

    def _move_to_pressure_switch(self):
        toolhead = self.printer.lookup_object('toolhead')
        toolhead.manual_move([self.pressure_switch_x, self.pressure_switch_y, self.z_hop], self.approach_speed * 10)
        logging.info("[ZOffsetCalculator] Moved to pressure switch: X=%.1f, Y=%.1f",
                     self.pressure_switch_x, self.pressure_switch_y)

    def _probe_with_proximity_sensor(self, gcmd):
        toolhead = self.printer.lookup_object('toolhead')
        gcode = self.printer.lookup_object('gcode')

        # Move probe (not nozzle) above pressure switch — compensate for probe offset
        actual_x = self.pressure_switch_x - self.probe_x_offset
        actual_y = self.pressure_switch_y - self.probe_y_offset

        # Check travel distance is within limits
        pos = toolhead.get_position()
        travel = ((pos[0] - actual_x) ** 2 + (pos[1] - actual_y) ** 2) ** 0.5
        if travel > self.max_probe_travel * 50:
            raise gcmd.error("ZOffsetCalculator: Travel distance %.1f too large" % travel)

        toolhead.manual_move([actual_x, actual_y, self.z_hop], self.approach_speed * 10)
        gcode.run_script_from_command("PROBE")

        pos = toolhead.get_position()
        logging.info("[ZOffsetCalculator] Proximity probe result: Z=%.4f", pos[2])

        # Retract after probe
        toolhead.manual_move([None, None, pos[2] + self.retract_dist], self.approach_speed * 10)

        return pos[2]

    def _probe_with_pressure_switch(self, gcmd):
        toolhead = self.printer.lookup_object('toolhead')
        gcode = self.printer.lookup_object('gcode')
        probe_pressure = self.printer.lookup_object('probe_pressure')

        zendstop_p = probe_pressure.run_probe(gcmd)

        for attempt in range(1, self.max_probe_times):
            # Z hop between probes
            if self.z_hop:
                pos = toolhead.get_position()
                toolhead.manual_move([None, None, pos[2] + self.z_hop], self.approach_speed * 10)

            gcmd.respond_info("ZoffsetCalibration: Verifying probe %d/%d"
                              % (attempt, self.max_probe_times - 1))
            gcode.run_script_from_command("M400")
            gcode.run_script_from_command("G4 P%d" % int(self.probe_delay))

            zendstop_p1 = probe_pressure.run_probe(gcmd)
            diff_z = abs(zendstop_p1[2] - zendstop_p[2])
            zendstop_p = zendstop_p1

            if diff_z <= self.samples_tolerance:
                gcmd.respond_info("ZoffsetCalibration: Pressure check success (diff=%.4f)" % diff_z)
                break
        else:
            raise gcmd.error("ZoffsetCalibration: Pressure probe exceeded %d attempts"
                             % self.max_probe_times)

        logging.info("[ZOffsetCalculator] Pressure switch triggered at Z=%.4f", zendstop_p[2])
        return zendstop_p[2]

    def _save_z_offset(self, z_offset, save_config, gcmd):
        gcode = self.printer.lookup_object('gcode')

        # Apply z_offset immediately for the current session
        gcode.run_script_from_command("SET_GCODE_OFFSET Z=%.4f MOVE=0" % z_offset)
        gcmd.respond_info("Z offset applied for current session: %.4f" % z_offset)

        # Save to config for persistence across reboots
        configfile = self.printer.lookup_object('configfile')
        configfile.set('probe', 'z_offset', "%.4f" % z_offset)

        gcmd.respond_info("Z offset saved to config: %.4f" % z_offset)
        logging.info("[ZOffsetCalculator] Z offset saved: %.4f", z_offset)

        if save_config:
            gcode.run_script_from_command("SAVE_CONFIG")


def load_config(config):
    return ZOffsetCalculator(config)
