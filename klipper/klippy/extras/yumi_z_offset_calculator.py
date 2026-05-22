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
        self.max_probe_times = config.getint('max_probe_times', 6)
        self.z_hop = config.getfloat("z_hop", 10.0)
        self.samples_tolerance = config.getfloat("samples_tolerance", 0.02)
        self.probe_delay = config.getfloat("probe_delay", 1000.0)

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

        # Move nozzle above pressure switch
        gcmd.respond_info("Moving to pressure switch position...")
        self._move_to_pressure_switch()
        gcode.run_script_from_command("M400")
        gcode.run_script_from_command("G4 P%d" % int(self.probe_delay))

        # Probe with pressure switch (nozzle touches physically)
        gcmd.respond_info("Probing with pressure switch...")
        self._probe_with_pressure_switch(gcmd)

        # Nozzle is touching = this is Z=0
        gcode.run_script_from_command("SET_KINEMATIC_POSITION Z=0")
        gcmd.respond_info("Z=0 set at nozzle contact point")

        # Lift nozzle
        toolhead.manual_move([None, None, 10.0], self.approach_speed * 10)

        gcmd.respond_info("Z calibration complete!")
        logging.info("[ZOffsetCalculator] Z=0 set at pressure switch contact")

    def _move_to_pressure_switch(self):
        toolhead = self.printer.lookup_object('toolhead')
        toolhead.manual_move([self.pressure_switch_x, self.pressure_switch_y, self.z_hop],
                             self.approach_speed * 10)
        logging.info("[ZOffsetCalculator] Moved to pressure switch: X=%.1f, Y=%.1f",
                     self.pressure_switch_x, self.pressure_switch_y)

    def _probe_with_pressure_switch(self, gcmd):
        toolhead = self.printer.lookup_object('toolhead')
        gcode = self.printer.lookup_object('gcode')
        probe_pressure = self.printer.lookup_object('probe_pressure')

        zendstop_p = probe_pressure.run_probe(gcmd)

        for attempt in range(1, self.max_probe_times):
            # Z hop between probes
            if self.z_hop:
                pos = toolhead.get_position()
                toolhead.manual_move([None, None, pos[2] + self.z_hop],
                                     self.approach_speed * 10)

            gcmd.respond_info("Verifying probe %d/%d"
                              % (attempt, self.max_probe_times - 1))
            gcode.run_script_from_command("M400")
            gcode.run_script_from_command("G4 P%d" % int(self.probe_delay))

            zendstop_p1 = probe_pressure.run_probe(gcmd)
            diff_z = abs(zendstop_p1[2] - zendstop_p[2])
            zendstop_p = zendstop_p1

            if diff_z <= self.samples_tolerance:
                gcmd.respond_info("Pressure check success (diff=%.4f)" % diff_z)
                break
        else:
            raise gcmd.error("Pressure probe exceeded %d attempts"
                             % self.max_probe_times)

        logging.info("[ZOffsetCalculator] Pressure switch contact at Z=%.4f",
                     zendstop_p[2])


def load_config(config):
    return ZOffsetCalculator(config)
