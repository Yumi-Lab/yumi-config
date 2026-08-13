import unittest

from qc.qc_yms import build_label_tspl


class TestBuildLabelTspl(unittest.TestCase):
    def test_label_bytes_crlf_and_qr(self):
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
        self.assertIn("SIZE 39 mm,39 mm", text)
        self.assertIn("SHIFT 8", text)
        self.assertIn("GAP 2 mm,0 mm", text)
        self.assertIn("SET PEEL ON", text)
        self.assertIn('TEXT 16,16,"4",0,1,1,"QC PASS"', text)
        self.assertIn('TEXT 16,64,"2",0,1,1,"YMS-LIGHT"', text)
        self.assertIn('TEXT 16,96,"2",0,1,1,"2024-01-01 12:34"', text)
        self.assertIn('TEXT 16,200,"1",0,1,1,"YMSL-042"', text)
        self.assertIn('QRCODE 290,40,M,4,A,0,"https://qc.yumi-lab.com/report/YMSL-042"', text)
        self.assertIn("PRINT 1,1", text)

    def test_ascii_safe(self):
        report = {
            "overall_result": "FAIL",
            "printer_id": "YMSP-999",
            "qc_model": "YMS-PRO",
            "date_end": "2024-06-15T08:00:00",
        }
        tspl = build_label_tspl(report)
        # doit pouvoir être décodé en ASCII sans perte
        tspl.decode("ascii")


if __name__ == "__main__":
    unittest.main()


class TestLabelFail(unittest.TestCase):
    """v1.4 : etiquette de REJET systematique pour un FAIL (position + raison
    + code QCFL-), jamais utilisee comme numero de serie."""

    def test_fail_label_layout(self):
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
        self.assertIn('"POSITION 11"', tspl)
        self.assertIn("sensor_mute", tspl)
        self.assertIn("QCFL-QJTVY-FKZDF", tspl)
        self.assertIn("QRCODE", tspl)
        self.assertIn("https://qc.yumi-lab.com/report/QCFL-QJTVY-FKZDF", tspl)

    def test_pass_label_unchanged(self):
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
