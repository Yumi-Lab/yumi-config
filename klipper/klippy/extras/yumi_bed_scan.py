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
#   - result = centre of the bounding box (equal margins), saved with the
#     height of the first plane that saw metal.
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
# Command: BED_SCAN_ZERO [CENTER_X= CENTER_Y=] [RANGE_X= RANGE_Y=] [STEP_X= STEP_Y=] [Z_CLEAR=]
#   [Z_STEP=] [Z_FLOOR=] [PLANES=] — homes, scans, saves bed_detect_x/y/z in save_variables.
# Status (printer.yumi_bed_scan): found, x, y, z, points.
import logging


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
        self.last = {"found": False, "x": None, "y": None, "z": None, "points": 0}
        self.gcode.register_command('BED_SCAN_ZERO', self.cmd_BED_SCAN_ZERO,
                                    desc=self.cmd_BED_SCAN_ZERO_help)

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
            raise gcmd.error("BED_SCAN_ZERO: machine size unknown (_YUMI_MACHINE not run) "
                             "- give CENTER_X and CENTER_Y")
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
        if gcmd.get_float('CENTER_X', None) is None or gcmd.get_float('CENTER_Y', None) is None:
            bed = self._bed_size(gcmd)
            cx = gcmd.get_float('CENTER_X', bed / 2.0 + self.center_dx)
            cy = gcmd.get_float('CENTER_Y', bed + self.center_dy)
        else:
            cx, cy = gcmd.get_float('CENTER_X'), gcmd.get_float('CENTER_Y')
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
        mx, my, (x0, x1, y0, y1) = bbox_center(hits)
        self.last = {"found": True, "x": mx, "y": my, "z": first_z, "points": len(hits)}
        for name, val in (("bed_detect_x", mx), ("bed_detect_y", my), ("bed_detect_z", first_z)):
            self.gcode.run_script_from_command("SAVE_VARIABLE VARIABLE=%s VALUE=%.3f" % (name, val))
        gcmd.respond_info("BED_SCAN_ZERO: metal footprint %d point(s), top plane Z%.2f, "
                          "box X[%.1f..%.1f] Y[%.1f..%.1f] -> centre X%.2f Y%.2f saved (bed_detect_x/y/z)"
                          % (len(hits), first_z, x0, x1, y0, y1, mx, my))
        self.gcode.run_script_from_command("M117 Metal ref: X%.1f Y%.1f" % (mx, my))
        logging.info("yumi_bed_scan: %s", self.last)


def load_config(config):
    return YumiBedScan(config)
