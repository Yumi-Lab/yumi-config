import os
import re
import unittest

CFG = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "qc_printer_YMS12.cfg")
HEAT_POSITIONS = (3, 4, 5, 8, 9, 10)


def _section(cfg, header):
    """Texte d'une section [header] jusqu'au prochain en-tete de section (ou la fin
    du fichier -- le delayed_gcode de demarrage des ventilos est la derniere section)."""
    m = re.search(r"^\[%s\]\n(.*?)(?=^\[|\Z)" % re.escape(header), cfg, re.M | re.S)
    assert m, "section [%s] absente" % header
    return m.group(1)


class TestYms12HeatConfig(unittest.TestCase):
    """Garde-fous sur la cfg GENEREE du banc (16/09/2026) : ventilos des
    plateaux chauffants coupes pendant TOUTE la chauffe QC et verdict sur le
    pic de temperature -- les deux causes des FAIL de chauffe vus sur les
    bancs .108/.109 (heater_fan qui soufflait des 25C ; boitier a 85.6C
    echantillonne a 82.9C au tick final)."""

    @classmethod
    def setUpClass(cls):
        with open(CFG, encoding="utf-8") as f:
            cls.cfg = f.read()

    def test_yms_fans_are_generic_not_heater_fans(self):
        for p in HEAT_POSITIONS:
            self.assertIn("[fan_generic YMS-%d-fan]" % p, self.cfg)
            self.assertNotIn("[heater_fan YMS-%d-fan]" % p, self.cfg)

    def test_fans_run_at_boot_for_the_plug_in_check(self):
        # Regle Nicolas 16/09 : brancher un YMS = son ventilo tourne (controle
        # fonctionnel) ; la coupure ne vaut que pendant la chauffe du QC.
        boot = _section(self.cfg, "delayed_gcode _qc_heat_fans_boot")
        self.assertIn("initial_duration:", boot)
        self.assertIn("_QC_HEAT_FANS_ON", boot)
        fans_on = _section(self.cfg, "gcode_macro _QC_HEAT_FANS_ON")
        for p in HEAT_POSITIONS:
            self.assertIn("SET_FAN_SPEED FAN=YMS-%d-fan SPEED=1.0" % p, fans_on)

    def test_load_all_does_not_switch_fans_back_on(self):
        # Jusqu'au 16/09 QC_LOAD_ALL rallumait les ventilos juste apres
        # QC_HEAT_START : ils soufflaient pendant tout le chargement + stress.
        self.assertNotIn("_QC_HEAT_FANS_ON", _section(self.cfg, "gcode_macro QC_LOAD_ALL"))

    def test_heat_start_and_wait_cut_the_fans(self):
        for macro in ("gcode_macro QC_HEAT_START", "gcode_macro QC_HEAT_WAIT"):
            self.assertIn("SET_FAN_SPEED FAN=YMS-{t}-fan SPEED=0", _section(self.cfg, macro), macro)

    def test_wait_resets_peaks_and_uses_360s(self):
        wait = _section(self.cfg, "gcode_macro QC_HEAT_WAIT")
        self.assertIn("VARIABLE=peak_{t} VALUE=0", wait)
        self.assertIn("default(360)", wait)

    def test_state_macro_has_one_peak_variable_per_heat_position(self):
        state = _section(self.cfg, "gcode_macro _QC_HEAT_ALL_STEP")
        for p in HEAT_POSITIONS:
            self.assertIn("variable_peak_%d: 0.0" % p, state)

    def test_step_decides_on_peak_and_switches_fans_on_after_verdict(self):
        step = _section(self.cfg, "delayed_gcode _qc_heat_all_step")
        self.assertIn('[v["peak_" ~ t]|float, h.temperature]|max', step)
        self.assertIn("VARIABLE=peak_{t} VALUE={h.temperature}", step)
        # les lignes lues par qc_yms.extract_measures gardent leur format
        self.assertIn('heat timeout, {"%.1f" % peak}C after {elapsed}s (target {target}C)', step)
        self.assertIn('heat OK, %.1fC reached (target %dC)" % (t - 1, peak, target)', step)
        # ventilo rallume seulement dans la branche verdict, apres coupure heater
        verdict = step.split("{% if ns.alldone or elapsed >= v.timeout|int %}", 1)[1]
        self.assertIn("SET_HEATER_TEMPERATURE HEATER=YMS-{t}-heater TARGET=0", verdict)
        self.assertIn("SET_FAN_SPEED FAN=YMS-{t}-fan SPEED=1.0", verdict)
        self.assertNotIn("SPEED=1.0", step.split("{% if ns.alldone or elapsed >= v.timeout|int %}", 1)[0])


if __name__ == "__main__":
    unittest.main()
