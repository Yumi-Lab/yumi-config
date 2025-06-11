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
        
        # State management
        self.is_printing = False
        self.enabled = True  # Enabled by default after reboot
        
        # New configuration parameters
        self.pitch_view = config.getboolean('pitch_view', False)
        self.blockage_detection = config.getboolean('blockage_detection', False)
        
        if self.blockage_detection:
            self.min_pitch = config.getfloat('min_pitch')
            self.max_pitch = config.getfloat('max_pitch')
            self.blockage_threshold = config.getint('blockage_threshold')
            self.reset_threshold = config.getint('reset_threshold', 1, minval=1)
            self.anomaly_count = 0
            self.normal_count = 0
            self.total_anomalies = 0
            self.triggered = False
        
        # Pitch calculation state
        self.last_event_pos = None
        self.last_event_delta = 0
        
        # Configure pins
        buttons = self.printer.load_object(config, 'buttons')
        buttons.register_buttons([switch_pin], self.encoder_event)
        
        # Get printer objects
        self.reactor = self.printer.get_reactor()
        self.runout_helper = filament_switch_sensor.RunoutHelper(config)
        self.get_status = self.runout_helper.get_status
        self.extruder = None
        self.estimated_print_time = None
        self.filament_runout_pos = None
        
        # Register event handlers
        self.printer.register_event_handler('klippy:ready', self._handle_ready)
        self.printer.register_event_handler('idle_timeout:printing', self._handle_printing)
        self.printer.register_event_handler('idle_timeout:ready', self._handle_not_printing)
        self.printer.register_event_handler('idle_timeout:idle', self._handle_not_printing)
        
        # Register our custom handler only if not already registered
        gcode = self.printer.lookup_object('gcode')
        if not hasattr(gcode, '_filament_sensor_registered'):
            gcode.register_command('SET_FILAMENT_SENSOR', self.cmd_SET_FILAMENT_SENSOR,
                                 desc="Enable/disable filament sensor")
            gcode._filament_sensor_registered = True
    
    def cmd_SET_FILAMENT_SENSOR(self, gcmd):
        sensor_name = gcmd.get('SENSOR', 'filament_motion_sensor')
        if sensor_name != 'filament_motion_sensor':
            return
            
        enable = gcmd.get_int('ENABLE', 1)
        self.enabled = bool(enable)
        self.runout_helper.filament_present = bool(enable)
        
        if self.enabled:
            logging.info("Filament motion sensor enabled")
            # Reset detection state when re-enabling
            self.last_event_pos = None
            self.anomaly_count = 0
            self.triggered = False
        else:
            logging.info("Filament motion sensor disabled")
    
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
        self.is_printing = True
        if self.enabled:
            self.reactor.update_timer(self._extruder_pos_update_timer,
                    self.reactor.NOW)
    
    def _handle_not_printing(self, print_time):
        self.is_printing = False
        self.reactor.update_timer(self._extruder_pos_update_timer,
                self.reactor.NEVER)
    
    def _get_extruder_pos(self, eventtime=None):
        if eventtime is None:
            eventtime = self.reactor.monotonic()
        print_time = self.estimated_print_time(eventtime)
        return self.extruder.find_past_position(print_time)
    
    def _extruder_pos_update_event(self, eventtime):
        extruder_pos = self._get_extruder_pos(eventtime)
        present = extruder_pos < self.filament_runout_pos
        self.runout_helper.note_filament_present(eventtime, present)
        return eventtime + CHECK_RUNOUT_TIMEOUT
    
    def _is_my_extruder_active(self):
        toolhead = self.printer.lookup_object('toolhead', None)
        if not toolhead:
            return False
        active_extruder = getattr(toolhead, 'extruder', None)
        return active_extruder and active_extruder.get_name() == self.extruder_name
    
    def encoder_event(self, eventtime, state):
        # Skip processing if conditions not met
        if (not self.is_printing or 
            not self.enabled or 
            self.extruder is None or
            not self._is_my_extruder_active()):
            return
            
        current_pos = self._get_extruder_pos(eventtime)
        
        # Update filament position and state
        self