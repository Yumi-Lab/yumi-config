import importlib.util
import json
import os
import threading
import unittest
import warnings
from http.server import BaseHTTPRequestHandler, HTTPServer

warnings.filterwarnings("ignore", category=ResourceWarning)

_SCRIPT = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "scripts", "sandbox_machine_test.py")


def _load_module():
    spec = importlib.util.spec_from_file_location("sandbox_machine_test", _SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class AckHandler(BaseHTTPRequestHandler):
    last_headers = None
    last_body = None

    def do_POST(self):
        AckHandler.last_headers = dict(self.headers)
        length = int(self.headers.get("Content-Length", 0))
        AckHandler.last_body = self.rfile.read(length)
        body = json.dumps({"status": "ok", "sandbox": True}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


class TestSandboxMachineReport(unittest.TestCase):
    """Harnais sandbox : poste le rapport machine complet contre un serveur
    local (même chemin de code que la prod) et vérifie l'ack + le payload."""

    @classmethod
    def setUpClass(cls):
        cls.mod = _load_module()

    def setUp(self):
        AckHandler.last_headers = None
        AckHandler.last_body = None
        self.server = HTTPServer(("127.0.0.1", 0), AckHandler)
        self.thread = threading.Thread(target=self.server.serve_forever,
                                       daemon=True)
        self.thread.start()
        self.url = "http://127.0.0.1:%d/api/qc/report" % self.server.server_port

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()

    def test_post_ack_200_and_payload(self):
        report = self.mod.build_report()
        status, body = self.mod.post_report(report, "tok-sandbox", url=self.url)
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["status"], "ok")

        sent = json.loads(AckHandler.last_body.decode("utf-8"))
        self.assertIs(sent["sandbox"], True)
        self.assertNotIn("technician", sent)
        self.assertEqual(sent["overall_result"], "PASS")
        self.assertEqual(len(sent["tests"]), 13)
        token_header = (AckHandler.last_headers.get("X-QC-Token")
                        or AckHandler.last_headers.get("X-Qc-Token"))
        self.assertEqual(token_header, "tok-sandbox")

    def test_http_error_returns_code_and_body(self):
        class Boom(AckHandler):
            def do_POST(self):
                # Lire le corps AVANT la réponse : sinon la connexion HTTP/1.0
                # se ferme avec le body non lu en buffer -> RST TCP -> le
                # client peut lever ConnectionResetError avant d'avoir lu la
                # réponse (miroir de AckHandler.do_POST).
                self.rfile.read(int(self.headers.get("Content-Length", 0)))
                self.send_response(422)
                self.end_headers()
                self.wfile.write(b'{"error":"bad payload"}')
        server = HTTPServer(("127.0.0.1", 0), Boom)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            url = "http://127.0.0.1:%d/api/qc/report" % server.server_port
            status, body = self.mod.post_report(
                self.mod.build_report(), "tok", url=url)
            self.assertEqual(status, 422)
            self.assertIn("bad payload", body)
        finally:
            server.shutdown()
            server.server_close()

    def test_load_token_env_precedence(self):
        os.environ["QC_TOKEN"] = "env-tok"
        try:
            self.assertEqual(self.mod.load_token(), "env-tok")
        finally:
            del os.environ["QC_TOKEN"]

    def test_main_against_local_server(self):
        """main() complet (token env + ack sandbox) contre le serveur local."""
        os.environ["QC_TOKEN"] = "tok-sandbox"
        try:
            rc = self.mod.main(["--url", self.url])
        finally:
            del os.environ["QC_TOKEN"]
        self.assertEqual(rc, 0)
        sent = json.loads(AckHandler.last_body.decode("utf-8"))
        self.assertIs(sent["sandbox"], True)

    def test_main_retest_against_local_server(self):
        """main --retest : HOME tempdir + seed -> rapport retest posté, ack OK."""
        old_home = os.environ.get("HOME")
        os.environ["QC_TOKEN"] = "tok-sandbox"
        try:
            rc = self.mod.main(["--url", self.url, "--retest"])
        finally:
            del os.environ["QC_TOKEN"]
            if old_home:
                os.environ["HOME"] = old_home
        self.assertEqual(rc, 0)
        sent = json.loads(AckHandler.last_body.decode("utf-8"))
        self.assertIs(sent["retest"], True)
        self.assertEqual(sent["retest_reason"], "previous_report_pass")
        self.assertIs(sent["sandbox"], True)


class TestSandboxReportCompleteness(unittest.TestCase):
    """L9 : le rapport sandbox est COMPLET — measures sur les 13 tests,
    software_versions, invariants intacts."""

    @classmethod
    def setUpClass(cls):
        cls.mod = _load_module()
        cls.report = cls.mod.build_report()

    def test_all_13_tests_have_measures(self):
        self.assertEqual(len(self.report["tests"]), 13)
        for entry in self.report["tests"]:
            self.assertIn("measures", entry,
                          "measures manquantes sur %s" % entry["id"])
            self.assertIn("fail_reason", entry["measures"])
            self.assertIsNone(entry["measures"]["fail_reason"],
                              "fail_reason non null sur un PASS (%s)"
                              % entry["id"])

    def test_measures_spot_checks(self):
        by_id = {t["id"]: t["measures"] for t in self.report["tests"]}
        self.assertEqual(by_id["mcu_check"]["mcu_uid"], self.mod.SIMULATED_UID)
        self.assertEqual(by_id["z_tap_calib"]["spread_mm"], 0.0312)
        self.assertEqual(by_id["z_tap_calib"]["n_taps"], 15)
        self.assertEqual(by_id["z_tap_calib"]["converged_n"], 3)
        self.assertEqual(by_id["home_x"]["sg_thrs"], 63)
        self.assertEqual(by_id["home_x"]["taps_valides"], 3)
        self.assertEqual(by_id["heat_bed"]["ramp_s"], 95)
        self.assertEqual(by_id["heat_bed"]["target_c"], 60)
        self.assertIsNone(by_id["heat_bed"]["reached_c"])  # null assumé (L5)
        self.assertEqual(by_id["e1_head"]["feed_mm"], 412)
        self.assertEqual(by_id["e1_head"]["feed_budget_mm"], 800)
        self.assertTrue(by_id["e1_head"]["head_reached"])
        self.assertTrue(by_id["cutter"]["motion_first_detect"])
        self.assertEqual(by_id["z_tap_home"]["tap_z_mm"], 486.1075)
        self.assertAlmostEqual(
            by_id["screws_tilt"]["max_deviation_mm"], 0.04123, places=5)
        self.assertEqual(len(by_id["screws_tilt"]["corrections"]), 3)
        for fan in ("fan_motherboard", "fan_part", "fan_hotend"):
            self.assertIs(by_id[fan]["visual_ack"], True)

    def test_software_versions_present(self):
        sv = self.report.get("software_versions", {})
        self.assertEqual(sv.get("klipper_version"), "v0.12.0-159-gabcd1234")
        self.assertEqual(sv.get("firmware_version"),
                         {"mcu": "v0.12.0-159-gabcd1234"})

    def test_invariants_intact(self):
        self.assertNotIn("technician", self.report)
        self.assertNotIn("retest", self.report)  # jamais sans rapport précédent
        self.assertEqual(self.report["overall_result"], "PASS")
        self.assertEqual(self.report["machine_uid"], self.mod.SIMULATED_UID)
        self.assertEqual(self.report["printer_id"], self.mod.SIMULATED_UID)
        self.assertIs(self.report["sandbox"], True)

    def test_retest_detected_after_seed(self):
        """seed_previous_report sous HOME tempdir -> retest: true au build."""
        old_home = os.environ.get("HOME")
        import tempfile
        os.environ["HOME"] = tempfile.mkdtemp(prefix="qc-test-retest-")
        try:
            self.mod.seed_previous_report()
            report = self.mod.build_report()
        finally:
            if old_home:
                os.environ["HOME"] = old_home
        self.assertIs(report.get("retest"), True)
        self.assertEqual(report.get("retest_reason"), "previous_report_pass")

    def test_check_ack_sandbox(self):
        ack = self.mod.check_ack_sandbox('{"status": "ok", "sandbox": true}')
        self.assertEqual(ack["status"], "ok")
        for bad in ('{"status": "ok"}',           # marqueur absent
                    '{"sandbox": false}',          # marqueur faux
                    'not json',                    # non JSON
                    '["sandbox"]'):                # pas un objet
            with self.assertRaises(ValueError, msg=bad):
                self.mod.check_ack_sandbox(bad)


if __name__ == "__main__":
    unittest.main()
