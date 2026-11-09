"""yumi_filament_head — the load to the head switch, replayed on Klipper stubs.

The switch is a button (live state); the feed is queued straight into the toolhead in `step` mm
moves and stops within one step after the switch sees the filament. Klipper's drip_move drops the
E axis, so there is no MCU-stopped continuous extruder move: this quasi-continuous stepping is the
mechanism, and its overshoot bound is what these tests pin down.
"""
import importlib.util
import types
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
MODULE = HERE.parent.parent / "klipper" / "klippy" / "extras" / "yumi_filament_head.py"


def load_module():
    spec = importlib.util.spec_from_file_location("yumi_filament_head_under_test", MODULE)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class Config:
    def __init__(self, printer, values):
        self.printer, self.values = printer, values

    def get_printer(self):
        return self.printer

    def get(self, key, default=None):
        return self.values.get(key, default)

    def getfloat(self, key, default=None, **kw):
        v = self.values.get(key, default)
        return None if v is None else float(v)


class Gcmd:
    def __init__(self, **params):
        self.params = {k.upper(): v for k, v in params.items()}
        self.messages = []

    def get(self, name, default=None, **kw):
        return self.params.get(name, default)

    def get_float(self, name, default=None, **kw):
        v = self.params.get(name, default)
        return None if v is None else float(v)

    def get_int(self, name, default=None, **kw):
        v = self.params.get(name, default)
        return None if v is None else int(v)

    def respond_info(self, msg):
        self.messages.append(msg)

    def get_commandline(self):
        return "YUMI_LOAD_TO_HEAD " + " ".join("%s=%s" % kv for kv in self.params.items())

    def error(self, msg):
        return CommandError(msg)


class CommandError(Exception):
    pass


class Feeder:
    """A YMS motion sensor as Klipper publishes it: filament_detected stays True while the encoder
    ticks (the YMS has filament), and drops once the extruder travelled detection_length mm
    without a tick. Starts True, like a sensor left armed by MOTION_SENSOR_INIT."""
    def __init__(self, has_filament, detection_length=50.):
        self.has_filament = has_filament
        self.detection_length = detection_length
        self.detected = True
        self.last_tick = 0.

    def moved_to(self, e_pos):
        if self.has_filament:
            self.last_tick = e_pos
            self.detected = True
        elif e_pos - self.last_tick >= self.detection_length:
            self.detected = False

    def get_status(self, eventtime):
        return {"filament_detected": self.detected, "enabled": True}


class Printer:
    """Toolhead whose E position is what the switch reacts to: filament seen at E >= switch_at."""
    def __init__(self, switch_at=47.):
        self.switch_at = switch_at
        self.feeders = {}
        self.pos = [0., 0., 0., 0.]
        self.moves = []
        self.scripts = []
        self.variables = {}
        self.button_callback = None
        self.handlers = {}
        self.command_error = CommandError
        self.reactor = types.SimpleNamespace(monotonic=lambda: 0., pause=lambda t: None)
        self.pushers = {}   # 'extruder0' -> the extruder it is synced to, None = synced to nothing
        toolhead = types.SimpleNamespace(manual_move=self._manual_move, wait_moves=lambda: None,
                                         get_position=lambda: list(self.pos),
                                         get_extruder=lambda: types.SimpleNamespace(
                                             get_name=lambda: "extruder"))
        gcode = types.SimpleNamespace(register_command=lambda *a, **k: None,
                                      run_script_from_command=self.scripts.append,
                                      respond_raw=self.scripts.append)
        self.objects = {
            "toolhead": toolhead, "gcode": gcode,
            "gcode_move": types.SimpleNamespace(reset_last_position=lambda: self.scripts.append("<resync>")),
            "save_variables": types.SimpleNamespace(allVariables=self.variables),
        }

    def with_tip(self, stub=None, **variables):
        """A _YUMI_TIP macro the module can read, as SET_GCODE_VARIABLE would leave it. `stub` goes
        to variables.cfg instead, which is where the blade records it so it survives a restart."""
        self.objects["gcode_macro _YUMI_TIP"] = types.SimpleNamespace(variables=dict(variables))
        if stub is not None:
            self.variables["head_stub"] = stub
        return self

    def with_hotend(self, temperature):
        self.objects["extruder"] = types.SimpleNamespace(
            get_heater=lambda: types.SimpleNamespace(
                get_status=lambda t: {"temperature": temperature}))
        return self

    def _manual_move(self, coord, speed):
        for i, v in enumerate(coord):
            if v is not None:
                self.pos[i] = v
        self.moves.append((round(self.pos[3], 3), speed))
        for f in self.feeders.values():
            f.moved_to(self.pos[3])
        self.button_callback(0., 1 if self.pos[3] >= self.switch_at else 0)

    def lookup_objects(self, module=None):
        """Klipper's filter: every object, or those named `module` / `module <name>`."""
        objs = (list(self.objects.items())
                + [("filament_motion_sensor " + n, f) for n, f in self.feeders.items()]
                + [("extruder_stepper " + n, types.SimpleNamespace(motion_queue=q))
                   for n, q in self.pushers.items()])
        return [(n, o) for n, o in objs if module is None or n == module or n.startswith(module + " ")]

    def lookup_object(self, name, default=KeyError):
        if name in self.objects:
            return self.objects[name]
        if default is KeyError:
            raise KeyError(name)
        return default

    def load_object(self, config, name):
        assert name == "buttons"
        return types.SimpleNamespace(register_buttons=self._register)

    def _register(self, pins, callback):
        self.button_callback = callback

    def get_reactor(self):
        return self.reactor

    def register_event_handler(self, name, cb):
        self.handlers.setdefault(name, []).append(cb)


def head(switch_at=47., **config):
    mod = load_module()
    printer = Printer(switch_at)
    values = {"pin": "^!PA8", "speed": 16.7, "step": 5, "settle": 0}
    values.update(config)
    h = mod.YumiFilamentHead(Config(printer, values))
    for cb in printer.handlers.get("klippy:ready", []):
        cb()
    return h, printer


class LoadToHead(unittest.TestCase):
    def test_stops_within_one_step_after_the_switch(self):
        h, p = head(switch_at=47.)
        g = Gcmd()
        h.cmd_LOAD(g)
        self.assertEqual(p.pos[3], 50., "47 mm to the switch, 5 mm steps: stops at 50")
        self.assertTrue(h.present)
        self.assertEqual(h.loaded_mm, 50.)
        self.assertIn("reached the head after 50 mm", g.messages[-1])
        self.assertEqual(p.scripts[-2:], ["<resync>", "M117 Filament loaded"], "g-code E resynced after the feed")

    def test_preload_is_one_move_then_steps(self):
        h, p = head(switch_at=47.)
        h.cmd_LOAD(Gcmd(PRELOAD=40))
        self.assertEqual([m[0] for m in p.moves], [40., 45., 50.])

    def test_step_is_the_call_parameter_or_the_default(self):
        """Declared on the call = a longer step for that call; not declared = the config default.
        Nothing is stored (Nicolas, 07/09)."""
        h, p = head(switch_at=47.)
        self.assertEqual(h.load_step(Gcmd()), 5.)
        self.assertEqual(h.load_step(Gcmd(STEP=20)), 20.)
        self.assertEqual(h.load_step(Gcmd()), 5., "the parameter does not stick")
        p.variables["load_step"] = 40
        self.assertEqual(h.load_step(Gcmd()), 5., "no saved variable is ever read")

    def test_already_at_the_head_does_not_move(self):
        h, p = head(switch_at=0.)
        p.button_callback(0., 1)
        g = Gcmd()
        h.cmd_LOAD(g)
        self.assertEqual(p.moves, [])
        self.assertIn("already at the head", g.messages[0])

    def test_no_filament_after_max(self):
        h, p = head(switch_at=10000.)
        with self.assertRaises(CommandError) as cm:
            h.cmd_LOAD(Gcmd(MAX=30))
        self.assertIn("No filament at the head after 30 mm", str(cm.exception))
        self.assertEqual(p.pos[3], 30.)

    def test_bypass_loads_blind_without_reading(self):
        h, p = head(switch_at=10000.)
        h.bypass = True
        g = Gcmd()
        h.cmd_LOAD(g)
        self.assertIn("BYPASSED", g.messages[0])
        self.assertEqual(p.moves, [], "no _YUMI_TIP macro in the stub: blind length 0, nothing fed")


class FeedingYms(unittest.TestCase):
    """SENSOR= names the YMS that feeds: its encoder must tick, or the load gives up early —
    Nicolas 07/09: 'tu vois bien que le motion sensor ne tourne pas, tu devrais détecter qu'il
    n'y a pas de filament et t'arrêter' (a T3 on an empty YMS fed 2 x 2500 mm, 10 minutes)."""
    def test_empty_yms_stops_after_the_check_length_not_max_load(self):
        h, p = head(switch_at=10000.)
        p.feeders["YMS-3"] = Feeder(has_filament=False, detection_length=50.)
        with self.assertRaises(CommandError) as cm:
            h.cmd_LOAD(Gcmd(SENSOR="YMS-3", MAX=2500))
        self.assertIn("No filament coming from YMS-3", str(cm.exception))
        self.assertEqual(p.pos[3], 60., "detection_length 50 + two 5 mm steps, not 2500")

    def test_a_failed_load_during_a_print_pauses_it_and_resume_retries(self):
        """C335 08/11: the YMS cable was unplugged and the print was lost. During a print the
        failure is an alert + PAUSE, the load kept for RESUME (YUMI_RETRY_PENDING)."""
        h, p = head(switch_at=10000.)
        p.feeders["YMS-3"] = Feeder(has_filament=False, detection_length=50.)
        state = {"state": "printing"}
        p.objects["print_stats"] = types.SimpleNamespace(get_status=lambda t: state)
        h.cmd_LOAD(Gcmd(SENSOR="YMS-3"))  # no exception: the print is not stopped
        self.assertTrue(any(str(x).startswith("!! No filament coming from YMS-3") for x in p.scripts))
        self.assertEqual(p.scripts[-1], "PAUSE")
        self.assertTrue(h.get_status(0.)["pending"])
        state["state"] = "paused"
        h.cmd_RETRY_PENDING(Gcmd())
        self.assertEqual(p.scripts[-1], "YUMI_LOAD_TO_HEAD SENSOR=YMS-3", "RESUME runs the same load again")
        self.assertFalse(h.get_status(0.)["pending"])

    def test_yms_with_filament_loads_to_the_head_as_before(self):
        h, p = head(switch_at=147.)
        p.feeders["YMS-3"] = Feeder(has_filament=True)
        g = Gcmd(SENSOR="YMS-3")
        h.cmd_LOAD(g)
        self.assertEqual(p.pos[3], 150.)
        self.assertIn("reached the head after 150 mm", g.messages[-1])

    def test_without_sensor_only_the_head_switch_is_watched(self):
        h, p = head(switch_at=10000.)
        p.feeders["YMS-3"] = Feeder(has_filament=False)
        with self.assertRaises(CommandError) as cm:
            h.cmd_LOAD(Gcmd(MAX=200))
        self.assertIn("No filament at the head after 200 mm", str(cm.exception))
        self.assertEqual(p.pos[3], 200.)

    def test_unknown_sensor_name_is_an_error_not_a_silent_skip(self):
        h, p = head(switch_at=47.)
        with self.assertRaises(CommandError) as cm:
            h.cmd_LOAD(Gcmd(SENSOR="YMS-9"))
        self.assertIn("SENSOR=YMS-9", str(cm.exception))
        self.assertEqual(p.moves, [])

    def test_feeder_check_option_overrides_the_derived_length(self):
        h, p = head(switch_at=10000., feeder_check=30)
        p.feeders["YMS-2"] = Feeder(has_filament=False, detection_length=7.)
        with self.assertRaises(CommandError):
            h.cmd_LOAD(Gcmd(SENSOR="YMS-2"))
        self.assertEqual(p.pos[3], 30.)

    def test_preload_on_an_empty_yms_stops_right_after_the_preload(self):
        h, p = head(switch_at=10000.)
        p.feeders["YMS-3"] = Feeder(has_filament=False, detection_length=50.)
        with self.assertRaises(CommandError) as cm:
            h.cmd_LOAD(Gcmd(SENSOR="YMS-3", PRELOAD=100))
        self.assertIn("No filament coming from YMS-3", str(cm.exception))
        self.assertEqual(p.pos[3], 100., "the known part is fed in one move, then the verdict")

    def test_bypassed_head_sensor_still_catches_an_empty_yms(self):
        h, p = head(switch_at=10000., blind_load=200)
        h.bypass = True
        p.feeders["YMS-3"] = Feeder(has_filament=False, detection_length=50.)
        with self.assertRaises(CommandError) as cm:
            h.cmd_LOAD(Gcmd(SENSOR="YMS-3"))
        self.assertIn("No filament coming from YMS-3", str(cm.exception))
        self.assertEqual(p.pos[3], 60.)

    def test_bypassed_head_sensor_feeds_the_blind_length_when_the_yms_ticks(self):
        h, p = head(switch_at=10000., blind_load=200)
        h.bypass = True
        p.feeders["YMS-3"] = Feeder(has_filament=True)
        h.cmd_LOAD(Gcmd(SENSOR="YMS-3"))
        self.assertEqual(p.pos[3], 200.)
        self.assertEqual(h.loaded_mm, 200.)


class UnloadCheck(unittest.TestCase):
    def test_pulls_until_the_switch_releases(self):
        h, p = head(switch_at=30.)
        p.pos[3] = 60.
        p.button_callback(0., 1)
        g = Gcmd()
        h.cmd_UNLOAD_CHECK(g)
        self.assertFalse(h.present)
        self.assertLess(p.pos[3], 30.)
        self.assertGreaterEqual(p.pos[3], 25., "released within one 5 mm step below the switch")
        self.assertIn("out of the head after", g.messages[-1])

    def test_nothing_to_do_when_already_released(self):
        h, p = head(switch_at=30.)
        g = Gcmd()
        h.cmd_UNLOAD_CHECK(g)
        self.assertEqual(p.moves, [])
        self.assertIn("out of the head", g.messages[0])



class APusherHasToBeSyncedOrNothingMoves(unittest.TestCase):
    """On a ChromaX12 an E move turns nothing unless T<n> synced a YMS pusher first.

    [extruder] there is a fictitious motor on free pins; the pushers are the YMS steppers, a metre
    of tube away. T<n>'s else branch -- the one that runs OUTSIDE a print -- deliberately syncs
    none of them. So a sequence launched by hand drives the phantom: the g-code E axis advances
    exactly as commanded, every move reports success, and not one millimetre of filament moves.

    Measured on the C335 on 09/11: a full unload ran that way. 347 mm of commanded retraction, a
    blade stroke fired on filament that was still whole, and the only visible symptom was a head
    sensor that would not release -- which sent the whole investigation after the wrong cause for
    half an hour. The lesson is not "sync first", it is that this must REFUSE instead of pretending.
    """

    def _head(self, pushers):
        h, p = head()
        p.pushers = dict(pushers)
        return h, p

    def test_no_synced_pusher_refuses_instead_of_feeding_the_phantom(self):
        h, p = self._head({"extruder0": None, "extruder1": None})
        with self.assertRaises(CommandError) as cm:
            h.cmd_LOAD(Gcmd())
        msg = str(cm.exception)
        self.assertIn("No pusher is synced", msg)
        self.assertIn("extruder", msg, "the refusal names the extruder it found active")
        self.assertIn("T<n>", msg, "and how to fix it in the normal case")
        self.assertIn("SYNC_EXTRUDER_MOTION", msg, "and how to fix it by hand, outside a print")
        self.assertEqual(p.moves, [], "not a single move may be queued")

    def test_a_synced_pusher_lets_the_load_through(self):
        h, p = self._head({"extruder0": "extruder", "extruder1": None})
        h.cmd_LOAD(Gcmd())
        self.assertTrue(p.moves, "with a pusher synced the load feeds normally")

    def test_the_unload_check_refuses_too_rather_than_pulling_on_nothing(self):
        h, p = self._head({"extruder0": None})
        with self.assertRaises(CommandError) as cm:
            h.cmd_UNLOAD_CHECK(Gcmd())
        self.assertIn("No pusher is synced", str(cm.exception))
        self.assertEqual(p.moves, [], "it must not report 'unload failed' after pulling on air")

    def test_the_synced_pusher_is_published_for_the_macros(self):
        """YUMI_UNLOAD_TIP drives E itself, so it needs to read this before retracting."""
        h, _ = self._head({"extruder0": None, "extruder1": None})
        self.assertEqual(h.get_status(0.)["pusher"], "")
        h, _ = self._head({"extruder0": "extruder", "extruder1": None})
        self.assertEqual(h.get_status(0.)["pusher"], "extruder0")

    def test_a_direct_drive_is_never_refused(self):
        """No extruder_stepper at all: [extruder] is the real motor and always moves."""
        h, p = self._head({})
        self.assertEqual(h.get_status(0.)["pusher"], "extruder")
        h.cmd_LOAD(Gcmd())
        self.assertTrue(p.moves)



class TheLoadCurrentIsWeakOnPurpose(unittest.TestCase):
    """Loading pushes filament down a tube; the run current is sized for retractions and for
    pushing melt through the nozzle. Nicolas 09/11: "il n'y a pas besoin d'avoir de force. Si il y
    a une contrainte, il ne faut pas qu'on bouffe le filament." A pusher too weak for an
    obstruction skips and the load reports a failure; one strong enough grinds a flat into the
    filament and from then on nothing feeds at all. CURRENT= exists because finding the lowest
    current that still pulls a full metre of tube is a sweep, and a config option costs a restart
    per value."""

    def _head(self, **cfg):
        h, p = head(**cfg)
        p.pushers = {"extruder0": "extruder", "extruder1": None}
        p.objects["tmc2209 extruder_stepper extruder0"] = types.SimpleNamespace(
            get_status=lambda t: {"run_current": 1.2, "hold_current": 0.3})
        return h, p

    def _currents(self, scripts):
        return [l for l in scripts if isinstance(l, str) and l.startswith("SET_TMC_CURRENT")]

    def test_the_configured_current_is_applied_then_put_back(self):
        h, p = self._head(load_current=0.6)
        h.cmd_LOAD(Gcmd())
        c = self._currents(p.scripts)
        self.assertEqual(len(c), 2, "exactly one lowering and one restore")
        self.assertIn("CURRENT=0.600", c[0])
        self.assertIn("STEPPER=extruder0", c[0])
        self.assertIn("CURRENT=1.200", c[1], "the run current must come back, not the weak one")

    def test_the_prime_after_the_switch_is_gentle_too(self):
        """Once the switch has seen the filament, push gently: the prime runs on the weak current as
        well (a forced load jammed the blue on 09/11). Two windows, descent then prime, each put
        back to the run current."""
        h, p = self._head(load_current=0.4)
        p.with_hotend(235.).with_tip(purge_speed=3.7)
        h.cmd_LOAD(Gcmd(HEAD_TO_NOZZLE="78"))
        c = self._currents(p.scripts)
        self.assertEqual(len(c), 4, "lower+restore for the descent, then for the prime: %r" % c)
        self.assertIn("CURRENT=0.400", c[0]); self.assertIn("CURRENT=1.200", c[1])
        self.assertIn("CURRENT=0.400", c[2]); self.assertIn("CURRENT=1.200", c[3])

    def test_a_prime_without_a_flow_limit_is_refused_and_says_how_to_set_it(self):
        """The prime speed is purge_speed of _YUMI_TIP, the flow limit the purges obey too. Without
        it there is no safe speed to guess (5 mm/s made the pusher skip on 09/11)."""
        h, p = self._head(load_current=0.4)
        p.with_hotend(235.)
        with self.assertRaises(Exception) as e:
            h.cmd_LOAD(Gcmd(HEAD_TO_NOZZLE="78"))
        self.assertIn("SET_GCODE_VARIABLE MACRO=_YUMI_TIP VARIABLE=purge_speed", str(e.exception))
        self.assertIn("CURRENT=1.200", self._currents(p.scripts)[-1],
                      "the run current comes back even when the prime is refused")

    def test_only_the_synced_pusher_is_touched(self):
        h, p = self._head(load_current=0.6)
        h.cmd_LOAD(Gcmd())
        self.assertNotIn("extruder1", " ".join(self._currents(p.scripts)),
                         "an unsynced pusher does not move, so its current is none of our business")

    def test_current_on_the_call_wins_over_the_config(self):
        h, p = self._head(load_current=0.6)
        h.cmd_LOAD(Gcmd(CURRENT="0.4"))
        self.assertIn("CURRENT=0.400", self._currents(p.scripts)[0])

    def test_zero_leaves_the_current_alone(self):
        h, p = self._head(load_current=0.6)
        h.cmd_LOAD(Gcmd(CURRENT="0"))
        self.assertEqual(self._currents(p.scripts), [],
                         "0 means do not touch, not set the driver to zero amps")

    def test_unconfigured_leaves_the_current_alone(self):
        h, p = self._head()
        h.cmd_LOAD(Gcmd())
        self.assertEqual(self._currents(p.scripts), [])

    def test_the_current_is_restored_even_when_the_load_fails(self):
        """The restore is the whole point: a machine left at 0.6 A would under-extrude silently."""
        h, p = self._head(load_current=0.6, max_load=10)
        p.switch_at = 10000.          # the switch never triggers
        with self.assertRaises(CommandError):
            h.cmd_LOAD(Gcmd())
        c = self._currents(p.scripts)
        self.assertIn("CURRENT=1.200", c[-1], "the run current comes back on the failure path too")



class TheBladeLeavesAStubAndTheLoadOwnsIt(unittest.TestCase):
    """A cut at 70 mm leaves everything below it in the head; the next load expels it through the
    nozzle on its mandatory prime (see AColourChangeLoadsThroughAFreeSwitch). Where the machine
    leaves that stub, under the switch, the switch is FREE.

    A switch that reads filament while a stub is booked is therefore not a case to force but the
    symptom of a failed cut. Measured on the C335 on 09/11: a cut through a section that was still
    soft pinched instead of severing, the stub rode up with the filament onto the switch, and the
    blind feed this case used to take pushed 250 mm at full current against it. So it is refused,
    and nothing moves.
    """

    def _head(self, stub, switch_present):
        h, p = head()
        p.pushers = {"extruder0": "extruder"}
        p.with_tip(stub=stub, head_to_nozzle=78., blade_len=70., cut_clear=85.).with_hotend(215.)
        if switch_present:
            p.button_callback(0., 1)
        return h, p

    def test_a_held_switch_with_a_stub_booked_is_refused_not_forced(self):
        h, p = self._head(53., switch_present=True)
        with self.assertRaises(CommandError) as cm:
            h.cmd_LOAD(Gcmd())
        msg = str(cm.exception)
        self.assertIn("cut most likely failed", msg, "it says what the held switch means")
        self.assertIn("SAVE_VARIABLE VARIABLE=head_stub VALUE=0", msg, "and how to clear the books")
        self.assertEqual(p.moves, [], "not a millimetre is fed")
        self.assertNotIn("SAVE_VARIABLE VARIABLE=head_stub VALUE=0", p.scripts,
                         "the stub is still there: the books must say so")

    def test_no_stub_leaves_the_normal_path_alone(self):
        h, p = self._head(0., switch_present=False)
        h.cmd_LOAD(Gcmd())
        self.assertNotIn("SAVE_VARIABLE VARIABLE=head_stub VALUE=0", p.scripts)
        self.assertGreater(h.get_status(0.)["loaded_mm"], 0.)

    def test_a_macro_variable_is_not_where_the_stub_lives(self):
        """It has to survive a restart: a gcode variable lives in RAM, filament in the head does
        not care about restarts."""
        h, p = head()
        p.pushers = {"extruder0": "extruder"}
        p.with_hotend(215.)
        p.objects["gcode_macro _YUMI_TIP"] = types.SimpleNamespace(
            variables={"stub": 53., "head_to_nozzle": 78.})
        self.assertEqual(h.get_status(0.)["stub"], 0.)

    def test_the_stub_is_published(self):
        h, _ = self._head(53., switch_present=False)
        self.assertEqual(h.get_status(0.)["stub"], 53.)


class AColourChangeLoadsThroughAFreeSwitch(unittest.TestCase):
    """The blind feed assumed the filament coming back is the one just cut, parked at 150 mm. A
    colour change brings ANOTHER filament from another YMS, possibly a metre away: 150 mm blind
    would stop far short. But where the machine leaves the stub, its top at blade_len, under the
    switch, the switch is FREE -- it read False after every clean cut on 09/11. So the arriving
    filament, whichever YMS it comes from, stops at the switch, and the head_to_nozzle prime then
    brings it to the nozzle and expels the stub on the same travel: verified, not blind."""

    SEQ = dict(first_len=7., cut_len=10., slow_len=20., pull_len=110., head_to_nozzle=78.,
               blade_len=70., cut_clear=80., purge_speed=3.7)
    STUB = 53.

    def _head(self, switch_at, temperature=215.):
        h, p = head(switch_at=switch_at, speed=16.7)
        p.pushers = {"extruder0": "extruder"}
        p.with_tip(stub=self.STUB, **self.SEQ).with_hotend(temperature)
        return h, p

    def test_another_filament_far_back_still_reaches_the_nozzle(self):
        h, p = self._head(switch_at=600.)
        g = Gcmd()
        h.cmd_LOAD(g)
        self.assertNotIn("blind", " ".join(g.messages), "a free switch must be used, not bypassed")
        loaded = h.get_status(0.)["loaded_mm"]
        self.assertGreaterEqual(loaded, 600. + self.SEQ["head_to_nozzle"] - 1e-6,
                                "fed %.1f mm: it stopped before the switch or skipped the prime"
                                % loaded)
        self.assertIn("SAVE_VARIABLE VARIABLE=head_stub VALUE=0", p.scripts)

    def test_the_prime_that_expels_the_stub_runs_at_nozzle_speed(self):
        h, p = self._head(switch_at=600.)
        h.cmd_LOAD(Gcmd())
        last = p.moves[-1]
        self.assertEqual(last[1], self.SEQ["purge_speed"],
                         "the stub goes through the orifice: purge_speed of _YUMI_TIP")

    def test_the_cut_filament_coming_back_is_verified_too(self):
        """Parked at 150 mm, it meets the switch at 78 after ~72 mm, then primes 78."""
        h, p = self._head(switch_at=72.)
        g = Gcmd()
        h.cmd_LOAD(g)
        self.assertNotIn("blind", " ".join(g.messages))
        self.assertAlmostEqual(h.get_status(0.)["loaded_mm"], 75. + 78., delta=0.01,
                               msg="to the switch within one step, then the full prime")

    def test_an_unreadable_hotend_temperature_is_not_hot_enough(self):
        h, p = head(switch_at=72.)
        p.pushers = {"extruder0": "extruder"}
        p.with_tip(stub=self.STUB, **self.SEQ)          # no hotend object: unreadable
        with self.assertRaises(CommandError) as cm:
            h.cmd_LOAD(Gcmd())
        self.assertIn("cannot be read", str(cm.exception))

    def test_a_cold_hotend_stops_before_the_prime_and_keeps_the_books(self):
        h, p = self._head(switch_at=72., temperature=150.)
        with self.assertRaises(CommandError) as cm:
            h.cmd_LOAD(Gcmd())
        self.assertIn("blocks the head", str(cm.exception))
        self.assertNotIn("SAVE_VARIABLE VARIABLE=head_stub VALUE=0", p.scripts,
                         "the stub is still there: the books must say so")



class AKnownParkIsReachedInOneMove(unittest.TestCase):
    """After a cut the machine knows where this pusher's tip waits (park_<pusher>, 155 mm) and where
    the switch is (78 mm): the stretch between them has nothing to detect. The stepped search reads
    the switch every step and each read stops the motion -- 950 mm took 137 s for a commanded
    16.7 mm/s on 09/11 -- so that stretch goes in one move and only the end is searched. Nicolas:
    "le but dans ce changement de fil, c'est d'aller vite"."""

    TIP = dict(head_to_nozzle=78., blade_len=70., cut_clear=85., purge_speed=3.7)

    def _head(self, park, switch_after, stub=0., slack=None):
        h, p = head(switch_at=switch_after, speed=16.7, step=5, switch_speed=2.)
        p.pushers = {"extruder0": "extruder"}
        p.with_tip(stub=stub, **self.TIP).with_hotend(215.)
        if park:
            p.variables["park_extruder0"] = park
        if slack is not None:
            p.variables["slack_extruder0"] = slack
        return h, p

    def test_the_known_stretch_is_one_move_then_the_end_is_searched(self):
        h, p = self._head(park=155., switch_after=77.)
        h.cmd_LOAD(Gcmd())
        self.assertEqual(p.moves[0], (72.0, 16.7),
                         "155 - 78 - 5 = 72 mm in a single move, got %r" % (p.moves[:3],))
        self.assertAlmostEqual(h.get_status(0.)["loaded_mm"], 77., delta=0.01,
                               msg="the switch is found to the millimetre")
        self.assertIn("SAVE_VARIABLE VARIABLE=park_extruder0 VALUE=0", p.scripts,
                      "the record is consumed: the tip is no longer parked")

    def test_the_last_millimetres_before_the_switch_are_gentle(self):
        """The switch is a tiny plastic micro-switch (Nicolas, 09/11: "c'est hyper fragile"): from
        5 mm before it, 1 mm steps at switch_speed until it triggers."""
        h, p = self._head(park=155., switch_after=77.)
        h.cmd_LOAD(Gcmd())
        search = [m for m in p.moves if 72. < m[0] <= 77. + 1e-6]
        self.assertEqual(search, [(73., 2.), (74., 2.), (75., 2.), (76., 2.), (77., 2.)])

    def test_the_switch_is_passed_gently_then_the_prime_speeds_up(self):
        """"tu le passes et apres tu peux reaccelerer": 5 mm at switch_speed, then purge_speed."""
        h, p = self._head(park=155., switch_after=77., stub=53.)
        h.cmd_LOAD(Gcmd())
        after = [m for m in p.moves if m[0] > 77. + 1e-6]
        self.assertEqual(after, [(82., 2.), (155., self.TIP["purge_speed"])])

    def test_the_real_distance_is_measured_and_learned_per_pusher(self):
        """The filament takes up slack in the tube: from 155 the switch came after 87 mm on
        YMS-1 on 09/11, not 77. The measured difference is kept for this pusher."""
        h, p = self._head(park=155., switch_after=87.)
        h.cmd_LOAD(Gcmd())
        self.assertIn("SAVE_VARIABLE VARIABLE=slack_extruder0 VALUE=10.0", p.scripts)

    def test_a_learned_slack_moves_the_fast_stretch(self):
        h, p = self._head(park=155., switch_after=87., slack=10.)
        h.cmd_LOAD(Gcmd())
        self.assertEqual(p.moves[0], (82.0, 16.7), "77 + 10 - 5 in one move: %r" % (p.moves[:2],))
        self.assertEqual(len([m for m in p.moves if 82. < m[0] <= 87. + 1e-6]), 5,
                         "then the same five gentle millimetres")

    def test_a_tip_moved_by_hand_is_not_learned(self):
        """Learning a 145 mm error would send the next fast move straight into the switch."""
        h, p = self._head(park=155., switch_after=222.)
        h.cmd_LOAD(Gcmd())
        self.assertFalse([s for s in p.scripts if isinstance(s, str) and "slack_" in s])

    def test_a_wrong_record_only_costs_time(self):
        """The tip was pulled further back by hand: the stepped search simply goes on."""
        h, p = self._head(park=155., switch_after=300.)
        h.cmd_LOAD(Gcmd())
        self.assertGreaterEqual(h.get_status(0.)["loaded_mm"], 300.)

    def test_without_a_record_there_is_no_big_move(self):
        h, p = self._head(park=0., switch_after=77.)
        h.cmd_LOAD(Gcmd())
        steps = [b[0] - a[0] for a, b in zip([(0., 0.)] + p.moves, p.moves)]
        self.assertTrue(all(x <= 5. + 1e-6 for x in steps),
                        "a fresh feeder is searched step by step from the start: %r" % steps[:4])

    def test_the_whole_prime_runs_at_nozzle_speed(self):
        """The bore under the stub is not empty: melt stays stuck to the walls over the first
        25 mm. A fast first stretch on the opposite premise pushed hard into that residue and
        stalled the pusher on 09/11 ("tu pousses super fort alors que tu es deja en train
        d'extruder")."""
        h, p = self._head(park=0., switch_after=10., stub=53.)
        h.cmd_LOAD(Gcmd())
        at_switch = h.get_status(0.)["loaded_mm"] - 78.
        prime = [sp for pos, sp in p.moves if pos > at_switch + 5. + 1e-6]
        self.assertEqual(set(prime), {self.TIP["purge_speed"]},
                         "past the switch, the prime pushes melt: purge_speed of _YUMI_TIP only, "
                         "got %r" % sorted(set(prime)))


if __name__ == "__main__":
    unittest.main()
