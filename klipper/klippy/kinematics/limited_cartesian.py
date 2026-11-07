# Cartesian kinematics with a velocity and an acceleration cap on X and on Y, the way stock
# Klipper caps Z (max_z_velocity / max_z_accel). Option names follow Kalico's
# limited_cartesian: max_x_velocity, max_y_velocity, max_x_accel, max_y_accel (default: the
# toolhead's max_velocity / max_accel, i.e. no cap).
#
# Why (C335 08/11): Y carries the bed on the C-series. The heavy C335 plate skipped belt teeth
# on a start-G-code travel at 500 mm/s and 20000 mm/s^2, which X takes fine and needs for the
# pop-tool scrape. Stock Klipper has one max_accel for X and Y; capping it for the bed's sake
# would cap X and the slicer too. Each move is limited by the share of it that runs on a capped
# axis: a pure X move keeps the full toolhead limits.
#
# Symlinked into klipper/klippy/kinematics/ by yumi-config install.sh (an addition, Klipper's
# tree stays clean). Selected per machine by the generator: `kinematics: limited_cartesian`.
from . import cartesian


class LimitedCartKinematics(cartesian.CartKinematics):
    def __init__(self, toolhead, config):
        cartesian.CartKinematics.__init__(self, toolhead, config)
        max_velocity, max_accel = toolhead.get_max_velocity()
        self.axis_caps = []
        for i, axis in enumerate("xy"):
            velocity = config.getfloat('max_%s_velocity' % axis, max_velocity,
                                       above=0., maxval=max_velocity)
            accel = config.getfloat('max_%s_accel' % axis, max_accel,
                                    above=0., maxval=max_accel)
            self.axis_caps.append((i, velocity, accel))

    def check_move(self, move):
        cartesian.CartKinematics.check_move(self, move)
        for i, velocity, accel in self.axis_caps:
            if move.axes_d[i]:
                ratio = move.move_d / abs(move.axes_d[i])
                move.limit_speed(velocity * ratio, accel * ratio)


def load_kinematics(toolhead, config):
    return LimitedCartKinematics(toolhead, config)
