"""Local-only HTTP server: static UI, a JSON action endpoint, and one SSE stream.

Binds to 127.0.0.1 on a random port and requires a per-launch token, because a browser
page on the same machine can otherwise be reached by any other process or web page.

The token alone was not enough. A page on any origin can *send* to a loopback port, and DNS
rebinding turns a hostile hostname into 127.0.0.1 inside the victim's browser — so every request is
also checked here for the Host it asked for and the Origin it came from, and every response carries a
Content-Security-Policy. Nothing in this module writes to a project.
"""
from __future__ import annotations

import json
import queue
import secrets
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from ..errors import AgentError
from ..labels import friendly_error
from ..redaction import redact

STATIC = Path(__file__).resolve().parent / "static"
MAX_BODY = 1_000_000
DRAIN_CHUNK = 65_536
LOOPBACK = frozenset(("127.0.0.1", "localhost", "::1"))

# `script-src 'self'` with no 'unsafe-inline' is the line that matters, and index.html's boot script
# moved to its own file for it. Inline *styles* stay allowed: eight template strings set layout
# values inline (progress-bar widths, avatar colours), and a style cannot execute script.
CSP = ("default-src 'none'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
       "img-src 'self' data:; font-src 'self'; connect-src 'self'; "
       "frame-ancestors 'none'; base-uri 'none'; form-action 'none'; object-src 'none'")
SAFE_HEADERS = (
    ("content-security-policy", CSP),
    ("x-frame-options", "DENY"),
    ("x-content-type-options", "nosniff"),
    # The launch URL carries the session token in its query string; a referrer would be told it.
    ("referrer-policy", "no-referrer"),
    ("cross-origin-opener-policy", "same-origin"),
)


class Hub:
    """Fan-out for the SSE stream; a slow client drops events instead of blocking the agent."""

    def __init__(self) -> None:
        self._clients: list[queue.Queue] = []
        self._lock = threading.Lock()

    def subscribe(self) -> queue.Queue:
        client: queue.Queue = queue.Queue(maxsize=256)
        with self._lock:
            self._clients.append(client)
        return client

    def unsubscribe(self, client: queue.Queue) -> None:
        with self._lock:
            if client in self._clients:
                self._clients.remove(client)

    def publish(self, event: dict) -> None:
        with self._lock:
            clients = list(self._clients)
        for client in clients:
            try:
                client.put_nowait(event)
            except queue.Full:
                pass


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "AICodeEngineer/1.0"

    # Placeholders. The real four are set on a subclass per launch — see serve().
    token: str = ""
    controller = None
    hub: Hub = Hub()
    port: int = 0

    def log_message(self, *_args):        # silence per-request stderr noise
        pass

    # ---------------- plumbing ----------------
    def _guard(self) -> tuple[str, int] | None:
        """Refuse a request that is not addressed to this server, before reading its body.

        A browser will happily send a POST to 127.0.0.1 from a page on another site — the token is
        what stops it being *answered*. `Host` is the other half: DNS rebinding makes a hostile
        hostname resolve to loopback inside the victim's own browser, and then the page is
        same-origin with this server and can read the answer. Checking that the request was aimed at
        a loopback name on our own port closes that, and checking `Origin` refuses the page that only
        wanted to write.

        Returns the sentence and the status, or None when the request is addressed here. Each route
        answers in its own way, because a POST still has its body in the socket.
        """
        if not self._host_allowed(self.headers.get("host", "")):
            return "This server answers only loopback requests addressed to itself.", 425
        origin = self.headers.get("origin") or self.headers.get("referer") or ""
        if origin and not self._origin_allowed(origin):
            return "That request came from another origin.", 403
        return None

    def _origin_allowed(self, value: str) -> bool:
        """Where the page that asked lives — a stricter question than the one `Host` answers.

        A client may send `Host: localhost` with no port, so the port check above has to allow a
        missing port. An `Origin` never does: a browser always states one, and it always carries a
        scheme. So `https://localhost` or `http://localhost` is a different page from this one —
        this server is plain http on one random port, and a request from anywhere else is a page
        that wants to write into the session while not being the page that holds it.
        """
        try:
            parsed = urllib.parse.urlparse(value)
        except ValueError:
            return False
        if parsed.scheme != "http":
            return False
        if parsed.hostname not in LOOPBACK:
            return False
        return parsed.port == self.port

    def _host_allowed(self, authority: str) -> bool:
        """A loopback name, on this port — no rebinding, no other service wearing our token."""
        if not authority:
            return False
        try:
            parsed = urllib.parse.urlparse("//" + authority)
        except ValueError:
            return False
        if parsed.hostname not in LOOPBACK:
            return False
        return parsed.port in (None, self.port)

    def _safe(self) -> None:
        """The headers every response carries, so a new route cannot forget them."""
        for name, value in SAFE_HEADERS:
            self.send_header(name, value)

    def _authorized(self, query: dict) -> bool:
        if not self.token:
            # serve() has not handed this handler a token. Two empty strings are equal, so with no
            # guard an unconfigured server would let every unauthenticated request through.
            return False
        supplied = query.get("t", [""])[0] or self.headers.get("X-Auth-Token", "")
        return secrets.compare_digest(supplied, self.token)

    def _json(self, value, code=200):
        body = json.dumps(value, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self._safe()
        self.send_header("content-type", "application/json; charset=utf-8")
        self.send_header("content-length", str(len(body)))
        self.send_header("cache-control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _text(self, text, code=400):
        body = text.encode("utf-8")
        self.send_response(code)
        self._safe()
        self.send_header("content-type", "text/plain; charset=utf-8")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _fail(self, exc: Exception, code: int = 500):
        """The answer for a request that went wrong: never the exception text as it stands.

        An `AgentError` is a sentence this program wrote on purpose, so it is repeated — redacted and
        capped, because some of them interpolate a provider's message or a folder path. Anything else
        is somebody else's failure, and the browser gets one fixed line with a reference. The detail
        goes to the Activity log, which is where a developer is looking anyway.
        """
        reference = secrets.token_hex(3)
        record = getattr(self.controller, "note_failure", None)
        if record is not None:
            record(reference, redact(f"{type(exc).__name__}: {exc}")[:600])
        if isinstance(exc, AgentError):
            return self._text(redact(friendly_error(exc))[:400], code)
        return self._text(f"The window could not complete that request (id {reference}). "
                          "The reason is in the Activity log.", code)

    def _refuse(self, text, code):
        """Answer a rejected POST without leaving its body in the socket.

        A client that is still writing when the answer arrives gets its connection reset under
        Windows, which turns a clean 403 into a broken pipe on the caller's side.
        """
        self._drain(int(self.headers.get("content-length") or 0))
        # Any part of an oversized body we did not read is not a request, so this connection ends
        # after the answer instead of being parsed as one.
        self.close_connection = True
        return self._text(text, code)

    def _query(self) -> dict:
        return urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)

    def _drain(self, length: int) -> None:
        """Read a rejected body out of the socket so the client can finish its send.

        Everything declared has to be consumed, or Windows aborts the connection under a client
        that is still writing. The ceiling keeps a hostile content-length from turning that into
        an unbounded read.
        """
        remaining = min(length, MAX_BODY + 8 * DRAIN_CHUNK)
        while remaining > 0:
            block = self.rfile.read(min(DRAIN_CHUNK, remaining))
            if not block:
                break
            remaining -= len(block)

    def _body(self) -> dict:
        length = int(self.headers.get("content-length") or 0)
        if length > MAX_BODY:
            # Answering 400 while a megabyte is still in flight makes Windows abort the socket
            # from under the client, which is what made the oversized-body test flaky.
            self._drain(length)
            raise ValueError("Request body too large.")
        raw = self.rfile.read(length) if length else b"{}"
        try:
            value = json.loads(raw.decode("utf-8") or "{}")
        except ValueError:
            raise ValueError("Body must be JSON.") from None
        return value if isinstance(value, dict) else {}

    # ---------------- routes ----------------
    def do_GET(self):  # noqa: N802
        refused = self._guard()
        if refused:
            return self._text(*refused)
        path = urllib.parse.urlparse(self.path).path
        query = self._query()
        if not path.startswith("/api/"):
            # Bundled assets carry no user data, and a browser never sends a query string
            # for a stylesheet link.
            return self._static(path)
        if not self._authorized(query):
            return self._text("Not authorised for this UI session.", 403)
        return self._get_api(path, query)

    def do_POST(self):  # noqa: N802
        refused = self._guard()
        if refused:
            return self._refuse(*refused)
        path = urllib.parse.urlparse(self.path).path
        if not self._authorized(self._query()):
            return self._refuse("Not authorised for this UI session.", 403)
        if path not in {"/api/action", "/api/confirm"}:
            return self._refuse("Unknown endpoint.", 404)
        try:
            body = self._body()
        except ValueError as exc:
            # Both texts this can raise are written in _body() — "Body must be JSON." and "Request
            # body too large." — so repeating one says something useful and leaks nothing.
            return self._text(str(exc), 400)
        try:
            if path == "/api/confirm":
                self.controller.set_reply(str(body.get("id", "")), body)
                return self._json({"ok": True})
            result = self.controller.action(str(body.get("type", "")), body, self.hub.publish)
            return self._json({"ok": True, "result": result, "state": self.controller.snapshot()})
        except Exception as exc:                              # a UI must never take the server down
            return self._fail(exc)

    def _get_api(self, path: str, query: dict):
        if path == "/api/bootstrap":
            return self._json(self.controller.snapshot())
        if path == "/api/fs":
            try:
                files_raw = query.get("files", [""])[0]
                want = [f.strip() for f in files_raw.split(",") if f.strip()] if files_raw else None
                return self._json(self.controller.list_dir(query.get("path", [""])[0], want_files=want))
            except (AgentError, OSError, ValueError) as exc:
                return self._fail(exc, 400)
        if path == "/api/project":
            # The drawer walks the folder to measure it, so it is asked for on open rather
            # than carried in every snapshot.
            try:
                return self._json(self.controller.project_info(query.get("project", [""])[0]))
            except (AgentError, OSError, ValueError) as exc:
                return self._fail(exc, 400)
        if path == "/api/events":
            return self._stream()
        return self._text("Unknown endpoint.", 404)

    def _stream(self):
        client = self.hub.subscribe()
        self.send_response(200)
        self._safe()
        self.send_header("content-type", "text/event-stream; charset=utf-8")
        self.send_header("cache-control", "no-store")
        self.send_header("x-accel-buffering", "no")
        self.end_headers()
        try:
            self.wfile.write(b"retry: 1500\n\n")
            self.wfile.flush()
            while True:
                try:
                    event = client.get(timeout=15)
                    chunk = f"data: {json.dumps(event, ensure_ascii=False)}\n\n".encode("utf-8")
                except queue.Empty:
                    chunk = b": keep-alive\n\n"
                self.wfile.write(chunk)
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass
        finally:
            self.hub.unsubscribe(client)

    def _static(self, path: str):
        name = "index.html" if path in {"/", ""} else path.lstrip("/")
        target = (STATIC / name).resolve()
        if not target.is_file() or not target.is_relative_to(STATIC):
            return self._text("Not found.", 404)
        kinds = {".html": "text/html", ".css": "text/css", ".js": "text/javascript",
                 ".svg": "image/svg+xml", ".png": "image/png", ".woff2": "font/woff2", ".json": "application/json"}
        body = target.read_bytes()
        self.send_response(200)
        self._safe()
        self.send_header("content-type", kinds.get(target.suffix, "application/octet-stream") + "; charset=utf-8")
        self.send_header("content-length", str(len(body)))
        self.send_header("cache-control", "no-cache")
        self.end_headers()
        self.wfile.write(body)


def serve(controller, host: str = "127.0.0.1", port: int = 0):
    """Start serving and return (server, url, token). Call server.shutdown() to stop."""
    if host not in LOOPBACK:
        raise AgentError("The UI binds to loopback only.")
    token = secrets.token_urlsafe(16)
    # One handler *class* per launch. `Handler` carries its session on the class because
    # `BaseHTTPRequestHandler` is instantiated by the socket server with no room for constructor
    # arguments, and writing the session on the shared class instead meant a second `serve()` in the
    # same process took over the first one's authentication: the first window would answer the
    # second's token and drive the second's controller. A subclass per server keeps two windows in
    # one process — which the desktop launcher can be asked for — apart in all four fields.
    bound = type("SessionHandler", (Handler,), {"token": token, "controller": controller, "hub": Hub()})
    server = ThreadingHTTPServer((host, port), bound)
    server.daemon_threads = True
    actual = server.server_address[1]
    # The Host check compares against the port this process actually owns, which is only knowable
    # after binding: a caller that asked for port 0 gets a random one.
    bound.port = actual
    threading.Thread(target=server.serve_forever, name="ui-http", daemon=True).start()
    return server, f"http://{host}:{actual}/?t={token}", token
