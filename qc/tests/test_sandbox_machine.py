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


if __name__ == "__main__":
    unittest.main()
