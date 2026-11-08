# Filament Yumi Smart Motion Sensor — V0.25.0.Beta
#
# Copyright (C) 2021 Xtrack33 by YUMI
# This file may be distributed under the terms of the GNU GPLv3 license.
#
# ════════════════════════════════════════════════════════════════════════════════════════════
# READ THIS FIRST: THE HARDWARE DECIDES EVERYTHING BELOW
# ════════════════════════════════════════════════════════════════════════════════════════════
#
# The YMS encoder wheel does not sit still. The whole sensor body rides on a SLIDE, and the
# filament is gripped inside it by silicone, so there is real friction between the two.
#
#   FILAMENT ADVANCES   the body is already against its end stop, so it cannot follow. The
#                       filament is forced to slip past the wheel -> THE WHEEL TURNS -> a tick.
#   FILAMENT RETRACTS   the body is free to travel back, about 10 mm of it. It rides along with
#                       the filament instead of letting it slip -> THE WHEEL DOES NOT TURN ->
#                       no tick at all.
#
# So this sensor measures REAL ADVANCES ONLY. It is blind to retractions by design, and that is
# not a limitation: it is the point.
#
# WHY THE SLIDE EXISTS — the history, because it explains the two modes
# ---------------------------------------------------------------------
# The sensor used to be FIXED, and the pitch analysis below (hold mode) was written for it.
# Then pressure advance was enabled, and it broke: PA modulates the extruder constantly, the
# pitch left its bounds, and the analysis had no way to tell PA apart from a jam. It paused
# prints that were perfectly healthy. Retractions were never the problem — PA was.
#
# The fix was MECHANICAL, not software: the slide. PA's modulation is of the order of a
# millimetre; the slide absorbs ten. The body moves, the wheel does not, and the encoder never
# sees PA at all. No threshold to tune, no PA value it stops working at.
#
# THE TWO MODES ARE TWO MECHANICAL STATES OF THE SAME SENSOR
# ---------------------------------------------------------
#   mode: free   the body is free on its slide. The production configuration. A tick means
#                "filament really advanced", and nothing finer can be read, because small
#                movements never reach the wheel.
#   mode: hold   the body is mechanically BLOCKED. Every movement now turns the wheel, so the
#                spacing between ticks (the "pitch") becomes a real measurement and the pitch
#                analysis is meaningful. This is the legacy configuration, the one PA broke.
#
# KNOWN GAP, as of 2026-11-09
# ---------------------------
# `blockage_detection` is implemented in the HOLD branch only. A machine running `free` — which
# is every machine with the slide — therefore has NO jam detection, whatever the config asks
# for. The option is accepted and silently dropped (see __init__). This is why jam detection has
# never fired in production: it was written for the configuration that was abandoned when the
# slide was built. The detector that suits a mobile sensor is a SUM comparison, not a pitch:
# count the ticks over a window of commanded extruder travel and compare with what that travel
# should have produced. PA cannot fool a sum — it is conservative, it redistributes filament in
# time without changing the total — and on this hardware the slide has already removed it anyway.
#
# ════════════════════════════════════════════════════════════════════════════════════════════
# WHAT "PRESENT" AND "ABSENT" MEAN HERE
# ════════════════════════════════════════════════════════════════════════════════════════════
#
#   PRESENT   only when the encoder ticks, i.e. filament really moving through the YMS. Never
#             inferred from "the extruder barely moved": doing that made an EMPTY YMS look
#             loaded, because no movement produces no evidence either way.
#   ABSENT    when the tracked extruder has travelled `detection_length` since the last tick.
#             Checked every 250 ms while the toolhead moves, and once more the moment it stops,
#             so a move that crosses the threshold just before stopping is never missed between
#             two timer ticks.
#
# NO GRACE PERIOD AFTER A TICK, and that is deliberate. T0's MOTION_SENSOR_INIT re-arms every
# YMS by moving the fictive extruder 50 mm with no feeder synced, less than 2 s after the
# loading YMS last ticked. A time-based grace swallowed that move: the sensor stayed "present"
# and a second insertion into the same YMS was never seen.
#
# WHICH TICKS COUNT (bench 2026-09-06)
# ------------------------------------
# Ticks arriving at rest right after a load, once the motors were released, used to re-arm
# "present" and the next insertion was never seen. So a tick is only evidence when:
#
#   FEEDING     the extruder moves AND this YMS's motor is synced to it — filament is driven.
#   INSERTION   the toolhead is idle (idle_timeout not "Printing") and someone pushes filament
#               in by hand.
#   ignored     everything else: a tick while the extruder moves but ANOTHER feeder drives it
#               (drag, vibration, EMI), or a tick at rest while the toolhead has not settled —
#               filament relaxing after the motors were released. Counted and logged in
#               klippy.log as "encoder tick ignored", at most once every 5 s with its count, so
#               a noisy encoder shows up without flooding the log.
#
# ════════════════════════════════════════════════════════════════════════════════════════════
# OPTIONS — what each one is for, not just what it is
# ════════════════════════════════════════════════════════════════════════════════════════════
#
# WIRING AND SCOPE
#   switch_pin          the YMS encoder pin.
#   extruder            whose travel is compared with the ticks (default `extruder`). On a
#                       CHROMAX head this is the FICTIVE extruder: the real pushers are the
#                       YMS steppers synced to it.
#   motor               this YMS's own feeder, an [extruder_stepper <name>]. A tick while the
#                       extruder moves only counts as feeding when THAT feeder is synced to it.
#                       Without it, any tick during movement counts, and a jam on channel 2
#                       could be masked by channel 1 doing the work. YMS-n is fed by
#                       extruder{n-1}.
#   mode                free or hold — see the hardware section above. This is not a preference,
#                       it must match how the sensor is mounted.
#
# ABSENCE (both modes)
#   detection_length    mm of commanded extruder travel without a single tick before the
#                       filament is declared absent (default 7). This is the ONLY detector that
#                       works in free mode today. Note what it costs: at a normal print flow of
#                       ~2 mm/s of filament, 50 mm is about 24 s, and with blockage_threshold 2
#                       that is ~48 s of the gear biting a stationary filament before anything
#                       happens — which deepens the flat it is grinding. Shorter is better here,
#                       as long as it stays above the longest legitimate gap between ticks.
#
# OBSERVATION (both modes)
#   pitch_view          echo every pitch to the console. For tuning by eye, not for production:
#                       it writes a line per tick.
#   data_logging        CSV of every tick, one row with everything that was known at that
#                       instant (default off, /tmp/filament_sensor_log.csv). This is the right
#                       source for analysis — pitch_view drowns the console, the CSV does not.
#                       Reopened at each print start so one file is one print.
#   log_file_path       where that CSV goes.
#   low_pitch_filter    mm; two ticks closer than this are encoder noise, not filament movement,
#                       and are left out of the pitch (default 0.015). Without it, contact
#                       bounce would show up as an impossibly small pitch and read as a jam.
#
# PITCH ANALYSIS AND BLOCKAGE — hold mode only, see the known gap above
#   blockage_detection  pause the print when the averaged pitch leaves [min_pitch, max_pitch]
#                       (default off). Accepted and IGNORED in free mode.
#   min_pitch           mm per tick below which feeding is suspect: the extruder is commanding
#                       travel but the wheel barely turns, which is what a jam looks like.
#   max_pitch           mm per tick above which it is suspect the other way: the wheel turns far
#                       less often than the travel demands, which is slip.
#   pitch_window        ticks averaged before a verdict (default 4). A single pitch is far too
#                       noisy to act on; the average is what is judged.
#   blockage_threshold  abnormal windows required before declaring a blockage (default 2). The
#                       contract is CONSECUTIVE: one normal window must clear the run, otherwise
#                       ordinary intermittent noise accumulates into a false pause.
#   reset_motion_sensor_threshold
#                       normal windows needed to clear a pending anomaly, and to re-arm the
#                       sensor after a pause (default 16). Deliberately much larger than
#                       blockage_threshold: it should be quick to suspect and slow to forgive.
#   post_retract_skip   windows ignored after a travel or retraction artefact (default 2). The
#                       filament rebounds after a long move and the first windows are not
#                       representative.
#   retraction_min      mm; a negative delta smaller than this is a pressure-advance
#                       micro-retraction, not a real one, and must not reset the measurement
#                       window (default 0.1). In free mode PA never reaches the wheel, so this
#                       only matters in hold.
#
# KLIPPER-STANDARD BEHAVIOUR
#   pause_on_runout, runout_gcode, insert_gcode, event_delay, pause_delay
#                       exactly as Klipper's own filament sensors. Yumi machines set
#                       pause_on_runout False and do the deciding in runout_gcode, so the
#                       printer can verify a suspicion before giving up on a print.
#
# COMMANDS   SET_FILAMENT_SENSOR SENSOR=<name> ENABLE=0|1, QUERY_FILAMENT_SENSOR SENSOR=<name>.
# STATUS     printer["filament_yumi_smart_motion_sensor <name>"]: filament_detected, enabled.
#
import logging
import csv
import os
import time
from . import filament_switch_sensor

CHECK_RUNOUT_TIMEOUT = .250  # 250ms for better responsiveness
MOTION_WINDOW = .1           # s: the extruder is "moving" at a tick if it travelled within this window
IGNORED_LOG_PERIOD = 5.      # s: ignored ticks are logged at most once per period, with their count

class FilamentYumiSmartMotionSensor:
    def __init__(self, config):
        # Basic configuration
        self.printer = config.get_printer()
        self.config = config
        switch_pin = config.get('switch_pin')
        self.extruder_name = config.get('extruder', 'extruder')
        self.motor_name = config.get('motor', None)
        self.motor = None
        self.ignored_ticks = 0
        self.ignored_log_time = 0.
        self.detection_length = config.getfloat('detection_length', 7.0, above=0.)
        self.sensor_name = config.get_name().split()[-1]

        # Mode: free (mobile sensor) or hold (fixed encoder)
        self.mode = config.get('mode', 'free').strip().lower()
        if self.mode not in ('free', 'hold'):
            raise config.error(
                "Invalid mode '%s' for %s, must be 'free' or 'hold'"
                % (self.mode, config.get_name()))

        self.pitch_view = config.getboolean('pitch_view', False)  # both: show pitch in console
        self.data_logging = config.getboolean('data_logging', False)  # both: enable CSV logging
        self.log_file_path = config.get('log_file_path',
                                        '/tmp/filament_sensor_log.csv')  # both: CSV log path
        self.low_pitch_filter = config.getfloat('low_pitch_filter', 0.015, above=0.)  # both: noise floor

        self.event_seq = 0

        # All params consumed to avoid Klipper "option not valid" — hold-only are ignored in free
        self.blockage_detection = config.getboolean('blockage_detection', False)  # hold: enable blockage detection
        self.min_pitch = config.getfloat('min_pitch', 1.0, minval=0.)  # hold: pitch below = suspect
        self.max_pitch = config.getfloat('max_pitch', 2.4, minval=0.)  # hold: pitch above = suspect
        self.blockage_threshold = config.getint('blockage_threshold', 2, minval=1)  # hold: abnormal count to trigger
        self.reset_motion_sensor_threshold = config.getint(
            'reset_motion_sensor_threshold', 16, minval=1)  # hold: normal count to clear anomaly
        self.pitch_window = config.getint('pitch_window', 4, minval=1)  # hold: ticks averaged per window
        self.post_retract_skip = config.getint('post_retract_skip', 2, minval=0)  # hold: windows skipped after travel
        self.retraction_min = config.getfloat('retraction_min', 0.1, above=0.)  # hold: min delta for real retraction
        # In free mode, disable blockage_detection regardless of config
        if self.mode == 'free':
            self.blockage_detection = False
        # Runtime state for blockage detection
        if self.blockage_detection:
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
        self.last_tick_pos = None   # extruder position at the last encoder tick (presence)

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
    def _handle_ready(self):
        self.extruder = self.printer.lookup_object(self.extruder_name)
        if self.motor_name:
            self.motor = self.printer.lookup_object('extruder_stepper ' + self.motor_name, None)
            if self.motor is None:
                self.motor = self.printer.lookup_object(self.motor_name)
        self.estimated_print_time = (
            self.printer.lookup_object('mcu').estimated_print_time)
        self._extruder_pos_update_timer = self.reactor.register_timer(
            self._extruder_pos_update_event)
        self.start_time = self.reactor.monotonic()
        self.last_valid_pos = self._get_extruder_pos()
        self.last_tick_pos = self.last_valid_pos
        self.last_retraction_pos = self.last_valid_pos
        self.ignore_next_pitch = False

    def _handle_printing(self, print_time):
        # Pitch bookkeeping restarts with the print; the presence reference (last_tick_pos)
        # does not: a YMS that has not ticked for detection_length mm of extruder travel is
        # empty, whether that travel happened before or after this move started.
        self.last_valid_pos = self._get_extruder_pos()
        self.last_retraction_pos = self.last_valid_pos
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
        logging.info("%s: pitch bookkeeping reset, toolhead moving (mode=%s, window=%s)",
                     self.sensor_name, self.mode, pitch_window_info)

        self.reactor.update_timer(
            self._extruder_pos_update_timer, self.reactor.NOW)

    def _handle_not_printing(self, print_time):
        self.reactor.update_timer(
            self._extruder_pos_update_timer, self.reactor.NEVER)
        # The toolhead stopped: judge the final position now, so a move that crossed the
        # threshold right before the stop is never missed between two timer ticks.
        self._check_presence(self.reactor.monotonic())
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
    def _check_presence(self, eventtime):
        """Only the ABSENCE is decided here: the extruder travelled detection_length since
        the last encoder tick. Presence is the encoder's business (encoder_event) — declaring
        "present" from "the extruder did not move much" made an empty YMS look loaded, and
        a time-based grace after a tick hid the re-arm move of MOTION_SENSOR_INIT."""
        if self.last_tick_pos is None:
            return
        extruder_moved = self._get_extruder_pos(eventtime) - self.last_tick_pos
        if extruder_moved < self.detection_length:
            return
        if not self.runout_triggered:
            logging.info(
                "%s: extruder moved %.2f mm since the last encoder tick "
                "(threshold %.2f mm): no filament", self.sensor_name,
                extruder_moved, self.detection_length)
            self.runout_triggered = True
        self.runout_helper.note_filament_present(eventtime, False)

    def _extruder_pos_update_event(self, eventtime):
        self._check_presence(eventtime)
        return eventtime + CHECK_RUNOUT_TIMEOUT

    # ------------------------------------------------------------------
    # Encoder event — which ticks count, then mode-specific processing
    # ------------------------------------------------------------------
    def _motor_synced(self):
        """Is this YMS's feeder driven by the tracked extruder right now? No feeder declared: yes."""
        if self.motor is None:
            return True
        real = getattr(self.motor, 'extruder_stepper', self.motor)
        return getattr(real, 'motion_queue', None) == self.extruder_name

    def _classify_tick(self, eventtime):
        """'feeding', 'insertion', or None for a tick that is not filament being fed or inserted."""
        print_time = self.estimated_print_time(eventtime)
        pos = self.extruder.find_past_position(print_time)
        moving = abs(pos - self.extruder.find_past_position(print_time - MOTION_WINDOW)) > 1e-6
        if moving:
            if self._motor_synced():
                return 'feeding'
            reason = ("extruder moving, motor %s not synced to it: another feeder is pushing, "
                      "or the re-arm move of MOTION_SENSOR_INIT (nothing feeds this YMS)" % self.motor_name)
        else:
            state = self.printer.lookup_object('idle_timeout').get_status(eventtime)['state']
            if state != "Printing":
                return 'insertion'
            reason = ("extruder at rest but the toolhead has not settled yet: filament relaxing "
                      "after the motors were released")
        self.ignored_ticks += 1
        if eventtime - self.ignored_log_time >= IGNORED_LOG_PERIOD:
            logging.info("%s: encoder tick ignored (%d since last report) — %s",
                         self.sensor_name, self.ignored_ticks, reason)
            self.ignored_log_time = eventtime
            self.ignored_ticks = 0
        return None

    def encoder_event(self, eventtime, state):
        if self.extruder is None or self.last_valid_pos is None:
            return
        if self._classify_tick(eventtime) is None:
            return
        # Filament really fed or inserted: the absence threshold starts again from here.
        self.last_tick_pos = self._get_extruder_pos(eventtime)
        self.last_event_time = eventtime
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
