import os
import sys
import tempfile
import types
import unittest
from unittest import mock

QC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _wizard():
    """qc_wizard without GTK nor KlipperScreen: only its module-level exit path is exercised."""
    gi = types.ModuleType("gi")
    gi.require_version = lambda *a: None
    repo = types.ModuleType("gi.repository")
    for name in ("Gdk", "Gtk", "GLib", "Pango"):
        setattr(repo, name, mock.MagicMock())
    panel = types.ModuleType("ks_includes.screen_panel")
    panel.ScreenPanel = object
    stubs = {"gi": gi, "gi.repository": repo, "ks_includes": types.ModuleType("ks_includes"),
             "ks_includes.screen_panel": panel}
    with mock.patch.dict(sys.modules, stubs):
        if QC_DIR not in sys.path:
            sys.path.insert(0, QC_DIR)
        sys.modules.pop("qc_wizard", None)
        import qc_wizard
        return qc_wizard


class TestExitQcMode(unittest.TestCase):
    def setUp(self):
        self.wiz = _wizard()
        self.dir = tempfile.mkdtemp()
        self.prod = os.path.join(self.dir, "printer.cfg")
        self.backup = os.path.join(self.dir, "printer.cfg.qc-backup")
        self.aside = os.path.join(self.dir, "printer.cfg.qc-exit")
        patches = {"PROD_CFG": self.prod, "BACKUP_CFG": self.backup, "QC_EXIT_CFG": self.aside}
        for k, v in patches.items():
            p = mock.patch.object(self.wiz, k, v)
            p.start()
            self.addCleanup(p.stop)
        self.screen = mock.MagicMock()

    def _write(self, path, text):
        with open(path, "w") as f:
            f.write(text)

    def _read(self, path):
        with open(path) as f:
            return f.read()

    def test_backup_put_back(self):
        self._write(self.prod, "[gcode_macro _QC_MODE]\n")
        self._write(self.backup, "[printer]\nkinematics: cartesian\n")
        self.wiz.exit_qc_mode(self.screen)
        self.assertEqual(self._read(self.prod), "[printer]\nkinematics: cartesian\n")
        self.assertFalse(os.path.exists(self.backup))
        self.screen._ws.send_method.assert_called_once_with("machine.services.restart", {"service": "klipper"})

    def test_qc_cfg_without_backup_set_aside(self):
        self._write(self.prod, "[gcode_macro _QC_MODE]\n")
        self.wiz.exit_qc_mode(self.screen)
        self.assertFalse(os.path.exists(self.prod))
        self.assertEqual(self._read(self.aside), "[gcode_macro _QC_MODE]\n")

    def test_production_cfg_without_backup_untouched(self):
        """15/11: a second Autoconfig press on the C235 (DD cfg back, no backup any more) would
        have moved the production cfg aside; it only restarts the service."""
        self._write(self.prod, "[printer]\nkinematics: cartesian\n")
        self.wiz.exit_qc_mode(self.screen)
        self.assertEqual(self._read(self.prod), "[printer]\nkinematics: cartesian\n")
        self.assertFalse(os.path.exists(self.aside))
        self.screen._ws.send_method.assert_called_once_with("machine.services.restart", {"service": "klipper"})


if __name__ == "__main__":
    unittest.main()
