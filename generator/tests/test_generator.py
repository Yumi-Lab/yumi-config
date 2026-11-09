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
        # the bed rides on Y: its weight sets the Y cap per size (C335 08/11, belt teeth skipped)
        ("printer", "kinematics"), ("printer", "max_y_accel"),
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
        ("printer", "kinematics"): "limited_cartesian caps Y for the heavy C335 bed (08/11, belt teeth skipped at "
                                   "20000); the factory QC cfgs keep cartesian until validated on a QC bench",
        ("extruder", "step_pin"): "QC declares [extruder] on the fictive pins (CHROMAX-style cfg on a DD head); "
                                  "the direct drive product wires the real motor on E0",
        ("extruder", "dir_pin"): "same",
        ("extruder", "enable_pin"): "same",
        ("autotune_tmc stepper_x", "motor"): "V31 and V03 share the same constants; the catalog keeps V31 on the C series",
        ("autotune_tmc stepper_y", "motor"): "same",
        ("autotune_tmc stepper_z", "tuning_goal"): "performance in the trunk: in stealthChop the C335 Z stalled "
                                                   "from 20 mm/s (07/11); the factory QC keeps auto until a QC "
                                                   "bench validates it",
        ("yumi_z_tap", "max_probe_times"): "15 in the trunk: a Z stalled against the bed fired the sensor 32 times in "
                                           "30 s without a stable contact (C335 07/11); the factory QC keeps 200 until "
                                           "a QC bench validates the bounded tap",
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
        """A KNOWN entry that no longer differs on any machine is stale: remove it."""
        pairs = [(parse((QC_DIR / ("qc_printer_%s.cfg" % m)).read_text(encoding="utf-8")),
                  parse(generator.generate("%s_DD_LW_04" % m, catalog=CATALOG))) for m in MACHINES]
        for sec, opt in self.KNOWN:
            self.assertTrue(any(norm(qc[sec].get(opt)) != norm(gen[sec].get(opt)) for qc, gen in pairs), (sec, opt))


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


class TipSizingIsThePrimaryPathBladeIsTheNetting(unittest.TestCase):
    """The tip is shaped by COOLING SIZED in the 1.9 mm bore of the PEEK heatbreak, the narrowest
    point of the head: that was the project's initial objective, and the blade was added only as
    a safety net (Nicolas, 09/11). The star PTFE below the heatbreak is the widest bore of the
    path at 2.7-3.0 mm, where warm filament is free to swell, so the sequence must cross it FAST
    (first_len + cut_len, tip to 17 mm) and only then crawl (slow_len, tip 17 -> 37 mm) so the tip
    enters the 1.9 bore and cools to size. Arming the blade before cut_len moved that crawl into
    the wide section instead, warm and free to swell, and the head clogged."""

    PRODUCTS = ("C235_CX12_LW_04_7YMS", "C335_CX12_LW_04_2YMS", "C435_CX12_LW_04_2YMS")

    def _tip(self, product):
        return macros(generator.generate(product, catalog=CATALOG))[
            "gcode_macro YUMI_UNLOAD_TIP"].split("gcode:", 1)[1]

    def test_the_fast_transit_reaches_the_sizing_bore_before_any_crawl(self):
        for product in self.PRODUCTS:
            tip = self._tip(product)
            i_first = tip.index("G1 E-{done}")
            i_cut = tip.index("G1 E-{cut_len}")
            i_slow = tip.index("G1 E-{slow_len}")
            self.assertLess(i_first, i_cut, "%s: first_len then cut_len, both fast" % product)
            self.assertLess(i_cut, i_slow,
                            "%s: the crawl comes AFTER the fast transit, never during it" % product)
            between = tip[i_cut:i_slow]
            self.assertNotIn("G1 E", between.replace("G1 E-{cut_len}", "", 1),
                             "%s: nothing may extrude between the transit and the crawl" % product)

    def test_the_blade_is_armed_by_default(self):
        """Reversed on 09/11, on a measurement, not a preference.

        The sized tip came out in one piece and cleaner than any before it -- and then would not
        go back in. It jammed, and Nicolas had to cut it by hand, pull the PTFE and start over:
        "le bout chaud regonfle, il faut absolument qu'il soit froid avant d'etre retire". Shaping
        cannot beat that, because the tip leaves the nozzle molten whatever the sequence does. A
        blade cut lands above the heatsink, on cold filament, and cannot swell. His verdict:
        "couper ne fait pas perdre beaucoup de temps et au moins ca garantit que ca fonctionne".
        So the cut is the default on every unload path and CUT=0 is what has to be asked for.
        Machines whose cutter is bypassed (cut_filament_bypass, SET_CUT_FILAMENT_BYPASS) fall back
        to the shaping path on their own -- CUT_FILAMENT handles the bypass itself."""
        for product in self.PRODUCTS:
            tip = self._tip(product)
            self.assertIn("params.CUT|default(1)|int == 1", tip,
                          "%s: the blade is the guarantee, CUT=0 declines it" % product)
            self.assertNotIn("params.CUT|default(0)", tip,
                             "%s: no path may leave the blade disarmed by default" % product)

    def test_the_blade_never_cuts_while_the_tip_is_still_hot(self):
        """One cut per branch, and in both the tip is already out of the melt when it happens.

        Moving the blade earlier on 09/11 clogged the head: the stroke landed on filament still in
        the hot wide bore, where it swells. In the legacy branch the cut comes after the cut_len
        transit and before the crawl; in the sizing branch it comes after the dwell, which is the
        coldest the tip ever gets. Indexes are taken per branch, since both now cut."""
        for product in self.PRODUCTS:
            tip = self._tip(product)
            sizing, _, legacy = tip.partition("{% else %}")
            calls = [l for l in tip.splitlines()
                     if re.match(r"\s*(\{%[^%]*%\}\s*)?YUMI_CUT_TIP\b", l)]
            self.assertEqual(len(calls), 2,
                             "%s: exactly one cut per unload branch, got %r" % (product, calls))
            self.assertLess(sizing.index("G4 P{dwell_ms}"), sizing.index("YUMI_CUT_TIP"),
                            "%s: the sizing branch cuts only once the dwell has set the tip"
                            % product)
            self.assertLess(legacy.index("G1 E-{cut_len}"), legacy.index("YUMI_CUT_TIP"),
                            "%s: the blade never cuts before the tip has left the hot zone"
                            % product)
            self.assertLess(legacy.index("YUMI_CUT_TIP"), legacy.index("G1 E-{slow_len}"),
                            "%s: and never during the sizing crawl" % product)


class ConfirmRunoutBeforeGivingUp(unittest.TestCase):
    """A YMS encoder runout is a suspicion, not a verdict: a flaky read kills a print for
    nothing (Nicolas, 08/11). The sensor must hand over to a verification that pauses first,
    checks whether the feeder really delivers, and only then decides."""

    PRODUCTS = ("C235_CX12_LW_04_7YMS", "C335_CX12_LW_04_2YMS", "C435_CX12_LW_04_2YMS")

    def test_every_sensor_hands_over_instead_of_pausing_blind(self):
        for product in self.PRODUCTS:
            cfg = generator.generate(product, catalog=CATALOG)
            runouts = re.findall(r"runout_gcode:(.*?)insert_gcode:", cfg, re.S)
            self.assertTrue(runouts, product)
            for n, block in enumerate(runouts, 1):
                self.assertIn("YUMI_CONFIRM_RUNOUT SENSOR=YMS-%d" % n, block,
                              "%s: YMS-%d must hand over, not pause blind" % (product, n))
                self.assertNotIn("filament encoder runout", block,
                                 "%s: the bare PAUSE + error is gone from the sensor" % product)

    def test_it_pauses_before_it_verifies(self):
        """One never keeps depositing on a suspicion: PAUSE comes before any motion or feed."""
        m = macros(generator.generate("C335_CX12_LW_04_2YMS", catalog=CATALOG))
        code = m["gcode_macro YUMI_CONFIRM_RUNOUT"].split("gcode:", 1)[1]
        self.assertLess(code.index("PAUSE"), code.index("_YUMI_POP_TOOL"))
        self.assertLess(code.index("PAUSE"), code.index("YUMI_LOAD_TO_HEAD"))

    def test_a_confirmed_jam_cuts_and_waits_a_false_reading_resumes(self):
        for product in self.PRODUCTS:
            m = macros(generator.generate(product, catalog=CATALOG))
            code = m["gcode_macro YUMI_CONFIRM_RUNOUT"].split("gcode:", 1)[1]
            self.assertIn("printer.yumi_filament_head.pending", code,
                          "%s: the verdict comes from the module, not from a guess" % product)
            jam, ok = code.split("{% else %}", 1)[0], code.split("{% else %}", 1)[1]
            self.assertIn("YUMI_CUT_TIP", jam, "%s: a confirmed jam cuts the tip" % product)
            self.assertNotIn("RESUME", jam, "%s: a confirmed jam never resumes by itself" % product)
            self.assertIn("RESUME", ok, "%s: a false reading resumes the print" % product)
            self.assertFalse(re.search(r"G1 X-?\d", code), "no hard-coded X")

    def test_nothing_verifiable_keeps_the_old_behaviour(self):
        """No head sensor, or bypassed: nothing can be checked, so the old message is kept and
        the print stays paused. A verification that cannot verify must not decide."""
        m = macros(generator.generate("C335_CX12_LW_04_2YMS", catalog=CATALOG))
        code = m["gcode_macro YUMI_CONFIRM_RUNOUT"].split("gcode:", 1)[1]
        self.assertIn("head.bypass", code, "a bypassed head sensor verifies nothing")
        tail = code.rsplit("{% else %}", 1)[1]
        self.assertIn("filament encoder runout", tail, "the old message is the fallback")
        self.assertNotIn("RESUME", tail, "an unverifiable runout does not resume")


class NoMacroLeavesTheAxisLimits(unittest.TestCase):
    """The audit rule, made permanent. UNLOAD_FILAMENT walked to a hard-coded X-12 where
    position_min is -10: Klipper refused the move and the macro died before touching the
    extruder, so it never unloaded anything (found 08/11). Literal coordinates are only read
    in ABSOLUTE context -- under G91 the number is a delta, not a position."""

    PRODUCTS = ("C235_DD_LW_04", "C235_CX12_LW_04_7YMS", "C335_CX12_LW_04_2YMS", "C435_CX12_LW_04_2YMS")

    def test_every_literal_coordinate_is_reachable(self):
        for product in self.PRODUCTS:
            cfg = generator.generate(product, catalog=CATALOG)
            settings = parse(cfg)
            limits = {}
            for ax in "xyz":
                s = settings.get("stepper_" + ax, {})
                if "position_min" in s and "position_max" in s:
                    limits[ax] = (float(s["position_min"]), float(s["position_max"]))
            self.assertTrue(limits, product)
            for name, body in macros(cfg).items():
                absolute = True   # Klipper boots in G90
                for line in body.splitlines():
                    bare = line.split("#", 1)[0].strip()
                    if re.match(r"^G91\b", bare):
                        absolute = False
                    elif re.match(r"^G90\b", bare):
                        absolute = True
                    elif absolute and re.match(r"^G[01]\b", bare):
                        for ax, val in re.findall(r"\b([XYZ])(-?[0-9]+(?:\.[0-9]+)?)\b", bare):
                            lo, hi = limits.get(ax.lower(), (None, None))
                            if lo is None:
                                continue
                            self.assertGreaterEqual(float(val), lo,
                                "%s / %s: %s -> %s%s is below position_min %s" % (product, name, bare, ax, val, lo))
                            self.assertLessEqual(float(val), hi,
                                "%s / %s: %s -> %s%s is above position_max %s" % (product, name, bare, ax, val, hi))


class ManualLoadAndUnload(unittest.TestCase):
    """UNLOAD_FILAMENT and LOAD_FILAMENT, the two macros the operator presses by hand."""

    def test_unload_delegates_instead_of_rolling_its_own_geometry(self):
        for product in ("C235_CX12_LW_04_7YMS", "C335_CX12_LW_04_2YMS", "C435_CX12_LW_04_2YMS"):
            code = macros(generator.generate(product, catalog=CATALOG))["gcode_macro UNLOAD_FILAMENT"]
            code = code.split("gcode:", 1)[1]
            self.assertIn("YUMI_UNLOAD_TIP", code, "%s: the single source does the sequence" % product)
            self.assertFalse(re.search(r"G1 X-?\d", code), "%s: no hand-rolled X any more" % product)
            self.assertNotIn("G92 E", code, "%s: G92 corrupts the slicer E bookkeeping" % product)

    def test_load_uses_m83_because_g91_does_not_make_e_relative(self):
        """Klipper overrides absolute_coord with absolute_extrude for the E axis, so under G91
        the feeds stayed absolute: E50 then E30 fed 50 mm and then pulled 20 mm BACK, and which
        of the two happened depended on the mode the previous print left behind."""
        code = macros(generator.generate("C335_CX12_LW_04_2YMS", catalog=CATALOG))["gcode_macro LOAD_FILAMENT"]
        code = code.split("gcode:", 1)[1]
        self.assertIn("M83", code)
        self.assertNotIn("G91", code, "G91 does not make E relative")
        self.assertIn("SAVE_GCODE_STATE NAME=yumi_load_filament", code, "the mode must not leak")
        self.assertIn("RESTORE_GCODE_STATE NAME=yumi_load_filament", code)


class EveryYmsWatchesForJams(unittest.TestCase):
    """All the YMS are identical hardware (Nicolas, 09/11), so all of them must carry the smart
    motion sensor. The jam detection used to be emitted for i == 1 only: it sat on YMS-2 while
    YMS-1 -- the channel actually plugged in -- ran a bare Klipper motion sensor with no pitch
    and no blockage detection. The jam was watched by nothing, and the only remaining check was
    an absence over detection_length 50 mm, about 48 s at a normal flow."""

    PRODUCTS = ("C235_CX12_LW_04_7YMS", "C335_CX12_LW_04_2YMS", "C435_CX12_LW_04_2YMS")

    def test_no_channel_is_left_with_a_bare_sensor(self):
        for product in self.PRODUCTS:
            cfg = generator.generate(product, catalog=CATALOG)
            found = re.findall(r"\[(filament_\w+) (YMS-\d+)\]", cfg)
            self.assertTrue(found, product)
            for typ, name in found:
                self.assertEqual(typ, "filament_yumi_smart_motion_sensor",
                                 "%s / %s: identical hardware, identical sensor" % (product, name))

    def test_each_channel_watches_its_own_feeder(self):
        """YMS-n is fed by extruder_stepper extruder{n-1}: a tick counts only when THAT feeder
        drives, so a wrong mapping would blame the wrong channel for a jam."""
        for product in self.PRODUCTS:
            cfg = generator.generate(product, catalog=CATALOG)
            blocks = re.findall(
                r"\[filament_yumi_smart_motion_sensor (YMS-(\d+))\]((?:\n(?!\[)[^\n]*)*)", cfg)
            self.assertTrue(blocks, product)
            for name, num, body in blocks:
                self.assertIn("blockage_detection: True", body, "%s / %s" % (product, name))
                motor = re.search(r"motor: (\S+)", body)
                self.assertIsNotNone(motor, "%s / %s: no feeder declared" % (product, name))
                self.assertEqual(motor.group(1), "extruder%d" % (int(num) - 1),
                                 "%s / %s must watch its own feeder" % (product, name))
                for option in ("min_pitch:", "max_pitch:", "blockage_threshold:", "pitch_view:"):
                    self.assertIn(option, body, "%s / %s: %s missing" % (product, name, option))


class NoHeadSensor(unittest.TestCase):
    """A machine without the head sensor — an older head, or the per-machine override
    {"filament_head": null} used on the bench (08/09) to rule the module out of an MCU-link
    fault: no [yumi_filament_head] section, the pin is never configured, T<n> select and sync
    exactly as before the module and nothing loads."""

    def test_override_none_removes_the_module_and_the_load(self):
        cfg = generator.generate("C235_CX12_LW_04_7YMS", {"filament_head": None}, catalog=CATALOG)
        self.assertNotIn("[yumi_filament_head]", cfg)
        self.assertNotIn("PA8", cfg)
        m = macros(cfg)
        self.assertNotIn("YUMI_LOAD_TO_HEAD", m["gcode_macro T2"])
        self.assertIn("ACTIVATION YMS-3", m["gcode_macro T2"])
        # every other call of the module (tip-shaping unload check, bench tip test, direct-drive
        # T0) is guarded at run time on the same line: no unknown command without the section
        for name, body in m.items():
            for line in body.splitlines():
                if re.match(r"\s*(\{%[^%]*%\}\s*)?(YUMI_LOAD_TO_HEAD|YUMI_UNLOAD_CHECK)\b", line):
                    self.assertIn("'yumi_filament_head' in printer", line, "%s: %s" % (name, line.strip()))
        self.assertIn("SYNC_EXTRUDER_MOTION EXTRUDER=extruder2 MOTION_QUEUE=extruder", m["gcode_macro T2"])

    def test_default_product_keeps_the_load(self):
        cfg = generator.generate("C235_CX12_LW_04_7YMS", catalog=CATALOG)
        self.assertIn("[yumi_filament_head]", cfg)
        self.assertIn("YUMI_LOAD_TO_HEAD SENSOR=YMS-3 {rawparams}", macros(cfg)["gcode_macro T2"])


class LoadParameters(unittest.TestCase):
    """T<n> forwards its parameters to YUMI_LOAD_TO_HEAD: the slicer writes `T1 PRELOAD=100` when
    it knows how far the filament was pulled back, one move instead of five steps."""

    def test_t_macros_forward_rawparams(self):
        cfg = generator.generate("C235_CX12_LW_04_7YMS", catalog=CATALOG)
        m = macros(cfg)
        for t in range(7):
            body = m["gcode_macro T%d" % t]
            self.assertIn("YUMI_LOAD_TO_HEAD SENSOR=YMS-%d {rawparams}" % (t + 1), body,
                          "T%d must name the YMS it loads from, so an empty YMS is caught early" % t)
        dd = macros(generator.generate("C235_DD_LW_04", catalog=CATALOG))
        self.assertIn("YUMI_LOAD_TO_HEAD SENSOR=filament_sensor {rawparams}", dd["gcode_macro T0"],
                      "direct drive: the head's own runout sensor is the feeder to watch")
        header = generator.module_doc("yumi_filament_head")
        self.assertIn("PRELOAD=", header)


class EventHandlerSignatures(unittest.TestCase):
    """Klipper event handlers of our modules must accept whatever the event sends: motor_off
    comes with no argument in this Klipper, older ones passed print_time. A fixed signature
    turned every M18 into an internal error and Klipper into a restart loop (bench, 07/09)."""

    def test_motor_off_handlers_accept_no_argument(self):
        import ast
        for fname in generator.DOCUMENTED_MODULES.values():
            src = (generator.EXTRAS_DIR / fname).read_text(encoding="utf-8")
            tree = ast.parse(src)
            handlers = set()
            for node in ast.walk(tree):
                if isinstance(node, ast.Call) and getattr(node.func, "attr", "") == "register_event_handler" \
                        and node.args and isinstance(node.args[0], ast.Constant) \
                        and node.args[0].value == "stepper_enable:motor_off":
                    handlers.add(node.args[1].attr)
            for node in ast.walk(tree):
                if isinstance(node, ast.FunctionDef) and node.name in handlers:
                    positional = [a.arg for a in node.args.args if a.arg != "self"]
                    required = positional[:len(positional) - len(node.args.defaults)]
                    self.assertEqual(required, [], "%s.%s must be callable with no argument" % (fname, node.name))


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


class ExtrasModulesLoad(unittest.TestCase):
    """Klipper only knows a [section] whose module defines load_config: a cut-off yumi_z_tap.py
    compiled fine and halted the C335 on 'Section yumi_z_tap is not a valid config section'."""

    def test_every_yumi_module_defines_load_config(self):
        extras = HERE.parent.parent / "klipper" / "klippy" / "extras"
        for module in sorted(extras.glob("yumi_*.py")):
            self.assertIn("\ndef load_config(config):", module.read_text(encoding="utf-8"), module.name)


class PopToolAccelerationIsGivenBack(unittest.TestCase):
    """The pop-tool run needs 20000 mm/s2 in X; the macros that raise it give back the
    acceleration they came with (C335 07/11: after a print every Y move ran at 20000)."""

    def test_macros_restore_the_acceleration(self):
        cfg = generator.generate("C335_CX12_LW_04_2YMS", catalog=CATALOG)
        m = macros(cfg)
        for name in ("YUMI_TOOL_CHANGE", "_YUMI_LEAVE_PART_AND_UNLOAD"):
            body = m["gcode_macro %s" % name]
            self.assertIn("{% set prev_accel = printer.toolhead.max_accel %}", body)
            self.assertEqual(body.rstrip().splitlines()[-1].strip(), "SET_VELOCITY_LIMIT ACCEL={prev_accel}")


class ZMovesCarryTheirSpeed(unittest.TestCase):
    """A Z move without F inherits the last XY feedrate (capped at max_z_velocity): on the C335
    (07/11) the mesh lift after BED_DETECTION stalled the leadscrew and Klipper believed Z=100."""

    def test_every_macro_z_move_has_a_feedrate(self):
        import re
        for product in ("C235_DD_LW_04", "C335_CX12_LW_04_2YMS", "C435_CX12_HF_04_7YMS"):
            cfg = generator.generate(product, catalog=CATALOG)
            for line in cfg.splitlines():
                code = line.split("#", 1)[0].strip()
                if re.match(r"G[01]\b.*\bZ", code):
                    self.assertRegex(code, r"\bF", "%s: %s" % (product, code))


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
        # C335 07/11 still held the point a C235 had measured: the point belongs to a bed size
        self.assertIn("sv.bed_detect_bed|default(0)|float != bed", det)
        ref = m["gcode_macro _BED_DETECT_AT_REF"]
        self.assertIn("BED_REF_PROBE X={mx} Y={my}", ref)
        self.assertIn("BED_PLATE_PRESENT X={x_max - bed_probe_x_inset} Y={my}", ref)
        self.assertNotIn("\n    PROBE\n", ref)
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

    def test_yms_sensor_init_never_unhomes_the_axes(self):
        """MOTION_SENSOR_INIT ran M18 (all motors off, X/Y/Z unhomed) 10 s after every Klipper
        start: a G28 run in that window was undone as soon as it ended (C335 07/11)."""
        cfg = generator.generate("C335_CX12_LW_04_2YMS", catalog=CATALOG)
        init = cfg.split("[gcode_macro MOTION_SENSOR_INIT]", 1)[1].split("\n[", 1)[0]
        for unhoming in ("M18", "M84", "SET_KINEMATIC_POSITION"):
            self.assertNotIn(unhoming, init)
        self.assertIn("if not name.startswith('stepper_')", init)


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

    def test_print_start_lifts_the_nozzle_before_anything_heats(self):
        """A print launched with the nozzle resting low (previous print stopped at Z 2.5) heated
        it there (C335 08/11): once the profile guard passed, PRINT_START lifts with the watched
        lift (Z not trusted, stops at the top end stop) and forgets Z for G28."""
        start = macros(generator.generate("C235_DD_LW_04", catalog=CATALOG))["gcode_macro PRINT_START"]
        lift = start.index("YUMI_Z_LIFT DIST=10 SPEED=5")
        self.assertLess(start.index("CANCEL_PRINT_DEFAULT"), lift, "a refused file never moves")
        self.assertLess(start.index("SET_KINEMATIC_POSITION Z=0 SET_HOMED=Z"), lift)
        self.assertLess(lift, start.index("SET_KINEMATIC_POSITION CLEAR=Z"))
        self.assertNotRegex(start, r"M10[49]|M1[49]0", "PRINT_START heats nothing: the slicer does, after it")

    def test_c335_caps_y_not_x(self):
        """C335 08/11: the heavy Y bed skipped teeth at 20000; X keeps 20000 for the pop tool."""
        cfg = generator.generate("C335_CX12_LW_04_2YMS", catalog=CATALOG)
        printer = cfg[cfg.index("[printer]"):]
        printer = printer[:printer.index("\n[")]
        self.assertIn("kinematics: limited_cartesian", printer)
        self.assertIn("max_y_accel: 4000", printer)
        self.assertIn("max_accel: 20000", printer)
        self.assertNotIn("max_x_accel", printer)
        self.assertIn("kinematics: cartesian", generator.generate("C235_DD_LW_04", catalog=CATALOG))

    def test_g28_z_alone_keeps_an_xy_really_found(self):
        """The start G-code homes XY while the bed heats, then G28 Z taps on the hot bed: G28 Z
        re-homes X/Y only when the sensorless homing has not found them (its own state, not
        homed_axes, which SET_KINEMATIC_POSITION fakes)."""
        cfg = generator.generate("C235_DD_LW_04", catalog=CATALOG)
        o = cfg[cfg.index("[homing_override]"):]
        o = o[:o.index("\n[", 1)]
        self.assertIn("{% set found = printer.yumi_sensorless_homing.homed %}", o)
        self.assertIn("{% set do_x = home_all or has_x or (has_z and not xy_found) %}", o)
        self.assertIn("{% if do_x %}\n        YUMI_SENSORLESS_HOME AXIS=X", o)
        self.assertIn("{% if do_y %}\n        YUMI_SENSORLESS_HOME AXIS=Y", o)
        retract = o.index("SET_KINEMATIC_POSITION X={x_max} Y={y_min}")
        self.assertLess(o.index("{% if do_x or do_y %}"), retract, "a kept XY is never overwritten")
        self.assertNotIn("homed_axes", o)

    def test_yms_are_scanned_at_boot_and_at_print_start(self):
        """Which YMS are plugged (C335 08/11: a print lost on an unplugged YMS cable): scanned
        at boot for the screen, again by PRINT_START, the starting tool's YMS required there."""
        m = macros(generator.generate("C235_DD_LW_04", catalog=CATALOG))
        self.assertIn("YMS_SCAN", m["delayed_gcode welcome"])
        start = m["gcode_macro PRINT_START"]
        scan = start.index("YMS_SCAN{% if params.TOOL is defined %} REQUIRE={params.TOOL|int + 1}")
        self.assertLess(start.index("SET_KINEMATIC_POSITION CLEAR=Z"), scan, "nozzle off the bed first")
        self.assertLess(start.index("CANCEL_PRINT_DEFAULT"), scan, "a refused file never moves")

    def test_resume_retries_what_paused_the_print_first(self):
        """RESUME runs the pending load / YMS check before priming or moving back, and moves
        nothing when the axes are not homed (a pause from PRINT_START, before G28)."""
        resume = macros(generator.generate("C235_DD_LW_04", catalog=CATALOG))["gcode_macro RESUME"]
        retry = resume.index("YUMI_RETRY_PENDING")
        self.assertLess(retry, resume.index("G1 X{x_park} F6000"))
        self.assertLess(retry, resume.index("G1 E{prime|float}"))
        self.assertIn("{% if homed %}\n        G1 X{x_park} F6000", resume)
        self.assertIn("{% if homed and printer.extruder.can_extrude", resume)
        self.assertLess(retry, resume.index("RESUME_BASE {get_params}"))

    def test_tip_sequence_is_one_source(self):
        m = macros(generator.generate("C235_DD_LW_04", catalog=CATALOG))
        tip = m["gcode_macro _YUMI_TIP"]
        self.assertIn("variable_cut_speed: 65", tip)
        self.assertIn("variable_cut_len: 10", tip)
        unload = m["gcode_macro YUMI_UNLOAD_TIP"]
        for step in ("done", "cut_len", "slow_len", "pull_len"):
            self.assertIn("G1 E-{%s}" % step, unload)
        self.assertIn("{% set done = 0.0 if params.SKIP_FIRST|default(0)|int == 1 else first_len %}",
                      unload)
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
        # the release check moved into _YUMI_UNLOAD_VERIFY: a macro's template is rendered BEFORE
        # its commands run, so read from inside the sequence the stub the cut is about to record
        # would still hold the previous cut's value. The chain has to stay unbroken.
        self.assertIn("_YUMI_UNLOAD_VERIFY", yms["gcode_macro YUMI_UNLOAD_TIP"])
        verify = yms["gcode_macro _YUMI_UNLOAD_VERIFY"]
        self.assertIn("YUMI_UNLOAD_CHECK", verify)
        self.assertIn("stub", verify, "it decides on the stub the cut just recorded")
        self.assertNotIn("t.stub", yms["gcode_macro YUMI_UNLOAD_TIP"],
                         "the sequence writes the stub (STUB= to the blade) but must never READ "
                         "it: at render time it still holds the previous cut's value")
        self.assertIn("YUMI_LOAD_TO_HEAD", yms["gcode_macro YUMI_TIP_TEST"])


class Comments(unittest.TestCase):
    def test_catalog_comments_reach_the_cfg(self):
        cfg = generator.generate("C235_DD_LW_04", catalog=CATALOG)
        self.assertIn("max_probe_times: 15  # taps before giving up", cfg)
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



class TipSizingArithmetic(unittest.TestCase):
    """Where the tip actually STOPS, by rendering the macro and adding up its E moves.

    The sizing path only works because the tip stops ABOVE the stuck zone, so the one number that
    has to be right is the position reached before the dwell. It was NOT right: the sizing retract
    read ``sizing_len - first_len + push_len`` and subtracted first_len even when the caller had
    not done that retract. CANCEL_PRINT is such a caller -- it retracts over the part itself and
    passes SKIP_FIRST=1 -- so there the tip stopped 7 mm short, at 23 mm, inside the 25 mm that
    stays welded to the walls. That is the two-piece tip this whole path exists to avoid, produced
    silently, with no error and nothing in the log. Text matching could not see it; only adding the
    moves up can, which is why this class renders instead of grepping.
    """

    PRODUCT = "C235_DD_LW_04"

    def _render(self, **params):
        import jinja2
        m = macros(generator.generate(self.PRODUCT, catalog=CATALOG))
        tip_vars = {}
        for line in m["gcode_macro _YUMI_TIP"].split("\n"):
            if line.startswith("variable_"):
                k, _, v = line[len("variable_"):].partition(":")
                tip_vars[k.strip()] = float(v.strip())
        body = m["gcode_macro YUMI_UNLOAD_TIP"].split("gcode:", 1)[1]

        def raise_error(msg):
            raise RuntimeError(msg)

        # Klipper's own delimiters (klippy/extras/gcode_macro.py): single braces, not {{ }}
        env = jinja2.Environment("{%", "%}", "{", "}")
        out = env.from_string(body).render(
            printer={"gcode_macro _YUMI_TIP": tip_vars},
            params={k: str(v) for k, v in params.items()},
            action_raise_error=raise_error,
        )
        # tip height above the nozzle = minus the cumulative E
        moves, cum = [], 0.0
        for line in out.split("\n"):
            line = line.strip()
            if line.startswith("G1 E"):
                cum += float(line.split("E", 1)[1].split()[0])
                moves.append(("move", -cum))
            elif line.startswith("G4 P"):
                moves.append(("dwell", -cum))
        return tip_vars, moves

    def _sizing_height(self, **params):
        tip_vars, moves = self._render(SIZE=1, **params)
        dwells = [h for kind, h in moves if kind == "dwell"]
        self.assertEqual(len(dwells), 1, "the sizing path holds the tip exactly once")
        return tip_vars, dwells[0], moves

    def test_the_tip_stops_at_sizing_len_whoever_did_the_first_retract(self):
        for skip in (0, 1):
            tip_vars, height, _ = self._sizing_height(SKIP_FIRST=skip)
            self.assertAlmostEqual(
                height, tip_vars["sizing_len"], places=6,
                msg="SKIP_FIRST=%d: the tip is held at %.1f mm, not at sizing_len=%.1f"
                    % (skip, height, tip_vars["sizing_len"]))

    def test_the_held_tip_is_clear_of_the_stuck_zone(self):
        for skip in (0, 1):
            tip_vars, height, _ = self._sizing_height(SKIP_FIRST=skip)
            self.assertGreater(
                height, tip_vars["stuck_len"],
                "SKIP_FIRST=%d: held at %.1f mm, inside the %.1f mm that stays welded -- this is "
                "the tip that snaps in two" % (skip, height, tip_vars["stuck_len"]))

    def test_the_cooling_moves_never_dip_back_into_the_stuck_zone(self):
        tip_vars, height, moves = self._sizing_height()
        stuck = tip_vars["stuck_len"]
        # everything from the moment the tip first clears the stuck zone up to the dwell
        after = []
        for kind, h in moves:
            if after or h > stuck:
                after.append(h)
            if kind == "dwell":
                break
        self.assertGreater(len(after), 1, "the cooling moves must show up in the rendered sequence")
        self.assertGreater(min(after), stuck,
                           "a cooling move brings the tip down to %.1f mm, inside the %.1f mm "
                           "stuck zone" % (min(after), stuck))

    def test_the_cooling_moves_are_a_round_trip(self):
        """They shear the thread; they must not move the tip, or they would undo the sizing."""
        tip_vars, with_moves, _ = self._sizing_height()
        _, without, _ = self._sizing_height(COOL_MOVES=0)
        self.assertAlmostEqual(with_moves, without, places=6,
                               msg="the cooling moves shifted the held tip by %.3f mm"
                                   % (with_moves - without))

    def test_a_tip_asked_for_inside_the_stuck_zone_is_refused_not_silently_made(self):
        tip_vars, _, _ = self._sizing_height()
        bad = tip_vars["stuck_len"] - 2
        with self.assertRaises(RuntimeError) as cm:
            self._render(SIZE=1, SIZING_LEN=bad)
        msg = str(cm.exception)
        self.assertIn("SIZING_LEN", msg, "the refusal must name the parameter at fault")
        self.assertIn(str(bad), msg, "the refusal must quote the value received")
        self.assertIn("docs/hotend-chromax-x12.md", msg, "the refusal must point at the drawing")

    def test_the_stub_handed_to_the_blade_is_what_the_blade_really_leaves(self):
        """The cut severs at blade_len, so what stays in the head is blade_len minus wherever the
        tip is at that moment. The load is told that number and pushes exactly it out through the
        nozzle, so a wrong one either leaves a plug behind or purges filament for nothing. It has
        to follow the geometry, not be typed in: both branches are checked, and both cut at a
        different height."""
        for params, expected_tip in ((dict(SIZE=1), "sizing_len"), (dict(), "legacy")):
            tip_vars, moves = self._render(**params)
            out_lines = self._cut_lines(**params)
            self.assertEqual(len(out_lines), 1, "exactly one cut per branch taken")
            stub = float(out_lines[0].split("STUB=")[1].split()[0])
            if expected_tip == "sizing_len":
                tip_at_cut = tip_vars["sizing_len"]
            else:
                tip_at_cut = tip_vars["first_len"] + tip_vars["cut_len"]
            self.assertAlmostEqual(
                stub, tip_vars["blade_len"] - tip_at_cut, places=6,
                msg="%s branch: told the load %.1f mm, but the blade leaves %.1f"
                    % (expected_tip, stub, tip_vars["blade_len"] - tip_at_cut))
            self.assertGreater(stub, 0., "a cut always leaves something below the blade")

    def _cut_lines(self, **params):
        import jinja2
        m = macros(generator.generate(self.PRODUCT, catalog=CATALOG))
        tip_vars = {}
        for line in m["gcode_macro _YUMI_TIP"].split("\n"):
            if line.startswith("variable_"):
                k, _, v = line[len("variable_"):].partition(":")
                tip_vars[k.strip()] = float(v.strip())
        body = m["gcode_macro YUMI_UNLOAD_TIP"].split("gcode:", 1)[1]
        env = jinja2.Environment("{%", "%}", "{", "}")
        out = env.from_string(body).render(
            printer={"gcode_macro _YUMI_TIP": tip_vars},
            params={k: str(v) for k, v in params.items()},
            action_raise_error=lambda m: (_ for _ in ()).throw(RuntimeError(m)))
        return [l.strip() for l in out.split("\n") if l.strip().startswith("YUMI_CUT_TIP")]

    def test_a_cut_ends_the_pulling_there(self):
        """slow_len + pull_len drag the tip out of the head and up into the PTFE -- 130 mm, for the
        NO-CUT path. After a cut at blade_len the filament is already above the heatsink, out of
        the hot zone, so pulling further buys nothing and only makes the reload longer. Nicolas,
        09/11, seeing the tip parked at 200 mm: "t'es monte super haut, je n'ai jamais demande de
        monter aussi haut... je dois du faire 70 plus 10." The clearance now lives in YUMI_CUT_TIP,
        which is the only place that knows whether the blade really fired."""
        tip_vars, _ = self._render()
        self.assertAlmostEqual(tip_vars["blade_len"] + tip_vars["cut_clear"], 155., places=6,
                               msg="the figure Nicolas set on camera: 150, where the tip just "
                                   "shows at the inlet grommet, plus 5 mm so that it shows on every "
                                   "inlet, the blue one included")
        for params, cut in ((dict(SIZE=1), True), (dict(), True),
                            (dict(SIZE=1, CUT=0), False), (dict(CUT=0), False)):
            text = self._rendered(**params)
            has_pull = "E-%g" % tip_vars["pull_len"] in text.replace(" ", "")
            self.assertEqual(has_pull, not cut,
                             "%s: the long no-cut pull must be %s"
                             % (params, "absent after a cut" if cut else "present without one"))

    def _rendered(self, **params):
        import jinja2
        m = macros(generator.generate(self.PRODUCT, catalog=CATALOG))
        tip_vars = {}
        for line in m["gcode_macro _YUMI_TIP"].split("\n"):
            if line.startswith("variable_"):
                k, _, v = line[len("variable_"):].partition(":")
                tip_vars[k.strip()] = float(v.strip())
        body = m["gcode_macro YUMI_UNLOAD_TIP"].split("gcode:", 1)[1]
        env = jinja2.Environment("{%", "%}", "{", "}")
        return env.from_string(body).render(
            printer={"gcode_macro _YUMI_TIP": tip_vars},
            params={k: str(v) for k, v in params.items()},
            action_raise_error=lambda m: (_ for _ in ()).throw(RuntimeError(m)))

    def test_the_blade_never_cuts_a_section_that_was_still_soft(self):
        """The section the blade cuts was, a moment earlier, at the height of the stub's length.
        CUT_LEN=40 put it at 23 mm, inside the 25 mm stuck zone: it pinched instead of severing,
        dragged the stub onto the head switch, and the load stalled (09/11). The default must stay
        clear of that, and a value that is not must be refused before anything moves."""
        tip_vars, _ = self._render()
        foot = tip_vars["first_len"] + tip_vars["cut_len"]
        stub = tip_vars["blade_len"] - foot
        self.assertLessEqual(foot, tip_vars["stuck_len"],
                             "the stub's foot at %.0f mm left the hot zone: it freezes swollen and "
                             "wedges (hotend blocked 09/11 at 30)" % foot)
        self.assertGreaterEqual(stub, tip_vars["stuck_len"] + 5,
                                "the default cuts a section from %.0f mm" % stub)
        for bad in (30, 40):
            with self.assertRaises(RuntimeError) as cm:
                self._render(CUT_LEN=bad)
        msg = str(cm.exception)
        self.assertIn("CUT_LEN=40", msg)
        self.assertIn("at most", msg, "the refusal says what is accepted")

    def test_a_cooling_stroke_that_would_dip_into_the_stuck_zone_is_refused(self):
        tip_vars, _, _ = self._sizing_height()
        too_long = tip_vars["sizing_len"] - tip_vars["stuck_len"] + 1
        with self.assertRaises(RuntimeError) as cm:
            self._render(SIZE=1, COOL_LEN=too_long)
        msg = str(cm.exception)
        self.assertIn("COOL_LEN", msg)
        self.assertIn("COOL_MOVES=0", msg, "the refusal must say how to proceed anyway")



class InsertingFilamentFeedsTheRightYms(unittest.TestCase):
    """Inserting filament into a YMS outside a print must feed THAT YMS and nothing else.

    It used to go through T{i+1}, because outside a print T<k>'s else branch doubles as
    "insertion into YMS-k" -- but only T0..T{N-1} are generated, so the LAST YMS of every machine
    called a macro that does not exist. Measured on the C335 on 09/11: filament inserted into
    YMS-2 printed "Unknown command T2", and LOAD_YMS fed its 50 mm to YMS-1, which happened to be
    synced. With nothing synced the feed goes to the fictitious extruder and the insertion
    silently does nothing -- on the last YMS of every multi-YMS printer in the fleet.
    """

    PRODUCTS = ("C335_CX12_LW_04_2YMS", "C235_CX12_LW_04_7YMS")

    def _inserts(self, product):
        cfg = generator.generate(product, catalog=CATALOG)
        blocks = re.findall(r"\ninsert_gcode:\n((?:[ \t]+.*\n)+)", cfg)
        return cfg, blocks

    def test_no_insertion_calls_a_tool_macro_that_does_not_exist(self):
        for product in self.PRODUCTS:
            cfg, blocks = self._inserts(product)
            defined = set(re.findall(r"^\[gcode_macro (T\d+)\]", cfg, re.M))
            for b in blocks:
                called = set(re.findall(r"^\s+(T\d+)\s*$", b, re.M))
                self.assertTrue(called <= defined, "%s: insertion calls %s, only %s exist"
                                % (product, sorted(called - defined), sorted(defined)))

    def test_outside_a_print_each_insertion_syncs_its_own_pusher_alone(self):
        for product in self.PRODUCTS:
            _, blocks = self._inserts(product)
            n_yms = len(blocks)
            self.assertGreater(n_yms, 1, product)
            for i, b in enumerate(blocks):
                outside = b.split("{% else %}", 1)[1].split("{% endif %}", 1)[0]
                self.assertIn("SYNC_EXTRUDER_MOTION EXTRUDER=extruder%d MOTION_QUEUE=extruder" % i,
                              outside, "%s YMS-%d: its own pusher is not synced" % (product, i + 1))
                for j in range(n_yms):
                    if j != i:
                        self.assertIn('SYNC_EXTRUDER_MOTION EXTRUDER=extruder%d MOTION_QUEUE=""' % j,
                                      outside, "%s YMS-%d: extruder%d left synced, it would move "
                                      "too" % (product, i + 1, j))
                self.assertIn("LOAD_YMS YMS=%d" % (i + 1), b,
                              "%s YMS-%d: LOAD_YMS must be told which YMS it feeds" % (product, i + 1))

    def _load_yms(self, n, pusher):
        import jinja2
        m = macros(generator.generate("C335_CX12_LW_04_2YMS", catalog=CATALOG))
        body = m["gcode_macro LOAD_YMS"].split("gcode:", 1)[1]
        env = jinja2.Environment("{%", "%}", "{", "}")

        def raise_error(msg):
            raise RuntimeError(msg)

        printer = {"yumi_filament_head": {"pusher": pusher},
                   "save_variables": {"variables": {"printing_start": False}}}

        class P(dict):
            def __getattr__(self, k):
                v = self[k]
                return P(v) if isinstance(v, dict) else v
        return env.from_string(body).render(printer=P(printer), params={"YMS": str(n)},
                                            action_raise_error=raise_error)

    def test_load_yms_feeds_when_its_own_pusher_alone_is_synced(self):
        out = self._load_yms(2, "extruder1")
        self.assertIn("G1 E50", out)

    def test_load_yms_refuses_another_yms_pusher(self):
        with self.assertRaises(RuntimeError) as cm:
            self._load_yms(2, "extruder0")
        msg = str(cm.exception)
        self.assertIn("extruder1", msg, "the refusal names the pusher it needs")
        self.assertIn("extruder0", msg, "and the one it found")

    def test_load_yms_refuses_two_pushers_at_once(self):
        with self.assertRaises(RuntimeError):
            self._load_yms(2, "extruder0,extruder1")

    def test_load_yms_refuses_the_fictitious_extruder(self):
        with self.assertRaises(RuntimeError):
            self._load_yms(2, "")



class TheCutRecordsWhereItsTipWaits(unittest.TestCase):
    """The load reads park_<pusher> to go straight to the switch instead of searching step by
    step. The record has to be written by the cut that parks the tip, with the same two values the
    unload uses, and only for a single synced pusher."""

    def test_yumi_cut_tip_records_the_park_of_the_synced_pusher(self):
        m = macros(generator.generate("C335_CX12_LW_04_2YMS", catalog=CATALOG))
        body = m["gcode_macro YUMI_CUT_TIP"]
        self.assertIn("SAVE_VARIABLE VARIABLE=park_{pusher} VALUE={t.blade_len + t.cut_clear}", body)
        self.assertIn('"," not in pusher', body, "never with two pushers synced")
        self.assertLess(body.index("CUT_FILAMENT"), body.index("park_{pusher}"),
                        "recorded only once the blade has actually fired")


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
