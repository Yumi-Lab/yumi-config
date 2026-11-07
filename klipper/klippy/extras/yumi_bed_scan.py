# BED_SCAN_ZERO — locate the metal reference plate with the inductive probe
#
# The Yumi C series has a perforated steel plate behind the bed (nozzle wiper)
# that BED_DETECTION uses as a fixed reference. Its exact position differs
# from one machine to another, so it is measured once by a scan and stored in
# save_variables (bed_detect_x/y/z, nozzle coordinates).
#
# Algorithm (validated on the bench as a delayed_gcode state machine, moved
# here so it runs SYNCHRONOUSLY: BED_DETECTION -> scan -> mesh -> print can be
# chained inside a start g-code without racing the print):
#   - fresh G28 first (the Z reference of the scan is the nozzle tap),
#   - serpentine XY grid swept plane by plane from z_clear downwards by z_step,
#     the probe is only QUERIED (never PROBE: it would crash without metal),
#   - from the first plane where the metal triggers, `planes` planes are
#     scanned (the inductive field is not uniform, the lower plane widens the
#     footprint) and the UNION of the triggered points is kept,
#   - result = the metal point farthest from any non-metal point or the window border
#     (the plate is a strip wider than the window and perforated), ties to the bed-size
#     formula point; saved with the height of the first plane that saw metal and the bed
#     size it belongs to (bed_detect_bed).
#   - the nozzle stays above the bed while the probe is behind it: the sweep
#     never goes below z_floor (nozzle frame, Z=0 = tap).
#   - accelerations are lowered during the sweep (short moves at 20000 mm/s²
#     hammer the frame) and restored afterwards.
#
# Configuration ([yumi_bed_scan]) and the defaults of every parameter live in
# the printer.cfg generator's catalog; BED_SCAN_ZERO accepts the same names
# as parameters (CENTER_X, CENTER_Y, RANGE_X, RANGE_Y, STEP_X, STEP_Y,
# Z_CLEAR, Z_STEP, Z_FLOOR, PLANES).
#
# Options ([yumi_bed_scan], nozzle coordinates, values in the generator's catalog):
#   center_dx, center_dy  scan centre = (bed/2 + center_dx, bed + center_dy) (defaults 8, -23)
#   range_x, range_y      half-widths of the scanned window in mm (defaults 10, 8)
#   step_x, step_y        grid pitch in mm (defaults 2, 2)
#   z_clear               height of the first plane swept (default 0.6)
#   z_step                descent between two planes (default 0.1)
#   z_floor               lowest plane ever swept, nozzle frame (default 0.1)
#   planes                planes kept after the first contact (default 3)
#   speed, z_speed        XY and Z speeds of the sweep in mm/s (defaults 150, 10)
#   lift                  Z clearance before an XY travel (default 5)
#   accel                 acceleration during the sweep, restored afterwards (default 1000)
#   plate_probe_floor     lowest Z of the BED_PLATE_PRESENT probe, Z=0 = reference trigger (default 0)
# Command: BED_SCAN_ZERO [CENTER_X= CENTER_Y=] [RANGE_X= RANGE_Y=] [STEP_X= STEP_Y=] [Z_CLEAR=]
#   [Z_STEP=] [Z_FLOOR=] [PLANES=] — homes, scans, saves bed_detect_x/y/z/bed in save_variables.
# Command: BED_REF_PROBE X= Y= — PROBE on the reference at the [yumi_z_tap] tap_current; on a
#   miss, lift, unhome Z and stop (BED_DETECTION phase 1).
# Command: BED_PLATE_PRESENT X= Y= — PROBE on the print plate tab at the tap current, bounded at
#   plate_probe_floor; no trigger = no print plate, clear error (BED_DETECTION phase 2).
# Status (printer.yumi_bed_scan): found, x, y, z, points.
import logging
import math


def serpentine(cx, cy, rx, ry, sx, sy):
    """Grid points centred on (cx, cy), ±rx/±ry, pitch sx/sy, rows swept back
    and forth from the far (+Y) row so the head never crosses the field."""
    nx = int((2.0 * rx) / sx) + 1
    ny = int((2.0 * ry) / sy) + 1
    pts = []
    for j in range(ny):
        gy = cy + ry - j * sy
        cols = range(nx) if j % 2 == 0 else range(nx - 1, -1, -1)
        for i in cols:
            pts.append((round(cx - rx + i * sx, 2), round(gy, 2)))
    return pts


def planes(z_clear, z_step, z_floor):
    """Scan heights from z_clear down to z_floor (included, 1e-6 tolerance)."""
    out, z = [], z_clear
    while z >= z_floor - 1e-6:
        out.append(round(z, 3))
        z -= z_step
    return out


def bbox_center(points):
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    return ((min(xs) + max(xs)) / 2.0, (min(ys) + max(ys)) / 2.0,
            (min(xs), max(xs), min(ys), max(ys)))


def interior_point(hits, grid, target, step_x, step_y):
    """The metal point farthest from anything that is not known metal: a grid point without
    metal, or the ring just outside the window (unknown). Ties go to the point closest to
    `target` (the bed-size formula), so every machine size lands on the same spot of the plate.
    The bounding-box centre only followed the window when the plate is wider than it (C335
    07/11: three windows on the same plate gave X 172.5, 169.5, 160)."""
    hit = set(hits)
    misses = [p for p in grid if p not in hit]
    xs = [p[0] for p in grid]
    ys = [p[1] for p in grid]
    x0, x1 = min(xs) - step_x, max(xs) + step_x
    y0, y1 = min(ys) - step_y, max(ys) + step_y

    def clearance(p):
        d = min(p[0] - x0, x1 - p[0], p[1] - y0, y1 - p[1])
        for m in misses:
            d = min(d, math.hypot(p[0] - m[0], p[1] - m[1]))
        return d

    best = max(hit, key=lambda p: (round(clearance(p), 6),
                                   -math.hypot(p[0] - target[0], p[1] - target[1])))
    return best, clearance(best)


class YumiBedScan:
    def __init__(self, config):
        self.printer = config.get_printer()
        self.gcode = self.printer.lookup_object('gcode')
        # scan window, nozzle coordinates: centre = (bed/2 + center_dx, bed + center_dy)
        self.center_dx = config.getfloat('center_dx', 8.0)
        self.center_dy = config.getfloat('center_dy', -23.0)
        self.range_x = config.getfloat('range_x', 10.0, above=0.)
        self.range_y = config.getfloat('range_y', 8.0, above=0.)
        self.step_x = config.getfloat('step_x', 2.0, above=0.)
        self.step_y = config.getfloat('step_y', 2.0, above=0.)
        self.z_clear = config.getfloat('z_clear', 0.6)
        self.z_step = config.getfloat('z_step', 0.1, above=0.)
        self.z_floor = config.getfloat('z_floor', 0.1)
        self.planes = config.getint('planes', 3, minval=1)
        self.speed = config.getfloat('speed', 150.0, above=0.)        # XY, mm/s
        self.z_speed = config.getfloat('z_speed', 10.0, above=0.)
        self.lift = config.getfloat('lift', 5.0, above=0.)            # clearance before XY travel
        self.accel = config.getfloat('accel', 1000.0, above=0.)       # during the sweep
        # BED_PLATE_PRESENT never probes lower than this, in the frame BED_DETECTION leaves
        # (Z=0 = trigger level on the reference plate, a height the scan never touched at).
        self.plate_probe_floor = config.getfloat('plate_probe_floor', 0.0)
        self.last = {"found": False, "x": None, "y": None, "z": None, "points": 0}
        self.gcode.register_command('BED_SCAN_ZERO', self.cmd_BED_SCAN_ZERO,
                                    desc=self.cmd_BED_SCAN_ZERO_help)
        self.gcode.register_command('BED_PLATE_PRESENT', self.cmd_BED_PLATE_PRESENT,
                                    desc=self.cmd_BED_PLATE_PRESENT_help)
        self.gcode.register_command('BED_REF_PROBE', self.cmd_BED_REF_PROBE,
                                    desc=self.cmd_BED_REF_PROBE_help)

    def get_status(self, eventtime):
        return dict(self.last)

    # ── helpers ────────────────────────────────────────────────────────
    def _bed_size(self, gcmd):
        try:
            macro = self.printer.lookup_object('gcode_macro _YUMI_MACHINE')
            bed = float(macro.variables.get('bed_size', 0))
        except Exception:
            bed = 0.
        if bed <= 0:
            raise gcmd.error("BED_SCAN_ZERO: machine size unknown (_YUMI_MACHINE not run): the "
                             "scanned point is saved for a bed size, run _YUMI_MACHINE first")
        return bed

    def _query(self, toolhead, probe):
        toolhead.wait_moves()
        print_time = toolhead.get_last_move_time()
        return bool(probe.mcu_probe.query_endstop(print_time))

    def _limits(self):
        kin = self.printer.lookup_object('toolhead').get_kinematics()
        st = kin.get_status(self.printer.get_reactor().monotonic())
        return st['axis_minimum'], st['axis_maximum']

    # ── command ────────────────────────────────────────────────────────
    cmd_BED_SCAN_ZERO_help = ("Scan the metal reference plate with the inductive probe and save "
                              "bed_detect_x/y/z. Params: CENTER_X CENTER_Y RANGE_X RANGE_Y STEP_X "
                              "STEP_Y Z_CLEAR Z_STEP Z_FLOOR PLANES")

    def cmd_BED_SCAN_ZERO(self, gcmd):
        toolhead = self.printer.lookup_object('toolhead')
        probe = self.printer.lookup_object('probe', None)
        if probe is None or not hasattr(probe, 'mcu_probe'):
            raise gcmd.error("BED_SCAN_ZERO: no [probe] (inductive sensor) configured")
        rx = gcmd.get_float('RANGE_X', self.range_x, above=0.)
        ry = gcmd.get_float('RANGE_Y', self.range_y, above=0.)
        sx = gcmd.get_float('STEP_X', self.step_x, above=0.)
        sy = gcmd.get_float('STEP_Y', self.step_y, above=0.)
        z_clear = gcmd.get_float('Z_CLEAR', self.z_clear)
        z_step = gcmd.get_float('Z_STEP', self.z_step, above=0.)
        z_floor = gcmd.get_float('Z_FLOOR', self.z_floor)
        max_planes = gcmd.get_int('PLANES', self.planes, minval=1)
        bed = self._bed_size(gcmd)
        cx = gcmd.get_float('CENTER_X', bed / 2.0 + self.center_dx)
        cy = gcmd.get_float('CENTER_Y', bed + self.center_dy)
        if z_floor > z_clear:
            raise gcmd.error("BED_SCAN_ZERO: Z_FLOOR above Z_CLEAR")

        # A scan is a recalibration: fresh homing, the Z reference is the tap.
        gcmd.respond_info("BED_SCAN_ZERO: G28 (Z reference = nozzle tap)")
        self.gcode.run_script_from_command("G28")
        toolhead.wait_moves()

        lo, hi = self._limits()
        grid = [p for p in serpentine(cx, cy, rx, ry, sx, sy)
                if lo[0] <= p[0] <= hi[0] and lo[1] <= p[1] <= hi[1]]
        if not grid:
            raise gcmd.error("BED_SCAN_ZERO: scan window outside the axes")
        heights = planes(z_clear, z_step, z_floor)
        gcmd.respond_info("BED_SCAN_ZERO: %d points, centre (%.1f, %.1f) X±%g Y±%g, planes %s"
                          % (len(grid), cx, cy, rx, ry, ", ".join("%.2f" % z for z in heights)))

        prev_accel = toolhead.max_accel
        self.gcode.run_script_from_command("SET_VELOCITY_LIMIT ACCEL=%.0f" % self.accel)
        hits, first_z, planes_left = [], None, max_planes
        try:
            toolhead.manual_move([None, None, self.lift], self.z_speed)
            for z in heights:
                plane_hits = []
                toolhead.manual_move([grid[0][0], grid[0][1], None], self.speed)
                toolhead.manual_move([None, None, z], self.z_speed)
                for (x, y) in grid:
                    toolhead.manual_move([x, y, None], self.speed)
                    if self._query(toolhead, probe):
                        plane_hits.append((x, y))
                if plane_hits:
                    if first_z is None:
                        first_z = z
                    hits.extend(plane_hits)
                    gcmd.respond_info("BED_SCAN_ZERO: plane Z%.2f: %d point(s) (total %d)"
                                      % (z, len(plane_hits), len(hits)))
                if first_z is not None:
                    planes_left -= 1
                    if planes_left <= 0:
                        break
        finally:
            toolhead.manual_move([None, None, self.lift], self.z_speed)
            toolhead.wait_moves()
            self.gcode.run_script_from_command("SET_VELOCITY_LIMIT ACCEL=%.0f" % prev_accel)

        if not hits:
            self.last = {"found": False, "x": None, "y": None, "z": None, "points": 0}
            raise gcmd.error("BED_SCAN_ZERO: no metal detected down to Z%.2f in X[%.1f..%.1f] "
                             "Y[%.1f..%.1f] — lower Z_FLOOR or move the window (CENTER_X/CENTER_Y)"
                             % (z_floor, cx - rx, cx + rx, cy - ry, cy + ry))
        _bx, _by, (x0, x1, y0, y1) = bbox_center(hits)
        (mx, my), margin = interior_point(hits, grid, (cx, cy), sx, sy)
        self.last = {"found": True, "x": mx, "y": my, "z": first_z, "points": len(hits)}
        # bed_detect_bed: the machine size the point belongs to; BED_DETECTION scans again
        # when the pad now drives another size (C335 07/11 still held its C235 point).
        for name, val in (("bed_detect_x", mx), ("bed_detect_y", my), ("bed_detect_z", first_z),
                          ("bed_detect_bed", bed)):
            self.gcode.run_script_from_command("SAVE_VARIABLE VARIABLE=%s VALUE=%.3f" % (name, val))
        gcmd.respond_info("BED_SCAN_ZERO: metal footprint %d point(s), top plane Z%.2f, "
                          "box X[%.1f..%.1f] Y[%.1f..%.1f] -> most interior metal point X%.2f Y%.2f "
                          "(%.1f mm from any non-metal) saved (bed_detect_x/y/z)"
                          % (len(hits), first_z, x0, x1, y0, y1, mx, my, margin))
        self.gcode.run_script_from_command("M117 Metal ref: X%.1f Y%.1f" % (mx, my))
        logging.info("yumi_bed_scan: %s", self.last)


    def _probe_at_tap_current(self):
        """PROBE with the Z motor at the [yumi_z_tap] tap_current: until the probe triggers the Z
        position is not trusted, so a miss makes the motor give way instead of forcing (C335
        07/11: the plate-tab probe went 5 mm under the reference on a bare bed and the Z motor
        skipped steps at full current). Run/hold current restored whatever happens."""
        settings = self.printer.lookup_object('configfile').get_status(0)['settings']
        tap_current = settings.get('yumi_z_tap', {}).get('tap_current', 0.)
        tmc = settings.get('tmc2209 stepper_z', {})
        if tap_current:
            self.gcode.run_script_from_command(
                "SET_TMC_CURRENT STEPPER=stepper_z CURRENT=%.3f HOLDCURRENT=%.3f"
                % (tap_current, tap_current))
        try:
            self.gcode.run_script_from_command("PROBE")
        finally:
            if tap_current:
                self.gcode.run_script_from_command(
                    "SET_TMC_CURRENT STEPPER=stepper_z CURRENT=%.3f HOLDCURRENT=%.3f"
                    % (tmc.get('run_current'), tmc.get('hold_current', tmc.get('run_current'))))

    cmd_BED_REF_PROBE_help = ("Probe the reference plate at X Y with the inductive probe at the Z tap "
                              "current ([yumi_z_tap] tap_current); on a miss, lift and stop")

    def cmd_BED_REF_PROBE(self, gcmd):
        """A failed probe lifts the nozzle from where it really is, forgets Z and stops."""
        toolhead = self.printer.lookup_object('toolhead')
        x, y = gcmd.get_float('X'), gcmd.get_float('Y')
        toolhead.manual_move([x, y, None], self.speed)
        try:
            self._probe_at_tap_current()
        except self.printer.command_error as e:
            self.gcode.run_script_from_command("SET_KINEMATIC_POSITION Z=0 SET_HOMED=Z")
            toolhead.manual_move([None, None, self.lift], self.z_speed)
            toolhead.wait_moves()
            toolhead.get_kinematics().clear_homing_state("z")
            raise gcmd.error("BED_DETECTION: reference plate not found at X%.1f Y%.1f (%s) -> nozzle "
                             "lifted %.0f mm, Z unhomed; run BED_SCAN_ZERO"
                             % (x, y, str(e).split('\n')[0], self.lift))

    cmd_BED_PLATE_PRESENT_help = ("Is the print plate on the bed? Probe its tab at X Y, never lower than "
                                  "plate_probe_floor in the reference frame; no trigger = no plate")

    def cmd_BED_PLATE_PRESENT(self, gcmd):
        """Probe on the print plate tab. The descent is bounded: Z is re-declared so that
        stepper_z position_min falls on plate_probe_floor (Z=0 = trigger level on the reference
        plate, where the scan never touched anything), and it runs at the tap current. No
        trigger down to that floor = no print plate, a clear error instead of 'No trigger'."""
        toolhead = self.printer.lookup_object('toolhead')
        x, y = gcmd.get_float('X'), gcmd.get_float('Y')
        z_min = self.printer.lookup_object('configfile').get_status(0)['settings'][
            'stepper_z']['position_min']
        toolhead.manual_move([x, y, None], self.speed)
        shift = z_min - self.plate_probe_floor
        self.gcode.run_script_from_command("SET_KINEMATIC_POSITION Z=%.3f SET_HOMED=Z"
                                           % (toolhead.get_position()[2] + shift))
        try:
            self._probe_at_tap_current()
        except self.printer.command_error as e:
            if "No trigger" not in str(e):
                raise
            pos = toolhead.get_position()
            pos[2] += self.lift
            toolhead.manual_move(pos, self.z_speed)
            toolhead.wait_moves()
            raise gcmd.error("BED_DETECTION: print plate absent or far out of level (no metal at its "
                             "tab X%.1f Y%.1f down to Z%.2f above the reference) -> put the print "
                             "plate on the bed, check it lies flat" % (x, y, self.plate_probe_floor))
        gcmd.respond_info("BED_PLATE_PRESENT: print plate tab at X%.1f Y%.1f, trigger %.3f mm above "
                          "the reference trigger level" % (x, y, toolhead.get_position()[2] - shift))


def load_config(config):
    return YumiBedScan(config)
