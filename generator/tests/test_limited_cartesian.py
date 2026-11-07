"""klipper/klippy/kinematics/limited_cartesian.py: X / Y caps the way Z is capped.

C335 08/11: the heavy Y bed skipped belt teeth at 500 mm/s and 20000 mm/s^2; X keeps 20000 for
the pop tool. Each move is limited by its share on a capped axis."""
import importlib.util
import math
import sys
import types
import unittest
from pathlib import Path

MODULE = Path(__file__).resolve().parents[2] / "klipper" / "klippy" / "kinematics" / "limited_cartesian.py"


class CartKinematics:  # stand-in for Klipper's: its own checks are not under test here
    def __init__(self, toolhead, config):
        pass

    def check_move(self, move):
        pass


def load():
    pkg = types.ModuleType("kinematics")
    pkg.__path__ = []
    pkg.cartesian = types.SimpleNamespace(CartKinematics=CartKinematics)
    sys.modules["kinematics"] = pkg
    sys.modules["kinematics.cartesian"] = pkg.cartesian
    spec = importlib.util.spec_from_file_location("kinematics.limited_cartesian", MODULE)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class Config:
    def __init__(self, **values):
        self.values = values

    def getfloat(self, name, default, above=None, maxval=None):
        v = self.values.get(name, default)
        assert maxval is None or v <= maxval
        return v


class Move:
    def __init__(self, dx, dy):
        self.axes_d = [dx, dy, 0., 0.]
        self.move_d = math.hypot(dx, dy)
        self.velocity, self.accel = 700., 20000.

    def limit_speed(self, speed, accel):
        self.velocity = min(self.velocity, speed)
        self.accel = min(self.accel, accel)


class LimitedCartesian(unittest.TestCase):
    def setUp(self):
        toolhead = types.SimpleNamespace(get_max_velocity=lambda: (700., 20000.))
        self.kin = load().load_kinematics(toolhead, Config(max_y_accel=4000.))

    def check(self, dx, dy):
        m = Move(dx, dy)
        self.kin.check_move(m)
        return m

    def test_pure_x_keeps_the_toolhead_limits(self):
        m = self.check(60., 0.)
        self.assertEqual((m.velocity, m.accel), (700., 20000.))

    def test_pure_y_is_capped(self):
        self.assertEqual(self.check(0., -334.).accel, 4000.)

    def test_a_diagonal_is_capped_by_its_y_share(self):
        m = self.check(-61., -334.)  # the start G-code's travel to the prime line
        y_accel = m.accel * 334. / m.move_d
        self.assertAlmostEqual(y_accel, 4000., places=6)


if __name__ == "__main__":
    unittest.main()
