"""Error screen Menu > Autoconfig (Nicolas 15/11): a QC cfg whose board does not answer (HyperDrive
unplugged during a HyperDrive QC) leaves Klipper in error and the main menu is gone. One button
leaves QC mode and lets the machine detection rebuild printer.cfg."""
import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gtk  # noqa: E402

from ks_includes.screen_panel import ScreenPanel  # noqa: E402
from panels.qc_wizard import exit_qc_mode  # noqa: E402


class Panel(ScreenPanel):
    def __init__(self, screen, title):
        super().__init__(screen, title)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10, vexpand=True)
        box.pack_start(Gtk.Label(label="退出QC模式，重新识别机器\nLeave QC mode and detect the machine again",
                                 wrap=True), False, False, 10)
        button = self._gtk.Button("refresh", "自动配置 / Autoconfig", "color3")
        button.connect("clicked", lambda widget: exit_qc_mode(self._screen))
        box.pack_start(button, True, True, 0)
        self.content.add(box)
