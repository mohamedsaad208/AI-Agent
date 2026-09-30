"""The HTTP boundary itself: who may talk to the server, and what it will serve.

These run against a real listener on a loopback port because the guarantees that matter
here are exactly the ones a fake would not exercise — the token check, the static path
confinement, and a blocked worker thread waking up when the browser answers.
"""
import json
from pathlib import Path
import inspect
import socket
import struct
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import unittest
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ai_code_engineer import intent
from ai_code_engineer.errors import PolicyError
from ai_code_engineer.webapp import server as server_module
from ai_code_engineer.webapp.controller import AgentController, initials_for
from ai_code_engineer.webapp.fake import PROJECT_INFO, FakeController


class Stub:
    """The smallest thing that satisfies the contract, so the transport is what is tested."""

    def __init__(self):
        self.received = []
        self.reply = threading.Event()
        self.answer = {}
        self.granted = {"demo2"}
        self.failures = []

    def snapshot(self):
        return {"ok": True, "busy": False}

    def action(self, type_, payload, emit):
        self.received.append((type_, payload))
        if type_ == "blocks":
            emit({"kind": "confirm", "id": "r1", "title": "Apply?"})
            self.reply.wait(20)
            return self.answer
        if type_ == "definitely-not-an-action":
            raise ValueError("Unknown action: " + type_)
        return {"echo": type_}

    def list_dir(self, path, want_files=None):
        return {"path": str(Path(path).parent), "dirs": [], "files": [], "parent": None}

    def project_info(self, key):
        if key not in self.granted:
            raise PolicyError("Unknown project.")
        return {"key": key, "name": Path(str(key)).name}

    def set_reply(self, request_id, reply):
        self.answer = reply
        self.reply.set()

    def note_failure(self, reference, detail):
        # The real controller writes this to the Activity log; the stub only has to remember that
        # the server asked, because the pair — a fixed sentence out, the reason in — is the contract.
        self.failures.append((reference, detail))


class TransportHardening(unittest.TestCase):
    """Who the server will talk to, and what it says when it answers.

    A token proves the request knows the session. It does not prove the request came from the page
    that holds it, which is the difference `Host` and `Origin` make — and it says nothing at all to
    the browser about what that page may run, which is what the Content-Security-Policy is for.
    """

    @classmethod
    def setUpClass(cls):
        cls.stub = Stub()
        cls.server, cls.url, cls.token = server_module.serve(cls.stub)
        # Registered first so it runs last: shutdown() stops the loop, server_close() returns the
        # listening socket. Only the first was called, so each class leaked a bound port.
        cls.addClassCleanup(cls.server.server_close)
        cls.addClassCleanup(cls.server.shutdown)
        cls.base = cls.url.rsplit("?", 1)[0]
        cls.authority = urllib.parse.urlparse(cls.base).netloc

    def _send(self, path, headers=None, body=None, token=None, query=""):
        url = self.base + path + "?t=" + urllib.parse.quote(token if token is not None else self.token) + query
        request = urllib.request.Request(url, data=body, headers={"Connection": "close", **(headers or {})})
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                return response.status, response.headers, response.read()
        except urllib.error.HTTPError as exc:
            return exc.code, exc.headers, exc.read()

    def test_a_host_that_is_not_this_server_is_refused(self):
        # DNS rebinding: the attacker's hostname resolves to 127.0.0.1 inside the victim's browser,
        # so the request arrives on loopback with somebody else's name in it. The token would still
        # be needed to answer it — but this is the header that says whether the page asking is the
        # page this server was opened for.
        for host in ("evil.example", "127.0.0.1.evil.example", "localhost.attacker.io"):
            code, _headers, body = self._send("api/bootstrap", {"Host": host})
            self.assertEqual(code, 425, host)
            self.assertNotIn(self.token, body.decode(), "a refusal must not echo the session token")

    def test_the_loopback_names_this_server_accepts(self):
        for host in (self.authority, "127.0.0.1:%d" % self.server.server_address[1],
                     "localhost:%d" % self.server.server_address[1]):
            code, _headers, _body = self._send("api/bootstrap", {"Host": host})
            self.assertEqual(code, 200, host)

    def test_a_wrong_port_is_not_this_server_either(self):
        code, _h, _b = self._send("api/bootstrap", {"Host": "127.0.0.1:%d" % (self.server.server_address[1] + 1)})
        self.assertEqual(code, 425)

    def test_a_request_from_another_page_is_refused(self):
        for origin in ("http://evil.example", "http://127.0.0.1:9999", "https://localhost",
                      # A loopback page on the default port is not this page: the server is http on
                      # one random port, and an Origin states the port it lived on.
                      "http://localhost", "http://127.0.0.1",
                      # file:// and a sandboxed frame both arrive as `null`.
                      "null"):
            code, _h, _b = self.post({"Origin": origin}, {"type": "new_chat"})
            self.assertEqual(code, 403, origin)

    def test_the_page_this_server_serves_is_the_only_one_asking(self):
        code, _h, _b = self.post({"Origin": "http://" + self.authority}, {"type": "new_chat"})
        self.assertEqual(code, 200)
        code, _h, _b = self.post({"Referer": "http://%s/index.html" % self.authority}, {"type": "new_chat"})
        self.assertEqual(code, 200)
        # The other loopback name for the same port is the same page.
        port = self.server.server_address[1]
        code, _h, _b = self.post({"Origin": "http://localhost:%d" % port}, {"type": "new_chat"})
        self.assertEqual(code, 200)

    def test_a_request_with_no_origin_at_all_is_still_authenticated_by_its_token(self):
        # curl and the test client send no Origin; the token is what carries them. This is the
        # defence-in-depth rule stated rather than assumed.
        code, _h, _b = self.post({}, {"type": "new_chat"})
        self.assertEqual(code, 200)
        code, _h, _b = self.post({"Origin": "http://evil.example"}, {"type": "new_chat"}, token="wrong")
        self.assertEqual(code, 403)

    def post(self, headers, payload, token=None):
        return self._send("api/action", headers, json.dumps(payload).encode(), token)

    def test_every_answer_carries_the_policy(self):
        for path, extra in (("index.html", ""), ("api/bootstrap", ""), ("nope.css", ""),
                            ("api/project", "&project=demo2")):
            url = self.base + path + "?t=" + urllib.parse.quote(self.token) + extra
            request = urllib.request.Request(url, headers={"Connection": "close"})
            try:
                with urllib.request.urlopen(request, timeout=10) as response:
                    headers = response.headers
            except urllib.error.HTTPError as exc:      # a 404 has to answer like every other response
                headers = exc.headers
            directives = {part.split()[0]: part.strip() for part in
                          headers["content-security-policy"].split(";") if part.strip()}
            # The directive that decides whether a page can run code. `style-src` keeps its
            # 'unsafe-inline' on purpose: eight template strings set layout values inline, and a
            # style cannot execute script. So this asserts on script-src alone, not the whole header.
            self.assertEqual(directives["script-src"], "script-src 'self'", path)
            self.assertEqual(directives["default-src"], "default-src 'none'", path)
            self.assertEqual(directives["frame-ancestors"], "frame-ancestors 'none'", path)
            self.assertEqual(headers["x-frame-options"], "DENY", path)
            self.assertEqual(headers["x-content-type-options"], "nosniff", path)
            self.assertEqual(headers["referrer-policy"], "no-referrer", path)

    def test_the_refusal_answers_carry_it_too(self):
        for headers in ({"Host": "evil.example"}, {"Origin": "http://evil.example"}):
            code, response, _body = self._send("api/bootstrap", headers)
            self.assertIn("content-security-policy", response, headers)

    def test_the_boot_script_is_a_file_because_the_policy_has_no_unsafe_inline(self):
        # index.html used to set the theme in an inline <script> before first paint. A script-src
        # that means it would block that, and 'unsafe-inline' would block nothing — so it is a file,
        # which still runs before the body is parsed.
        index = Path(server_module.STATIC, "index.html").read_text(encoding="utf-8")
        self.assertNotIn("<script>", index)
        self.assertIn('<script src="boot.js"></script>', index)
        code, headers, body = self._send("boot.js")
        self.assertEqual(code, 200)
        self.assertIn("text/javascript", headers["content-type"])
        self.assertIn("dataset.style", body.decode())

    def test_an_agent_error_keeps_its_own_sentence(self):
        # A PolicyError was written by this program to be read in the UI, so a 400 still says what
        # went wrong. The removal is only for somebody else's exception, and the two are told apart
        # by type — not by whether the text happens to look harmless.
        code, _h, body = self._send("api/project", query="&project=elsewhere")
        self.assertEqual(code, 400)
        self.assertEqual(body.decode(), "Unknown project.")

    def test_a_failure_the_server_did_not_expect_keeps_its_reason_off_the_wire(self):
        code, _h, body = self.post({}, {"type": "definitely-not-an-action"})
        text = body.decode()
        self.assertEqual(code, 500)
        self.assertNotIn("ValueError", text)
        self.assertNotIn("definitely-not-an-action", text)
        self.assertIn("Activity log", text)


class HttpBoundaryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.stub = Stub()
        cls.server, cls.url, cls.token = server_module.serve(cls.stub)
        # Registered first so it runs last: shutdown() stops the loop, server_close() returns the
        # listening socket. Only the first was called, so each class leaked a bound port.
        cls.addClassCleanup(cls.server.server_close)
        cls.addClassCleanup(cls.server.shutdown)
        cls.base = cls.url.rsplit("?", 1)[0]

    def get(self, path, token=None, query="", headers=None):
        token = self.token if token is None else token
        url = self.base + path + "?t=" + urllib.parse.quote(token if token is not None else "") + query
        return self._open(url, headers=headers)

    def _open(self, url, body=None, headers=None):
        request = urllib.request.Request(url, data=body,
                                         headers={"Connection": "close", **(headers or {})})
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                return response.status, response.read()
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read()

    def post(self, path, payload, token=None):
        token = self.token if token is None else token
        return self._open(self.base + path + "?t=" + urllib.parse.quote(token),
                          json.dumps(payload).encode())

    # ------------------------------ the token ------------------------------
    def test_api_without_a_token_is_refused(self):
        code, _body = self._open(self.base + "api/bootstrap")
        self.assertEqual(code, 403)

    def test_a_wrong_token_is_refused(self):
        code, _body = self.get("/api/bootstrap", token="not-the-launch-token")
        self.assertEqual(code, 403)

    def test_a_post_without_a_token_never_reaches_the_controller(self):
        before = len(self.stub.received)
        code, _body = self._open(self.base + "api/action", json.dumps({"type": "send"}).encode())
        self.assertEqual(code, 403)
        self.assertEqual(len(self.stub.received), before)

    def test_the_bootstrap_payload_arrives(self):
        code, body = self.get("/api/bootstrap")
        self.assertEqual(code, 200)
        self.assertEqual(json.loads(body), {"ok": True, "busy": False})

    def test_the_events_stream_refuses_an_unauthenticated_listener(self):
        """Accepted, this route blocks for the life of the tab — so the token has to fail first."""
        code, body = self._open(self.base + "api/events")
        self.assertEqual(code, 403)
        self.assertIn(b"authorised", body)

    def test_an_empty_launch_token_authors_nobody(self):
        """Two empty strings compare equal, so a handler that never got one must answer nothing."""
        bound = self.server.RequestHandlerClass
        saved = bound.token
        bound.token = ""
        self.addCleanup(setattr, bound, "token", saved)
        self.assertEqual(self.get("/api/bootstrap", token="")[0], 403)
        self.assertEqual(self.post("/api/action", {"type": "new_chat"})[0], 403)

    def test_the_same_token_travels_in_a_header_as_in_the_query(self):
        """The query string is what the launch URL carries, and it is also what a proxy log, a
        history entry and a referrer would repeat — so the header form exists. Both must open the
        same doors, or the header quietly becomes a second, untested way in."""
        url = self.base + "api/bootstrap"
        self.assertEqual(self._open(url, headers={"X-Auth-Token": self.token})[0], 200)
        self.assertEqual(self._open(url, headers={"X-Auth-Token": "not-the-launch-token"})[0], 403)
        # A header must not outrank a query that is present and wrong.
        self.assertEqual(self.get("/api/bootstrap", token="wrong",
                                 headers={"X-Auth-Token": self.token})[0], 403)
        self.assertEqual(self._open(self.base + "api/action", json.dumps({"type": "new_chat"}).encode(),
                                    {"X-Auth-Token": self.token})[0], 200)

    def test_a_closed_stream_takes_its_subscriber_out_of_the_hub(self):
        """A tab that is closed, reloaded or navigated away from is the normal way this endpoint
        ends, and every queue the hub keeps holding is a queue the agent keeps filling.

        The handler only learns the socket is gone when it next writes, so the disconnect is
        followed by a publish — that write is what has to fail, and the cleanup has to run anyway.
        """
        hub = self.server.RequestHandlerClass.hub
        port = self.server.server_address[1]
        before = len(hub._clients)
        client = socket.create_connection(("127.0.0.1", port), timeout=10)
        client.sendall(("GET /api/events?t=%s HTTP/1.1\r\nHost: 127.0.0.1:%d\r\n"
                        "Connection: close\r\n\r\n" % (urllib.parse.quote(self.token), port)).encode())
        header = client.recv(4096)
        self.assertIn(b"200", header.split(b"\r\n", 1)[0], header[:80])
        # SO_LINGER with a zero timeout sends RST rather than FIN, so the next server-side write
        # cannot succeed into a buffer the peer will never read.
        client.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0))
        client.close()
        deadline = time.monotonic() + 10
        while len(hub._clients) >= before + 1 and time.monotonic() < deadline:
            hub.publish({"kind": "log", "entry": {"kind": "job", "text": "write-me", "ts": ""}})
            threading.Event().wait(0.05)
        self.assertEqual(len(hub._clients), before,
                         "the stream left its client queued — a slow leak per closed tab")
        # And the server is still serving on the same session afterwards.
        self.assertEqual(self.get("/api/bootstrap")[0], 200)

    def test_two_servers_in_one_process_do_not_share_a_session(self):
        """The desktop launcher can be asked for a second window, and the second `serve()` used to
        write its token and its controller onto the *shared* handler class — which did not open a
        second door, it replaced the first one's lock."""
        other = Stub()
        server2, url2, token2 = server_module.serve(other)
        self.addCleanup(server2.server_close)
        self.addCleanup(server2.shutdown)
        base2 = url2.rsplit("?", 1)[0]
        self.assertNotEqual(token2, self.token)
        self.assertIsNot(self.server.RequestHandlerClass, server2.RequestHandlerClass)
        self.assertEqual(self.get("/api/bootstrap")[0], 200)
        # Each token opens only its own server, and each server answers from its own controller.
        code, body = self.get("/api/bootstrap")
        self.assertEqual(json.loads(body), self.stub.snapshot())
        code, body = self._open(base2 + "api/bootstrap?t=" + urllib.parse.quote(token2))
        self.assertEqual(code, 200)
        self.assertEqual(json.loads(body), other.snapshot())
        self.assertEqual(self._open(base2 + "api/bootstrap?t=" + urllib.parse.quote(self.token))[0], 403)
        self.assertEqual(self.get("/api/bootstrap", token=token2)[0], 403)
        # Each server knows only its own port: a request wearing the other window's address is
        # refused rather than answered with the right token.
        port1 = self.server.server_address[1]
        code, _body = self._open(base2 + "api/bootstrap?t=" + urllib.parse.quote(token2),
                                 headers={"Host": "127.0.0.1:%d" % port1})
        self.assertEqual(code, 425)

    # ------------------------- what a refused POST leaves behind -------------------------
    def test_a_refused_post_gets_its_answer_with_the_body_still_in_flight(self):
        oversized = b'{"type":"send","text":"' + b"z" * (4 * server_module.DRAIN_CHUNK) + b'"}'
        code, body = self._open(self.base + "api/action", oversized)
        self.assertEqual(code, 403)
        self.assertIn(b"authorised", body)
        self.assertEqual(self._open(self.base + "api/bootstrap?t=" + urllib.parse.quote(self.token))[0], 200)

    def test_an_unknown_post_endpoint_drains_before_it_answers(self):
        code, body = self._open(self.base + "api/nonsense?t=" + urllib.parse.quote(self.token),
                                json.dumps({"type": "new_chat"}).encode())
        self.assertEqual(code, 404)
        self.assertIn(b"Unknown", body)
        self.assertEqual(self._open(self.base + "api/bootstrap?t=" + urllib.parse.quote(self.token))[0], 200)

    def test_the_only_get_endpoints_are_the_ones_the_client_asks_for(self):
        self.assertEqual(self.get("/api/bootstrap")[0], 200)
        self.assertEqual(self.get("/api/fs", query="&path=C%3A")[0], 200)
        self.assertEqual(self.get("/api/project", query="&project=demo2")[0], 200)
        # /api/options was the endpoint no client called; a fourth route would be the same drift.
        self.assertEqual(self.get("/api/options")[0], 404)

    def test_the_timeout_the_browser_widget_advertises_is_the_one_the_server_clamps_to(self):
        """The number input in the settings drawer is static markup; the clamp is not."""
        import re
        from ai_code_engineer import config
        static = Path(__file__).resolve().parents[1] / "src/ai_code_engineer/webapp/static/app.js"
        widget = static.read_text(encoding="utf-8")
        self.assertIn(f'min="{config.REQUEST_TIMEOUT_LOW}" max="{config.REQUEST_TIMEOUT_HIGH}"', widget)
        self.assertEqual(config.clamp_request_timeout("5"), config.REQUEST_TIMEOUT_LOW)
        self.assertEqual(config.clamp_request_timeout(100000), config.REQUEST_TIMEOUT_HIGH)
        self.assertEqual(config.clamp_request_timeout("nonsense", 420), 420)
        self.assertEqual(config.clamp_request_timeout(None), config.REQUEST_TIMEOUT_DEFAULT)

    def test_an_unknown_project_answers_the_reason_rather_than_a_server_error(self):
        code, body = self.get("/api/project", query="&project=never-granted")
        self.assertEqual(code, 400)
        self.assertIn(b"Unknown project", body)

    def test_a_project_node_is_closed_until_the_user_opens_it(self):
        """The tree opens collapsed, and one click both ways has to work.

        Shape assertions rather than behaviour, because the decision is nine lines of browser JS
        and no Python test runs a DOM. The arm that matters is `chosen === undefined`: a node the
        user never touched falls back to the default, a node they did keeps their answer. The
        branch-in-front clause that used to sit in the same `||` chain re-opened the active
        project behind the click, so that project could never be collapsed.
        """
        import re
        static = Path(__file__).resolve().parents[1] / "src/ai_code_engineer/webapp/static/app.js"
        tree = re.sub(r"\s+", " ", static.read_text(encoding="utf-8"))
        self.assertIn("expanded: {}", tree)                                     # nothing remembered at boot
        self.assertIn("const open = chosen === undefined ? matched : chosen;", tree)
        self.assertIn("state.expanded[group.key] = !open", tree)                # and the click undoes itself
        self.assertIn('const matched = !!q && group.chats.some', tree)          # only a search may override
        for group in FakeController().snapshot()["projects"]:
            self.assertNotIn("open", group, f"{group['name']} must not decide its own arrow")

    # ------------------------------ static ------------------------------
    def test_bundled_assets_need_no_token_but_stay_inside_the_package(self):
        code, body = self._open(self.base + "app.css")
        self.assertEqual(code, 200)
        self.assertIn(b"--accent", body)
        for attempt in ("..%2f..%2fserver.py", "%2e%2e/%2e%2e/server.py", "nope.css"):
            self.assertEqual(self._open(self.base + attempt)[0], 404, attempt)

    def test_the_index_is_served_at_the_root(self):
        code, body = self._open(self.base)
        self.assertEqual(code, 200)
        self.assertIn(b"<title>AI Code Engineer</title>", body)

    # ------------------------------ actions ------------------------------
    def test_an_unknown_action_is_a_server_error_not_a_silent_success(self):
        code, body = self.post("/api/action", {"type": "definitely-not-an-action"})
        self.assertEqual(code, 500)
        self.assertIn("Activity log", body.decode())

    def test_the_reason_for_a_failed_request_never_travels_in_the_response(self):
        # The stub raises ValueError("Unknown action: …"), which is a message this program wrote and
        # harmless — and exactly the shape that a provider's 401 body or a Windows path also takes.
        # What the boundary does with an unknown exception cannot depend on what is inside it.
        code, body = self.post("/api/action", {"type": "definitely-not-an-action"})
        text = body.decode()
        self.assertEqual(code, 500)
        self.assertNotIn("ValueError", text)
        self.assertNotIn("definitely-not-an-action", text)
        reference = text.split("id ")[1].split(")")[0]
        recorded = dict((r, d) for r, d in self.stub.failures)
        self.assertIn(reference, recorded, "the id has to lead somewhere")
        self.assertIn("Unknown action", recorded[reference])

    def test_an_action_returns_the_state_the_client_re_renders_from(self):
        code, body = self.post("/api/action", {"type": "new_chat"})
        self.assertEqual(code, 200)
        self.assertEqual(json.loads(body)["state"], {"ok": True, "busy": False})

    def test_a_body_that_is_not_json_is_rejected(self):
        code, _body = self._open(self.base + "api/action?t=" + urllib.parse.quote(self.token), b"not json")
        self.assertEqual(code, 400)

    def test_an_oversized_body_is_refused(self):
        code, _body = self._open(self.base + "api/action?t=" + urllib.parse.quote(self.token),
                                 b'{"type":"x","pad":"' + b"z" * (server_module.MAX_BODY + 10) + b'"}')
        self.assertEqual(code, 400)

    def test_a_refused_body_is_drained_not_dropped(self):
        """A client still writing a rejected body must not have its socket pulled away.

        Windows answers that with ConnectionAbortedError [WinError 10053] instead of the 400,
        which is what made the oversized-body test fail in roughly one run in three.
        """
        oversized = b'{"type":"x","pad":"' + b"z" * (server_module.MAX_BODY
                                                     + 4 * server_module.DRAIN_CHUNK) + b'"}'
        code, body = self._open(self.base + "api/action?t=" + urllib.parse.quote(self.token), oversized)
        self.assertEqual(code, 400)
        self.assertIn(b"too large", body)
        # And the server is still serving afterwards.
        self.assertEqual(self._open(self.base + "api/bootstrap?t=" + urllib.parse.quote(self.token))[0], 200)

    # ------------------------- the blocked round trip -------------------------
    def test_a_confirm_event_blocks_the_worker_until_the_browser_answers(self):
        done = threading.Event()
        outcome = {}

        def call():
            code, body = self.post("/api/action", {"type": "blocks"})
            outcome["code"], outcome["body"] = code, json.loads(body)
            done.set()

        thread = threading.Thread(target=call, daemon=True)
        thread.start()
        time.sleep(0.5)
        # The action is still in flight: the controller is waiting on the front-end.
        self.assertNotIn("code", outcome)
        self.post("/api/confirm", {"id": "r1", "ok": True})
        self.assertTrue(done.wait(10), "the browser reply never woke the worker")
        thread.join(10)
        self.assertEqual(outcome["code"], 200)
        self.assertTrue(outcome["body"]["result"]["ok"])


class ControllerSurfaceTests(unittest.TestCase):
    """The server talks to a controller through a handful of named methods, and --fake has to
    answer every one of them: it is the window the interface is reviewed in, so a route that only
    the real controller serves makes the preview throw on the first click that opened it."""

    def server_calls(self):
        import inspect
        import re
        source = Path(inspect.getfile(server_module)).read_text(encoding="utf-8")
        called = set(re.findall(r"self\.controller\.(\w+)\(", source))
        self.assertTrue(called, "the server stopped calling the controller?")
        return called

    def test_both_controllers_answer_every_method_the_server_calls(self):
        for name in sorted(self.server_calls()):
            for cls in (AgentController, FakeController):
                self.assertTrue(callable(getattr(cls, name, None)),
                                f"{cls.__name__} does not answer {name}(), which the server calls")

    def client_actions(self):
        """Every action name the page can post, read out of the client source."""
        import re
        source = (Path(__file__).resolve().parents[1] /
                  "src/ai_code_engineer/webapp/static/app.js").read_text(encoding="utf-8")
        sent = set(re.findall(r"send(?:Quiet)?\(\s*'([a-z_]+)'", source))
        # A sheet that needs the reply's payload posts through `api()` directly, because `send()` drops
        # `result` on the floor. Both forms are the client posting an action, so both are collected --
        # a gate that only reads one of them stops covering the newest verb the day it is added.
        sent |= set(re.findall(r"api\(\s*'/api/action'\s*,\s*\{\s*type:\s*'([a-z_]+)'", source))
        # choose() builds its action from the kind: send('set_' + kind, ...).
        sent |= {"set_model", "set_mode", "set_recipe"}
        sent.discard("set_")
        self.assertIn("show_graph", sent, "the graph button stopped posting its action")
        self.assertGreater(len(sent), 20, "the client stopped sending actions?")
        return sent

    def test_every_action_the_client_sends_is_answered_by_both_controllers(self):
        """`--fake` is the window the interface gets reviewed in.

        An action it never learned used to fall off the end of its dispatch and change nothing at
        all, which is how the model filter sat dead in the preview for a week while every test
        stayed green. The real controller raises on an unknown action; the scripted one now says so
        out loud, and this test is what keeps the two lists honest.
        """
        import re
        from ai_code_engineer.webapp import controller as controller_module
        from ai_code_engineer.webapp import fake as fake_module
        real = Path(inspect.getfile(controller_module)).read_text(encoding="utf-8")
        block = real.split("handlers = {", 1)[1].split("\n        }", 1)[0]
        handled = set(re.findall(r'"([a-z_]+)":', block))
        self.assertGreater(len(handled), 20, "the dispatch table was not found where it lives")
        self.assertEqual(sorted(self.client_actions() - handled), [],
                         "the page posts actions the controller refuses")

        scripted = Path(inspect.getfile(fake_module)).read_text(encoding="utf-8")
        answered = set(re.findall(r'type == "([a-z_]+)"', scripted)) | set(fake_module.PREVIEW_ONLY)
        self.assertEqual(sorted(self.client_actions() - answered), [],
                         "the preview window ignores these in silence")

    # The preview's blocking dialogs and its model-backed job are this file's other tests'
    # business; calling them here would wait for a browser that is not there.
    ASYNC_OR_BLOCKING = {"send", "apply", "run", "pick_project", "pick_plan", "queue_resume"}

    def test_the_scripted_handlers_execute_and_not_only_exist(self):
        """The parity test above is a regex over source, which is how a scripted handler that dies
        on its first line still passes: `git_branch` was answered by the preview in every grep and
        raised a NameError the moment the chip was actually clicked."""
        events = []
        controller = FakeController()
        payload = {"value": "120", "text": "one more thing", "id": "q3", "index": 0,
                   "tab": "diff", "style": "midnight", "chat": "c-1", "path": "a.py",
                   "kind": "session", "back": ""}
        for name in sorted(self.client_actions() - self.ASYNC_OR_BLOCKING):
            try:
                controller.action(name, dict(payload, type=name), events.append)
            except Exception as exc:                       # noqa: BLE001 - raising IS the failure
                self.fail(f"the preview raised on {name}: {type(exc).__name__}: {exc}")
        self.assertEqual(controller.git_base, "main", "the chip's scripted branch never happened")
        self.assertTrue(controller.branch.startswith("agent/task-"), controller.branch)

    def test_the_contract_declares_exactly_the_methods_the_server_calls(self):
        import re
        from ai_code_engineer.webapp import contract
        source = Path(inspect.getfile(contract)).read_text(encoding="utf-8")
        declared = set(re.findall(r"\n    def (\w+)\(", source))
        self.assertEqual(declared, self.server_calls(),
                         "the Protocol and the server now disagree about what a controller owes")

    def test_every_scripted_project_answers_the_drawer_with_the_same_fields(self):
        controller = FakeController()
        first = controller.project_info("demo2")
        for key in ("demo2", "demo_repo"):
            info = controller.project_info(key)
            self.assertEqual(set(info), set(first), f"{key} sends a different drawer payload")
            self.assertEqual(set(info["context"]), set(first["context"]))
            self.assertEqual(set(info["toolchain"]), set(first["toolchain"]))
        with self.assertRaises(PolicyError):
            controller.project_info("never-granted")

    def test_the_preview_projects_are_the_ones_the_sidebar_lists(self):
        listed = {group["key"] for group in FakeController().snapshot()["projects"]}
        self.assertEqual(listed, set(PROJECT_INFO),
                         "a sidebar folder whose drawer cannot be answered")


    def test_the_preview_snapshot_carries_every_field_the_client_reads(self):
        """`--fake` is the window the design is reviewed in. A field only the real controller
        sends is a control that renders as nothing there, and nobody notices for a week."""
        import re
        static = Path(__file__).resolve().parents[1] / "src/ai_code_engineer/webapp/static/app.js"
        source = static.read_text(encoding="utf-8")
        reads = set(re.findall(r"DATA\.(\w+)", source))
        snapshot = FakeController().snapshot()
        self.assertEqual(sorted(reads - set(snapshot)), [],
                         "the client reads fields the scripted controller never sends")
        branches = set(re.findall(r"(?:DATA\.branch|\bbranch)\s*(?:\|\| \{\})?\.(\w+)", source))
        self.assertEqual(sorted(branches - set(snapshot["branch"])), [],
                         "the client reads branch fields the preview never sends")

    def test_the_real_and_scripted_snapshots_agree_on_their_top_level_keys(self):
        import tempfile
        with tempfile.TemporaryDirectory() as temp:
            controller = AgentController(Path(temp))
            self.addCleanup(controller.close)
            scripted = set(FakeController().snapshot())
            real = set(controller.snapshot())
            self.assertEqual(sorted(scripted - real), [],
                             "the preview sends a field the real controller never does")


class ThePreviewKnowsTheThreePositions(unittest.TestCase):
    """`--fake` is the window the design is reviewed in, so a mode has to be reviewable there.

    A preview that only ever takes the yes path lets a refusal ship as a colour change: the badge, the
    row it adds, the switch it disarms and the question a command asks all have to be reachable here.
    """

    def preview(self, mode="read"):
        controller = FakeController()
        events: list = []
        if mode:
            controller.action("set_composer", {"value": mode}, events.append)
        return controller, events

    def test_a_name_nobody_sent_lands_on_the_promise_that_writes_nothing(self):
        controller, events = self.preview(mode="")
        controller.action("set_composer", {"value": "READ-ONLY "}, events.append)
        self.assertEqual(controller.composer, "chat")

    def test_an_imperative_is_answered_with_an_explanation_and_no_proposal(self):
        controller, events = self.preview()
        controller.action("send", {"text": "fix the duplicate email guard"}, events.append)
        said = "\n".join(str(event["message"].get("text", ""))
                         for event in events if event.get("kind") == "message")
        self.assertIn(intent.no_proposal(), said)
        # The preview always opens with a scripted proposal on screen, so what is being checked here is
        # that this Send did not start the proposal job at all.
        self.assertFalse(controller.busy, "the scripted proposal job was started anyway")
        self.assertNotIn("propose", [row["kind"] for row in controller.log])

    def test_apply_rollback_and_a_block_are_refused_before_they_are_offered(self):
        controller, events = self.preview()
        review = controller.snapshot()["review"]
        self.assertFalse(review["canApply"], "the button is not drawn either")
        self.assertFalse(review["canRollback"])
        controller.action("apply", {}, events.append)
        self.assertIn(intent.no_write("Apply"), controller.status_line)
        controller.action("rollback", {}, events.append)
        self.assertIn(intent.no_write("Roll back"), controller.status_line)
        self.assertEqual(controller.state, "WAITING_APPROVAL", "a refusal changed the task's state")
        controller.action("apply_block", {"path": "a.py", "content": "x"}, events.append)
        self.assertIn(intent.no_proposal(), controller.status_line)

    def test_the_two_git_writes_are_refused_in_the_preview_too(self):
        """A branch switch rewrites tracked files and a restore replaces bytes, so both are writes by
        any definition — and they were the two the shipped window still allowed in Read-only."""
        controller, events = self.preview()
        controller.action("git_branch", {}, events.append)
        self.assertIn(intent.no_write("Switching branches"), controller.status_line)
        self.assertEqual(controller.branch, "main", "a refusal moved HEAD anyway")
        controller.action("git_restore", {}, events.append)
        self.assertIn(intent.no_write("Restoring files from git"), controller.status_line)

    def test_the_preview_carries_the_folder_s_own_declaration(self):
        """`declared` is the field the lock on the badge is drawn from; a preview without it cannot
        review the one part of this change a person has to be able to recognise at a glance."""
        controller, events = self.preview()
        declared = controller.snapshot()["declared"]
        self.assertTrue(declared["sealed"])
        self.assertEqual(declared["by"], "the web window")
        self.assertEqual(declared["note"], "", "badge and fact agree, so there is nothing to explain")
        controller.action("set_composer", {"value": "chat"}, events.append)
        self.assertEqual(controller.snapshot()["declared"]["note"],
                         intent.followed("web", declared["at"]),
                         "choosing Chat over a sealed folder leaves it sealed, and says so")

    def test_the_write_switch_cannot_be_armed_and_rearming_the_mode_does_not_either(self):
        controller, events = self.preview()
        controller.action("set_auto_apply", {"value": True}, events.append)
        self.assertIn(intent.no_auto_apply(), controller.status_line)
        self.assertFalse(controller.auto_apply)
        controller.action("set_composer", {"value": "change"}, events.append)
        self.assertFalse(controller.auto_apply, "switching back armed what the refusal prevented")

    def test_a_command_the_preview_may_not_run_asks_before_it_does(self):
        controller, events = self.preview()
        asked = threading.Thread(target=lambda: controller.action("run", {"fix": False}, events.append))
        asked.start()
        deadline = time.time() + 5
        question = next((event for event in events if event.get("kind") == "confirm"), None)
        while question is None and time.time() < deadline:
            time.sleep(0.01)
            question = next((event for event in events if event.get("kind") == "confirm"), None)
        self.assertIsNotNone(question, "the preview ran without asking")
        self.assertIn("mvn -B test", question["message"])
        self.assertEqual(question["confirm"], "Run it")
        runs = controller.runs
        controller.set_reply(question["id"], {"ok": False})
        asked.join(5)
        self.assertEqual(controller.runs, runs, "the answer was no")
        self.assertIn(intent.run_declined(), controller.status_line)


class TheWriteCardAndTheChips(unittest.TestCase):
    """UI 3.1: the notice is written by the server, and one diff painter serves two surfaces.

    These read the client source because no Python test runs a DOM. What they hold still is the
    part that drifts silently: a sentence rebuilt in JavaScript loses the language the server
    chose, and a second diff loop in the rail stops following the rail's own tabs.
    """

    @classmethod
    def setUpClass(cls):
        static = Path(__file__).resolve().parents[1] / "src/ai_code_engineer/webapp/static"
        cls.js = (static / "app.js").read_text(encoding="utf-8")
        cls.html = (static / "index.html").read_text(encoding="utf-8")
        cls.flat = " ".join(cls.js.split())

    def test_the_banner_is_a_sentence_the_server_wrote(self):
        self.assertIn("DATA.banner.text", self.js,
                      "the card rebuilt this sentence from a count, which is how it stayed "
                      "English under an Arabic task")
        self.assertNotIn("Number(DATA.banner)", self.js, "the count is no longer prose's raw material")

    def test_both_controllers_answer_the_banner_with_the_same_fields(self):
        import tempfile
        with tempfile.TemporaryDirectory() as temp:
            real = AgentController(Path(temp))
            self.addCleanup(real.close)
            self.assertEqual(sorted(real.snapshot()["banner"]), sorted(FakeController().snapshot()["banner"]))

    def test_both_controllers_say_whether_the_files_are_on_disk(self):
        import tempfile
        with tempfile.TemporaryDirectory() as temp:
            real = AgentController(Path(temp))
            self.addCleanup(real.close)
            for card in (real.snapshot()["artifact"], FakeController().snapshot()["artifact"]):
                self.assertIn("written", card,
                              "a chip has to say proposed or saved, and only these two can tell it")

    def test_one_diff_painter_feeds_the_view_and_the_rail(self):
        self.assertEqual(self.js.count("function paintDiff("), 1)
        self.assertEqual(self.js.count("function paintCode("), 1)
        # One definition plus one call site: an inline second loop is what would stop following
        # the rail's tabs, and it is exactly what renderReview used to hold.
        self.assertEqual(self.js.count("diffRows("), 2, "the row model has grown a second caller")

    def test_a_file_list_drives_the_chips_from_the_review_block(self):
        """The messages on disk carry no paths, so the chips read the live review block — which
        is also why they cannot go stale after a rollback."""
        self.assertIn("chipCard(DATA.review)", self.js)
        self.assertIn("el('button', 'file-chip'", self.js)
        self.assertIn("esc(f.path)", self.js)
        self.assertIn("+ kindTag(f.kind)", self.js,
                      "one badge builder for every file surface, so a delete cannot be drawn as M")
        self.assertIn("chip.onclick = () => openFile(i)", self.js)
        self.assertEqual(self.js.count("openFile(i)"), 2,
                         "a chip and a rail row are the same control; a third surface would not be")

    def test_a_preview_the_rail_cannot_hold_opens_in_a_sheet(self):
        """UI 4.0 deleted the pane this used to fall back to: below 1180px the same card is mounted
        in a modal sheet, so a chip answers at every width without leaving Chat."""
        self.assertIn("if (railHidden()) viewerSheet();", self.js)
        body = self.js.split("function viewerSheet()")[1].split("\n}\n")[0]
        self.assertIn("modal(box", body, "the sheet is the window's own dialog, not a new overlay")
        self.assertNotIn("switchView('review')", self.js,
                         "the pane is gone; a call that switches to it is a dead end")

    def test_a_tool_row_takes_its_direction_from_its_own_text(self):
        """dir="auto" reads the first strong character, and a tool row opens with its English
        author label, so an Arabic notice can never resolve to RTL on its own."""
        self.assertIn("ARABIC_RUN.test(m.text) ? 'rtl' : 'auto'", self.flat)


class TheChangesPaneIsGone(unittest.TestCase):
    """UI 4.0: the Changes pane was deleted, not hidden — "ملهاش لازمة … عايز أشيلها خالص".

    A removal needs a test that the removal is the current shape, or the next refactor "restores"
    it. What the pane owned went to the surfaces beside the chat: its three decisions to the rail's
    artifact card and to the preview, its Checks tab to the preview, and its narrow-width job to a
    sheet. Deleting it deleted no data — the rail and the chips read the same snapshot block.
    """

    @classmethod
    def setUpClass(cls):
        static = Path(__file__).resolve().parents[1] / "src/ai_code_engineer/webapp/static"
        cls.js = (static / "app.js").read_text(encoding="utf-8")
        cls.html = (static / "index.html").read_text(encoding="utf-8")
        cls.css = (static / "app.css").read_text(encoding="utf-8")
        cls.flat = " ".join(cls.js.split())

    def test_the_markup_has_no_third_view_and_no_tab_for_it(self):
        self.assertNotIn("view-review", self.html)
        self.assertNotIn('data-view="review"', self.html)
        self.assertNotIn(">Changes</button>", self.html, "the tab name is gone; the word stays in sentences")
        for kept in ('data-view="task"', 'data-view="details"', 'id="view-task"', 'id="view-details"'):
            self.assertIn(kept, self.html, f"the pane removal took {kept} with it")

    def test_the_client_has_no_pane_left_to_render(self):
        self.assertNotIn("renderReview", self.js)
        self.assertNotIn("switchView('review')", self.js)
        self.assertIn("for (const id of ['task', 'details'])", self.js,
                      "markTabs still toggles a view nobody can switch to")
        self.assertNotIn("ICON.ext", self.js, "the ↗ link that used it is gone")

    def test_the_rails_two_surfaces_hold_the_three_decisions(self):
        """Apply must stay a visible click: D31 means a delete never auto-applies, and "you review
        first" is only true while there is somewhere on screen to do the clicking."""
        self.assertEqual(self.js.count("function changeActions("), 1, "one builder, two hosts")
        self.assertIn("changeActions(r, art);", self.js)
        self.assertIn("changeActions(r, card);", self.js)
        self.assertIn("apply.disabled = !r.canApply;", self.js)
        self.assertIn("verify.disabled = !r.canMutate;", self.js)
        self.assertIn("undo.disabled = !r.canRollback;", self.js,
                      "an interrupted apply is exactly when the escape has to be on screen")

    def test_a_sealed_folder_is_drawn_locked_on_the_badge(self):
        """The lock is the only thing that separates "I chose Read-only" from "this folder was chosen
        for", and a field the front end never reads would leave the two looking identical."""
        self.assertIn("const declared = DATA.declared || {};", self.js)
        self.assertIn("if (declared.sealed) {", self.js)
        self.assertIn("b.classList.add('sealed');", self.js)
        self.assertIn("b.insertAdjacentHTML('afterbegin', ICON.lock + ' ');", self.js)
        self.assertIn("if (declared.note) b.title = declared.note;", self.js)
        self.assertIn(".pill.mode.sealed {", self.css)
        self.assertIn("lock: '<svg", self.js, "the glyph has to exist in the table the badge reads")
        self.assertEqual(self.js.count("const ICON = {"), 1)

    def test_the_preview_carries_the_panes_checks_tab(self):
        self.assertIn("['checks', 'Checks']", self.js)
        self.assertIn("(view.checks || [])", self.js)
        self.assertIn("el('div', 'pv-checks', esc(lines.join('\\n')))", self.js,
                      "server-built sentences drawn as prose, never re-assembled from counts")
        self.assertIn(".pv-checks {", self.css)
        self.assertIn(".pv-acts {", self.css)

    def test_a_proposal_shows_itself_without_moving_the_user_off_chat(self):
        """The emit arrives from the worker before the snapshot that carries the change set, so the
        intent is recorded and taken up by the render that has the files."""
        self.assertIn("case 'view': showView(msg.value); break;", self.js)
        self.assertIn("state.previewWant = asClick ? 'sheet' : 'rail';", self.js)
        self.assertIn("if (!state.previewWant) return;", self.js)
        self.assertIn("renderRail(); renderLog();", self.js)
        self.assertIn("takePreviewOffer();", self.flat.split("renderRail(); renderLog();")[1][:120],
                      "the render never picks the intent up, so a proposal stays invisible")

    def test_the_old_bookmark_still_opens_something(self):
        self.assertIn("if (PARAMS.get('view')) showView(PARAMS.get('view'), true);", self.js)
        self.assertIn("if (value !== 'review' && value !== 'preview') { switchView(value); return; }", self.js)

    def test_the_panes_own_css_rules_went_with_it(self):
        for rule in (".work {", ".banner {", ".files {", ".frow", ".diff {"):
            self.assertNotIn(rule, self.css, f"{rule} styles a surface that no longer exists")
        self.assertIn(".pv-sheet .pv-body {", self.css, "the sheet needs the height the rail cannot spare")

    def test_closing_the_sheet_leaves_no_preview_behind(self):
        """Found in the live window: the rail is display:none at that width, so its copy of the
         preview stayed in the DOM after the sheet closed — and a widened window showed a file the
         user had just dismissed."""
        body = self.js.split("function viewerSheet()")[1].split("\n}\n")[0]
        self.assertIn("SHEET = null; state.railFile = -1; renderRail();", body)

    def test_the_snapshot_still_sends_every_field_the_viewer_reads(self):
        """The pane was a consumer of `review`, not its owner: deleting it must not shave a field."""
        import tempfile
        needed = {"state", "tone", "title", "detail", "canApply", "canMutate", "canRollback",
                  "files", "selected", "tab", "view"}
        with tempfile.TemporaryDirectory() as temp:
            real = AgentController(Path(temp))
            self.addCleanup(real.close)
            for block in (real.snapshot()["review"], FakeController().snapshot()["review"]):
                self.assertEqual(sorted(needed - set(block)), [])
                self.assertEqual(sorted(block["view"]), ["after", "before", "checks", "diff"])


class AnAnswerThatArrivesWhileYouWait(unittest.TestCase):
    """Phase 3 on the page: the reply is painted from the fragments, and the two windows agree on it.

    `case 'token'` sat dead in the client for four rounds — the stub existed, no Python file ever sent
    the event. So the guard here is not only that the page draws a stream, but that every kind it
    listens for is one a server can actually emit, which is the class of lie a source-string test on
    one side alone cannot catch.
    """

    @classmethod
    def setUpClass(cls):
        static = Path(__file__).resolve().parents[1] / "src/ai_code_engineer/webapp/static"
        cls.js = (static / "app.js").read_text(encoding="utf-8")
        cls.flat = " ".join(cls.js.split())
        package = Path(__file__).resolve().parents[1] / "src/ai_code_engineer"
        cls.servers = "".join((package / name).read_text(encoding="utf-8") for name in
                              ("host.py", "webapp/controller.py", "webapp/fake.py", "webapp/server.py"))

    def test_every_event_kind_the_page_listens_for_is_one_a_server_can_send(self):
        import re
        heard = set(re.findall(r"case '([a-z_]+)':", self.js))
        sent = set(re.findall(r"\"kind\": \"([a-z_]+)\"", self.servers))
        # Three of these kinds are written once and passed as a variable, so a regex on the literal
        # alone would call them dead: the shared stream sink, and the ask helper that blocks on them.
        sent |= set(re.findall(r"self\._stream\([^)]*\"([a-z_]+)\"", self.servers))
        sent |= set(re.findall(r"_ask\(\"([a-z_]+)\"", self.servers))
        self.assertEqual(sorted(heard - sent), [],
                         "the page handles an event nothing emits, which is a silent feature")

    def test_the_stream_is_painted_from_a_buffer_of_its_own(self):
        """Not from DATA.messages: a snapshot goes out on other events, and a half-answer held only in
        the message list would be wiped mid-sentence by a push that knows nothing about it."""
        self.assertIn("let STREAM = ''", self.js)
        self.assertNotIn("last.text += text", self.js,
                         "the reply was appended to a message that the next snapshot would replace")
        self.assertIn("STREAM || DATA.pending", self.flat)

    def test_an_arriving_line_is_escaped_and_the_first_one_makes_its_own_bubble(self):
        body = self.js.split("function appendToken(")[1].split("\n}\n")[0]
        self.assertIn("renderThread();", body, "the first chunk arrives before the bubble exists")
        self.assertIn("$('scroller').scrollTop", body)
        self.assertIn('<span class="typing-line">${esc(STREAM || DATA.pending)}</span>', self.js)

    def test_the_buffer_is_cleared_at_both_ends_of_a_job(self):
        # a stale stream would reappear under the next question, and the finished answer must not
        # be shown twice: once as arrived text and once as the stored message
        self.assertIn("if ((msg.message || {}).role === 'assistant') STREAM = '';", self.js)
        self.assertIn("if (busy && (LIVE.length || STREAM)) { LIVE.length = 0; STREAM = '';", self.js)

    def test_the_preview_streams_its_read_only_answer(self):
        controller = FakeController()
        events = []
        controller.action("set_composer", {"value": "read"}, events.append)
        controller.action("send", {"text": "where is the duplicate guard?"}, events.append)
        answer = "The duplicate-email guard lives in `UserService.create()`"
        deadline = time.time() + 8
        while time.time() < deadline and not any(message["text"].startswith(answer[:40])
                                                 for message in controller.messages):
            time.sleep(0.05)
        kinds = [event["kind"] for event in events]
        self.assertIn("token", kinds, kinds)
        self.assertGreater(sum(1 for kind in kinds if kind == "token"), 1,
                           "one chunk is not a stream")
        self.assertFalse(controller.busy, "the preview claims no job for an answer that streams")
        first = next(index for index, event in enumerate(events) if event["kind"] == "token")
        self.assertTrue(all(event["text"] and event["ts"] for event in events[first:first + 2]))
        self.assertLess(first, len(events) - 1, "the answer lands after the pieces of it")
        self.assertTrue(any(message["text"].startswith(answer[:40]) and
                            message["role"] == "assistant" for message in controller.messages),
                        "the streamed answer has to land as the message it was cut from")


class TheStepRowsInTheThread(unittest.TestCase):
    """UI 4.1's client half: a row is a handle, the sentence is the server's, and the chevron appears
    only when the server has said there is something behind it.

    These are source assertions because no Python test runs a DOM, so what gets pinned is the clauses
    that carry the decisions — the same reason `test_a_row_with_nothing_stored_behind_it_answers_in_words`
    sits on the other side of this file.
    """

    @classmethod
    def setUpClass(cls):
        static = Path(__file__).resolve().parents[1] / "src/ai_code_engineer/webapp/static"
        cls.js = (static / "app.js").read_text(encoding="utf-8")
        cls.css = (static / "app.css").read_text(encoding="utf-8")
        cls.flat = " ".join(cls.js.split())
        cls.row = cls.js.split("function stepRow(")[1].split("\n}\n")[0]
        cls.body = cls.js.split("function stepBody(")[1].split("\n}\n")[0]

    def test_the_row_prints_the_servers_sentence_rather_than_building_one(self):
        self.assertIn("esc(m.text)", self.row)
        self.assertNotIn("step.action +", self.row,
                         "the row rebuilt its own label, which is how an Arabic task got an English thread")

    def test_the_chevron_comes_from_the_servers_answer_with_one_client_exception(self):
        self.assertIn("(open || live || step.detail)", self.row)
        self.assertIn("DATA.busy && isLast && step.action === 'executing'", self.row,
                      "the running row is the one detail the client knows about without asking")

    def test_opening_fetches_and_closing_asks_for_nothing(self):
        toggle = self.js.split("function toggleStep(")[1].split("\n}\n")[0]
        self.assertIn("send('step_detail', { id })", toggle)
        self.assertIn("sendQuiet('step_detail', { id: '' })", toggle)
        self.assertIn("if (live) renderThread();", toggle,
                      "a running command was asked of the server, which has nothing stored yet")

    def test_the_body_draws_named_sections_and_paths_that_open_the_viewer(self):
        self.assertIn("for (const [title, lines] of (detail.sections || []))", self.body)
        self.assertIn("esc(detail.note)", self.body)
        self.assertIn("DATA.step_detail.id === step.id", self.body,
                      "one row's block must not be painted under another row's name")
        files = self.js.split("function stepFileRow(")[1].split("\n}\n")[0]
        self.assertIn("openFile(at)", files)
        self.assertIn("kindTag(known.kind)", files, "a path in a step wears the same badge as a chip")
        self.assertIn("no longer in the change set", files)

    def test_streamed_output_lands_inside_the_row_that_is_running(self):
        chunk = self.js.split("function pushChunk(")[1].split("\n}\n")[0]
        self.assertIn("'.steprow.live.open .st-out'", chunk)
        self.assertIn("while (host.children.length > LIVE_MAX)", chunk,
                      "an unbounded append would grow the open row forever")

    def test_a_row_opened_because_it_was_running_closes_when_the_job_ends(self):
        """Otherwise it sits open and empty: the stored block was never fetched, and the streaming
        output it was showing has moved on into Activity."""
        self.assertIn("if (state.openStep && !data.busy && !(data.step_detail"
                      " && data.step_detail.id === state.openStep)) {", self.flat)

    def test_the_activity_list_admits_what_the_cap_dropped(self):
        log = self.js.split("function renderLog(")[1].split("\n}\n")[0]
        self.assertIn("if (DATA.log_note)", log)
        self.assertIn("esc(DATA.log_note)", log, "the sentence is the server's, so it is only printed")

    def test_the_rows_have_a_style_of_their_own(self):
        for rule in (".steprow {", ".st-head {", ".st-chev {", ".st-body {", ".st-out {",
                     ".st-files {", ".st-note {", ".steprow.live .st-head {"):
            self.assertIn(rule, self.css, f"{rule} is missing, so the row renders as bare text")

    def test_the_row_does_not_borrow_the_plan_cards_class(self):
        """Found in the preview window: `.step` was already the plan card's row in the rail, so the two
        rules for one name each wore the other's layout, and a probe for `.st-txt` found nothing."""
        self.assertIn("el('div', 'steprow'", self.js)
        self.assertEqual(self.css.count(".step {"), 1,
                         "the plan card's `.step` rule has to stay the only one of that name")


class AskAgainStaysInTheBox(unittest.TestCase):
    """The retry control refills the composer; only the user's Send sends.

    The reason this is enforced rather than trusted: a click that started a task would be a click
    that can write files, and with a folder's Auto-Apply switch on there is no review step after
    it. The refusal to overwrite unsent typing is the other half — the control must never be the
    thing that loses what somebody was writing.
    """

    @classmethod
    def setUpClass(cls):
        static = Path(__file__).resolve().parents[1] / "src/ai_code_engineer/webapp/static"
        cls.js = (static / "app.js").read_text(encoding="utf-8")
        cls.flat = " ".join(cls.js.split())

    def test_exactly_one_place_in_the_client_starts_a_task(self):
        self.assertEqual(self.js.count("send('send'"), 1,
                         "a second caller means a click can now send without the Send button")
        self.assertNotIn("send('send'", self.js.split("function askAgain()")[1].split("function renderComposer")[0])

    def test_it_reads_the_thread_and_refuses_when_there_is_nothing_to_repeat(self):
        self.assertIn("if (m.role === 'user' && (m.text || '').trim()) return m.text;", self.flat)
        self.assertIn("Nothing has been sent in this chat yet", self.js)

    def test_it_leaves_unsent_typing_alone_and_a_running_task_unchanged(self):
        """The busy refusal was written while the composer was disabled during a run. UI 3.2 made
        the box typable and Enter queue, so refusing the refill blocked the path that works."""
        self.assertIn("ta.value.trim() !== text.trim()", self.flat)
        self.assertIn("Your message is still unsent", self.js)
        again = self.js.split("function askAgain()")[1].split("function renderComposer")[0]
        self.assertNotIn("DATA.busy", again,
                         "a running task must not refuse a refill the queue would have taken")

    def test_the_refill_is_marked_as_the_draft_the_server_already_holds(self):
        """The client ignores a draft echo it recognises as its own typing, so a refill that did
        not register would be dropped on the next state push."""
        self.assertIn("state.lastDraftSent = text;", self.flat)
        self.assertIn("sendQuiet('set_draft', { text })", self.flat)

    def test_the_control_is_not_rendered_over_an_empty_thread(self):
        self.assertIn("if (lastAsked())", self.js)
        self.assertIn("Ask your last message again", self.js, "the command palette needs the same route")


class TheQueueStrip(unittest.TestCase):
    """A message typed during a run queues instead of vanishing, and the strip is its record.

    The rule this holds is the one the last round learned twice: sentences the client builds itself
    are sentences the client can only build in English.
    """

    @classmethod
    def setUpClass(cls):
        static = Path(__file__).resolve().parents[1] / "src/ai_code_engineer/webapp/static"
        cls.js = (static / "app.js").read_text(encoding="utf-8")
        cls.css = (static / "app.css").read_text(encoding="utf-8")

    def test_send_becomes_queue_only_while_a_task_runs(self):
        self.assertIn("if (DATA.busy)", self.js.split("function submit()")[1].split("function autosize")[0])
        self.assertIn("queue_add", self.js)
        self.assertIn("(DATA.busy ? 'Queue ' : 'Send ')", self.js)

    def test_the_box_stays_typable_during_a_run(self):
        self.assertIn("ta.disabled = false;", self.js,
                      "disabling it is what made the message unsendable rather than queued")
        self.assertNotIn("ta.disabled = !!DATA.busy", self.js)

    def test_the_strip_prints_the_servers_sentences_not_its_own(self):
        for key in ("q.when", "q.when_detached", "q.held_note", "q.elsewhere_note"):
            self.assertIn(key, self.js, f"the client stopped reading {key}")
        self.assertNotIn("'runs when the current task ends'", self.js)

    def test_the_four_actions_are_all_there(self):
        for action in ("queue_now", "queue_edit", "queue_drop", "queue_chat", "queue_resume"):
            self.assertIn(f"send('{action}'", self.js)


class TheBranchChip(unittest.TestCase):
    """The git chip is decoration until it is clicked, and then it moves HEAD.

    That combination is the reason for these: the same element that must stay silent for a folder
    that is not a repository is the only route to a task branch, so "quiet" and "wired" both have to
    hold at once.
    """

    @classmethod
    def setUpClass(cls):
        static = Path(__file__).resolve().parents[1] / "src/ai_code_engineer/webapp/static"
        cls.js = (static / "app.js").read_text(encoding="utf-8")
        cls.css = (static / "app.css").read_text(encoding="utf-8")
        cls.body = cls.js.split("function gitChip()")[1].split("\n}\n")[0]

    def test_the_restore_pill_appears_only_while_an_offer_is_standing(self):
        """The escalation has to be visible when it exists and absent otherwise: a red button that
        replaces files must never look like a permanent part of the composer."""
        pill = self.js.split("function restorePill()")[1].split("\n}\n")[0]
        self.assertIn("if (!offer || !offer.paths) return null", pill)
        self.assertIn("send('git_restore')", pill)
        self.assertIn("const restore = restorePill();", self.js, "it is never drawn")
        self.assertIn(".pill.restore {", self.css)
        self.assertIn("var(--bad-ink)", self.css.split(".pill.restore {")[1].split("}")[0],
                      "it wears the red the contrast audit already grades")

    def test_it_is_a_button_that_asks_the_server_and_never_touches_git_itself(self):
        self.assertIn("el('button', 'pill git", self.body)
        self.assertIn("send('git_branch', { back: back })", self.body)
        self.assertNotIn("innerHTML = ICON.branch + '<span>' + ref", self.body)

    def test_a_branch_name_from_git_is_escaped_before_it_is_markup(self):
        self.assertIn("esc(shown", self.body)

    def test_a_task_branch_shows_its_tail_and_keeps_its_whole_name_reachable(self):
        """"agent/task-<the task's own words>-<session>" is 40+ characters, and untrimmed the chip
        measured wider than every other control on its composer line."""
        self.assertIn("ref.length > 22", self.body)
        self.assertIn("ref.slice(-21)", self.body)
        self.assertIn("'Branch ') + ref", self.body, "the title carries the full name")

    def test_the_return_trip_only_appears_when_this_window_is_the_one_that_left(self):
        self.assertIn("g.base && g.base !== ref", self.body,
                      "a base equal to the current branch is not a way back")
        self.assertIn("\\u21a9", self.body)

    def test_a_folder_that_is_not_a_repository_still_gets_no_chip(self):
        self.assertIn("if (!g.repo) return null", self.js.split("function gitChip()")[1][:400])


class TheRowCopyButton(unittest.TestCase):
    """Every message the server produced can be copied; the user's own rows are not offered.

    Two constraints shaped this. A row action bound inside the bubble dies the first time a reply
    streams, because `appendToken` rewrites that node's innerHTML; and the clipboard has to receive
    the text the server wrote, not the markup the browser built from it.
    """

    @classmethod
    def setUpClass(cls):
        static = Path(__file__).resolve().parents[1] / "src/ai_code_engineer/webapp/static"
        cls.js = (static / "app.js").read_text(encoding="utf-8")
        cls.flat = " ".join(cls.js.split())

    def test_it_is_offered_on_server_rows_and_not_on_yours(self):
        self.assertIn("if (m.role !== 'user' && (m.text || '').trim()) body.appendChild(msgActions(i));",
                      self.flat)
        self.assertNotIn("if (m.role === 'user') body.appendChild(msgActions", self.js)

    def test_the_button_lives_outside_the_bubble_it_survives(self):
        """A handler attached inside `.bub` is deleted by the next streamed chunk."""
        body = self.js.split("function renderThread()")[1].split("function msgActions")[0]
        self.assertIn("body.appendChild(msgActions(i))", body)
        self.assertNotIn("bub.appendChild(msgActions", body)

    def test_the_icon_is_there_without_needing_a_hover(self):
        css = (Path(__file__).resolve().parents[1] /
               "src/ai_code_engineer/webapp/static/app.css").read_text(encoding="utf-8")
        block = css.split(".macts {", 1)[1].split("}", 1)[0]
        self.assertNotIn("opacity", block,
                         "a copy button that only appears while the pointer crosses its row is a "
                         "control nobody finds")
        self.assertNotIn(".msg:hover .macts", css)

    def test_one_delegated_listener_answers_the_whole_thread(self):
        self.assertEqual(self.js.count("$('thread').addEventListener('click'"), 1)
        self.assertIn("event.target.closest('[data-copy-row]')", self.js)

    def test_it_copies_the_message_text_rather_than_the_rendered_markup(self):
        handler = self.js.split("$('thread').addEventListener('click'")[1].split("});")[0]
        self.assertIn("navigator.clipboard.writeText(message.text || '')", " ".join(handler.split()))
        self.assertNotIn("innerHTML", handler, "a copied bubble carries the markdown back out")

    def test_the_row_index_is_resolved_against_the_live_thread(self):
        """A stale index would copy the wrong message; the dataset is written from the same loop
        that renders, and read back through DATA.messages at click time."""
        self.assertIn("copy.dataset.copyRow = index;", self.flat)
        self.assertIn("DATA.messages[Number(hit.dataset.copyRow)]", self.flat)


class TheModelSheetFilter(unittest.TestCase):
    """The list you search is the list you actually pick from, and a keystroke stays local."""

    @classmethod
    def setUpClass(cls):
        static = Path(__file__).resolve().parents[1] / "src/ai_code_engineer/webapp/static"
        cls.js = (static / "app.js").read_text(encoding="utf-8")
        cls.flat = " ".join(cls.js.split())

    def test_the_picker_itself_has_the_search_box(self):
        sheet = self.js.split("function choose(")[1].split("function openSettings")[0]
        self.assertIn("Search models", sheet)
        self.assertIn("input.oninput", sheet)
        self.assertNotIn("set_filter", sheet,
                         "filtering a list the sheet already holds should not pay a round trip")

    def test_enter_picks_the_first_match(self):
        sheet = self.js.split("function choose(")[1].split("function openSettings")[0]
        self.assertIn("const hit = matches()[0];", " ".join(sheet.split()))
        self.assertIn("send('set_model', { value: hit.value })", " ".join(sheet.split()))

    def test_the_count_says_what_the_search_hidden(self):
        self.assertIn("+ ' of ' + entries.length + ' models'", self.js)

    def test_a_search_that_matches_nothing_says_so(self):
        self.assertIn("No model matches that search.", self.js)


class ShortcutsYieldToTyping(unittest.TestCase):
    def test_the_guards_come_before_any_binding(self):
        flat = " ".join((Path(__file__).resolve().parents[1] /
                         "src/ai_code_engineer/webapp/static/app.js").read_text(encoding="utf-8").split())
        self.assertIn("if (e.altKey || e.repeat || handsBusy()) return;", flat,
                      "AltGr on an Arabic layout is Ctrl+Alt, so the palette opened on the key "
                      "that types")
        self.assertLess(flat.index("if (e.altKey || e.repeat || handsBusy()) return;"),
                        flat.index("const mod = e.ctrlKey || e.metaKey;"))

    def test_shift_is_part_of_the_binding_not_a_free_modifier(self):
        """Without the `!e.shiftKey` guards, Ctrl+Shift+K matched the Ctrl+K branch first and
        opened the palette on the way to starting a chat."""
        flat = " ".join((Path(__file__).resolve().parents[1] /
                         "src/ai_code_engineer/webapp/static/app.js").read_text(encoding="utf-8").split())
        self.assertIn("key === 'k' && !e.shiftKey", flat)
        self.assertIn("key === 'k' && e.shiftKey", flat)

    def test_the_key_hint_printed_on_a_button_is_the_binding_that_exists(self):
        html = (Path(__file__).resolve().parents[1] / "src/ai_code_engineer/webapp/static/index.html")\
            .read_text(encoding="utf-8")
        self.assertIn('id="new-chat"', html)
        self.assertNotIn(">Ctrl K<", html, "this chip promised a shortcut the window did not implement")
        self.assertIn("Ctrl+Shift+K", html)


class TheActivityStrip(unittest.TestCase):
    """UI 3.4: the live line has its own surface, and a state push cannot wipe a true sentence.

    The old handler wrote progress text into the header subtitle, which `render()` then reset from
    `DATA.header.subtitle` — so mid-run the strip's job disappeared while work was still going.
    """

    @classmethod
    def setUpClass(cls):
        static = Path(__file__).resolve().parents[1] / "src/ai_code_engineer/webapp/static"
        cls.js = (static / "app.js").read_text(encoding="utf-8")
        cls.css = (static / "app.css").read_text(encoding="utf-8")
        cls.flat = " ".join(cls.js.split())

    def test_a_status_event_no_longer_writes_the_header_subtitle(self):
        self.assertNotIn("$('title').nextElementSibling", self.js,
                         "the subtitle names the folder and step; a progress line does not belong there")
        self.assertIn("state.status = msg.text; paintStatus(); break;", self.flat)

    def test_the_snapshot_is_the_source_so_a_push_refreshes_rather_than_erases(self):
        self.assertIn("state.status = DATA.status || '';", self.flat)
        handler = self.js.split("function paintStatus()")[1].split("\n}")[0]
        self.assertIn("const line = state.status || '';", " ".join(handler.split()))
        self.assertIn("bar.textContent = line;", " ".join(handler.split()))

    def test_the_strip_speaks_the_tasks_language(self):
        handler = self.js.split("function paintStatus()")[1].split("\n}")[0]
        self.assertIn("ARABIC_RUN.test(line) ? 'rtl' : 'auto'", handler)

    def test_it_only_disappears_when_there_is_nothing_said_and_nothing_running(self):
        self.assertIn("bar.classList.toggle('hidden', !line && !state.busy)", self.flat)
        self.assertNotIn("$('busybar').classList.toggle('hidden', !busy)", self.js)

    def test_the_strip_is_a_line_of_words_not_two_pixels_of_shimmer(self):
        block = self.css.split(".busybar {", 1)[1].split("}", 1)[0]
        self.assertIn("min-height", block)
        self.assertNotIn("height: 2px", block)
        self.assertIn(".busybar.busy::after { opacity: 1 }", self.css,
                      "the shimmer is still the signal that something is running")
        self.assertIn("font-size: 11.5px; color: var(--faint)", self.css)

    def test_the_preview_carries_the_field_the_real_window_sends(self):
        controller = FakeController()
        self.assertIn("status", controller.snapshot(),
                      "the strip cannot be reviewed in --fake without the field")
        self.assertTrue(controller.snapshot()["messages"], "the scripted thread should show steps")
        said = " ".join(m["text"] for m in controller.snapshot()["messages"]
                        if m.get("author") == "Steps")
        for glyph in ("Reading file", "Searching code", "Scanning project files", "Proposed changes"):
            self.assertIn(glyph, said, "the step rows are the thing being reviewed here")


class InitialsTests(unittest.TestCase):
    """The sidebar mark has to separate folders that share a prefix."""

    def test_shared_prefixes_stay_distinct(self):
        self.assertEqual(initials_for("demo2"), "d2")
        self.assertEqual(initials_for("demo_repo"), "dr")
        self.assertEqual(initials_for("preview-proj"), "pp")
        self.assertEqual(initials_for("Spring Boot Auth"), "sb")
        self.assertEqual(initials_for("test"), "te")
        self.assertEqual(len({initials_for(n) for n in ("demo2", "demo_repo", "preview-proj")}), 3)

    def test_a_name_with_no_letters_does_not_crash(self):
        self.assertEqual(initials_for("2024"), "20")
        self.assertEqual(initials_for(""), "?")


class SnapshotShapeTests(unittest.TestCase):
    def test_the_draft_reaches_the_client_so_a_server_side_prefill_is_visible(self):
        import tempfile
        with tempfile.TemporaryDirectory() as temp:
            controller = AgentController(Path(temp))
            self.assertEqual(controller.snapshot()["draft"], "")
            controller._draft = "Fix the add function"
            self.assertEqual(controller.snapshot()["draft"], "Fix the add function")
            controller.close()

    def test_the_header_line_names_the_folder_step_and_model(self):
        import tempfile
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            repo = root / "repo"
            repo.mkdir()
            controller = AgentController(root)
            controller.repo = str(repo)
            controller.model = "test-local"
            controller.subtitle = controller._subtitle()
            self.assertIn("repo", controller.subtitle)
            self.assertIn("test-local", controller.subtitle)
            controller.close()


class DesignTokenTests(unittest.TestCase):
    """The stylesheet is a contract with tokens.css, and contrast is a claim worth enforcing."""

    @classmethod
    def setUpClass(cls):
        cls.static = Path(__file__).resolve().parents[1] / "src/ai_code_engineer/webapp/static"

    def test_every_token_the_components_use_is_defined(self):
        import re
        used = set(re.findall(r"var\(--([a-z0-9-]+)",
                              (self.static / "app.css").read_text(encoding="utf-8")))
        defined = set(re.findall(r"--([a-z0-9-]+):",
                                 (self.static / "tokens.css").read_text(encoding="utf-8")))
        self.assertEqual(sorted(used - defined), [],
                         "app.css reads tokens that no style family defines")

    def test_every_text_pair_clears_AA_in_every_style_and_theme(self):
        import importlib.util
        from contextlib import redirect_stdout
        import io
        path = Path(__file__).resolve().parents[1] / "tools/check_contrast.py"
        spec = importlib.util.spec_from_file_location("check_contrast", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        report = io.StringIO()
        with redirect_stdout(report):
            code = module.main()
        self.assertEqual(code, 0, report.getvalue())


class ReconnectedStreamTests(unittest.TestCase):
    """A dropped SSE reconnects silently, and everything it missed was a whole snapshot.

    Measured during M2: the review card held a stale verdict while the server waited on a pending
    proposal, so the Apply click that was there to be taken looked disabled — and the queue stalled
    behind a question the window no longer showed.
    """

    @classmethod
    def setUpClass(cls):
        static = Path(__file__).resolve().parents[1] / "src/ai_code_engineer/webapp/static/app.js"
        cls.js = static.read_text(encoding="utf-8")
        cls.connect = cls.js.split("function connectEvents()")[1].split("\n}\n")[0]

    def test_opening_a_stream_re_reads_the_snapshot(self):
        self.assertIn("api('/api/bootstrap').then(render)", self.connect)

    def test_a_stream_that_opens_resets_the_failure_budget(self):
        self.assertIn("streamRetries = 0", self.connect)

    def test_the_message_path_is_untouched(self):
        self.assertIn("src.onmessage = (row) => {", self.connect)


class DeafStreamTests(unittest.TestCase):
    """A 403 on the stream is indistinguishable from a blip to an EventSource, so it must not be
    retried as one forever. Counted on a stale token: 33 rejected requests, no message on screen."""

    @classmethod
    def setUpClass(cls):
        static = Path(__file__).resolve().parents[1] / "src/ai_code_engineer/webapp/static/app.js"
        cls.js = static.read_text(encoding="utf-8")
        cls.connect = cls.js.split("function connectEvents()")[1].split("\n}\n")[0]

    def test_the_retries_are_capped_and_the_window_says_something(self):
        self.assertIn("if (++streamRetries >= STREAM_RETRY_LIMIT)", self.connect)
        self.assertIn("streamGoneQuiet();", self.connect)

    def test_giving_up_closes_the_source(self):
        """Otherwise the browser keeps the failed stream open behind the message."""
        block = self.connect.split("if (++streamRetries >= STREAM_RETRY_LIMIT)")[1].split("}", 1)[0]
        self.assertIn("src.close()", block)

    def test_the_sentence_names_the_fix_not_the_symptom(self):
        body = self.js.split("function streamGoneQuiet()")[1].split("\n}\n")[0]
        self.assertIn("Reload this page", body)


class DeadWindowTests(unittest.TestCase):
    """A restart rotates the per-launch token, and an already-open tab keeps rendering real history
    while refusing every click. Reported as "the View button in history does nothing" on 2026-09-28;
    the button was wired correctly — the page was deaf, and a 4.2 s toast was the only trace.
    """

    @classmethod
    def setUpClass(cls):
        static = Path(__file__).resolve().parents[1] / "src/ai_code_engineer/webapp/static/app.js"
        cls.js = static.read_text(encoding="utf-8")
        cls.api = cls.js.split("async function api(")[1].split("\n}\n")[0]
        cls.deaf = cls.js.split("function windowGoneDeaf(")[1].split("\n}\n")[0]

    def test_a_refused_request_is_not_just_an_error_string(self):
        self.assertIn("if (res.status === 403) windowGoneDeaf(text);", self.api)

    def test_the_body_is_read_once_so_the_throw_doesnt_corrupt_the_reason(self):
        """A Response cannot be consumed twice: reading it for the notice and again for the Error
        threw a TypeError and hid the server's own sentence."""
        self.assertEqual(self.api.count("await res.text()"), 1)

    def test_the_notice_outlives_the_click_that_caused_it(self):
        self.assertNotIn("setTimeout", self.deaf)
        self.assertIn("data-reload", self.deaf)

    def test_ninety_clicks_produce_one_notice(self):
        self.assertIn("if (DEAF) return;", self.deaf)

    def test_the_dead_stream_and_the_refused_click_say_the_same_thing(self):
        body = self.js.split("function streamGoneQuiet()")[1].split("\n}\n")[0]
        self.assertIn("windowGoneDeaf(", body)

    def test_a_chip_with_no_file_behind_it_admits_it(self):
        open_file = self.js.split("function openFile(")[1].split("\n}\n")[0]
        self.assertIn("no longer in the change set", open_file)
        self.assertNotIn("if (!r.files || !r.files[index]) return;", open_file)


class ReplayedAsksAndBadgesTests(unittest.TestCase):
    """D33's client half and D31's chips: both are nine lines of browser JS, and no Python test
    runs a DOM, so the clauses that carry the decision are what gets pinned."""

    @classmethod
    def setUpClass(cls):
        static = Path(__file__).resolve().parents[1] / "src/ai_code_engineer/webapp/static/app.js"
        cls.js = static.read_text(encoding="utf-8")

    def test_a_question_is_drawn_once_whichever_way_it_arrived(self):
        """The id is the same object on the SSE path and inside every later snapshot; drawing both
        would stack a second sheet on one waiter, which is D29's duplicate one layer up."""
        self.assertIn("if (!msg || !msg.id || ASKS_DRAWN.has(msg.id)) return;", self.js)
        self.assertIn("case 'confirm': drawAsk(msg); break;", self.js)
        self.assertIn("case 'folder': drawAsk(msg); break;", self.js)
        self.assertIn("for (const ask of (data.asks || [])) drawAsk(ask);", self.js)

    def test_the_third_answer_sends_no_alongside_the_flag(self):
        """Every other reader of a confirm weighs yes/no, so the batch answer has to be a no that
        carries its reason rather than a third truth value."""
        self.assertIn("api('/api/confirm', { id: msg.id, ok: false, alt: true });", self.js)
        self.assertIn("if (msg.alt)", self.js)

    def test_a_deleted_file_is_badged_as_deleted_everywhere_a_file_is_badged(self):
        self.assertIn("chip-tag deleted", self.js)
        self.assertIn("Removed by this task", self.js)
        self.assertIn("kindTag(f.kind)", self.js, "the chip and the rail row must not drift apart")

    def test_a_restored_queue_row_says_where_it_came_from(self):
        self.assertIn("item.restored ? q.when_restored", self.js)


class ComposerButtonHandlesTests(unittest.TestCase):
    """Stop and Send share the composer's `.send` class; an id is what tells them apart.

    Found the expensive way in the dogfood run: a scripted `document.querySelector('.send')` fired
    while a task was running matched the Stop button, which is rendered first, and cancelled a
    seven-minute model turn four times over.
    """

    @classmethod
    def setUpClass(cls):
        static = Path(__file__).resolve().parents[1] / "src/ai_code_engineer/webapp/static/app.js"
        cls.js = static.read_text(encoding="utf-8")
        cls.bar = cls.js.split("function renderComposer()")[1].split("\n}\n")[0]

    def test_each_button_is_addressable_on_its_own(self):
        self.assertIn("s.id = 'stop';", self.bar)
        self.assertIn("sendBtn.id = 'send';", self.bar)

    def test_stop_is_still_the_styled_send_shaped_button(self):
        """The id carries the handle; the class still carries the look, so nothing re-tints the bar."""
        self.assertIn("el('button', 'send stop', 'Stop')", self.bar)
        self.assertLess(self.bar.index("'stop';"), self.bar.index("sendBtn.id"),
                        "Stop is still built before Send, which is the order that made the mistake")


class WithdrawnAskWindowTests(unittest.TestCase):
    """The browser half of a withdrawn question: the server says forget-it, the sheet goes away.

    The id is matched by walking the mounted scrims. A selector built from a network string throws
    on one stray quote, and the throw happens inside the event pump that feeds the whole window.
    """

    @classmethod
    def setUpClass(cls):
        static = Path(__file__).resolve().parents[1] / "src/ai_code_engineer/webapp/static"
        cls.js = (static / "app.js").read_text(encoding="utf-8")
        cls.flat = " ".join(cls.js.split())

    def test_the_event_reaches_a_handler(self):
        self.assertIn("case 'retract': retractAsk(msg.id); break;", self.flat)

    def test_every_question_the_server_waits_on_is_tagged(self):
        for name in ("askConfirm", "askFolder"):
            body = self.js.split(f"function {name}(msg)")[1].split("\n}\n")[0]
            self.assertIn("s.dataset.ask = msg.id;", body, f"{name} leaves no handle to withdraw it by")

    def test_the_withdrawal_walks_the_dom_instead_of_building_a_selector(self):
        body = self.js.split("function retractAsk(id)")[1].split("\n}\n")[0]
        self.assertIn("$('modal-root').children", body)
        self.assertNotIn("querySelector(", body)


class TheThoughtRowInPreview(unittest.TestCase):
    """The preview window has to carry the reasoning row the real window carries.

    A reviewer decides what a row looks like from this page, so the scripted sentence is built by the
    same function the engine calls: a preview that types out its own version reviews a fiction, and a
    count that does not match the text behind it reviews arithmetic nobody checks.
    """

    def rows(self):
        return [(message.get("step") or {}) for message in FakeController().snapshot()["messages"]
                if message.get("step")]

    def test_the_preview_scripts_one_row_with_a_thought_behind_it(self):
        thought = [row for row in self.rows() if row["action"] == "model_reasoning"]
        self.assertEqual(len(thought), 1, thought)
        self.assertTrue(thought[0]["detail"], "the chevron is the server's answer, not a guess")

    def test_the_sentence_is_the_ones_the_engine_would_write(self):
        from ai_code_engineer import labels
        from ai_code_engineer.webapp.fake import REASONING_SAMPLE
        message = next(message for message in FakeController().snapshot()["messages"]
                       if (message.get("step") or {}).get("id") == "st-r")
        self.assertEqual(message["text"],
                         labels.step_line(False, "model_reasoning", count=len(REASONING_SAMPLE),
                                          detail=REASONING_SAMPLE))
        self.assertIn(str(len(REASONING_SAMPLE)), message["text"],
                      "the row counts the characters it actually stores")

    def test_the_row_opens_to_the_whole_deliberation_under_its_own_title(self):
        from ai_code_engineer import labels
        from ai_code_engineer.webapp.fake import REASONING_SAMPLE
        controller = FakeController()
        controller.action("step_detail", {"id": "st-r"}, lambda event: None)
        block = controller.snapshot()["step_detail"]
        self.assertEqual(block["id"], "st-r")
        self.assertEqual(block["note"], "")
        title, lines = block["sections"][0]
        self.assertEqual(title, labels.detail_section(False, "reasoning"))
        self.assertEqual(lines, REASONING_SAMPLE.splitlines())

    def test_closing_the_row_asks_for_nothing(self):
        controller = FakeController()
        controller.action("step_detail", {"id": "st-r"}, lambda event: None)
        controller.action("step_detail", {"id": ""}, lambda event: None)
        self.assertIsNone(controller.snapshot()["step_detail"])


class TheGraphSheet(unittest.TestCase):
    """The client half of the module graph: a button, a fetch, and a drawing made only of server data.

    No Python test runs a DOM, so what gets pinned is the clauses that carry the decisions — that the
    picture is asked for rather than already in the page, that every string reaching the SVG came from
    the filesystem and is escaped on the way in, and that a click with nothing to draw says so.
    """

    @classmethod
    def setUpClass(cls):
        static = Path(__file__).resolve().parents[1] / "src/ai_code_engineer/webapp/static"
        cls.js = (static / "app.js").read_text(encoding="utf-8")
        cls.css = (static / "app.css").read_text(encoding="utf-8")
        cls.flat = " ".join(cls.js.split())
        cls.svg = cls.js.split("function graphSvg(")[1].split("\n}\n")[0]
        cls.sheet_body = cls.js.split("async function graphSheet()")[1].split("\n}\n")[0]
        cls.card = cls.js.split("function renderRail()")[1].split("\n}\n")[0]

    def test_the_button_posts_the_action_itself_rather_than_reusing_a_send(self):
        """`send()` drops the reply's payload, and the sheet is drawn from that payload — which is why
        this one calls the endpoint directly, and why the parity gate reads both forms."""
        self.assertIn("api('/api/action', { type: 'show_graph' })", self.flat)
        self.assertIn("graph.onclick = () => graphSheet();", self.card)

    def test_it_only_appears_when_there_is_a_project_to_draw(self):
        """The Sources card is the whole right panel; a graph entry with no folder behind it is a button
        that can only ever answer "nothing to draw yet"."""
        self.assertIn("if ((DATA.branch || {}).key) {", self.card)
        self.assertLess(self.card.index("if ((DATA.branch || {}).key) {"),
                        self.card.index("graph.onclick"), "the graph button escaped the project gate")

    def test_the_sheet_says_it_is_measuring_before_the_answer_lands(self):
        """A walk of a real repository takes seconds on a slow disk, and a sheet that appears blank first
        reads as an empty project rather than as work in progress."""
        self.assertIn("body.appendChild(el('div', 'quiet', 'Mapping this folder…'));", self.sheet_body)
        self.assertLess(self.sheet_body.index("Mapping this folder"), self.sheet_body.index("await api("))

    def test_the_svg_escapes_every_string_that_came_off_the_disk(self):
        """A folder name is data the operator's repository controls. It reaches a `<title>` and a
        `<text>` node, so an unescaped interpolation is the injection the rest of the window avoids."""
        for piece in ("esc(node.name)", "esc(String(node.name))", "esc(edge.from)", "esc(edge.to)",
                      "esc(data.caption"):
            self.assertIn(piece, self.svg + self.sheet_body, piece)
        self.assertNotIn(">${node.name}", self.svg, "a raw name interpolated into the drawing")
        self.assertNotIn(">${edge.from}", self.svg)

    def test_the_drawing_adds_no_opinion_of_its_own(self):
        """The columns, the counts and the caveat are the server's claims. A second copy of that maths in
        JS is the drift this project keeps paying for."""
        self.assertIn("const columns = {};", self.svg)
        for banned in ("modules,", "dependencies,", "cycle"):
            self.assertNotIn(banned, self.svg, "the client started writing the server's sentence")

    def test_an_answer_with_nothing_to_draw_shows_the_servers_words(self):
        self.assertIn("esc((data || {}).note", self.sheet_body)
        self.assertLess(self.sheet_body.index("(data || {}).note"), self.sheet_body.index("graphSvg(data)"),
                        "an empty sheet was drawn before the note could replace it")

    def test_the_graph_is_rendered_from_the_reply_not_from_the_snapshot(self):
        """Fetched, not shipped: if the picture ever rode a snapshot it would be rebuilt on every
        streamed log line, and the walk would happen in the wrong thread."""
        self.assertNotIn("DATA.graph", self.js)

    def test_the_classes_the_drawing_names_have_rules(self):
        for selector in (".graph .gnode rect", ".graph .gedge", ".graph .ghead", ".gscroll", ".gmods"):
            self.assertIn(selector + " {", self.css, selector)

    def test_the_paint_layer_still_carries_no_literal_colours(self):
        """`app.css` reads tokens only; an SVG that needs its own hex is where that rule would die."""
        block = self.css.split("/* -------------------------------- module graph")[1] \
            .split("/* -------------------------------- project drawer")[0]
        self.assertTrue(block.strip(), "the graph rules were not found where they live")
        self.assertNotIn("#", block, "a hard-coded colour in the graph rules")
        self.assertNotIn("rgb(", block)
        self.assertIn("var(--", block, "and still painted from tokens")


if __name__ == "__main__":
    unittest.main()
