# Filament Motion Sensor Module
#
# Copyright (C) 2021 Joshua Wherrett <thejoshw.code@gmail.com>
#
# This file may be distributed under the terms of the GNU GPLv3 license.
import logging
from . import filament_switch_sensor

CHECK_RUNOUT_TIMEOUT = .250

class EncoderSensor:
    def __init__(self, config):
        # Read config
        self.printer = config.get_printer()
        switch_pin = config.get('switch_pin')
        self.extruder_name = config.get('extruder')
        self.detection_length = config.getfloat(
                'detection_length', 7., above=0.)
        
        # New configuration parameters
        self.pitch_view = config.getboolean('pitch_view', False)
        self.blockage_detection = config.getboolean('blockage_detection', False)
        
        if self.blockage_detection:
            self.min_pitch = config.getfloat('min_pitch')
            self.max_pitch = config.getfloat('max_pitch')
            self.blockage_threshold = config.getint('blockage_threshold')
            self.anomaly_count = 0
        
        # Pitch calculation state
        self.last_event_pos = None
        
        # Configure pins
        buttons = self.printer.load_object(config, 'buttons')
        buttons.register_buttons([switch_pin], self.encoder_event)
        # Get printer objects
        self.reactor = self.printer.get_reactor()
        self.runout_helper = filament_switch_sensor.RunoutHelper(config)
        self.get_status = self.runout_helper.get_status
        self.extruder = None
        self.estimated_print_time = None
        # Initialise internal state
        self.filament_runout_pos = None
        # Register commands and event handlers
        self.printer.register_event_handler('klippy:ready',
                self._handle_ready)
        self.printer.register_event_handler('idle_timeout:printing',
                self._handle_printing)
        self.printer.register_event_handler('idle_timeout:ready',
                self._handle_not_printing)
        self.printer.register_event_handler('idle_timeout:idle',
                self._handle_not_printing)
    def _update_filament_runout_pos(self, eventtime=None):
        if eventtime is None:
            eventtime = self.reactor.monotonic()
        self.filament_runout_pos = (
                self._get_extruder_pos(eventtime) +
                self.detection_length)
    def _handle_ready(self):
        self.extruder = self.printer.lookup_object(self.extruder_name)
        self.estimated_print_time = (
                self.printer.lookup_object('mcu').estimated_print_time)
        self._update_filament_runout_pos()
        self._extruder_pos_update_timer = self.reactor.register_timer(
                self._extruder_pos_update_event)
    def _handle_printing(self, print_time):
        self.reactor.update_timer(self._extruder_pos_update_timer,
                self.reactor.NOW)
    def _handle_not_printing(self, print_time):
        self.reactor.update_timer(self._extruder_pos_update_timer,
                self.reactor.NEVER)
    def _get_extruder_pos(self, eventtime=None):
        if eventtime is None:
            eventtime = self.reactor.monotonic()
        print_time = self.estimated_print_time(eventtime)
        return self.extruder.find_past_position(print_time)
    def _extruder_pos_update_event(self, eventtime):
        extruder_pos = self._get_extruder_pos(eventtime)
        # Check for filament runout
        self.runout_helper.note_filament_present(eventtime,
                extruder_pos < self.filament_runout_pos)
        return eventtime + CHECK_RUNOUT_TIMEOUT
    def encoder_event(self, eventtime, state):
        if self.extruder is not None:
            current_pos = self._get_extruder_pos(eventtime)
            
            # Pitch calculation and display
            if self.last_event_pos is not None:
                distance = abs(current_pos - self.last_event_pos)
                
                # Display pitch in console if enabled
                if self.pitch_view:
                    gcode = self.printer.lookup_object('gcode')
                    msg = "Filament pitch: %.2f mm" % distance
                    gcode.respond_info(msg)
                
                # Blockage detection logic
                if self.blockage_detection:
                    if distance < self.min_pitch or distance > self.max_pitch:
                        self.anomaly_count += 1
                        if self.anomaly_count >= self.blockage_threshold:
                            logging.error("Filament blockage detected: pitch=%.2f mm", distance)
                    else:
                        self.anomaly_count = 0
            
            # Update last event position
            self.last_event_pos = current_pos
            
            # Original runout detection logic
            self._update_filament_runout_pos(eventtime)
            self.runout_helper.note_filament_present(eventtime, True)

def load_config_prefix(config):
    return EncoderSensor(config)