"""The systemd units of generator/ — the path to autoconfig.py is rendered by install.sh.

klipper.service's drop-in used %h: for a system unit systemd expands it to the MANAGER's home,
/root, whatever User= says. autoconfig --boot then failed at every Klipper start (permission
denied, silently, the '-' prefix) and printer.cfg was never regenerated after YUMI_SETUP, the
Printer Config panel or a recipe change.
"""
import re
import unittest
from pathlib import Path

GENERATOR = Path(__file__).resolve().parent.parent
INSTALL_SH = GENERATOR.parent / "install.sh"
UNITS = ("klipper-autoconfig.conf", "yumi-autoconfig.service")
PLACEHOLDER = "@PROJECT_DIR@"


class Units(unittest.TestCase):
    def exec_lines(self, text):
        return [l for l in text.splitlines() if re.match(r"(ExecStart|ExecStartPre|ConditionPathExists)=", l)]

    def test_no_systemd_specifier_in_paths(self):
        for name in UNITS:
            for line in self.exec_lines((GENERATOR / name).read_text()):
                self.assertNotIn("%", line, "%s: systemd specifier in %r" % (name, line))
                self.assertIn(PLACEHOLDER + "/generator/autoconfig.py", line, "%s: %r" % (name, line))

    def test_install_sh_renders_the_placeholder_for_every_unit(self):
        sh = INSTALL_SH.read_text()
        self.assertIn('sed "s#%s#$PROJECT_DIR#g"' % PLACEHOLDER, sh)
        for name in UNITS:
            self.assertRegex(sh, r'install_unit "\$PROJECT_DIR/generator/%s"' % re.escape(name), name)
        self.assertNotRegex(sh, r'cp "\$PROJECT_DIR/generator/(%s)"' % "|".join(map(re.escape, UNITS)),
                            "a unit copied verbatim keeps the placeholder")


if __name__ == "__main__":
    unittest.main()
