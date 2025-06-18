# Filament Yumi Smart_Motion Sensor Module -V0.21.2.Beta
#
# Copyright (C) 2021 Xtrack33 by YUMI
# Modifications: Robust reset after detection_length runout
#
# This file may be distributed under the terms of the GNU GPLv3 license.
import logging
import csv
import os
from . import filament_switch_sensor

CHECK_RUNOUT_TIMEOUT = .250  # 250ms for better responsiveness

class FilamentYumiSmartMotionSensor:  # MODIFICATION ICI
    def __init__(self, config):
        # Basic configuration
        self.printer = config.get_printer()
        self.config = config
        switch_pin = config.get('switch_pin')
        self.extruder_name = config.get('extruder', 'extruder')
        self.detection_length = config.getfloat('detection_length', 7.0, above=0.)
        self.sensor_name = config.get_name().split()[-1]  # Store sensor name
        
        # Detection parameters
        self.pitch_view = config.getboolean('pitch_view', False)
        self.blockage_detection = config.getboolean('blockage_detection', False)
        self.data_logging = config.getboolean('data_logging', False)
        self.log_file_path = config.get('log_file_path', '/tmp/filament_sensor_log.csv')
        self.low_pitch_filter = config.getfloat('low_pitch_filter', 0.015, above=0.)
        
        # Detection thresholds
        if self.blockage_detection:
            self.min_pitch = config.getfloat('min_pitch', 1.0, above=self.low_pitch_filter)
            self.max_pitch = config.getfloat('max_pitch', 2.4, above=self.min_pitch)
            self.blockage_threshold = config.getint('blockage_threshold', 2, minval=1)
            self.reset_motion_sensor_threshold = config.getint('reset_motion_sensor_threshold', 16, minval=1)
            self.anomaly_count = 0
            self.normal_count = 0
            self.pause_sent = False
        
        # State variables
        self.last_event_pos = None
        self.last_event_time = None
        self.last_valid_pos = None
        self.start_time = None
        self.log_file = None
        self.runout_triggered = False
        self.last_retraction_pos = None
        self.ignore_next_pitch = False
        
        # Hardware configuration
        buttons = self.printer.load_object(config, 'buttons')
        buttons.register_buttons([switch_pin], self.encoder_event)
        
        # Klipper initialization
        self.reactor = self.printer.get_reactor()
        self.runout_helper = filament_switch_sensor.RunoutHelper(config)
        self.get_status = self.runout_helper.get_status
        self.extruder = None
        self.estimated_print_time = None
        self.filament_runout_pos = None
        
        # Logging initialization
        if self.data_logging:
            self._init_data_logging()
        
        # Handlers
        self.printer.register_event_handler('klippy:ready', self._handle_ready)
        self.printer.register_event_handler('idle_timeout:printing', self._handle_printing)
        self.printer.register_event_handler('idle_timeout:ready', self._handle_not_printing)
        self.printer.register_event_handler('idle_timeout:idle', self._handle_not_printing)
        self.printer.register_event_handler('idle_timeout:paused', self._handle_paused)
    
    def _init_data_logging(self):
        """Initialize logging system"""
        try:
            log_dir = os.path.dirname(self.log_file_path)
            if log_dir and not os.path.exists(log_dir):
                os.makedirs(log_dir)
            
            self.log_file = open(self.log_file_path, 'a')
            if os.stat(self.log_file_path).st_size == 0:
                writer = csv.writer(self.log_file)
                writer.writerow([
                    'timestamp', 'sensor', 'position', 
                    'delta', 'state', 'status',
                    'anomaly_counter', 'normal_counter', 'runout_triggered'
                ])
            logging.info("Initialized logging for %s", self.config.get_name())
        except Exception as e:
            logging.error("Logging initialization error: %s", str(e))
            self.data_logging = False
    
    def _log_data(self, eventtime, sensor_name, position, delta, state, status):
        """Log data"""
        if not self.data_logging or self.log_file is None:
            return
        
        try:
            writer = csv.writer(self.log_file)
            writer.writerow([
                "%.6f" % eventtime,
                sensor_name,
                "%.3f" % position,
                "%.5f" % delta,
                'HIGH' if state else 'LOW',
                status,
                f"{self.anomaly_count}/{self.blockage_threshold}",
                f"{self.normal_count}/{self.reset_motion_sensor_threshold}",
                "YES" if self.runout_triggered else "NO"
            ])
            self.log_file.flush()
        except Exception as e:
            logging.error("Log write error: %s", str(e))

    def _update_filament_runout_pos(self, eventtime=None):
        """Update detection position BASED ON ACTUAL FILAMENT MOVEMENT"""
        if self.last_valid_pos is None:
            return
            
        # Use last VALID filament position instead of extruder position
        self.filament_runout_pos = self.last_valid_pos + self.detection_length
        logging.info("New runout threshold: %.2f mm (based on filament pos: %.2f)", 
                    self.filament_runout_pos, self.last_valid_pos)
    
    def _handle_ready(self):
        """Printer ready handler"""
        self.extruder = self.printer.lookup_object(self.extruder_name)
        self.estimated_print_time = (
            self.printer.lookup_object('mcu').estimated_print_time)
        self._update_filament_runout_pos()
        self._extruder_pos_update_timer = self.reactor.register_timer(
            self._extruder_pos_update_event)
        self.start_time = self.reactor.monotonic()
        self.last_valid_pos = self._get_extruder_pos()
        self.last_retraction_pos = self.last_valid_pos
        self.ignore_next_pitch = False
    
    def _handle_printing(self, print_time):
        """Printing start handler - Critical reset"""
        self._update_filament_runout_pos()
        self.last_valid_pos = self._get_extruder_pos()
        self.last_retraction_pos = self.last_valid_pos
        self.runout_triggered = False
        self.ignore_next_pitch = False
        
        logging.info("Detection reset for new print")
        self.printer.lookup_object('gcode').respond_info(
            f"// {self.sensor_name}: Filament sensor reset - ready for new detection")
        
        self.reactor.update_timer(self._extruder_pos_update_timer, self.reactor.NOW)
    
    def _handle_not_printing(self, print_time):
        self.reactor.update_timer(self._extruder_pos_update_timer, self.reactor.NEVER)
        if self.data_logging and self.log_file is not None:
            self.log_file.close()
            self.log_file = None
    
    def _handle_paused(self, print_time):
        """Paused state handler"""
        self.runout_triggered = True
        self.reactor.update_timer(self._extruder_pos_update_timer, self.reactor.NEVER)
        
        if self.data_logging and self.log_file is not None:
            self.log_file.close()
            self.log_file = None
    
    def _get_extruder_pos(self, eventtime=None):
        """Get extruder position"""
        if eventtime is None:
            eventtime = self.reactor.monotonic()
        print_time = self.estimated_print_time(eventtime)
        return self.extruder.find_past_position(print_time)
    
    def _extruder_pos_update_event(self, eventtime):
        """Continuous filament presence check"""
        if self.last_valid_pos is None:
            return eventtime + CHECK_RUNOUT_TIMEOUT
        
        extruder_pos = self._get_extruder_pos(eventtime)
        # Calculate ACTUAL extruder movement since last valid detection
        extruder_moved = extruder_pos - self.last_valid_pos
        
        # More robust detection logic:
        # 1. Consider filament present if we've moved less than detection_length
        # 2. Only trigger runout if extruder has moved beyond threshold WITHOUT filament movement
        filament_present = extruder_moved < self.detection_length
        
        # Additional safety: if we have recent events, trust them more than position
        if self.last_event_time and (eventtime - self.last_event_time) < 5.0:
            filament_present = True
        
        # If filament is absent, mark runout
        if not filament_present and not self.pause_sent:
            logging.info("Potential runout detected. Extruder moved: %.2fmm beyond last valid position (threshold: %.2fmm)",
                        extruder_moved, self.detection_length)
            self.runout_triggered = True

        self.runout_helper.note_filament_present(eventtime, filament_present)
        return eventtime + CHECK_RUNOUT_TIMEOUT
    
    def encoder_event(self, eventtime, state):
        """Handle sensor events"""
        if self.extruder is None or self.last_valid_pos is None:
            return

        current_pos = self._get_extruder_pos(eventtime)
        delta = current_pos - self.last_valid_pos
        abs_delta = abs(delta)
        status = "NORMAL"

        if self.low_pitch_filter > 0 and abs_delta <= self.low_pitch_filter:
            status = "FILTERED"
            if self.pitch_view:
                msg = f"{self.sensor_name} - Pitch: {abs_delta:.5f} mm [FILTERED]"
                self.printer.lookup_object('gcode').respond_info(msg)
            
            self.last_valid_pos = current_pos
            self.last_event_pos = current_pos
            self.last_event_time = eventtime
            
            if self.data_logging:
                self._log_data(eventtime, self.sensor_name, current_pos, delta, state, status)
            return

        if self.ignore_next_pitch and delta > 0:
            status = "IGNORED_AFTER_RETRACTION"
            if self.pitch_view:
                msg = f"{self.sensor_name} - Pitch: {abs_delta:.5f} mm [IGNORED (after retraction)]"
                self.printer.lookup_object('gcode').respond_info(msg)
            
            self.last_valid_pos = current_pos
            self.last_event_pos = current_pos
            self.last_event_time = eventtime
            self.last_retraction_pos = current_pos
            self.ignore_next_pitch = False
            
            if self.data_logging:
                self._log_data(eventtime, self.sensor_name, current_pos, delta, state, status)
            return

        if delta > 0:  # Extrusion
            # Reset runout state on ANY valid extrusion
            self.runout_triggered = False
            if self.pitch_view or self.blockage_detection:
                status = self._process_movement(eventtime, delta)
            
            self.last_valid_pos = current_pos
            self.last_event_pos = current_pos
            self.last_event_time = eventtime
            self.last_retraction_pos = current_pos
            
            self._update_filament_runout_pos(eventtime)
            self.runout_helper.note_filament_present(eventtime, True)
            
            if self.data_logging:
                self._log_data(eventtime, self.sensor_name, current_pos, delta, state, status)
                
        elif delta < 0:  # Retraction
            retraction_delta = self.last_retraction_pos - current_pos
            abs_retraction_delta = abs(retraction_delta)
            
            self.last_retraction_pos = current_pos
            self.ignore_next_pitch = True
            
            # CONSERVER les compteurs existants - NE PAS RÉINITIALISER
            # self.anomaly_count = 0  # <-- À SUPPRIMER
            # self.normal_count = 0   # <-- À SUPPRIMER
            
            # Mise à jour positionnelle seulement
            self.last_valid_pos = current_pos
            self.last_event_pos = current_pos
            self.last_event_time = eventtime
            
            msg = f"{self.sensor_name} - Pitch: {abs_retraction_delta:.3f} mm [RETRACTION]"
            self.printer.lookup_object('gcode').respond_info(msg)
            
            if self.data_logging:
                self._log_data(eventtime, self.sensor_name, current_pos, 
                            retraction_delta, state, "RETRACTION")
            return

    def _process_movement(self, eventtime, delta):
        abs_delta = abs(delta)
        base_msg = f"{self.sensor_name} - Pitch: {abs_delta:.3f} mm"
        status = "NORMAL"
        
        if abs_delta < self.min_pitch or abs_delta > self.max_pitch:
            status = "ABNORMAL"
            if not self.pause_sent:
                self.anomaly_count += 1
                self.normal_count = 0
                
                if self.anomaly_count >= self.blockage_threshold:
                    self._trigger_blockage(eventtime, abs_delta)
                    msg = f"// {base_msg} [{status} {self.anomaly_count}/{self.blockage_threshold}] - BLOCKAGE !!!"
                    self.printer.lookup_object('gcode').respond_raw(msg)
                else:
                    msg = f"{base_msg} [{status} {self.anomaly_count}/{self.blockage_threshold}] (Need {self.blockage_threshold - self.anomaly_count} more to block)"
                    self.printer.lookup_object('gcode').respond_info(msg)
            else:
                msg = f"{base_msg} [{status} IGNORED (System paused)]"
                self.printer.lookup_object('gcode').respond_info(msg)
        else:
            status = "NORMAL"
            
            if self.pause_sent:
                self.normal_count += 1
                if self.normal_count >= self.reset_motion_sensor_threshold:
                    self._reactivate_sensor()
                else:
                    msg = f"{base_msg} [{status}] (System reset in: {self.normal_count}/{self.reset_motion_sensor_threshold})"
                    self.printer.lookup_object('gcode').respond_info(msg)
            else:
                if self.anomaly_count > 0:
                    self.normal_count += 1
                    
                    if self.normal_count >= self.reset_motion_sensor_threshold:
                        msg = f"// {base_msg} [{status}] (Anomaly counter reset to 0/{self.blockage_threshold})"
                        self.printer.lookup_object('gcode').respond_info(msg)
                        self.anomaly_count = 0
                        self.normal_count = 0
                    else:
                        msg = f"{base_msg} [{status}] (Pending anomaly: {self.anomaly_count}/{self.blockage_threshold}, Clears in: {self.reset_motion_sensor_threshold - self.normal_count})"
                        self.printer.lookup_object('gcode').respond_info(msg)
                else:
                    msg = f"{base_msg} [{status}]"
                    self.printer.lookup_object('gcode').respond_info(msg)
        
        return status

    def _trigger_blockage(self, eventtime, pitch):
        self.runout_helper.note_filament_present(eventtime, False)
        self.pause_sent = True
        self.normal_count = 0

    def _reactivate_sensor(self):
        msg = f"// {self.sensor_name} - Sensor fully reset after {self.reset_motion_sensor_threshold} normal pitches (0/{self.blockage_threshold} anomalies)"
        logging.info(msg)
        self.printer.lookup_object('gcode').respond_info(msg)
        self.pause_sent = False
        self.normal_count = 0
        self.anomaly_count = 0

# MODIFICATION DES FONCTIONS DE CHARGEMENT ICI
def load_config(config):
    return FilamentYumiSmartMotionSensor(config)

def load_config_prefix(config):
    return FilamentYumiSmartMotionSensor(config)