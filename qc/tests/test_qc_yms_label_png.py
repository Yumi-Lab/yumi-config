import base64
import unittest
from unittest import mock

from qc.qc_yms import build_label_png_job

# Meme isolation reseau que test_qc_yms_label.py -- voir ce fichier pour le pourquoi.
_NO_LIVE_TEMPLATE = mock.patch("qc.render_qc_tspl.load_template", return_value=None)


@_NO_LIVE_TEMPLATE
class TestBuildLabelPngJob(unittest.TestCase):
    def test_yms_pass_job_shape(self, _mock_load_template):
        report = {
            "overall_result": "PASS", "printer_id": "YMSL-ABCDEF",
            "qc_model": "YMS-LIGHT", "date_end": "2026-08-26T10:00:00",
            "bench_position": 5,
        }
        job = build_label_png_job(report)
        self.assertEqual(set(job.keys()),
                         {"image", "qty", "gap_mm", "width_mm", "height_mm", "peel"})
        # Media YMS = 50x30mm (cf. render_qc_tspl.MEDIA) -- exige par pos80l-bridge
        # (png_to_tspl) pour son propre resize+seuillage.
        self.assertEqual((job["width_mm"], job["height_mm"]), (50, 30))
        self.assertEqual(job["gap_mm"], 2)
        self.assertTrue(job["peel"])
        self.assertEqual(job["qty"], 1)
        self.assertTrue(job["image"].startswith("data:image/png;base64,"))
        png = base64.b64decode(job["image"].split(",", 1)[1])
        self.assertEqual(png[:8], b"\x89PNG\r\n\x1a\n")  # signature PNG

    def test_machine_fail_job_shape(self, _mock_load_template):
        report = {
            "overall_result": "FAIL", "printer_id": "AABBCCDDEEFF",
            "qc_model": "C235", "date_end": "2026-08-26T10:00:00",
            "failed_tests": ["z_tap_calib"],
            "tests": [{"id": "z_tap_calib", "name": "Z TAP"}],
        }
        job = build_label_png_job(report, qty=2)
        self.assertEqual((job["width_mm"], job["height_mm"]), (39, 39))
        self.assertEqual(job["qty"], 2)
        self.assertTrue(job["image"].startswith("data:image/png;base64,"))

    def test_different_reports_render_different_images(self, _mock_load_template):
        base = {"printer_id": "YMSL-A", "qc_model": "YMS-LIGHT",
                "date_end": "2026-08-26T10:00:00", "bench_position": 1,
                "overall_result": "PASS"}
        other = dict(base, printer_id="YMSL-B", bench_position=12)
        job_a = build_label_png_job(base)
        job_b = build_label_png_job(other)
        self.assertNotEqual(job_a["image"], job_b["image"])


if __name__ == "__main__":
    unittest.main()
