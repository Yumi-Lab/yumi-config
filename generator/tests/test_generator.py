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
        ("yumi_sensorless_homing", "run_sgthrs_y"): "QC still re-applies 150 after the home; the trunk sets 0: re-applied "
                                                    "on aborted homes too, it poisoned the register and looped on "
                                                    "'aucun contact' (C235 06/09, sensorless-homing journal test 8)",
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
                     "gcode_macro _YUMI_POP_TOOL", "gcode_macro WIPE_NOZZLE", "gcode_macro _YUMI_WELCOME"):
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

    def test_periodic_refresh_is_proposed_on_the_screen_never_run_alone(self):
        """Every refresh_every prints (20) PRINT_END shows a prompt (action:prompt) with Later /
        Refresh now; the refresh (reference scan + mesh) runs only from that button. LOAD in a
        print never rebuilds an existing mesh, and only a LOAD from a running print counts (the
        welcome loads the mesh at every Klipper start; one such start scanned the bed by itself
        on 06/09 with the counter at 20)."""
        cfg = generator.generate("C235_DD_LW_04", catalog=CATALOG)
        m = macros(cfg)
        prof = m["gcode_macro BED_MESH_PROFILE"]
        self.assertIn("variable_refresh_every: 20", prof)
        self.assertNotIn("BED_SCAN_ZERO", prof, "no rebuild of an existing mesh from LOAD")
        self.assertIn('printer.print_stats.state == "printing"', prof)
        self.assertIn("{% if load and in_print %}", prof)
        self.assertIn("SAVE_VARIABLE VARIABLE=prints_since_mesh VALUE={prints + 1}", prof)
        prompt = m["gcode_macro _YUMI_MESH_REFRESH_PROMPT"]
        self.assertIn("prints >= every", prompt)
        self.assertIn("action:prompt_begin", prompt)
        self.assertIn("Later|_YUMI_PROMPT_END", prompt)
        self.assertIn("Refresh now|YUMI_MESH_REFRESH", prompt)
        self.assertNotIn("BED_SCAN_ZERO", prompt, "the prompt only asks")
        refresh = m["gcode_macro YUMI_MESH_REFRESH"]
        self.assertLess(refresh.index("BED_SCAN_ZERO"), refresh.index("BED_MESH_CALIBRATE"))
        end = m["gcode_macro PRINT_END"]
        self.assertLess(end.index("M84"), end.index("_YUMI_MESH_REFRESH_PROMPT"), "proposed once parked and off")
        self.assertIn("SAVE_VARIABLE VARIABLE=prints_since_mesh VALUE=0", m["gcode_macro BED_MESH_CALIBRATE"])


class StoppedPrint(unittest.TestCase):
    """A print stopped by an error must not leave the machine believing it still prints, and a
    boot must never feed filament to the head (bench 06/09: INIT_YMS's T0 ran the printing
    branch after an aborted print and pushed 2500 mm into nothing)."""

    def test_error_resets_the_print_state_without_moving(self):
        cfg = generator.generate("C235_CX12_LW_04_7YMS", catalog=CATALOG)
        sections = parse(cfg)
        self.assertEqual(sections["virtual_sdcard"].get("on_error_gcode"), "_YUMI_PRINT_ERROR")
        m = macros(cfg)["gcode_macro _YUMI_PRINT_ERROR"]
        self.assertIn("SAVE_VARIABLE VARIABLE=printing_start VALUE=False", m)
        self.assertIn("TURN_OFF_HEATERS", m)
        self.assertFalse(re.search(r"^\s*G[01] ", m, re.M), "no axis motion in an error handler")
        commands = [l.strip() for l in m.splitlines() if l.strip() and not l.strip().startswith("#")]
        self.assertLess(commands.index("SAVE_VARIABLE VARIABLE=printing_start VALUE=False"), commands.index("T0"),
                        "T0 must find printing_start False to take its idle (re-arm) branch")

    def test_boot_init_never_loads_to_the_head(self):
        cfg = generator.generate("C235_CX12_LW_04_7YMS", catalog=CATALOG)
        init = cfg.split("[delayed_gcode INIT_YMS]", 1)[1].split("\n[", 1)[0]
        delay = float(re.search(r"initial_duration: ([0-9.]+)", init).group(1))
        self.assertGreaterEqual(delay, 10, "INIT_YMS must run after the TMC autotune (UART collision on M18 at boot)")
        commands = [l.strip() for l in init.splitlines() if l.startswith(" ") and not l.strip().startswith("#")]
        self.assertLess(commands.index("SAVE_VARIABLE VARIABLE=printing_start VALUE=False"), commands.index("T0"))
        defined = set(re.findall(r"^\[gcode_macro ([A-Za-z0-9_]+)\]", cfg, re.M))
        for cmd in [c for c in commands if re.fullmatch(r"[A-Z][A-Z0-9_]+", c)]:
            self.assertIn(cmd, defined, "%s called at boot is not a macro of this cfg" % cmd)


class ToolChange(unittest.TestCase):
    """The slicer colour-change block is exactly YUMI_TOOL_CHANGE TOOL= TEMP= FLUSH=: the machine
    owns the sequence (contract with the Orca fork, 2026-09-06). Multicolour heads only."""

    def test_sequence_and_single_sources(self):
        m = macros(generator.generate("C235_CX12_LW_04_7YMS", catalog=CATALOG))
        body = m["gcode_macro YUMI_TOOL_CHANGE"].split("gcode:", 1)[1]   # the description names the steps too
        order = ["M104 S{unload_temp}", "G1 E-{tip.first_len}", "G1 Z{", "pop.approach_dx", "pop.pop_dx",
                 "M109 S{unload_temp}", "YUMI_UNLOAD_TIP SKIP_FIRST=1", "T{tool}", "M104 S{temp}",
                 "G1 E{c.prime_len}", "G1 E{flush}", "EXTRA_FLUSH", "M106 S255", "c.popoff_dx", "M106 S{fan}",
                 "RESTORE_GCODE_STATE"]
        positions = [body.index(step) for step in order]
        self.assertEqual(positions, sorted(positions), "tool change steps out of order")
        self.assertIn("|max", body.split("{% set flush")[1].split("%}")[0], "FLUSH floored at flush_min")
        self.assertNotIn("bed + 20", body)
        self.assertNotIn("bed + 11", body)
        self.assertIn("variable_popoff_dx: 11", m["gcode_macro _YUMI_CHANGE"])
        self.assertIn("variable_z_clearance: 3", m["gcode_macro _YUMI_CHANGE"])

    def test_direct_drive_has_no_tool_change(self):
        self.assertNotIn("[gcode_macro YUMI_TOOL_CHANGE]", generator.generate("C235_DD_LW_04", catalog=CATALOG))


class SensorlessHoming(unittest.TestCase):
    """No run_sgthrs re-applied after a home: the module's finally set it after the autotune, on
    aborted homes too, so one repeatability failure poisoned the StallGuard register (150) and
    every following coarse phase stalled 5.12 mm early — "aucun contact", in a loop (C235, 06/09)."""

    def test_no_stallguard_threshold_reapplied_after_homing(self):
        cfg = generator.generate("C235_DD_LW_04", catalog=CATALOG)
        sec = parse(cfg)["yumi_sensorless_homing"]
        for axis in ("x", "y"):
            self.assertEqual(float(sec.get("run_sgthrs_%s" % axis, 0)), 0.0, "run_sgthrs_%s must stay 0" % axis)


class Cutter(unittest.TestCase):
    """CUT_FILAMENT: one macro for every size. The cut ends at the configured X minimum (the
    lever, far left, a negative X); the head stops approach_offset mm before, pushes slowly to
    the minimum, comes back. Nothing hard-coded (Nicolas, 07/09)."""

    def test_geometry_from_config_on_every_size_and_head(self):
        for product in ("C235_DD_LW_04", "C235_CX12_LW_04_7YMS", "C335_CX12_LW_04_2YMS", "C435_CX12_LW_04_2YMS"):
            cfg = generator.generate(product, catalog=CATALOG)
            m = macros(cfg)
            cut = m["gcode_macro CUT_FILAMENT"]
            geo = m["gcode_macro _YUMI_CUTTER"]
            self.assertIn("{% set cut_x = printer.configfile.settings.stepper_x.position_min|float %}", cut, product)
            self.assertIn("{% set approach_x = cut_x + c.approach_offset|float %}", cut)
            self.assertIn("G1 X{approach_x} F{c.approach_speed}", cut)
            self.assertIn("G1 X{cut_x} F{c.cut_speed}", cut)
            self.assertLess(cut.index("F{c.approach_speed}"), cut.index("F{c.cut_speed}"), "approach first, then the slow push")
            self.assertIn('printer["yumi_sensorless_homing"].homed', cut, "real homing, not homed_axes")
            code = "\n".join(l for l in cut.split("gcode:", 1)[1].splitlines() if not l.strip().startswith("#"))
            self.assertNotIn("homed_axes", code, "toolhead.homed_axes is faked at boot by plr.cfg: never trust it for a move")
            self.assertFalse(re.search(r"G1 X-?\d", cut), "no hard-coded X in the cutter macro")
            x_min = float(parse(cfg)["stepper_x"]["position_min"])
            self.assertLess(x_min, 0, "%s: the cut point is the negative X minimum" % product)
            offset = float(re.search(r"variable_approach_offset: ([0-9.]+)", geo).group(1))
            self.assertEqual(x_min + offset, 10.0, "position_min -10 + offset = X10, where the head stops before the push")
            speeds = {k: float(v) for k, v in re.findall(r"variable_(approach_speed|cut_speed|release_speed): ([0-9.]+)", geo)}
            self.assertLess(speeds["cut_speed"], speeds["release_speed"])
            self.assertLessEqual(speeds["cut_speed"], 600, "the cut is a slow push")

    def test_bypass_variable_skips_the_cut_before_any_move(self):
        """cut_filament_bypass = 1 (panel, YUMI_SETUP CUTTER=0, SET_CUT_FILAMENT_BYPASS) makes
        CUT_FILAMENT a no-op: the slicer G-code keeps calling it, the printer decides, live."""
        m = macros(generator.generate("C235_CX12_LW_04_7YMS", catalog=CATALOG))
        cut = m["gcode_macro CUT_FILAMENT"].split("gcode:", 1)[1]
        self.assertIn("cut_filament_bypass|default(0)|int == 1", cut)
        self.assertLess(cut.index("cut_filament_bypass"), cut.index("G1 X"), "checked before any motion")
        self.assertLess(cut.index("cut_filament_bypass"), cut.index("yumi_sensorless_homing"), "a bypassed cut needs no homing")
        setter = m["gcode_macro SET_CUT_FILAMENT_BYPASS"]
        self.assertIn("SAVE_VARIABLE VARIABLE=cut_filament_bypass", setter)


class LoadParameters(unittest.TestCase):
    """T<n> forwards its parameters to YUMI_LOAD_TO_HEAD: the slicer writes `T1 PRELOAD=100` when
    it knows how far the filament was pulled back, one move instead of five steps."""

    def test_t_macros_forward_rawparams(self):
        cfg = generator.generate("C235_CX12_LW_04_7YMS", catalog=CATALOG)
        m = macros(cfg)
        for t in range(7):
            body = m["gcode_macro T%d" % t]
            self.assertIn("YUMI_LOAD_TO_HEAD {rawparams}", body, "T%d" % t)
        dd = macros(generator.generate("C235_DD_LW_04", catalog=CATALOG))
        self.assertIn("YUMI_LOAD_TO_HEAD {rawparams}", dd["gcode_macro T0"])
        header = generator.module_doc("yumi_filament_head")
        self.assertIn("PRELOAD=", header)


class ModuleDocs(unittest.TestCase):
    """Every Klipper module of this repo documents itself in printer.cfg: its header — what it
    does, every option, every command, the status fields — is emitted above its section, read
    from the module file (one source). Nothing configurable may be missing from that header."""

    INHERITED = {"filament_yumi_smart_motion_sensor": ("SET_FILAMENT_SENSOR", "QUERY_FILAMENT_SENSOR",
                                                       "pause_on_runout", "runout_gcode", "insert_gcode")}

    def test_every_option_command_and_parameter_is_in_the_header(self):
        for section, fname in generator.DOCUMENTED_MODULES.items():
            src = (generator.EXTRAS_DIR / fname).read_text(encoding="utf-8")
            header = generator.module_doc(section)
            options = set(re.findall(r"""config\.get(?:float|int|boolean)?\(\s*['"]([a-z_0-9]+)['"]""", src))
            commands = set(re.findall(r"""register_(?:mux_)?command\(\s*['"]([A-Z_]+)['"]""", src))
            commands |= set(re.findall(r'\("([A-Z_]+)", self\.cmd_', src))
            params = set(re.findall(r"""gcmd\.get(?:_float|_int)?\(\s*['"]([A-Z_]+)['"]""", src))
            self.assertTrue(options, fname)
            for name in options | commands | params | set(self.INHERITED.get(section, ())):
                self.assertIn(name, header, "%s: %s is not documented in the module header" % (fname, name))

    def test_header_sits_right_above_the_section(self):
        cfg = generator.generate("C235_CX12_LW_04_7YMS", catalog=CATALOG)
        for section in generator.DOCUMENTED_MODULES:
            doc = generator.module_doc(section)
            self.assertIn(doc, cfg, section)
            after = cfg[cfg.index(doc) + len(doc):].lstrip("\n")
            self.assertTrue(after.startswith("[%s" % section), "%s: header not directly above its section" % section)
        # direct drive: no YMS sensor, no smart sensor header either
        dd = generator.generate("C235_DD_LW_04", catalog=CATALOG)
        self.assertNotIn("filament_yumi_smart_motion_sensor", dd)


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

    def test_yms_insertion_flow_never_fakes_a_homed_printer(self):
        """T0's idle branch (insert re-arm) used SET_KINEMATIC_POSITION E=0: Klipper ignores E
        there and declares XYZ homed at 0,0,0 — every macro guarded by homed_axes then moves
        from a fake position. G92 E0 is the only E reset the flow needs."""
        cfg = generator.generate("C235_CX12_LW_04_7YMS", catalog=CATALOG)
        t0 = cfg.split("[gcode_macro T0]", 1)[1].split("\n[", 1)[0]
        self.assertNotIn("SET_KINEMATIC_POSITION", t0)
        self.assertIn("MOTION_SENSOR_INIT", t0)


class ParkPositions(unittest.TestCase):
    """Every park position of the macros fits the machine's X axis (Orca: bed+7 approach, bed+20 pop tool)."""

    def test_pop_tool_is_inside_the_axis_on_every_size(self):
        """One pop-tool macro for every park (PRINT_END, CANCEL_PRINT, tool change): its offsets fit every X axis."""
        import re as _re
        for mid, comp in generator.machines_of(CATALOG, "SMART_MAKER_1X"):
            cfg = generator.generate("%s_DD_LW_04" % mid, catalog=CATALOG)
            x_max = float(comp["stepper_x"]["position_max"])
            m = macros(cfg)
            pop = m["gcode_macro _YUMI_POP_TOOL"]
            offsets = {k: float(v) for k, v in _re.findall(r"variable_(approach_dx|pop_dx): ([0-9.]+)", pop)}
            self.assertEqual(set(offsets), {"approach_dx", "pop_dx"})
            for k, off in offsets.items():
                self.assertLessEqual(comp["bed_size"] + off, x_max, "%s: %s bed+%g beyond X max %g" % (mid, k, off, x_max))
            self.assertIn("G1 X{bed_size + approach_dx}", pop)
            self.assertIn("G1 X{bed_size + pop_dx}", pop)
            for user in ("gcode_macro _YUMI_LEAVE_PART_AND_UNLOAD",):
                self.assertIn("_YUMI_POP_TOOL", m[user])
            self.assertNotIn("bed_size + 20", m["gcode_macro CANCEL_PRINT"], "park position hard-coded outside _YUMI_POP_TOOL")


class CancelSequence(unittest.TestCase):
    """Cancel leaves the part at once: lift and travel before the slow shaping retract, moves guarded when unhomed."""

    def test_lift_and_park_come_before_the_tip_unload(self):
        m = macros(generator.generate("C235_DD_LW_04", catalog=CATALOG))
        body = m["gcode_macro _YUMI_LEAVE_PART_AND_UNLOAD"]
        first, lift, park, unload = body.index("G1 E-{tip.first_len}"), body.index("G1 Z+{dz}"), body.index("_YUMI_POP_TOOL"), body.index("YUMI_UNLOAD_TIP SKIP_FIRST=1")
        self.assertLess(first, lift)
        self.assertLess(lift, park)
        self.assertLess(park, unload)
        self.assertIn('printer["yumi_sensorless_homing"].homed', body, "moves gated by a real XY homing")
        self.assertLess(body.index("yumi_sensorless_homing"), lift)
        self.assertIn("M83", body)
        # both callers: unload while hot, heaters off afterwards, state back to idle
        for name in ("gcode_macro CANCEL_PRINT", "gcode_macro PRINT_END"):
            caller = m[name]
            self.assertLess(caller.index("_YUMI_LEAVE_PART_AND_UNLOAD"), caller.index("TURN_OFF_HEATERS"), name)
            self.assertIn("SAVE_VARIABLE VARIABLE=printing_start VALUE=False", caller, name)
            self.assertIn("SAVE_VARIABLE VARIABLE=was_interrupted VALUE=False", caller, name)
            self.assertFalse(re.search(r"^\s*(M104|M109) S0", caller, re.M), "%s: heaters off only through TURN_OFF_HEATERS, after the unload" % name)

    def test_print_end_is_the_whole_end_gcode(self):
        """The slicer end block is exactly PRINT_END: no hard-coded retract lengths, no park
        coordinates, PLR cleared here (guarded: plr.cfg is an include, not the trunk)."""
        m = macros(generator.generate("C235_DD_LW_04", catalog=CATALOG))
        end = m["gcode_macro PRINT_END"]
        self.assertFalse(re.search(r"G1 E-\d", end), "retract lengths live in _YUMI_TIP")
        self.assertNotIn("G0 X", end)
        self.assertIn('"gcode_macro clear_last_file" in printer', end)
        self.assertIn("clear_plr", end)
        self.assertLess(end.index("TURN_OFF_HEATERS"), end.index("M84"))

    def test_print_start_owns_the_print_state(self):
        """printing_start and the PLR arming are machine state: set by PRINT_START once the
        profile guard passed, never by the slicer block."""
        start = macros(generator.generate("C235_DD_LW_04", catalog=CATALOG))["gcode_macro PRINT_START"]
        self.assertIn("SAVE_VARIABLE VARIABLE=printing_start VALUE=True", start)
        self.assertIn("SAVE_VARIABLE VARIABLE=was_interrupted VALUE=True", start)
        self.assertIn('"gcode_macro save_last_file" in printer', start)
        self.assertLess(start.index("CANCEL_PRINT_DEFAULT"), start.index("printing_start VALUE=True"), "state set only when the guard passed")

    def test_tip_sequence_is_one_source(self):
        m = macros(generator.generate("C235_DD_LW_04", catalog=CATALOG))
        tip = m["gcode_macro _YUMI_TIP"]
        self.assertIn("variable_cut_speed: 65", tip)
        self.assertIn("variable_cut_len: 10", tip)
        unload = m["gcode_macro YUMI_UNLOAD_TIP"]
        for step in ("first_len", "cut_len", "slow_len", "pull_len"):
            self.assertIn("G1 E-{%s}" % step, unload)
        self.assertLess(unload.index("M83"), unload.index("G1 E-{cut_len}"))
        test = m["gcode_macro YUMI_TIP_TEST"]
        for key in ("params.CYCLES", "params.TEMP", "params.WAIT", "YUMI_UNLOAD_TIP", "G4 P{(wait * 1000)|int}", "M109 S{temp}"):
            self.assertIn(key, test)
        self.assertNotIn("G1 E-10 F2100", m["gcode_macro CANCEL_PRINT"])


class LeadTime(unittest.TestCase):
    """The validated extrusion tuning is generated: PA 0, smooth 0.04, lead 0.03 (fork option)."""

    def test_extruder_carries_the_lead_time_tuning(self):
        for product in ("C235_DD_LW_04", "C235_CX12_LW_04_7YMS"):
            ext = parse(generator.generate(product, catalog=CATALOG))["extruder"]
            self.assertEqual(norm(ext["pressure_advance"]), 0)
            self.assertEqual(norm(ext["pressure_advance_smooth_time"]), 0.04)
            self.assertEqual(norm(ext["lead_time"]), 0.03)


class PressureAdvanceMacro(unittest.TestCase):
    """The YMS SET_PRESSURE_ADVANCE override forwards every parameter to every extruder."""

    def test_parameters_are_forwarded_verbatim(self):
        m = macros(generator.generate("C235_CX12_LW_04_7YMS", catalog=CATALOG))["gcode_macro SET_PRESSURE_ADVANCE"]
        self.assertIn("params.items() if k != 'EXTRUDER'", m)
        self.assertIn("SET_PA_ORIG EXTRUDER=extruder6 {argstr}", m)
        self.assertIn("SET_PA_ORIG EXTRUDER={params.EXTRUDER} {argstr}", m)
        self.assertNotIn("ADVANCE={pa}", m)


class FilamentAtHead(unittest.TestCase):
    """A tool selection ends with the filament at the head sensor; an unload is checked by it."""

    def test_head_sensor_module_is_generated(self):
        for product in ("C235_DD_LW_04", "C235_CX12_LW_04_7YMS"):
            gen = parse(generator.generate(product, catalog=CATALOG))
            self.assertEqual(gen["yumi_filament_head"]["pin"], "^!PA8")  # pull-up: the switch line floats otherwise
            self.assertIn("head_to_nozzle", gen["yumi_filament_head"])
            # the pin is the module's endstop: no second user of PA8
            self.assertNotIn("filament_switch_sensor head_sensor", gen)

    def test_yumi_setup_macro_forwards_one_line(self):
        m = macros(generator.generate("C235_DD_LW_04", catalog=CATALOG))
        self.assertIn("gcode_shell_command yumi_setup", m)
        self.assertIn("prefs.py --apply --set", m["gcode_shell_command yumi_setup"])
        self.assertIn('RUN_SHELL_COMMAND CMD=yumi_setup PARAMS="{args|join(\' \')}"', m["gcode_macro YUMI_SETUP"])

    def test_tool_macros_load_to_the_head(self):
        dd = macros(generator.generate("C235_DD_LW_04", catalog=CATALOG))
        self.assertIn("YUMI_LOAD_TO_HEAD", dd["gcode_macro T0"])
        yms = macros(generator.generate("C235_CX12_LW_04_7YMS", catalog=CATALOG))
        for t in ("T0", "T1", "T6"):
            body = yms["gcode_macro %s" % t]
            self.assertIn("YUMI_LOAD_TO_HEAD", body, t)
            # only in the printing branch, never in the filament-insertion (init) mode
            self.assertLess(body.index("YUMI_LOAD_TO_HEAD"), body.index("{% else %}"), t)
        self.assertIn("YUMI_UNLOAD_CHECK", yms["gcode_macro YUMI_UNLOAD_TIP"])
        self.assertIn("YUMI_LOAD_TO_HEAD", yms["gcode_macro YUMI_TIP_TEST"])


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


class MacroTemplatesCompile(unittest.TestCase):
    """Every gcode_macro / delayed_gcode body of the generated cfgs must compile as a Jinja2
    template (Klipper's macro language): a stray {% endif %} took Klipper down at boot on the
    bench (2026-09-07). Skipped when jinja2 is not installed locally."""

    def test_every_macro_body_compiles(self):
        try:
            import jinja2
        except ImportError:
            self.skipTest("jinja2 not installed")
        env = jinja2.Environment()
        for product in ("C235_DD_LW_04", "C235_CX12_LW_04_7YMS", "C335_CX12_LW_04_2YMS"):
            cfg = generator.generate(product, catalog=CATALOG)
            for name, body in macros(cfg).items():
                gcode = body.split("gcode:", 1)[1] if "gcode:" in body else ""
                try:
                    env.parse(gcode)
                except jinja2.TemplateSyntaxError as e:
                    self.fail("%s / %s: %s (line %s)" % (product, name, e.message, e.lineno))
