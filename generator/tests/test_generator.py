"""generator.py — the common trunk stays common, the hardware matches the QC lineage.

Reference: qc/qc_printer_<MACHINE>.cfg (factory QC configs, validated on the machines).
"""
import re
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
import generator  # noqa: E402

CATALOG = generator.load_catalog()
QC_DIR = HERE.parent.parent / "qc"
MACHINES = [mid for mid, _ in generator.machines_of(CATALOG, "SMART_MAKER_1X")]

MACRO_PREFIXES = ("gcode_macro ", "delayed_gcode ", "gcode_shell_command ")


def parse(text):
    """{section: {option: value}} with comments stripped; multi-line values joined with |."""
    secs, cur, cont = {}, None, None
    for raw in text.splitlines():
        s = raw.split("#", 1)[0].rstrip()
        if not s.strip():
            continue
        m = re.match(r"^\[([^\]]+)\]", s)
        if m:
            cur = m.group(1).strip()
            secs.setdefault(cur, {})
            cont = None
            continue
        if cur is None:
            continue
        if s[0] in " \t":
            if cont:
                secs[cur][cont] += "|" + s.strip()
            continue
        k, v = re.split(r"[:=]", s, maxsplit=1)
        secs[cur][k.strip().lower()] = v.strip()
        cont = k.strip().lower()
    return secs


def norm(value):
    """Numbers compare as numbers, coordinate pairs as tuples, the rest as text."""
    if value is None:
        return None
    parts = [p.strip() for p in value.split(",")]
    try:
        nums = tuple(float(p) for p in parts)
        return nums[0] if len(nums) == 1 else nums
    except ValueError:
        return value.strip()


def hardware(secs):
    return {k: v for k, v in secs.items() if not k.startswith(MACRO_PREFIXES) and k != "include"}


def macros(text):
    """{macro section: body text} — comments kept, they are part of the macro."""
    out, cur = {}, None
    for line in text.splitlines():
        m = re.match(r"^\[([^\]]+)\]", line)
        if m:
            cur = m.group(1) if m.group(1).startswith(MACRO_PREFIXES) else None
            if cur:
                out[cur] = []
            continue
        if cur:
            out[cur].append(line.rstrip())
    return {k: "\n".join(v).strip() for k, v in out.items()}


def differences(a, b):
    """{(section, option): (a, b)} over the union of two parsed configs."""
    out = {}
    for sec in set(a) | set(b):
        sa, sb = a.get(sec, {}), b.get(sec, {})
        for opt in set(sa) | set(sb):
            va, vb = norm(sa.get(opt)), norm(sb.get(opt))
            if va != vb:
                out[(sec, opt)] = (sa.get(opt), sb.get(opt))
    return out


class CommonTrunk(unittest.TestCase):
    """Between two machine sizes only the geometry may differ; every macro is identical."""

    GEOMETRY = {
        ("stepper_x", "position_endstop"), ("stepper_x", "position_max"),
        ("stepper_y", "position_endstop"), ("stepper_y", "position_min"), ("stepper_y", "position_max"),
        ("stepper_z", "position_max"),
        ("bed_mesh", "mesh_min"), ("bed_mesh", "mesh_max"), ("bed_mesh", "zero_reference_position"),
        ("resonance_tester", "probe_points"),
        # the C435 Y axis has its own motor (MOTOR Y C435)
        ("autotune_tmc stepper_x", "motor"), ("autotune_tmc stepper_y", "motor"),
    }

    def _check_pair(self, product_a, product_b):
        a = generator.generate(product_a, catalog=CATALOG)
        b = generator.generate(product_b, catalog=CATALOG)
        diffs = differences(hardware(parse(a)), hardware(parse(b)))
        unexpected = {k for k in diffs if k not in self.GEOMETRY and not k[0] == "screws_tilt_adjust"}
        self.assertEqual(unexpected, set(), "%s vs %s: non-geometry differences %s" % (product_a, product_b, sorted(unexpected)))
        self.assertEqual(macros(a), macros(b), "%s vs %s: macros differ" % (product_a, product_b))

    def test_direct_drive_sizes_differ_only_in_geometry(self):
        for m in MACHINES[1:]:
            self._check_pair("%s_DD_LW_04" % MACHINES[0], "%s_DD_LW_04" % m)

    def test_chromax_sizes_differ_only_in_geometry(self):
        for m in MACHINES[1:]:
            self._check_pair("%s_CX12_LW_04_7YMS" % MACHINES[0], "%s_CX12_LW_04_7YMS" % m)

    def test_every_machine_size_is_in_the_catalog(self):
        self.assertEqual(MACHINES, ["C235", "C335", "C435"])


class QCReference(unittest.TestCase):
    """The generated hardware equals the factory QC config of the same machine, option by
    option, except the differences listed here with their reason."""

    SECTIONS = ("printer", "stepper_x", "stepper_y", "stepper_z", "tmc2209 stepper_x", "tmc2209 stepper_y",
                "tmc2209 stepper_z", "autotune_tmc stepper_x", "autotune_tmc stepper_y", "autotune_tmc stepper_z",
                "probe", "bed_mesh", "screws_tilt_adjust", "probe_pressure", "yumi_z_tap", "yumi_sensorless_homing",
                "extruder", "heater_bed", "verify_heater extruder", "verify_heater heater_bed",
                "thermistor 100K4190YUMI", "thermistor 100K3950YUMI", "motor_constants BJ42D29-28V31",
                "motor_constants BJ42D29-28V03", "motor_constants BJ42D07-06V02", "motor_constants BJ42D07-03V05")
    # (section, option): why the generated value legitimately differs from the QC file
    KNOWN = {
        ("extruder", "step_pin"): "QC declares [extruder] on the fictive pins (CHROMAX-style cfg on a DD head); "
                                  "the direct drive product wires the real motor on E0",
        ("extruder", "dir_pin"): "same",
        ("extruder", "enable_pin"): "same",
        ("autotune_tmc stepper_x", "motor"): "V31 and V03 share the same constants; the catalog keeps V31 on the C series",
        ("autotune_tmc stepper_y", "motor"): "same",
    }

    def _compare(self, machine):
        qc_file = QC_DIR / ("qc_printer_%s.cfg" % machine)
        qc = parse(qc_file.read_text(encoding="utf-8"))
        gen = parse(generator.generate("%s_DD_LW_04" % machine, catalog=CATALOG))
        wanted = {}
        for sec in self.SECTIONS:
            self.assertIn(sec, qc, "%s missing in %s" % (sec, qc_file.name))
            self.assertIn(sec, gen, "%s not generated for %s" % (sec, machine))
            for opt, val in qc[sec].items():
                wanted[(sec, opt)] = val
        wrong = {}
        for (sec, opt), val in wanted.items():
            if (sec, opt) in self.KNOWN:
                continue
            got = gen[sec].get(opt)
            if norm(got) != norm(val):
                wrong[(sec, opt)] = (val, got)
        self.assertEqual(wrong, {}, "%s: generated != QC (qc, generated)" % machine)

    def test_c235(self):
        self._compare("C235")

    def test_c335(self):
        self._compare("C335")

    def test_c435(self):
        self._compare("C435")

    def test_known_differences_still_differ(self):
        """A KNOWN entry that no longer differs is stale: remove it."""
        qc = parse((QC_DIR / "qc_printer_C235.cfg").read_text(encoding="utf-8"))
        gen = parse(generator.generate("C235_DD_LW_04", catalog=CATALOG))
        for sec, opt in self.KNOWN:
            self.assertNotEqual(norm(qc[sec].get(opt)), norm(gen[sec].get(opt)), (sec, opt))


class MachineMacro(unittest.TestCase):
    """One macro derives the machine class from the X axis length; nobody else does."""

    def test_single_classification_ladder(self):
        cfg = generator.generate("C235_DD_LW_04", catalog=CATALOG)
        ladders = [name for name, body in macros(cfg).items() if "x_max >=" in body]
        self.assertEqual(ladders, ["gcode_macro _YUMI_MACHINE"])

    def test_size_dependent_macros_read_the_single_macro(self):
        cfg = macros(generator.generate("C235_DD_LW_04", catalog=CATALOG))
        for name in ("gcode_macro PRINT_START", "gcode_macro SCREWS_TILT_CALCULATE",
                     "gcode_macro CANCEL_PRINT", "gcode_macro WIPE_NOZZLE", "gcode_macro _YUMI_WELCOME"):
            self.assertIn('printer["gcode_macro _YUMI_MACHINE"]', cfg[name], name)
        self.assertIn("_YUMI_MACHINE", cfg["delayed_gcode welcome"])

    def test_windows_recognise_each_machine_and_only_it(self):
        windows = generator.machine_windows(CATALOG, "SMART_MAKER_1X")
        for mid, comp in generator.machines_of(CATALOG, "SMART_MAKER_1X"):
            x = float(comp["stepper_x"]["position_max"])
            hits = [w[0] for w in windows if w[1] <= x <= w[2]]
            self.assertEqual(hits, [mid])

    def test_macro_has_no_literal_machine_value_but_the_windows(self):
        body = macros(generator.generate("C235_DD_LW_04", catalog=CATALOG))["gcode_macro _YUMI_MACHINE"]
        for mid, lo, hi, bed, z in generator.machine_windows(CATALOG, "SMART_MAKER_1X"):
            self.assertIn('"%s", %s, %s' % (mid, bed, z), body)


class BedMesh(unittest.TestCase):
    """A missing mesh profile is built, never a print aborted; persisted without a restart."""

    def test_profile_load_guard_and_no_restart_save(self):
        cfg = generator.generate("C235_DD_LW_04", catalog=CATALOG)
        m = macros(cfg)
        self.assertIn("gcode_macro BED_MESH_PROFILE", m)
        self.assertIn("rename_existing: BASE_BED_MESH_PROFILE", m["gcode_macro BED_MESH_PROFILE"])
        self.assertIn("BED_MESH_CALIBRATE PROFILE={load}", m["gcode_macro BED_MESH_PROFILE"])
        self.assertIn("Z_TAP", m["gcode_macro BED_MESH_PROFILE"])
        self.assertIn("gcode_shell_command save_mesh", m)
        self.assertIn("RUN_SHELL_COMMAND CMD=save_mesh", m["gcode_macro BED_MESH_CALIBRATE"])
        commands = [l.strip() for l in m["gcode_macro BED_MESH_CALIBRATE"].splitlines() if not l.strip().startswith("#")]
        self.assertFalse([l for l in commands if "SAVE_CONFIG" in l], "SAVE_CONFIG would restart Klipper mid-print")
        # startup stays passive: the welcome only loads a mesh that exists
        self.assertIn('"default" in printer.bed_mesh.profiles', m["gcode_macro _YUMI_WELCOME"])


class BedDetection(unittest.TestCase):
    """The metal reference is measured (BED_SCAN_ZERO), never guessed from the bed size."""

    def test_scan_first_when_not_calibrated(self):
        cfg = generator.generate("C235_DD_LW_04", catalog=CATALOG)
        m = macros(cfg)
        det = m["gcode_macro BED_DETECTION"]
        self.assertIn("'bed_detect_x' not in sv", det)
        self.assertIn("BED_SCAN_ZERO", det)
        self.assertIn("_BED_DETECT_AT_REF", det)
        self.assertNotIn("ref_offset", det)
        self.assertIn("sv.bed_detect_x|float", m["gcode_macro _BED_DETECT_AT_REF"])
        self.assertEqual(m["gcode_macro BED_DETECT_SYNC"].splitlines()[-1].strip(), "BED_DETECTION")
        sec = parse(cfg)["yumi_bed_scan"]
        for key in ("center_dx", "center_dy", "range_x", "range_y", "z_clear", "z_step", "z_floor", "planes", "accel"):
            self.assertIn(key, sec)


class SlicerProfileGuard(unittest.TestCase):
    """A file sliced for another bed size is refused by PRINT_START, before anything moves."""

    def test_print_start_checks_the_bed_size_from_the_slicer(self):
        m = macros(generator.generate("C235_DD_LW_04", catalog=CATALOG))["gcode_macro PRINT_START"]
        self.assertIn("params.BED_X", m)
        self.assertIn('Please re-slice with the " ~ machine.model ~ " profile."', m)
        self.assertIn('RESPOND TYPE=error MSG="{ns.reason}"', m)
        self.assertNotIn("matches", m)          # silent when the profile is right
        self.assertNotIn("M106 S140 P3", m)     # the motherboard fan is automatic (controller_fan)
        self.assertIn("CANCEL_PRINT_DEFAULT", m)
        self.assertIn('printer["gcode_macro _YUMI_MACHINE"]', m)
        # the check comes before the actions of the macro, and the cancel is Klipper's own (no motion)
        self.assertLess(m.index("CANCEL_PRINT_DEFAULT"), m.index("G92 E0"))
        cancel = macros(generator.generate("C235_DD_LW_04", catalog=CATALOG))["gcode_macro CANCEL_PRINT"]
        self.assertIn("rename_existing: CANCEL_PRINT_DEFAULT", cancel)


class ProductGuard(unittest.TestCase):
    """The cfg declares the head / hotend / nozzle it was generated for; PRINT_START checks them."""

    def test_product_macro_carries_the_slicer_labels(self):
        dd = macros(generator.generate("C235_DD_LW_04", catalog=CATALOG))["gcode_macro _YUMI_PRODUCT"]
        self.assertIn('variable_head: "Direct Drive"', dd)
        self.assertIn('variable_hotend: "Low waste"', dd)
        self.assertIn("variable_nozzle: 0.4", dd)
        cx = macros(generator.generate("C235_CX12_HF_04_7YMS", catalog=CATALOG))["gcode_macro _YUMI_PRODUCT"]
        self.assertIn('variable_head: "ChromaX12"', cx)
        self.assertIn('variable_hotend: "High Flow"', cx)

    def test_print_start_checks_head_hotend_nozzle(self):
        m = macros(generator.generate("C235_DD_LW_04", catalog=CATALOG))["gcode_macro PRINT_START"]
        for key in ("params.HEAD", "params.HOTEND", "params.NOZZLE", 'printer["gcode_macro _YUMI_PRODUCT"]',
                    "extruder.nozzle_diameter", "head. This machine is set as", "hotend. This machine is set as", "nozzle. This machine is set as", "change the head in Printer Config"):
            self.assertIn(key, m, key)
        commands = [l.strip() for l in m.splitlines() if not l.strip().startswith("#")]
        self.assertEqual(sum(1 for l in commands if l == "CANCEL_PRINT_DEFAULT"), 1)


class MacrosMatchHardware(unittest.TestCase):
    """A macro may only drive fans that exist as fan_generic; tools exist on every head."""

    def test_set_fan_speed_targets_are_fan_generic(self):
        for product in ("C235_DD_LW_04", "C235_CX12_LW_04_7YMS"):
            cfg = generator.generate(product, catalog=CATALOG)
            fans = {s.split(" ", 1)[1] for s in parse(cfg) if s.startswith("fan_generic ")}
            targets = set(re.findall(r"SET_FAN_SPEED FAN=(\w+)", cfg))
            self.assertTrue(targets <= fans, "%s: SET_FAN_SPEED on non fan_generic %s" % (product, targets - fans))

    def test_t0_exists_once_on_every_head(self):
        for product in ("C235_DD_LW_04", "C235_CX12_LW_04_7YMS"):
            cfg = generator.generate(product, catalog=CATALOG)
            self.assertEqual(cfg.count("[gcode_macro T0]"), 1, product)


class ParkPositions(unittest.TestCase):
    """Every park position of the macros fits the machine's X axis (Orca: bed+7 approach, bed+20 pop tool)."""

    def test_cancel_print_parks_inside_the_axis(self):
        import re as _re
        for mid, comp in generator.machines_of(CATALOG, "SMART_MAKER_1X"):
            cfg = generator.generate("%s_DD_LW_04" % mid, catalog=CATALOG)
            x_max = float(comp["stepper_x"]["position_max"])
            body = macros(cfg)["gcode_macro CANCEL_PRINT"]
            offsets = [float(o) for o in _re.findall(r"G1 X\{bed_size \+ ([0-9.]+)\}", body)]
            self.assertTrue(offsets, "no bed-relative park move in CANCEL_PRINT")
            for off in offsets:
                self.assertLessEqual(comp["bed_size"] + off, x_max, "%s: bed+%g beyond X max %g" % (mid, off, x_max))


class CancelSequence(unittest.TestCase):
    """Cancel leaves the part at once: lift and travel before the slow shaping retract, moves guarded when unhomed."""

    def test_lift_and_park_come_before_the_slow_retract(self):
        body = macros(generator.generate("C235_DD_LW_04", catalog=CATALOG))["gcode_macro CANCEL_PRINT"]
        lift, park, slow, long = body.index("G1 Z+{dz}"), body.index("G1 X{bed_size + 20}"), body.index("G1 E-20 F300"), body.index("G1 E-110")
        self.assertLess(lift, park)
        self.assertLess(park, slow)
        self.assertLess(slow, long)
        self.assertIn('"xyz" in printer.toolhead.homed_axes', body)
        self.assertLess(body.index("homed_axes"), lift)
        # heaters and motors are switched off in every case, after the park
        self.assertLess(long, body.index("TURN_OFF_HEATERS"))


class LeadTime(unittest.TestCase):
    """The validated extrusion tuning is generated: PA 0, smooth 0.04, lead 0.03 (fork option)."""

    def test_extruder_carries_the_lead_time_tuning(self):
        for product in ("C235_DD_LW_04", "C235_CX12_LW_04_7YMS"):
            ext = parse(generator.generate(product, catalog=CATALOG))["extruder"]
            self.assertEqual(norm(ext["pressure_advance"]), 0)
            self.assertEqual(norm(ext["pressure_advance_smooth_time"]), 0.04)
            self.assertEqual(norm(ext["lead_time"]), 0.03)


class Comments(unittest.TestCase):
    def test_catalog_comments_reach_the_cfg(self):
        cfg = generator.generate("C235_DD_LW_04", catalog=CATALOG)
        self.assertIn("max_probe_times: 200  # 最大探测次数", cfg)
        self.assertIn("[motor_constants BJ42D29-28V31]  # MOTOR X/Y C SERIES", cfg)
        self.assertIn("#interpolate: True", cfg)
        self.assertIn("beta: 4300  # 4190 #4460 pour descendre de 20°", cfg)


class Heads(unittest.TestCase):
    def test_direct_drive_motor_is_on_e0(self):
        gen = parse(generator.generate("C235_DD_LW_04", catalog=CATALOG))
        slot0 = CATALOG["components"]["SMART_MAKER_1X"]["extruder_slots"][0]
        self.assertEqual(gen["extruder"]["step_pin"], slot0["step_pin"])
        self.assertEqual(gen["extruder"]["dir_pin"], slot0["dir_pin"])
        self.assertEqual(gen["tmc2209 extruder"]["uart_pin"], slot0["uart_pin"])
        self.assertIn("autotune_tmc extruder", gen)
        self.assertFalse([s for s in gen if s.startswith("extruder_stepper")])
        self.assertEqual(gen["filament_motion_sensor filament_sensor"]["switch_pin"], slot0["filament_sensor_pin"])

    def test_chromax_extruder_is_fictive_and_feeders_are_the_slots(self):
        cfg = generator.generate("C235_CX12_LW_04_7YMS", catalog=CATALOG)
        gen = parse(cfg)
        fake = CATALOG["components"]["CHROMAX_X12"]["config"]["extruder"]
        self.assertEqual(gen["extruder"]["step_pin"], fake["step_pin"])
        self.assertNotIn("tmc2209 extruder", gen)
        self.assertIn("moteur FICTIF", cfg)
        # YMS-1 turns like YMS-2 (bench C235: PB10 not inverted, MK12 50:17)
        self.assertEqual(gen["extruder_stepper extruder0"]["dir_pin"], "PB10")
        self.assertEqual(gen["extruder_stepper extruder0"]["gear_ratio"], "50:17")
        self.assertEqual(gen["extruder_stepper extruder1"]["dir_pin"], "PA4")
        self.assertEqual(gen["extruder_stepper extruder1"]["gear_ratio"], "50:17")
        self.assertEqual(gen["extruder_stepper extruder2"]["step_pin"], "smartbox:PB12")
        self.assertEqual(gen["extruder_stepper extruder2"]["dir_pin"], "!smartbox:PB10")
        self.assertEqual(len([s for s in gen if s.startswith("extruder_stepper")]), 7)
        # Klipper's pin syntax is [!] [chip:] pin — a modifier after the chip is a config error
        for sec, opts in gen.items():
            for opt, val in opts.items():
                self.assertNotRegex(val, r"[A-Za-z0-9_]+:[!^~]", "%s/%s = %s" % (sec, opt, val))


if __name__ == "__main__":
    unittest.main()
