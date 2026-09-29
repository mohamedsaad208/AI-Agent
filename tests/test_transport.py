"""`request_json` on a real socket: the four promises that are properties of HTTP, not of the code.

The provider layer says it follows no redirect, uses no proxy, refuses a body it cannot hold and
never repeats a credential. None of those can be checked by patching `urlopen` — a patch replaces the
exact machinery the claim is about, and a test against it passes for the wrong reason. So these run
against a listener in this process and assert what came back, what was sent, and what was never
touched.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import socket
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import unittest
import urllib.parse

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ai_code_engineer.errors import ProviderError
from ai_code_engineer.providers import NoRedirect, request_json

# Credential-shaped on purpose: the 401 body echoes it back, so a leak is visible as text.
KEY = "sk-proj-a-synthetic-key-for-tests-0123456789"
BIG = 4096


class Probe(BaseHTTPRequestHandler):
    """A provider that answers each route the way a real one misbehaves."""

    protocol_version = "HTTP/1.1"

    def log_message(self, *_args):
        pass

    def _send(self, code, body, extra=()):
        raw = body if isinstance(body, bytes) else body.encode("utf-8")
        try:
            self.send_response(code)
            self.send_header("content-type", "application/json")
            self.send_header("content-length", str(len(raw)))
            for name, value in extra:
                self.send_header(name, value)
            self.end_headers()
            self.wfile.write(raw)
        except (ConnectionError, OSError):
            # The timeout case is the client giving up first; the traceback the socket server would
            # otherwise print belongs to the test's design, not to a failure in the code under test.
            self.close_connection = True

    def do_POST(self):
        self.read()

    def do_GET(self):
        self.read()

    def read(self):
        path = urllib.parse.urlparse(self.path).path
        self.server.hits.append(path)
        length = int(self.headers.get("content-length") or 0)
        if length:
            self.rfile.read(length)
        if path == "/ok":
            self._send(200, json.dumps({"answer": "here", "auth": self.headers.get("authorization", "")}))
        elif path == "/redirect":
            # A real 302 with a Location the client would happily follow, aimed at /ok.
            self._send(302, "", [("Location", "/ok")])
        elif path == "/denied":
            body = json.dumps({"error": {"message": "invalid api key " + KEY,
                                         "sent": self.headers.get("authorization", "")}})
            self._send(401, body)
        elif path == "/not-json":
            self._send(200, "this is not json {{{")
        elif path == "/json-but-not-an-object":
            self._send(200, json.dumps(["a", "list", "not", "a", "response"]))
        elif path == "/huge":
            self._send(200, json.dumps({"pad": "z" * BIG}))
        elif path == "/slow":
            time.sleep(3)
            self._send(200, json.dumps({"answer": "late"}))
        else:
            self._send(404, json.dumps({"error": "no such route"}))


class Wire(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), Probe)
        cls.server.daemon_threads = True
        cls.server.hits = []
        cls.port = cls.server.server_address[1]
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        # Registered first so it runs last: shutdown() stops the loop, server_close() returns the
        # listening socket. In the other order serve_forever wakes on a closed fd and prints.
        cls.addClassCleanup(cls.server.server_close)
        cls.addClassCleanup(cls.server.shutdown)

    def setUp(self):
        self.server.hits = []

    def url(self, path):
        return "http://127.0.0.1:%d%s" % (self.port, path)

    # ------------------------------ the happy wire ------------------------------
    def test_a_plain_answer_comes_back_as_a_dict(self):
        self.assertEqual(request_json(self.url("/ok"))["answer"], "here")

    def test_the_key_goes_in_the_header_and_nowhere_else(self):
        result = request_json(self.url("/ok"), payload={"a": 1}, key=KEY)
        self.assertEqual(result["auth"], "Bearer " + KEY)
        self.assertNotIn(KEY, self.server.hits[0], "the key must not ride in the URL")

    # ------------------------------ redirects ------------------------------
    def test_a_redirect_is_refused_rather_than_followed(self):
        with self.assertRaises(ProviderError) as caught:
            request_json(self.url("/redirect"))
        self.assertIn("302", str(caught.exception))
        self.assertEqual(self.server.hits, ["/redirect"],
                         "the target of the redirect was reached — that is a followed redirect")

    def test_the_redirect_handler_answers_nothing_to_a_302(self):
        # The unit side of the same rule: urllib only skips because this returns None.
        self.assertIsNone(NoRedirect().redirect_request(None, None, 302, "Found", None, "http://x/ok"))

    # ------------------------------ proxies ------------------------------
    def test_a_proxy_in_the_environment_is_not_used(self):
        """`ProxyHandler({})` is what stops a corporate or hostile proxy env from taking the bearer
        token somewhere the settings never pointed."""
        dead = threading.Event()
        listener = socket.socket()
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        self.addCleanup(listener.close)
        proxy_port = listener.getsockname()[1]
        reached = []

        def accept_once():
            try:
                connection, _peer = listener.accept()
                reached.append(connection.recv(4096))
                connection.close()
            except OSError:
                pass
            dead.set()

        thread = threading.Thread(target=accept_once, daemon=True)
        thread.start()
        saved = {name: os.environ.get(name) for name in ("HTTP_PROXY", "http_proxy", "ALL_PROXY")}

        def restore():
            for name, value in saved.items():
                if value is None:
                    os.environ.pop(name, None)
                else:
                    os.environ[name] = value

        os.environ["HTTP_PROXY"] = "http://127.0.0.1:%d" % proxy_port
        os.environ["ALL_PROXY"] = "http://127.0.0.1:%d" % proxy_port
        self.addCleanup(restore)
        try:
            self.assertEqual(request_json(self.url("/ok"), timeout=5)["answer"], "here")
        finally:
            listener.close()
        thread.join(5)
        self.assertEqual(reached, [], "the request went through a proxy the settings never named")

    # ------------------------------ what a failure says ------------------------------
    def test_a_status_code_is_reported_without_the_credential_that_got_it(self):
        with self.assertRaises(ProviderError) as caught:
            request_json(self.url("/denied"), key=KEY)
        text = str(caught.exception)
        self.assertIn("401", text)
        self.assertNotIn(KEY, text, "the provider echoed the key back and this repeated it")
        self.assertNotIn("127.0.0.1", text, "the URL is not something a UI should be shown")
        self.assertIn("no automatic retry or fallback", text)

    def test_a_body_that_will_not_parse_is_its_own_refusal(self):
        with self.assertRaises(ProviderError) as caught:
            request_json(self.url("/not-json"))
        self.assertIn("invalid json", str(caught.exception).lower())

    def test_valid_json_that_is_not_an_object_is_refused(self):
        """A list would raise `AttributeError` in the caller three frames away."""
        with self.assertRaises(ProviderError) as caught:
            request_json(self.url("/json-but-not-an-object"))
        self.assertIn("invalid response", str(caught.exception))

    def test_an_oversized_answer_is_refused_before_it_is_held(self):
        with self.assertRaises(ProviderError) as caught:
            request_json(self.url("/huge"), max_bytes=1024)
        self.assertIn("size limit", str(caught.exception))

    def test_a_response_that_never_arrives_is_a_timeout_not_a_hang(self):
        started = time.monotonic()
        with self.assertRaises(ProviderError) as caught:
            request_json(self.url("/slow"), timeout=1)
        self.assertLess(time.monotonic() - started, 2.5, "the timeout was not honoured")
        self.assertIn("timed out", str(caught.exception))

    def test_a_connection_to_nothing_reports_without_a_traceback_of_the_url(self):
        # A port this process just owned and released is the closest thing to a port that is
        # certainly nobody's; `port + 99` would be a guess.
        spare = socket.socket()
        spare.bind(("127.0.0.1", 0))
        dead = spare.getsockname()[1]
        spare.close()
        with self.assertRaises(ProviderError) as caught:
            request_json("http://127.0.0.1:%d/ok" % dead, timeout=3)
        self.assertIn("connection failed", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
