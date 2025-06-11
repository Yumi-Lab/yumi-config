# Filament Motion Sensor Module - Version Avancée
#
# Copyright (C) 2021 Joshua Wherrett <thejoshw.code@gmail.com>
# Modifications : Filtrage avancé + Affichage des compteurs
#
# This file may be distributed under the terms of the GNU GPLv3 license.
import logging
import csv
import os
from . import filament_switch_sensor

CHECK_RUNOUT_TIMEOUT = .500

class EncoderSensor:
    def __init__(self, config):
        # Configuration de base
        self.printer = config.get_printer()
        self.config = config
        switch_pin = config.get('switch_pin')
        self.extruder_name = config.get('extruder', 'extruder')
        self.detection_length = config.getfloat('detection_length', 7., above=0.)
        
        # Paramètres de détection
        self.pitch_view = config.getboolean('pitch_view', True)
        self.blockage_detection = config.getboolean('blockage_detection', True)
        self.data_logging = config.getboolean('data_logging', True)
        self.log_file_path = config.get('log_file_path', '/tmp/filament_sensor_log.csv')
        self.low_pitch_filter = config.getfloat('low_pitch_filter', 0.005, above=0.)
        
        # Seuils de détection
        if self.blockage_detection:
            self.min_pitch = config.getfloat('min_pitch', 0.1, above=self.low_pitch_filter)
            self.max_pitch = config.getfloat('max_pitch', 2.4, above=self.min_pitch)
            self.blockage_threshold = config.getint('blockage_threshold', 3, minval=1)
            self.reset_threshold = config.getint('reset_threshold', 5, minval=1)
            self.anomaly_count = 0
            self.normal_count = 0
            self.total_anomalies = 0
            self.triggered = False
        
        # Variables d'état
        self.last_event_pos = None
        self.last_event_time = None
        self.last_valid_pos = None
        self.start_time = None
        self.log_file = None
        
        # Configuration matérielle
        buttons = self.printer.load_object(config, 'buttons')
        buttons.register_buttons([switch_pin], self.encoder_event)
        
        # Initialisation Klipper
        self.reactor = self.printer.get_reactor()
        self.runout_helper = filament_switch_sensor.RunoutHelper(config)
        self.get_status = self.runout_helper.get_status
        self.extruder = None
        self.estimated_print_time = None
        self.filament_runout_pos = None
        
        # Initialisation logging
        if self.data_logging:
            self._init_data_logging()
        
        # Handlers
        self.printer.register_event_handler('klippy:ready', self._handle_ready)
        self.printer.register_event_handler('idle_timeout:printing', self._handle_printing)
        self.printer.register_event_handler('idle_timeout:ready', self._handle_not_printing)
        self.printer.register_event_handler('idle_timeout:idle', self._handle_not_printing)
    
    def _init_data_logging(self):
        """Initialisation du système de logging"""
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
                    'anomaly_counter', 'normal_counter'
                ])
            logging.info("Initialisation logging pour %s", self.config.get_name())
        except Exception as e:
            logging.error("Erreur initialisation logging: %s", str(e))
            self.data_logging = False
    
    def _log_data(self, eventtime, sensor_name, position, delta, state, status):
        """Journalisation des données"""
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
                f"{self.normal_count}/{self.reset_threshold}"
            ])
            self.log_file.flush()
        except Exception as e:
            logging.error("Erreur écriture log: %s", str(e))

    def _update_filament_runout_pos(self, eventtime=None):
        """Mise à jour de la position de détection"""
        if eventtime is None:
            eventtime = self.reactor.monotonic()
        self.filament_runout_pos = (
            self._get_extruder_pos(eventtime) + 
            self.detection_length)
    
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
    
    def _handle_printing(self, print_time):
        self.reactor.update_timer(self._extruder_pos_update_timer,
            self.reactor.NOW)
    
    def _handle_not_printing(self, print_time):
        self.reactor.update_timer(self._extruder_pos_update_timer,
            self.reactor.NEVER)
        if self.data_logging and self.log_file is not None:
            self.log_file.close()
            self.log_file = None
    
    def _get_extruder_pos(self, eventtime=None):
        """Obtention position extruder"""
        if eventtime is None:
            eventtime = self.reactor.monotonic()
        print_time = self.estimated_print_time(eventtime)
        return self.extruder.find_past_position(print_time)
    
    def _extruder_pos_update_event(self, eventtime):
        """Vérification présence filament"""
        extruder_pos = self._get_extruder_pos(eventtime)
        self.runout_helper.note_filament_present(eventtime,
            extruder_pos < self.filament_runout_pos)
        return eventtime + CHECK_RUNOUT_TIMEOUT
    
    def encoder_event(self, eventtime, state):
        """Gestion des événements du capteur"""
        if self.extruder is None or self.last_valid_pos is None:
            return

        current_pos = self._get_extruder_pos(eventtime)
        sensor_name = self.config.get_name().split()[-1]
        delta = current_pos - self.last_valid_pos
        abs_delta = abs(delta)
        status = "NORMAL"

        # Filtrage micro-mouvements
        if self.low_pitch_filter > 0 and abs_delta <= self.low_pitch_filter:
            status = "FILTERED"
            if self.pitch_view:
                msg = "%s - Pitch: %.5f mm [FILTERED]" % (sensor_name, abs_delta)
                self.printer.lookup_object('gcode').respond_info(msg)
            
            if self.data_logging:
                self._log_data(eventtime, sensor_name, current_pos, delta, state, status)
            return

        # Traitement mouvements valides
        if delta > 0:  # Extrusion
            if self.pitch_view or self.blockage_detection:
                status = self._process_movement(eventtime, delta, sensor_name)
        elif delta < 0 and self.pitch_view:  # Rétraction
            msg = "%s - Pitch: %.3f mm [RETRACTION]" % (sensor_name, abs_delta)
            self.printer.lookup_object('gcode').respond_info(msg)

        # Mise à jour position valide
        self.last_valid_pos = current_pos
        self.last_event_pos = current_pos
        self.last_event_time = eventtime
        
        # Journalisation
        if self.data_logging:
            self._log_data(eventtime, sensor_name, current_pos, delta, state, status)
        
        self._update_filament_runout_pos(eventtime)
        self.runout_helper.note_filament_present(eventtime, True)

    def _process_movement(self, eventtime, delta, sensor_name):
        """Traitement des mouvements valides"""
        abs_delta = abs(delta)
        status = "NORMAL"
        
        if self.blockage_detection:
            if abs_delta < self.min_pitch or abs_delta > self.max_pitch:
                status = "ABNORMAL"
                if self.normal_count >= self.reset_threshold:
                    self.anomaly_count = 1  # Nouvelle série
                else:
                    self.anomaly_count += 1
                self.total_anomalies += 1
                self.normal_count = 0
                
                if not self.triggered and self.anomaly_count >= self.blockage_threshold:
                    self._trigger_blockage(eventtime, abs_delta, sensor_name)
            else:
                status = "NORMAL"
                if self.anomaly_count >= self.blockage_threshold:
                    self.normal_count = 1  # Nouvelle série après blocage
                else:
                    self.normal_count += 1

                if self.normal_count >= self.reset_threshold:
                    self.anomaly_count = 0
                    self.normal_count = 0  # Reset complet
                    self.triggered = False

        if self.pitch_view:
            counter_info = ""
            if status == "ABNORMAL":
                display_count = min(self.anomaly_count, self.blockage_threshold)
                counter_info = f" [{display_count}/{self.blockage_threshold}]"
            elif status == "NORMAL" and self.blockage_detection:
                display_count = min(self.normal_count, self.reset_threshold) if self.reset_threshold > 0 else 0
                counter_info = f" [{display_count}/{self.reset_threshold}]"
            
            msg = "%s - Pitch: %.3f mm [%s%s]" % (
                sensor_name, abs_delta, status, counter_info)
            self.printer.lookup_object('gcode').respond_info(msg)
        
        return status

    def _trigger_blockage(self, eventtime, pitch, sensor_name):
        """Détection de blocage"""
        error_msg = "%s - BLOCKAGE! Pitch: %.3f mm [%d/%d] (Total: %d)" % (
            sensor_name, pitch, 
            self.anomaly_count, self.blockage_threshold,
            self.total_anomalies)
        logging.error(error_msg)
        self.printer.lookup_object('gcode').respond_info("// " + error_msg)
        self.runout_helper.note_filament_present(eventtime, False)
        self.triggered = True

def load_config(config):
    return EncoderSensor(config)

def load_config_prefix(config):
    return EncoderSensor(config)