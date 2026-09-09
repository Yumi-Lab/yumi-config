"""filament_yumi_smart_motion_sensor — presence contract replayed on Klipper stubs.

The bench sequence that broke (C235, 7 YMS, 2026-09-06): filament inserted into YMS-2 → its
encoder ticks → insert_gcode → T2 + LOAD_YMS (50 mm push, ticking) → T0 → MOTION_SENSOR_INIT
(50 mm of fictive extruder travel, no feeder synced, no tick) → "INSERT NEXT FILAMENT".
The re-arm move ends less than 2 s after the last tick; a 5 s grace kept the sensor "present",
so the next insertion into the same YMS produced no False→True transition and never loaded.
"""
import importlib
import sys
import types
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
EXTRAS = HERE.parent.parent / "klipper" / "klippy" / "extras"
PACKAGE = "yumi_extras_under_test"


class RunoutHelperStub:
    """Klipper's RunoutHelper, reduced to what matters: the state and its transitions."""
    def __init__(self, config):
        self.filament_present = False
        self.transitions = []

    def note_filament_present(self, eventtime, is_filament_present):
        if is_filament_present == self.filament_present:
            return
        self.filament_present = is_filament_present
        self.transitions.append((round(eventtime, 3), is_filament_present))

    def get_status(self, eventtime):
        return {"filament_detected": self.filament_present}


def load_module():
    pkg = types.ModuleType(PACKAGE)
    pkg.__path__ = [str(EXTRAS)]
    fss = types.ModuleType(PACKAGE + ".filament_switch_sensor")
    fss.RunoutHelper = RunoutHelperStub
    sys.modules[PACKAGE] = pkg
    sys.modules[PACKAGE + ".filament_switch_sensor"] = fss
    return importlib.import_module(PACKAGE + ".filament_yumi_smart_motion_sensor")


class Reactor:
    NOW = 0.
    NEVER = 9999999999999999.

    def __init__(self):
        self.now = 0.
        self.timers = []

    def monotonic(self):
        return self.now

    def register_timer(self, callback, waketime=NEVER):
        timer = [callback, waketime]
        self.timers.append(timer)
        return timer

    def update_timer(self, timer, waketime):
        timer[1] = waketime

    def register_callback(self, callback, waketime=NOW):
        callback(self.now)

    def run_timers(self, now):
        """Advance the clock and fire every armed timer once (the 250 ms check)."""
        self.now = now
        for timer in self.timers:
            if timer[1] != self.NEVER:
                timer[1] = timer[0](now)


class Config:
    def __init__(self, printer, values):
        self.printer, self.values = printer, values

    def get_printer(self):
        return self.printer

    def get_name(self):
        return "filament_yumi_smart_motion_sensor YMS-2"

    def get(self, key, default=None):
        return self.values.get(key, default)

    def getfloat(self, key, default=None, **kw):
        v = self.values.get(key, default)
        return None if v is None else float(v)

    def getboolean(self, key, default=None):
        return bool(self.values.get(key, default))

    def getint(self, key, default=None, **kw):
        return int(self.values.get(key, default))

    def error(self, msg):
        return Exception(msg)


class FeederStub:
    """Klipper's ExtruderStepper: motion_queue names the extruder it is synced to, or None."""
    def __init__(self, printer):
        self.printer = printer

    @property
    def motion_queue(self):
        return self.printer.motor_queue


class Printer:
    """Extruder positions are a step function of print time (= event time here), so a tick can
    tell whether the extruder moved within the last MOTION_WINDOW like Klipper's step history."""
    def __init__(self):
        self.reactor = Reactor()
        self.handlers = {}
        self.samples = [(-1e9, 0.)]           # (time, extruder position)
        self.state = "Ready"                  # idle_timeout state
        self.motor_queue = None               # motion_queue of [extruder_stepper extruder1]
        self.button_callback = None
        extruder = types.SimpleNamespace(find_past_position=self.find_past_position)
        mcu = types.SimpleNamespace(estimated_print_time=lambda eventtime: eventtime)
        gcode = types.SimpleNamespace(respond_info=lambda *a, **k: None, respond_raw=lambda *a, **k: None)
        idle = types.SimpleNamespace(get_status=lambda eventtime: {"state": self.state})
        motor = types.SimpleNamespace(extruder_stepper=FeederStub(self))
        self.objects = {"extruder": extruder, "mcu": mcu, "gcode": gcode, "idle_timeout": idle,
                        "extruder_stepper extruder1": motor}

    def set_pos(self, t, pos):
        self.samples.append((t, pos))

    def find_past_position(self, print_time):
        return [p for t, p in self.samples if t <= print_time][-1]

    def get_reactor(self):
        return self.reactor

    def load_object(self, config, name):
        assert name == "buttons"
        return types.SimpleNamespace(register_buttons=self._register_buttons)

    def _register_buttons(self, pins, callback):
        self.button_callback = callback

    def register_event_handler(self, name, callback):
        self.handlers.setdefault(name, []).append(callback)

    def lookup_object(self, name, default=KeyError):
        if name in self.objects:
            return self.objects[name]
        if default is KeyError:
            raise KeyError(name)
        return default

    def fire(self, name, *args):
        for cb in self.handlers.get(name, []):
            cb(*args)


class Bench:
    """One YMS sensor on a printer whose extruder we move by hand."""
    DETECTION_LENGTH = 50.

    def __init__(self, mode="free"):
        self.mod = load_module()
        self.printer = Printer()
        self.sensor = self.mod.FilamentYumiSmartMotionSensor(Config(self.printer, {
            "switch_pin": "PC13", "extruder": "extruder", "motor": "extruder1", "mode": mode,
            "detection_length": self.DETECTION_LENGTH, "blockage_detection": True}))
        self.helper = self.sensor.runout_helper
        self.printer.fire("klippy:ready")

    def sync_motor(self, synced):
        """T macros: SYNC_EXTRUDER_MOTION EXTRUDER=extruder1 MOTION_QUEUE=extruder / \"\" """
        self.printer.motor_queue = "extruder" if synced else None

    def tick(self, t, pos):
        self.printer.reactor.now = t
        self.printer.set_pos(t, pos)
        self.printer.button_callback(t, 1)

    def move_without_ticks(self, t, pos):
        self.printer.set_pos(t, pos)
        self.printer.reactor.run_timers(t)

    def toolhead_starts(self, t):
        self.printer.reactor.now = t
        self.printer.state = "Printing"
        self.printer.fire("idle_timeout:printing", t)

    def toolhead_stops(self, t):
        self.printer.reactor.now = t
        self.printer.state = "Ready"
        self.printer.fire("idle_timeout:ready", t)

    def push_with_ticks(self, t0, t1, pos0, pos1, pitch=2.):
        """A feeder pushing filament through its own encoder: a tick every `pitch` mm."""
        n = int((pos1 - pos0) / pitch)
        for i in range(1, n + 1):
            self.tick(t0 + (t1 - t0) * i / n, pos0 + pitch * i)


class InsertionFlow(unittest.TestCase):
    def test_second_insertion_into_the_same_yms_is_seen(self):
        b = Bench()
        # first insertion at idle: the encoder ticks → present → insert_gcode
        b.tick(100.0, 0.)
        self.assertEqual(b.helper.transitions, [(100.0, True)])
        # LOAD_YMS: 50 mm push at 1000 mm/min, the YMS ticks all along, its feeder synced (T2)
        b.toolhead_starts(100.1)
        b.sync_motor(True)
        b.push_with_ticks(100.1, 103.1, 0., 50.)
        b.move_without_ticks(103.2, 50.)
        self.assertTrue(b.helper.filament_present)
        # T0 → MOTION_SENSOR_INIT: 50 mm of extruder travel with no feeder synced (0.75 s),
        # the toolhead stops 0.5 s later — all of it within 2 s of the last tick
        b.sync_motor(False)
        b.move_without_ticks(103.9, 100.)
        b.toolhead_stops(104.4)
        self.assertFalse(b.helper.filament_present,
                         "the re-arm move must clear the presence even right after the last tick")
        # second insertion into the same YMS: a real False→True transition again
        b.tick(110.0, 100.)
        self.assertEqual(b.helper.transitions[-1], (110.0, True))
        self.assertEqual([p for _t, p in b.helper.transitions], [True, False, True])

    def test_another_yms_loading_clears_an_idle_yms(self):
        b = Bench()
        b.tick(100.0, 0.)                    # inserted long ago
        b.toolhead_stops(101.0)
        # much later another YMS loads: the shared extruder travels 100 mm, this one is silent
        b.toolhead_starts(500.0)
        b.move_without_ticks(500.5, 20.)
        self.assertTrue(b.helper.filament_present, "under the threshold: still present")
        b.move_without_ticks(503.0, 100.)
        self.assertFalse(b.helper.filament_present)
        b.toolhead_stops(504.0)
        self.assertFalse(b.helper.filament_present)

    def test_final_check_when_the_toolhead_stops(self):
        """The threshold crossed by the very last move, no timer tick after it."""
        b = Bench()
        b.tick(100.0, 0.)
        b.toolhead_starts(100.1)
        b.printer.set_pos(100.5, 60.)          # crossed, but no timer fired since
        b.toolhead_stops(100.9)
        self.assertFalse(b.helper.filament_present)

    def test_a_standing_extruder_never_asserts_presence(self):
        """Presence is the encoder's alone: an empty YMS stays empty while the extruder is still."""
        b = Bench()
        b.toolhead_starts(10.0)
        for t in (10.25, 10.5, 20.0):
            b.move_without_ticks(t, 0.)
        b.toolhead_stops(21.0)
        self.assertFalse(b.helper.filament_present)
        self.assertEqual(b.helper.transitions, [])

    def test_ticks_at_rest_before_the_toolhead_settles_are_not_an_insertion(self):
        """Bench 2026-09-06, second failure: once the load sequence ended and M18 released the
        motors, the encoder ticked (filament relaxing) while idle_timeout was still Printing.
        Those ticks re-armed "present" and the next insertion into the same YMS was never seen."""
        b = Bench()
        b.tick(100.0, 0.)
        b.toolhead_starts(100.1)
        b.sync_motor(True)
        b.push_with_ticks(100.1, 103.1, 0., 50.)
        b.sync_motor(False)
        b.move_without_ticks(103.9, 100.)         # MOTION_SENSOR_INIT done, extruder at rest now
        for t in (104.05, 104.2, 104.35):          # M18 → the filament settles back: ticks at rest
            b.tick(t, 100.)
        b.toolhead_stops(104.6)
        self.assertFalse(b.helper.filament_present, "settling ticks must not look like an insertion")
        b.tick(110.0, 100.)                        # the real insertion, toolhead idle
        self.assertEqual([p for _t, p in b.helper.transitions], [True, False, True])

    def test_ticks_while_another_feeder_pushes_are_a_disturbance(self):
        """The extruder moves for YMS-1's load (this YMS's motor not synced): a tick here is
        drag, vibration or noise, not filament fed by this YMS — it must not set "present"."""
        b = Bench()
        b.tick(100.0, 0.)
        b.toolhead_starts(100.1)
        b.sync_motor(False)
        b.move_without_ticks(102.0, 30.)
        b.tick(102.1, 32.)                         # spurious tick during another feeder's push
        b.move_without_ticks(104.0, 100.)
        b.toolhead_stops(104.6)
        self.assertFalse(b.helper.filament_present)
        self.assertEqual(b.sensor.last_tick_pos, 0., "an ignored tick must not move the reference")

    def test_without_a_declared_motor_any_tick_while_moving_is_feeding(self):
        b = Bench()
        b.sensor.motor = None
        b.toolhead_starts(100.0)
        b.push_with_ticks(100.0, 101.0, 0., 10.)
        self.assertTrue(b.helper.filament_present)

    def test_hold_mode_ticks_refresh_the_threshold_too(self):
        b = Bench(mode="hold")
        b.tick(100.0, 0.)
        b.toolhead_starts(100.1)
        b.sync_motor(True)
        b.push_with_ticks(100.1, 103.1, 0., 50.)
        self.assertTrue(b.helper.filament_present)
        b.move_without_ticks(103.5, 80.)      # 30 mm since the last tick: present
        self.assertTrue(b.helper.filament_present)
        b.move_without_ticks(104.0, 100.)     # 50 mm: absent
        self.assertFalse(b.helper.filament_present)


if __name__ == "__main__":
    unittest.main()
