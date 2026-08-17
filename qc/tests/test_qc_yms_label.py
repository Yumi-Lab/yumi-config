import unittest
from unittest import mock

from qc.qc_yms import build_label_tspl

# build_label_tspl delegue a render_qc_tspl.render_qc_label(), qui fetch en direct le
# gabarit LIVE sur label.yumi-lab.com avant de retomber sur les defauts embarques (meme
# principe que render_plaque.py pour la plaque M3). Ces tests verifient le comportement
# du gabarit PAR DEFAUT — ils doivent donc etre isoles du reseau/de l'etat prod (sinon un
# vrai design sauve par Nicolas sur le site fait echouer des assertions qui ne le
# concernent pas). On force le fetch a echouer (comme si le reseau etait injoignable),
# ce que le code gere deja normalement (repli silencieux).
_NO_LIVE_TEMPLATE = mock.patch("qc.render_qc_tspl.load_template", return_value=None)


@_NO_LIVE_TEMPLATE
class TestBuildLabelTspl(unittest.TestCase):
    def test_label_bytes_crlf_and_qr(self, _mock_load_template):
        report = {
            "overall_result": "PASS",
            "printer_id": "YMSL-042",
            "qc_model": "YMS-LIGHT",
            "date_end": "2024-01-01T12:34:56",
        }
        tspl = build_label_tspl(report)
        self.assertIsInstance(tspl, bytes)
        # CRLF line endings
        self.assertNotIn(b"\n\r", tspl)
        self.assertTrue(tspl.endswith(b"\r\n"))
        text = tspl.decode("ascii")
        self.assertIn("SIZE 50 mm,30 mm", text)
        self.assertIn("SHIFT 8", text)
        self.assertIn("GAP 2 mm,0 mm", text)
        self.assertIn("SET PEEL ON", text)
        self.assertIn('TEXT 180,14,"3",0,1,1,"QC PASS"', text)
        self.assertIn('"YMS-LIGHT"', text)
        self.assertIn('"2024-01-01 12:34"', text)
        self.assertIn('"YMSL-042"', text)
        self.assertIn('https://qc.yumi-lab.com/report/YMSL-042', text)
        self.assertIn('QRCODE', text)
        self.assertIn("PRINT 1,1", text)

    def test_ascii_safe(self, _mock_load_template):
        report = {
            "overall_result": "FAIL",
            "printer_id": "YMSP-999",
            "qc_model": "YMS-PRO",
            "date_end": "2024-06-15T08:00:00",
        }
        tspl = build_label_tspl(report)
        # doit pouvoir être décodé en ASCII sans perte
        tspl.decode("ascii")


@_NO_LIVE_TEMPLATE
class TestLabelFail(unittest.TestCase):
    """v1.4 : etiquette de REJET systematique pour un FAIL (position + raison
    + code QCFL-), jamais utilisee comme numero de serie."""

    def test_fail_label_layout(self, _mock_load_template):
        report = {
            "printer_id": "QCFL-QJTVY-FKZDF",
            "overall_result": "FAIL",
            "qc_model": "YMS-LIGHT",
            "date_end": "2026-08-13T12:34:56",
            "bench_position": 11,
            "measures": {"fail_reason": "sensor_mute"},
        }
        tspl = build_label_tspl(report).decode("ascii")
        self.assertIn('"QC FAIL"', tspl)
        self.assertIn('"POS 11"', tspl)
        self.assertIn("sensor_mute", tspl)
        self.assertIn("QCFL-QJTVY-FKZDF", tspl)
        self.assertIn("QRCODE", tspl)
        self.assertIn("https://qc.yumi-lab.com/report/QCFL-QJTVY-FKZDF", tspl)

    def test_pass_label_unchanged(self, _mock_load_template):
        report = {
            "printer_id": "YMSL-7K3MQ-X2R9F",
            "overall_result": "PASS",
            "qc_model": "YMS-LIGHT",
            "date_end": "2026-08-13T12:34:56",
            "bench_position": 3,
        }
        tspl = build_label_tspl(report).decode("ascii")
        self.assertIn('"QC PASS"', tspl)
        self.assertNotIn("POSITION", tspl)
        self.assertIn("YMSL-7K3MQ-X2R9F", tspl)


if __name__ == "__main__":
    unittest.main()
