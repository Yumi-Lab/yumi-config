# Filament Yumi Smart_Motion Sensor Module -V0.24.0.Beta
#
# Copyright (C) 2021 Xtrack33 by YUMI
# Modifications: Dual-mode (free/hold), sliding window pitch averaging
#
# This file may be distributed under the terms of the GNU GPLv3 license.
import logging
import csv
import os
import time
from . import filament_switch_sensor

CHECK_RUNOUT_TIMEOUT = .250  # 250ms for better responsiveness

class FilamentYumiSmartMotionSensor:
    def __init__(self, config):
        # Basic configuration
        self.printer = config.get_printer()
        self.config = config
        switch_pin = config.get('switch_pin')
        self.extruder_name = config.get('extruder', 'extruder')
        self.detection_length = config.getfloat('detection_length', 7.0, above=0.)
        self.sensor_name = config.get_name().split()[-1]

        # Mode: free (mobile sensor) or hold (fixed encoder)
        self.mode = config.get('mode', 'free').strip().lower()
        if self.mode not in ('free', 'hold'):
            raise config.error(
                "Invalid mode '%s' for %s, must be 'free' or 'hold'"
                % (self.mode, config.get_name()))

        # Common detection parameters
        self.pitch_view = config.getboolean('pitch_view', False)
        self.data_logging = config.getboolean('data_logging', False)
        self.log_file_path = config.get('log_file_path',
                                        '/tmp/filament_sensor_log.csv')
        self.low_pitch_filter = config.getfloat('low_pitch_filter', 0.015, above=0.)

        # Sequential event counter for logging
        self.event_seq = 0

        # Hold-mode specific: blockage detection parameters
        self.blockage_detection = False
        if self.mode == 'hold':
            self.blockage_detection = config.getboolean(
                'blockage_detection', False)
            if self.blockage_detection:
                self.min_pitch = config.getfloat(
                    'min_pitch', 1.0, above=self.low_pitch_filter)
                self.max_pitch = config.getfloat(
                    'max_pitch', 2.4, above=self.min_pitch)
                self.blockage_threshold = config.getint(
                    'blockage_threshold', 2, minval=1)
                self.reset_motion_sensor_threshold = config.getint(
                    'reset_motion_sensor_threshold', 16, minval=1)
                self.pitch_window = config.getint(
                    'pitch_window', 4, minval=1)
                self.post_retract_skip = config.getint(
                    'post_retract_skip', 2, minval=0)
                # Minimum retraction delta to count as real retraction
                # (not PA micro-retraction). PA typically does ~0.04mm.
                self.retraction_min = config.getfloat(
                    'retraction_min', 0.1, above=0.)
                self.skip_counter = 0
                self.anomaly_count = 0
                self.normal_count = 0
                self.pause_sent = False

        # Sliding window state (hold mode only, but initialized always
        # to keep logging code simple)
        self.window_tick_count = 0
        self.window_ext_start = None

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
        self.printer.register_event_handler('idle_timeout:printing',
                                            self._handle_printing)
        self.printer.register_event_handler('idle_timeout:ready',
                                            self._handle_not_printing)
        self.printer.register_event_handler('idle_timeout:idle',
                                            self._handle_not_printing)
        self.printer.register_event_handler('idle_timeout:paused',
                                            self._handle_paused)

    # ------------------------------------------------------------------
    # Logging
    # ------------------------------------------------------------------
    def _init_data_logging(self):
        """Initialize logging system with enriched columns"""
        try:
            log_dir = os.path.dirname(self.log_file_path)
            if log_dir and not os.path.exists(log_dir):
                os.makedirs(log_dir)

            self.log_file = open(self.log_file_path, 'a')
            if os.stat(self.log_file_path).st_size == 0:
                writer = csv.writer(self.log_file)
                writer.writerow([
                    'seq', 'wall_time', 'eventtime', 'dt_ms',
                    'sensor', 'ext_pos', 'last_valid_pos',
                    'delta', 'abs_delta',
                    'direction', 'status', 'state_HW',
                    'anomaly_cnt', 'normal_cnt', 'pause_sent',
                    'ignore_next', 'runout_trig',
                    'pitch_flag',
                    'win_ticks', 'avg_pitch'
                ])
            logging.info("Initialized logging for %s", self.config.get_name())
        except Exception as e:
            logging.error("Logging initialization error: %s", str(e))
            self.data_logging = False

    def _log_data(self, eventtime, current_pos, last_pos, delta,
                  direction, status, state, avg_pitch=0.0):
        """Write one enriched row to CSV + console debug line"""
        if not self.data_logging or self.log_file is None:
            return

        self.event_seq += 1
        abs_delta = abs(delta)

        # Time since last event in ms
        if self.last_event_time is not None:
            dt_ms = (eventtime - self.last_event_time) * 1000.0
        else:
            dt_ms = 0.0

        anom = self.anomaly_count if self.blockage_detection else 0
        norm = self.normal_count if self.blockage_detection else 0
        psent = self.pause_sent if self.blockage_detection else False

        # Pitch flag for quick visual scanning
        pitch_flag = ""
        if self.blockage_detection and avg_pitch > 0:
            if avg_pitch < self.min_pitch:
                pitch_flag = "LOW"
            elif avg_pitch > self.max_pitch:
                pitch_flag = "HIGH"
            else:
                pitch_flag = "OK"

        # Console output — compact one-liner
        gcode = self.printer.lookup_object('gcode')
        win_info = ""
        if self.blockage_detection:
            win_info = (f" w={self.window_tick_count}/"
                        f"{self.pitch_window}")
            if avg_pitch > 0:
                win_info += f" avg={avg_pitch:.3f}"
        gcode.respond_info(
            f"[LOG] #{self.event_seq} dt={dt_ms:.1f}ms "
            f"d={delta:+.4f} |d|={abs_delta:.4f} "
            f"{direction}/{status} "
            f"anom={anom}/{self.blockage_threshold if self.blockage_detection else '-'} "
            f"norm={norm} ign={self.ignore_next_pitch}{win_info}"
            + (f" !!{pitch_flag}" if pitch_flag in ("LOW", "HIGH") else ""))

        # CSV output
        try:
            writer = csv.writer(self.log_file)
            writer.writerow([
                self.event_seq,
                "%.3f" % time.time(),
                "%.6f" % eventtime,
                "%.1f" % dt_ms,
                self.sensor_name,
                "%.5f" % current_pos,
                "%.5f" % (last_pos if last_pos is not None else 0),
                "%.5f" % delta,
                "%.5f" % abs_delta,
                direction, status,
                'H' if state else 'L',
                anom, norm, psent,
                self.ignore_next_pitch,
                self.runout_triggered,
                pitch_flag,
                self.window_tick_count,
                "%.5f" % avg_pitch
            ])
            self.log_file.flush()
        except Exception as e:
            logging.error("Log write error: %s", str(e))

    # ------------------------------------------------------------------
    # Lifecycle handlers
    # ------------------------------------------------------------------
    def _update_filament_runout_pos(self, eventtime=None):
        """Update detection position BASED ON ACTUAL FILAMENT MOVEMENT"""
        if self.last_valid_pos is None:
            return
        self.filament_runout_pos = self.last_valid_pos + self.detection_length
        logging.info("New runout threshold: %.2f mm (based on filament pos: %.2f)",
                    self.filament_runout_pos, self.last_valid_pos)

    def _handle_ready(self):
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
        self._update_filament_runout_pos()
        self.last_valid_pos = self._get_extruder_pos()
        self.last_retraction_pos = self.last_valid_pos
        self.runout_triggered = False
        self.ignore_next_pitch = False
        self.event_seq = 0
        self.skip_counter = 0
        self.window_tick_count = 0
        self.window_ext_start = None

        if self.data_logging:
            if self.log_file is not None:
                self.log_file.close()
                self.log_file = None
            self._init_data_logging()

        pitch_window_info = self.pitch_window if self.blockage_detection else 1
        logging.info("Detection reset for new print")
        self.printer.lookup_object('gcode').respond_info(
            f"// {self.sensor_name}: Filament sensor reset - ready "
            f"(mode={self.mode}, window={pitch_window_info})")

        self.reactor.update_timer(
            self._extruder_pos_update_timer, self.reactor.NOW)

    def _handle_not_printing(self, print_time):
        self.reactor.update_timer(
            self._extruder_pos_update_timer, self.reactor.NEVER)
        if self.data_logging and self.log_file is not None:
            self.log_file.close()
            self.log_file = None

    def _handle_paused(self, print_time):
        self.runout_triggered = True
        self.reactor.update_timer(
            self._extruder_pos_update_timer, self.reactor.NEVER)
        if self.data_logging and self.log_file is not None:
            self.log_file.close()
            self.log_file = None

    def _get_extruder_pos(self, eventtime=None):
        if eventtime is None:
            eventtime = self.reactor.monotonic()
        print_time = self.estimated_print_time(eventtime)
        return self.extruder.find_past_position(print_time)

    # ------------------------------------------------------------------
    # Continuous runout check (timer-based)
    # ------------------------------------------------------------------
    def _extruder_pos_update_event(self, eventtime):
        if self.last_valid_pos is None:
            return eventtime + CHECK_RUNOUT_TIMEOUT

        extruder_pos = self._get_extruder_pos(eventtime)
        extruder_moved = extruder_pos - self.last_valid_pos
        filament_present = extruder_moved < self.detection_length

        if self.last_event_time and (eventtime - self.last_event_time) < 5.0:
            filament_present = True

        if not filament_present and not getattr(self, 'pause_sent', False):
            logging.info(
                "Potential runout detected. Extruder moved: %.2fmm "
                "beyond last valid position (threshold: %.2fmm)",
                extruder_moved, self.detection_length)
            self.runout_triggered = True

        self.runout_helper.note_filament_present(eventtime, filament_present)
        return eventtime + CHECK_RUNOUT_TIMEOUT

    # ------------------------------------------------------------------
    # Encoder event — dispatch to mode-specific processing
    # ------------------------------------------------------------------
    def encoder_event(self, eventtime, state):
        if self.extruder is None or self.last_valid_pos is None:
            return
        if self.mode == 'free':
            self._process_free(eventtime, state)
        else:
            self._process_hold(eventtime, state)

    # ------------------------------------------------------------------
    # FREE mode — mobile sensor, simple runout detection
    # ------------------------------------------------------------------
    def _process_free(self, eventtime, state):
        """Free mode: encoder tick = filament is moving = present.
        No pitch analysis, no blockage detection."""
        current_pos = self._get_extruder_pos(eventtime)

        self.runout_triggered = False
        self.last_valid_pos = current_pos
        self.last_event_pos = current_pos
        self.last_event_time = eventtime

        self._update_filament_runout_pos(eventtime)
        self.runout_helper.note_filament_present(eventtime, True)

    # ------------------------------------------------------------------
    # HOLD mode — fixed encoder, pitch analysis + blockage detection
    # ------------------------------------------------------------------
    def _process_hold(self, eventtime, state):
        """Hold mode: fixed encoder with pitch analysis and optional
        blockage detection via sliding window averaging."""
        current_pos = self._get_extruder_pos(eventtime)
        last_pos = self.last_valid_pos
        delta = current_pos - last_pos
        abs_delta = abs(delta)
        status = "NORMAL"

        # --- Micro-movement filter (noise floor) ---
        if self.low_pitch_filter > 0 and abs_delta <= self.low_pitch_filter:
            status = "FILTERED"
            if self.pitch_view:
                self.printer.lookup_object('gcode').respond_info(
                    f"{self.sensor_name} - Pitch: {abs_delta:.5f} mm "
                    f"[FILTERED]")

            if self.data_logging:
                self._log_data(eventtime, current_pos, last_pos, delta,
                               "MICRO", status, state)

            self.last_valid_pos = current_pos
            self.last_event_pos = current_pos
            self.last_event_time = eventtime
            return

        # --- Retraction (delta < 0) ---
        if delta < 0:
            # PA micro-retractions (~0.04mm) should not reset the window.
            # Only treat as real retraction if above retraction_min.
            if (self.blockage_detection
                    and abs_delta < self.retraction_min):
                # Absorb PA micro-retraction into the window as noise
                status = "PA_MICRO_RETRACT"
                if self.pitch_view:
                    self.printer.lookup_object('gcode').respond_info(
                        f"{self.sensor_name} - Pitch: {abs_delta:.5f} mm "
                        f"[PA_MICRO_RETRACT - absorbed]")
                if self.data_logging:
                    self._log_data(eventtime, current_pos, last_pos, delta,
                                   "PA_MICRO", status, state)
                # Update positions but do NOT reset window or set
                # ignore_next_pitch
                self.last_valid_pos = current_pos
                self.last_event_pos = current_pos
                self.last_event_time = eventtime
                return

            # Real retraction
            retraction_delta = self.last_retraction_pos - current_pos
            abs_retraction_delta = abs(retraction_delta)

            if self.data_logging:
                self._log_data(eventtime, current_pos, last_pos, delta,
                               "RETRACTION", "RETR", state)

            self.last_retraction_pos = current_pos
            self.ignore_next_pitch = True
            # Reset the window — retraction breaks the measurement
            self.window_tick_count = 0
            self.window_ext_start = None

            self.last_valid_pos = current_pos
            self.last_event_pos = current_pos
            self.last_event_time = eventtime

            msg = (f"{self.sensor_name} - Pitch: "
                   f"{abs_retraction_delta:.3f} mm [RETRACTION]")
            self.printer.lookup_object('gcode').respond_info(msg)
            return

        # --- Post-retraction ignore (first extrusion tick after retract) ---
        if self.ignore_next_pitch and delta > 0:
            status = "IGNORED_AFTER_RETRACTION"
            if self.pitch_view:
                self.printer.lookup_object('gcode').respond_info(
                    f"{self.sensor_name} - Pitch: {abs_delta:.5f} mm "
                    f"[IGNORED (after retraction)]")

            if self.data_logging:
                self._log_data(eventtime, current_pos, last_pos, delta,
                               "IGN_POST_RETR", status, state)

            self.last_valid_pos = current_pos
            self.last_event_pos = current_pos
            self.last_event_time = eventtime
            self.last_retraction_pos = current_pos
            self.ignore_next_pitch = False
            # Start fresh window from here
            self.window_tick_count = 0
            self.window_ext_start = current_pos
            return

        # --- Extrusion (delta > 0) — accumulate in sliding window ---
        self.runout_triggered = False

        # Initialize window start if needed
        if self.window_ext_start is None:
            self.window_ext_start = last_pos

        self.window_tick_count += 1

        avg_pitch = 0.0
        if (self.blockage_detection
                and self.window_tick_count >= self.pitch_window):
            # Window is full — evaluate averaged pitch
            net_delta = current_pos - self.window_ext_start
            avg_pitch = net_delta / self.window_tick_count
            status = self._process_averaged_pitch(eventtime, avg_pitch)
            # Reset window
            self.window_tick_count = 0
            self.window_ext_start = current_pos
        elif self.pitch_view and not self.blockage_detection:
            # pitch_view only, no blockage detection — show raw pitch
            msg = (f"{self.sensor_name} - Pitch: {abs_delta:.3f} mm "
                   f"[{status}]")
            self.printer.lookup_object('gcode').respond_info(msg)

        if self.data_logging:
            self._log_data(eventtime, current_pos, last_pos, delta,
                           "EXTRUSION", status, state, avg_pitch)

        self.last_valid_pos = current_pos
        self.last_event_pos = current_pos
        self.last_event_time = eventtime
        self.last_retraction_pos = current_pos

        self._update_filament_runout_pos(eventtime)
        self.runout_helper.note_filament_present(eventtime, True)

    # ------------------------------------------------------------------
    # Blockage detection on averaged pitch (hold mode only)
    # ------------------------------------------------------------------
    def _process_averaged_pitch(self, eventtime, avg_pitch):
        """Evaluate the averaged pitch from the sliding window."""
        abs_pitch = abs(avg_pitch)
        base_msg = (f"{self.sensor_name} - AvgPitch: {abs_pitch:.3f} mm "
                    f"(over {self.pitch_window} ticks)")
        status = "NORMAL"

        # --- Travel artifact filter ---
        if abs_pitch > self.max_pitch * 2.0:
            status = "TRAVEL_ARTIFACT"
            self.skip_counter = self.post_retract_skip
            self.printer.lookup_object('gcode').respond_info(
                f"{base_msg} [{status}] "
                f"(skip next {self.skip_counter} windows)")
            return status

        # --- Rebound filter ---
        if self.skip_counter > 0:
            self.skip_counter -= 1
            status = "SETTLING"
            self.printer.lookup_object('gcode').respond_info(
                f"{base_msg} [{status}] "
                f"(skip remaining {self.skip_counter})")
            return status

        # --- Normal blockage detection ---
        if abs_pitch < self.min_pitch or abs_pitch > self.max_pitch:
            status = "ABNORMAL"
            if not self.pause_sent:
                self.anomaly_count += 1
                self.normal_count = 0

                if self.anomaly_count >= self.blockage_threshold:
                    self._trigger_blockage(eventtime, abs_pitch)
                    msg = (f"// {base_msg} [{status} "
                           f"{self.anomaly_count}/"
                           f"{self.blockage_threshold}] - BLOCKAGE !!!")
                    self.printer.lookup_object('gcode').respond_raw(msg)
                else:
                    remaining = self.blockage_threshold - self.anomaly_count
                    msg = (f"{base_msg} [{status} "
                           f"{self.anomaly_count}/"
                           f"{self.blockage_threshold}] "
                           f"(Need {remaining} more to block)")
                    self.printer.lookup_object('gcode').respond_info(msg)
            else:
                msg = (f"{base_msg} [{status} IGNORED "
                       f"(System paused)]")
                self.printer.lookup_object('gcode').respond_info(msg)
        else:
            status = "NORMAL"

            if self.pause_sent:
                self.normal_count += 1
                if self.normal_count >= self.reset_motion_sensor_threshold:
                    self._reactivate_sensor()
                else:
                    msg = (f"{base_msg} [{status}] (System reset in: "
                           f"{self.normal_count}/"
                           f"{self.reset_motion_sensor_threshold})")
                    self.printer.lookup_object('gcode').respond_info(msg)
            else:
                if self.anomaly_count > 0:
                    self.normal_count += 1

                    if (self.normal_count
                            >= self.reset_motion_sensor_threshold):
                        msg = (f"// {base_msg} [{status}] (Anomaly "
                               f"counter reset to 0/"
                               f"{self.blockage_threshold})")
                        self.printer.lookup_object('gcode').respond_info(msg)
                        self.anomaly_count = 0
                        self.normal_count = 0
                    else:
                        clears = (self.reset_motion_sensor_threshold
                                  - self.normal_count)
                        msg = (f"{base_msg} [{status}] (Pending "
                               f"anomaly: {self.anomaly_count}/"
                               f"{self.blockage_threshold}, "
                               f"Clears in: {clears})")
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
        msg = (f"// {self.sensor_name} - Sensor fully reset after "
               f"{self.reset_motion_sensor_threshold} normal pitches "
               f"(0/{self.blockage_threshold} anomalies)")
        logging.info(msg)
        self.printer.lookup_object('gcode').respond_info(msg)
        self.pause_sent = False
        self.normal_count = 0
        self.anomaly_count = 0


def load_config(config):
    return FilamentYumiSmartMotionSensor(config)

def load_config_prefix(config):
    return FilamentYumiSmartMotionSensor(config)
