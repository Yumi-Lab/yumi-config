import re
import unittest
from unittest import mock

from qc.qc_yms import build_label_tspl
import qc.render_qc_tspl as render_qc_tspl

# build_label_tspl delegue a render_qc_tspl.render_qc_label(), qui fetch en direct le
# gabarit LIVE sur label.yumi-lab.com avant de retomber sur les defauts embarques (meme
# principe que render_plaque.py pour la plaque M3). Ces tests verifient le comportement
# du gabarit PAR DEFAUT — ils doivent donc etre isoles du reseau/de l'etat prod (sinon un
# vrai design sauve par Nicolas sur le site fait echouer des assertions qui ne le
# concernent pas). On force le fetch a echouer (comme si le reseau etait injoignable),
# ce que le code gere deja normalement (repli silencieux).
_NO_LIVE_TEMPLATE = mock.patch("qc.render_qc_tspl.load_template", return_value=None)

# v2 (22/08/2026) : l'etiquette est un UNIQUE bitmap Pillow (plus du TEXT/QRCODE TSPL
# natif) -- on ne peut plus chercher des sous-chaines de texte dans le flux. On verifie
# a la place la STRUCTURE (header ASCII, un seul bloc BITMAP, comptage d'octets exact)
# et la SENSIBILITE aux donnees (deux rendus avec un contenu different produisent des
# pixels differents), sans "lire" l'image.


def _parse_bitmap(tspl):
    """Decoupe un TSPL v2 en (header_ascii, (x, y, wbytes, h), corps_binaire, pied_ascii)."""
    m = re.search(rb"BITMAP (\d+),(\d+),(\d+),(\d+),0,", tspl)
    assert m, "aucune commande BITMAP trouvee dans le TSPL"
    x, y, wbytes, h = (int(g) for g in m.groups())
    header = tspl[:m.start()]
    body_start = m.end()
    body_len = wbytes * h
    body = tspl[body_start:body_start + body_len]
    footer = tspl[body_start + body_len:]
    return header, (x, y, wbytes, h), body, footer


@_NO_LIVE_TEMPLATE
class TestBuildLabelTspl(unittest.TestCase):
    def test_label_structure_yms_pass(self, _mock_load_template):
        report = {
            "overall_result": "PASS",
            "printer_id": "YMSL-042",
            "qc_model": "YMS-LIGHT",
            "date_end": "2024-01-01T12:34:56",
        }
        tspl = build_label_tspl(report)
        self.assertIsInstance(tspl, bytes)
        header, (x, y, wbytes, h), body, footer = _parse_bitmap(tspl)
        htext = header.decode("ascii")
        self.assertIn("SIZE 50 mm,30 mm", htext)
        self.assertIn("GAP 2 mm,0 mm", htext)
        self.assertIn("SHIFT 8", htext)
        self.assertIn("SET PEEL ON", htext)
        self.assertIn("CLS", htext)
        # media YMS 50x30mm a 8 dots/mm -> 400x240 dots
        self.assertEqual(h, 240)
        self.assertEqual(wbytes, (400 + 7) // 8)
        self.assertEqual(len(body), wbytes * h)
        self.assertEqual(footer, b"\r\nPRINT 1,1\r\n")
        # UN SEUL bloc BITMAP desormais (v1 en avait un par logo + du TEXT natif)
        self.assertEqual(tspl.count(b"BITMAP "), 1)

    def test_ascii_safe_header(self, _mock_load_template):
        report = {
            "overall_result": "FAIL",
            "printer_id": "YMSP-999",
            "qc_model": "YMS-PRO",
            "date_end": "2024-06-15T08:00:00",
            "bench_position": 4,
            "measures": {"fail_reason": "timeout"},
        }
        tspl = build_label_tspl(report)
        header, _, _, footer = _parse_bitmap(tspl)
        # le header (SIZE/GAP/...) et le pied (PRINT) restent du TSPL texte pur
        header.decode("ascii")
        footer.decode("ascii")

    def test_different_content_renders_different_pixels(self, _mock_load_template):
        """Deux rapports PASS avec un code different doivent produire des bitmaps
        DIFFERENTS -- preuve que la substitution de placeholders affecte vraiment
        le rendu, sans avoir a "lire" l'image."""
        base = {"overall_result": "PASS", "date_end": "2024-01-01T12:34:56", "qc_model": "YMS-LIGHT"}
        t1 = build_label_tspl(dict(base, printer_id="YMSL-AAAAAAAAAA"))
        t2 = build_label_tspl(dict(base, printer_id="YMSL-ZZZZZZZZZZ"))
        self.assertNotEqual(t1, t2)


class TestRenderPixelFidelity(unittest.TestCase):
    """Le point qui a motive le passage en bitmap (v1 texte natif ne pouvait pas rendre
    le gras ni une taille de QR exacte) : ces deux-la doivent maintenant visiblement
    changer les pixels."""

    def test_bold_weight_changes_pixels(self):
        data = {"qc_model": "YMS-LIGHT", "code": "X", "qr": "https://x"}
        el_reg = [{"t": "text", "x": 2, "y": 2, "sz": 4, "c": "TEST"}]
        el_bold = [{"t": "text", "x": 2, "y": 2, "sz": 4, "c": "TEST", "weight": "bold"}]
        out_reg = render_qc_tspl.render(el_reg, data, 50, 30)
        out_bold = render_qc_tspl.render(el_bold, data, 50, 30)
        self.assertNotEqual(out_reg, out_bold)

    def test_qr_reflects_actual_module_count(self):
        """La cellule QR (v1) etait devinee sans connaitre le nombre de modules --
        deux contenus de longueurs tres differentes doivent maintenant produire des
        QR de tailles differentes (plus de modules -> bitmap different)."""
        el = [{"t": "qr", "x": 2, "y": 2, "sz": 15, "c": "{qr}"}]
        short = render_qc_tspl.render(el, {"qr": "https://q.co/1"}, 50, 30)
        long = render_qc_tspl.render(
            el, {"qr": "https://qc.yumi-lab.com/report/" + "A" * 60}, 50, 30)
        self.assertNotEqual(short, long)


@_NO_LIVE_TEMPLATE
class TestLabelFail(unittest.TestCase):
    """v1.4 : etiquette de REJET systematique pour un FAIL (position + raison
    + code QCFL-), jamais utilisee comme numero de serie."""

    def test_fail_label_structure(self, _mock_load_template):
        report = {
            "printer_id": "QCFL-QJTVY-FKZDF",
            "overall_result": "FAIL",
            "qc_model": "YMS-LIGHT",
            "date_end": "2026-08-13T12:34:56",
            "bench_position": 11,
            "measures": {"fail_reason": "sensor_mute"},
        }
        tspl = build_label_tspl(report)
        header, (x, y, wbytes, h), body, footer = _parse_bitmap(tspl)
        self.assertIn("SIZE 50 mm,30 mm", header.decode("ascii"))
        self.assertEqual(len(body), wbytes * h)
        self.assertEqual(tspl.count(b"BITMAP "), 1)

    def test_pass_and_fail_render_differently(self, _mock_load_template):
        base = {
            "printer_id": "YMSL-7K3MQ-X2R9F",
            "qc_model": "YMS-LIGHT",
            "date_end": "2026-08-13T12:34:56",
        }
        tspl_pass = build_label_tspl(dict(base, overall_result="PASS"))
        tspl_fail = build_label_tspl(dict(
            base, overall_result="FAIL", bench_position=3,
            measures={"fail_reason": "sensor_mute"}))
        self.assertNotEqual(tspl_pass, tspl_fail)


if __name__ == "__main__":
    unittest.main()
