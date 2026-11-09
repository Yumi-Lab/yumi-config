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
    """A cut at 70 mm leaves everything below it in the head, and only the next load can take it
    out -- the pusher is upstream of the blade and has no grip on that piece, and the upstream
    filament could only push it by going back down into the melt, which would destroy the cold
    blade-cut tip the cut exists to produce.

    What makes that load special is that the piece SITS AT THE HEAD SWITCH and holds it. So
    `filament already at the head` is true of the stub and says nothing about the filament arriving
    behind it. Measured on the C335 on 09/11: the load read the switch, fed 0 mm, primed 78 mm into
    filament still far up the tube, announced it had pushed the stub out, and cleared the books.
    Nothing had moved and the books lied -- worse than the plug.

    The short-circuit stays for the case it was written for: a colour change with no cut and
    nothing withdrawn, where the filament really is at the head (Nicolas: "quand t'as qu'un
    filament, y'a pas de problème, c'est le même que tu rentres et que tu sors").
    """

    SEQ = dict(first_len=7., cut_len=10., slow_len=20., pull_len=110., head_to_nozzle=78.,
               blade_len=70., cut_clear=10.)
    STUB = 53.
    # the blade fires at 70 and the only pull that follows is the short clearance, so the upstream
    # tip waits at 70 + 10 = 80 -- NOT at the tip-shaping total, which belongs to the no-cut path
    WAITS_AT = 80.

    def _head(self, stub, temperature=215., **cfg):
        h, p = head(**cfg)
        p.pushers = {"extruder0": "extruder"}
        p.with_tip(stub=stub, **self.SEQ)
        if temperature is not None:
            p.with_hotend(temperature)
        return h, p

    def test_the_switch_is_not_believed_while_a_stub_holds_it(self):
        h, p = self._head(self.STUB)
        p.switch_at = -1.          # the switch reads present from the very start, as the stub does
        h.cmd_LOAD(Gcmd())
        self.assertAlmostEqual(h.get_status(0.)["loaded_mm"], self.WAITS_AT, places=3,
                               msg="fed %.1f mm: the load believed the switch and moved nothing"
                                   % h.get_status(0.)["loaded_mm"])

    def test_the_distance_is_the_blade_plus_the_clearance_and_nothing_else(self):
        """Not the tip-shaping total. slow_len and pull_len exist to drag the tip out of the head
        on the NO-CUT path -- 130 mm of it. After a cut the filament is already out of the hot zone
        at blade_len, so only the short clearance follows, and the reload has to feed exactly that
        sum back. Nicolas, 09/11, seeing the tip parked at 200 mm: it has to be 70 plus 10."""
        h, p = self._head(self.STUB)
        h.cmd_LOAD(Gcmd())
        self.assertAlmostEqual(h.get_status(0.)["loaded_mm"],
                               self.SEQ["blade_len"] + self.SEQ["cut_clear"], places=3)
        total = sum(self.SEQ[k] for k in ('first_len', 'cut_len', 'slow_len', 'pull_len'))
        self.assertNotAlmostEqual(h.get_status(0.)["loaded_mm"], total + self.STUB, places=3,
                                  msg="the no-cut lengths must not leak into the cut reload")

    def test_the_books_are_cleared_once_it_is_out(self):
        h, p = self._head(self.STUB)
        h.cmd_LOAD(Gcmd())
        self.assertIn("SAVE_VARIABLE VARIABLE=head_stub VALUE=0", p.scripts,
                      "a stub still on the books would be fed out again at the next load")

    def test_it_says_it_is_blind(self):
        """A blind feed assumes nobody pulled the filament further back by hand, so it has to say
        so rather than report a verified load."""
        h, _ = self._head(self.STUB)
        g = Gcmd()
        h.cmd_LOAD(g)
        said = " ".join(g.messages)
        self.assertIn("blind", said, "the operator has to know this load was not verified")
        self.assertIn("53", said, "and how much is being pushed out")

    def test_a_cold_hotend_refuses_and_says_what_cold_does(self):
        h, p = self._head(self.STUB, temperature=155.)
        with self.assertRaises(CommandError) as cm:
            h.cmd_LOAD(Gcmd())
        msg = str(cm.exception)
        self.assertIn("155", msg, "the refusal quotes the temperature it found")
        self.assertIn("53", msg, "and how much has to come out")
        self.assertIn("blocks the head", msg, "and what happens if it is pushed cold")
        self.assertEqual(p.moves, [], "not a millimetre may be fed")

    def test_an_unreadable_temperature_is_not_hot_enough(self):
        """Unknown must refuse here: pushing a stub cold is a measured way to block the head."""
        h, p = self._head(self.STUB, temperature=None)
        with self.assertRaises(CommandError) as cm:
            h.cmd_LOAD(Gcmd())
        self.assertIn("unreadable", str(cm.exception))
        self.assertEqual(p.moves, [])

    def test_a_stub_with_no_known_distance_refuses_rather_than_leaving_it_in(self):
        h, p = head()
        p.pushers = {"extruder0": "extruder"}
        p.with_tip(stub=self.STUB).with_hotend(215.)      # no sequence lengths: nothing to go on
        with self.assertRaises(CommandError) as cm:
            h.cmd_LOAD(Gcmd())
        msg = str(cm.exception)
        self.assertIn("MAX=", msg, "the refusal names the way to supply the distance")
        self.assertIn("head_stub VALUE=0", msg, "and how to clear the books if it was taken out")

    def test_the_call_can_supply_the_distance_itself(self):
        h, p = head()
        p.pushers = {"extruder0": "extruder"}
        p.with_tip(stub=self.STUB).with_hotend(215.)
        h.cmd_LOAD(Gcmd(MAX="200"))
        self.assertAlmostEqual(h.get_status(0.)["loaded_mm"], 200., places=3)

    def test_no_stub_leaves_the_normal_path_alone(self):
        """The short-circuit and the prime are for the colour change with no cut."""
        h, p = self._head(0.)
        h.cmd_LOAD(Gcmd())
        self.assertNotIn("SAVE_VARIABLE VARIABLE=head_stub VALUE=0", p.scripts)
        self.assertGreater(h.get_status(0.)["loaded_mm"], 0.)

    def test_a_macro_variable_is_not_where_the_stub_lives(self):
        """It has to survive a restart. A gcode variable does not: it lives in RAM and a restart
        resets it, while filament left in the head survives one perfectly well -- and the next
        load would then push fresh filament onto a plug it no longer knew about."""
        h, p = head()
        p.pushers = {"extruder0": "extruder"}
        p.with_hotend(215.)
        p.objects["gcode_macro _YUMI_TIP"] = types.SimpleNamespace(
            variables=dict(self.SEQ, stub=self.STUB))     # a macro variable, nothing persisted
        self.assertEqual(h.get_status(0.)["stub"], 0.,
                         "a macro variable must not be taken for the record")
        h.cmd_LOAD(Gcmd())
        self.assertNotIn("SAVE_VARIABLE VARIABLE=head_stub VALUE=0", p.scripts)

    def test_the_stub_is_published(self):
        h, _ = self._head(self.STUB)
        self.assertEqual(h.get_status(0.)["stub"], self.STUB)


if __name__ == "__main__":
    unittest.main()
