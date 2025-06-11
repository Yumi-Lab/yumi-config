# Filament Motion Sensor Module
#
# Copyright (C) 2021 Joshua Wherrett <thejoshw.code@gmail.com>
#
# This file may be distributed under the terms of the GNU GPLv3 license.
import logging
from collections import deque
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
        
        # Advanced blockage detection parameters
        self.blockage_detection = config.getboolean('blockage_detection', False)
        self.blockage_min_pitch = config.getfloat('blockage_min_pitch', 0.4)
        self.blockage_max_pitch = config.getfloat('blockage_max_pitch', 3.0)
        self.blockage_threshold = config.getint('blockage_threshold', 3)
        
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
        self.last_extruder_pos = None
        self.blockage_counter = 0
        self.pitch_history = deque(maxlen=50)
        
        # Register commands and event handlers
        self.printer.register_event_handler('klippy:ready',
                self._handle_ready)

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

    def is_retracting(self, current_pos):
        """Check if extruder is currently retracting"""
        if self.last_extruder_pos is None:
            return False
        return current_pos < self.last_extruder_pos

    def encoder_event(self, eventtime, state):
        if self.extruder is not None:
            self._update_filament_runout_pos(eventtime)
            # Check for filament insertion
            self.runout_helper.note_filament_present(eventtime, True)
            
            # Calculate pitch
            current_pos = self._get_extruder_pos(eventtime)
            if self.last_extruder_pos is not None:
                pitch = current_pos - self.last_extruder_pos
                abs_pitch = abs(pitch)
                
                # Display pitch (optional)
                msg = "PITCH = %.2fmm" % abs_pitch
                gcode = self.printer.lookup_object('gcode')
                gcode.respond_info(msg)
                
                # Advanced blockage detection
                if self.blockage_detection:
                    # Update pitch history
                    self.pitch_history.append(abs_pitch)
                    
                    # Check for abnormal pitch
                    is_abnormal = (abs_pitch < self.blockage_min_pitch or 
                                   abs_pitch > self.blockage_max_pitch)
                    
                    # Ignore during retractions
                    if is_abnormal and not self.is_retracting(current_pos):
                        self.blockage_counter += 1
                        if self.blockage_counter >= self.blockage_threshold:
                            logging.info("Filament blockage detected!")
                            self.runout_helper.note_filament_present(eventtime, False)
                    else:
                        self.blockage_counter = max(0, self.blockage_counter - 1)
            
            # Update position for next calculation
            self.last_extruder_pos = current_pos

def load_config_prefix(config):
    return EncoderSensor(config)