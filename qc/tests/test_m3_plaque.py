"""Plaque signalétique M3 : la tension et le courant imprimés doivent suivre la tension
secteur gravée au MCU (mains=) ; le rendu 220 V reste identique à la plaque d'avant (Nicolas, 12/09) -- bug du 12/09/2026 : gabarit « 220–240 V~ » codé en dur
et courant divisé par 220 en dur, toutes les machines 110 V sortaient avec une plaque 220 V."""
import os
import sys
import json
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "m3-plaque"))
import print_plaque  # noqa: E402
import render_plaque  # noqa: E402

DEVICE_LINE = ("// [mcu] board=SMART_MAKER_1_X;cpu=STM32F401;conn=UART;device=C235;"
               "x=SMART_TMC2209_V2;mains=110V;bedw=300;ssr=SSR10DA;lot=20260618;uid=BBA413")

# Gabarit tel que sauvé sur label.yumi-lab.com AVANT le fix : tension codée en dur.
LEGACY_TEMPLATE = {"version": 1, "label": {"w_mm": 75, "h_mm": 120}, "elements": [
    {"t": "text", "x": 4, "y": 42, "sz": 3.5, "c": "Model: {model}"},
    {"t": "serial", "x": 4, "y": 53, "sz": 3.2, "c": "S/N: {serial}", "mono": True, "fitw": 47},
    {"t": "text", "x": 4, "y": 58, "sz": 2.9, "c": "Rated Input: 220–240 V~, 50/60 Hz, {amps} A"},
]}
ROUTED_TEMPLATE = {"version": 1, "label": {"w_mm": 75, "h_mm": 120}, "elements": [
    {"t": "text", "x": 4, "y": 42, "sz": 3.5, "c": "Model: {model}"},
    {"t": "text", "x": 4, "y": 58, "sz": 2.9, "c": "Rated Input: {voltage}, 50/60 Hz, {amps} A"},
]}


class TestMainsRatings(unittest.TestCase):
    def test_table_covers_the_firmware_catalog(self):
        # firmware.yumi-lab.com catalog.json mains_voltages = ["220V", "110V"]
        self.assertEqual(set(print_plaque.MAINS_RATINGS), {"220V", "110V"})

    def test_220v_is_the_european_range(self):
        r = print_plaque.mains_rating("220V")
        self.assertEqual(r["voltage"], "220–240 V~")
        self.assertEqual(r["vmin"], 220)

    def test_110v_is_the_north_american_range(self):
        r = print_plaque.mains_rating("110V")
        self.assertEqual(r["voltage"], "100–120 V~")
        self.assertEqual(r["vmin"], 100)

    def test_norm_mains_accepts_the_qc_counter_variants(self):
        for raw in ("110", "110v", " 110V ", "110V"):
            self.assertEqual(print_plaque.norm_mains(raw), "110V")
        self.assertIsNone(print_plaque.mains_rating(""))
        self.assertIsNone(print_plaque.mains_rating(None))
        self.assertIsNone(print_plaque.mains_rating("380V"))


class TestRatedAmps(unittest.TestCase):
    """Courant = (bedw + PSU) / borne basse de la plage, arrondi au 1 A supérieur."""

    def test_c235_220v(self):
        elec, problems = print_plaque.electrical_rating("220V", "300")
        self.assertEqual(problems, [])
        self.assertEqual(elec["watts"], 420)
        self.assertEqual(elec["amps"], "2.0")
        self.assertEqual(elec["voltage"], "220–240 V~")

    def test_c235_110v_draws_more_current_than_220v(self):
        elec, problems = print_plaque.electrical_rating("110V", "300")
        self.assertEqual(problems, [])
        self.assertEqual(elec["voltage"], "100–120 V~")
        self.assertEqual(elec["amps"], "5.0")     # 420 W / 100 V = 4.2 -> 5 A

    def test_c435_uses_the_bed_power_of_its_voltage(self):
        # catalog : C435 750 W en 220 V, 550 W en 110 V -- bedw gravé par tension
        self.assertEqual(print_plaque.electrical_rating("220V", "750")[0]["amps"], "4.0")
        self.assertEqual(print_plaque.electrical_rating("110V", "550")[0]["amps"], "7.0")

    def test_manual_power_override(self):
        elec, problems = print_plaque.electrical_rating("220V", None, power="470 W")
        self.assertEqual(problems, [])
        self.assertEqual(elec["amps"], "3.0")

    def test_unknown_mains_refuses(self):
        elec, problems = print_plaque.electrical_rating("", "300")
        self.assertEqual(elec["voltage"], "?")
        self.assertEqual(elec["amps"], "?")
        self.assertEqual(len(problems), 1)
        self.assertIn("tension secteur inconnue", problems[0])

    def test_unknown_bed_power_refuses(self):
        elec, problems = print_plaque.electrical_rating("110V", None)
        self.assertEqual(elec["amps"], "?")
        self.assertEqual(elec["voltage"], "100–120 V~")
        self.assertEqual(len(problems), 1)
        self.assertIn("puissance plateau inconnue", problems[0])

    def test_custom_voltage_label_keeps_the_computed_current(self):
        elec, _ = print_plaque.electrical_rating("110V", "300", voltage="120 V~")
        self.assertEqual(elec["voltage"], "120 V~")
        self.assertEqual(elec["amps"], "5.0")


class TestDeviceParsing(unittest.TestCase):
    def test_parse_device_line_strips_the_respond_prefix(self):
        kv, clean = print_plaque.parse_yumi_config(DEVICE_LINE)
        self.assertEqual(kv["board"], "SMART_MAKER_1_X")
        self.assertEqual(kv["device"], "C235")
        self.assertEqual(kv["mains"], "110V")
        self.assertEqual(kv["bedw"], "300")
        self.assertTrue(clean.startswith("board="))

    def test_identity_from_store_carries_mains_and_bedw(self):
        store = [{"message": "// MCU_UID=2D0046000D51353234323830"}, {"message": DEVICE_LINE}]
        ident = print_plaque.identity_from_store(store)
        self.assertEqual(ident["serial"], "2D0046000D51353234323830")
        self.assertEqual(ident["model"], "C235")
        self.assertEqual(ident["mains"], "110V")
        self.assertEqual(ident["bedw"], "300")
        self.assertEqual(ident["lot"], "20260618")


class TestVoltageRouting(unittest.TestCase):
    def test_legacy_template_refuses_a_110v_machine(self):
        err = print_plaque.voltage_routing_error(LEGACY_TEMPLATE, "100–120 V~")
        self.assertIsNotNone(err)
        self.assertIn("{voltage}", err)

    def test_legacy_template_still_prints_a_220v_machine(self):
        # transition : le gabarit en dur est juste par coïncidence pour le 220 V
        self.assertIsNone(print_plaque.voltage_routing_error(LEGACY_TEMPLATE, "220–240 V~"))

    def test_hyphen_vs_en_dash_does_not_matter(self):
        tpl = {"elements": [{"t": "text", "c": "Input: 220-240 V~, 50/60 Hz, {amps} A"}]}
        self.assertIsNone(print_plaque.voltage_routing_error(tpl, "220–240 V~"))

    def test_routed_template_accepts_both_voltages(self):
        for v in ("220–240 V~", "100–120 V~"):
            self.assertIsNone(print_plaque.voltage_routing_error(ROUTED_TEMPLATE, v))

    def test_unknown_voltage_is_refused_even_with_a_routed_template(self):
        self.assertIsNotNone(print_plaque.voltage_routing_error(ROUTED_TEMPLATE, "?"))

    def test_embedded_default_template_routes_the_voltage(self):
        els = render_plaque.DEFAULT_TEMPLATE["elements"]
        inputs = [e for e in els if "{amps}" in str(e.get("c", ""))]
        self.assertEqual(len(inputs), 1)
        self.assertIn("{voltage}", inputs[0]["c"])
        self.assertIsNone(print_plaque.voltage_routing_error(render_plaque.DEFAULT_TEMPLATE, "100–120 V~"))

    def test_220v_rendering_is_identical_to_the_legacy_template(self):
        # Consigne Nicolas : le rendu 220 V ne bouge pas d'un pixel, seul le 110 V change.
        from PIL import ImageChops
        data = {"serial": "2D0046000D51353234323830", "model": "C235", "voltage": "220\u2013240 V~",
                "amps": "2.0", "power": "420 W", "lot": "", "origin": "Made in China", "maker": "",
                "qr": "https://qc.yumi-lab.com/report/X", "qr2": "https://go.yumi-lab.com"}
        legacy = render_plaque.render(data, LEGACY_TEMPLATE)
        routed = {"elements": [dict(e) for e in LEGACY_TEMPLATE["elements"]]}
        routed["elements"][2]["c"] = "Rated Input: {voltage}, 50/60 Hz, {amps} A"
        self.assertIsNone(ImageChops.difference(legacy, render_plaque.render(data, routed)).getbbox())

    def test_render_substitutes_the_voltage(self):
        data = {"serial": "X", "model": "C235", "voltage": "100–120 V~", "amps": "5.0",
                "power": "420 W", "lot": "", "origin": "Made in China", "maker": "",
                "qr": "https://qc.yumi-lab.com/report/X", "qr2": "https://go.yumi-lab.com"}
        img = render_plaque.render(data, ROUTED_TEMPLATE)
        self.assertEqual(img.size, (render_plaque.MM(75), render_plaque.MM(120)))
        # au moins des pixels blancs dans la bande de la ligne « Rated Input » (y = 58 mm)
        band = img.crop((0, render_plaque.MM(57), img.width, render_plaque.MM(62)))
        self.assertGreater(max(band.getdata()), 0)


if __name__ == "__main__":
    unittest.main()
