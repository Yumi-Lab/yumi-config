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
        toolhead = types.SimpleNamespace(manual_move=self._manual_move, wait_moves=lambda: None,
                                         get_position=lambda: list(self.pos))
        gcode = types.SimpleNamespace(register_command=lambda *a, **k: None,
                                      run_script_from_command=self.scripts.append)
        self.objects = {
            "toolhead": toolhead, "gcode": gcode,
            "gcode_move": types.SimpleNamespace(reset_last_position=lambda: self.scripts.append("<resync>")),
            "save_variables": types.SimpleNamespace(allVariables=self.variables),
        }

    def _manual_move(self, coord, speed):
        for i, v in enumerate(coord):
            if v is not None:
                self.pos[i] = v
        self.moves.append((round(self.pos[3], 3), speed))
        for f in self.feeders.values():
            f.moved_to(self.pos[3])
        self.button_callback(0., 1 if self.pos[3] >= self.switch_at else 0)

    def lookup_objects(self):
        return list(self.objects.items()) + [("filament_motion_sensor " + n, f) for n, f in self.feeders.items()]

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


if __name__ == "__main__":
    unittest.main()
