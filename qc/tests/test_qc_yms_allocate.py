import json
import threading
import time
import unittest
import warnings
from http.server import BaseHTTPRequestHandler, HTTPServer

from qc.qc_yms import allocate_yms_codes


warnings.filterwarnings("ignore", category=ResourceWarning)


class AllocHandler(BaseHTTPRequestHandler):
    response = None
    last_headers = None
    last_body = None

    def do_POST(self):
        AllocHandler.last_headers = dict(self.headers)
        length = int(self.headers.get("Content-Length", 0))
        AllocHandler.last_body = self.rfile.read(length)
        if self.response is None:
            self.send_response(500)
            self.end_headers()
            self.wfile.write(b"boom")
            return
        if self.response.get("delay"):
            time.sleep(self.response["delay"])
        self.send_response(self.response["status"])
        for h, v in self.response.get("headers", {}).items():
            self.send_header(h, v)
        self.end_headers()
        self.wfile.write(self.response["body"])

    def log_message(self, *args):
        pass


def run_server(handler):
    server = HTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server


class TestAllocateYmsCodes(unittest.TestCase):
    def setUp(self):
        AllocHandler.response = None
        AllocHandler.last_headers = None
        AllocHandler.last_body = None
        self.server = run_server(AllocHandler)
        self.url = "http://127.0.0.1:%d/api/qc/yms/allocate" % self.server.server_port

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()

    def test_ok_returns_count_ids(self):
        AllocHandler.response = {
            "status": 200,
            "headers": {"Content-Type": "application/json"},
            "body": json.dumps({"status": "ok", "yms_ids": ["YMSL-001", "YMSL-002"]}).encode(),
        }
        ids, err = allocate_yms_codes(self.url, "tok", 2, "light")
        self.assertEqual(err, "")
        self.assertEqual(ids, ["YMSL-001", "YMSL-002"])
        token_header = (
            AllocHandler.last_headers.get("X-QC-Token")
            or AllocHandler.last_headers.get("X-Qc-Token")
        )
        self.assertEqual(token_header, "tok")
        body = json.loads(AllocHandler.last_body)
        self.assertEqual(body, {"model": "light", "count": 2})

    def test_ok_unitary_yms_id(self):
        AllocHandler.response = {
            "status": 200,
            "body": json.dumps({"status": "ok", "yms_id": "YMSP-042"}).encode(),
        }
        ids, err = allocate_yms_codes(self.url, "tok", 1, "pro")
        self.assertEqual(err, "")
        self.assertEqual(ids, ["YMSP-042"])

    def test_http_500(self):
        AllocHandler.response = None
        ids, err = allocate_yms_codes(self.url, "tok", 12, "light")
        self.assertIsNone(ids)
        self.assertIn("HTTP 500", err)

    def test_invalid_json(self):
        AllocHandler.response = {
            "status": 200,
            "body": b"not-json",
        }
        ids, err = allocate_yms_codes(self.url, "tok", 1, "light")
        self.assertIsNone(ids)
        # json.loads lève JSONDecodeError dont le message varie selon les versions.
        self.assertIn("allocation impossible", err.lower())

    def test_count_mismatch(self):
        AllocHandler.response = {
            "status": 200,
            "body": json.dumps({"status": "ok", "yms_ids": ["YMSL-001"]}).encode(),
        }
        ids, err = allocate_yms_codes(self.url, "tok", 12, "light")
        self.assertIsNone(ids)
        self.assertIn("invalide", err)

    def test_timeout(self):
        AllocHandler.response = {
            "status": 200,
            "body": json.dumps({"status": "ok", "yms_ids": ["YMSL-001"]}).encode(),
            "delay": 2.0,
        }
        ids, err = allocate_yms_codes(self.url, "tok", 1, "light", timeout=0.05)
        self.assertIsNone(ids)
        self.assertIn("time", err.lower())


if __name__ == "__main__":
    unittest.main()


class TestAllocateResultParam(unittest.TestCase):
    """v1.4 : result="fail" envoye dans le body (famille QCFL- cote serveur)."""

    def test_fail_result_in_body(self):
        seen = {}

        class H(BaseHTTPRequestHandler):
            def do_POST(self):
                length = int(self.headers["Content-Length"])
                seen.update(json.loads(self.rfile.read(length)))
                body = json.dumps({"status": "ok",
                                   "yms_ids": ["QCFL-AAAAA-BBBBB"]}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *a):
                pass

        srv = HTTPServer(("127.0.0.1", 0), H)
        t = threading.Thread(target=srv.serve_forever, daemon=True)
        t.start()
        try:
            url = "http://127.0.0.1:%d/" % srv.server_port
            ids, err = allocate_yms_codes(url, "tok", 1, "light", result="fail")
            self.assertEqual(ids, ["QCFL-AAAAA-BBBBB"])
            self.assertEqual(seen.get("result"), "fail")
        finally:
            srv.shutdown()

    def test_pass_result_absent_du_body(self):
        seen = {}

        class H(BaseHTTPRequestHandler):
            def do_POST(self):
                length = int(self.headers["Content-Length"])
                seen.update(json.loads(self.rfile.read(length)))
                body = json.dumps({"status": "ok",
                                   "yms_ids": ["YMSL-AAAAA-BBBBB"]}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *a):
                pass

        srv = HTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            url = "http://127.0.0.1:%d/" % srv.server_port
            ids, _ = allocate_yms_codes(url, "tok", 1, "light")
            self.assertEqual(ids, ["YMSL-AAAAA-BBBBB"])
            self.assertNotIn("result", seen)
        finally:
            srv.shutdown()
