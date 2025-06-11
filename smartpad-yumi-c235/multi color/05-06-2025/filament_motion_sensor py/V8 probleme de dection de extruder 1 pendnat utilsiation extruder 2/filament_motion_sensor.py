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
            # New configuration for reset threshold
            self.reset_threshold = config.getint('reset_threshold', 1, minval=1)
            self.anomaly_count = 0
            self.normal_count = 0  # Count of consecutive normal pitches
            self.total_anomalies = 0  # Compteur total d'anomalies
            self.triggered = False    # Évite les déclenchements multiples
        
        # Pitch calculation state
        self.last_event_pos = None
        self.last_event_delta = 0  # Track movement direction
        
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
                delta = current_pos - self.last_event_pos
                distance = abs(delta)
                self.last_event_delta = delta  # Store direction for next event
                anomaly_detected = False
                trigger_action = False
                
                # Only process extrusion movements (positive delta) for anomalies
                if delta >= 0 and self.blockage_detection:
                    # Check if pitch is abnormal
                    if distance < self.min_pitch or distance > self.max_pitch:
                        self.anomaly_count += 1
                        self.total_anomalies += 1
                        self.normal_count = 0  # Reset consecutive normal count
                        anomaly_detected = True
                        
                        # Check if we should trigger action
                        if not self.triggered and self.anomaly_count >= self.blockage_threshold:
                            trigger_action = True
                            self.triggered = True  # Prevent multiple triggers
                
                # Display pitch in console if enabled
                if self.pitch_view:
                    gcode = self.printer.lookup_object('gcode')
                    
                    # Base message
                    msg = "Filament pitch: %.2f mm" % distance
                    
                    if self.blockage_detection:
                        if delta < 0:
                            # Retraction movement
                            msg += " [RETRACTION]"
                        elif anomaly_detected:
                            # Abnormal pitch during extrusion
                            if trigger_action:
                                msg += " [ANOMALY #%d - TRIGGERING PAUSE]" % self.total_anomalies
                            else:
                                msg += " [ANOMALY #%d - COUNT: %d/%d]" % (
                                    self.total_anomalies, self.anomaly_count, self.blockage_threshold)
                        else:
                            # Normal pitch during extrusion
                            if self.anomaly_count > 0:
                                # Count consecutive normal pitches
                                self.normal_count += 1
                                if self.normal_count >= self.reset_threshold:
                                    msg += " [NORMAL - RESETTING ANOMALY COUNT]"
                                    self.anomaly_count = 0
                                    self.triggered = False
                                else:
                                    msg += " [NORMAL - RESET COUNT: %d/%d]" % (
                                        self.normal_count, self.reset_threshold)
                            else:
                                msg += " [NORMAL]"
                    else:
                        # No blockage detection active
                        msg += " [NORMAL]"
                    
                    gcode.respond_info(msg)
                
                # Handle trigger action for both modes
                if trigger_action:
                    logging.error("Filament blockage detected: pitch=%.2f mm (Anomaly #%d)",
                                  distance, self.total_anomalies)
                    # Signal filament absence to trigger pause
                    self.runout_helper.note_filament_present(eventtime, False)
                # Handle blockage detection without pitch view
                elif self.blockage_detection and delta >= 0 and anomaly_detected and not self.triggered and self.anomaly_count >= self.blockage_threshold:
                    self.triggered = True
                    logging.error("Filament blockage detected: pitch=%.2f mm (Anomaly #%d)",
                                  distance, self.total_anomalies)
                    # Signal filament absence to trigger pause
                    self.runout_helper.note_filament_present(eventtime, False)
            
            # Update last event position
            self.last_event_pos = current_pos
            
            # Original runout detection logic
            self._update_filament_runout_pos(eventtime)
            self.runout_helper.note_filament_present(eventtime, True)

def load_config_prefix(config):
    return EncoderSensor(config)