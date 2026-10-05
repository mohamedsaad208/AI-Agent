"""Local-only HTTP server: static UI, a JSON action endpoint, and one SSE stream.

Two producers write on that one stream: the controller's own UI events and, through the agent's
event bus, every structured event a run records. Each leaves numbered, and a window of the recent
ones is kept, so a tab that blips its connection asks for what it missed instead of losing it.

Binds to 127.0.0.1 on a random port and requires a per-launch token, because a browser
page on the same machine can otherwise be reached by any other process or web page.

The token alone was not enough. A page on any origin can *send* to a loopback port, and DNS
rebinding turns a hostile hostname into 127.0.0.1 inside the victim's browser — so every request is
also checked here for the Host it asked for and the Origin it came from, and every response carries a
Content-Security-Policy. Nothing in this module writes to a project.
"""
from __future__ import annotations

import json
import os
import queue
import secrets
import threading
import urllib.parse
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .. import events as ux
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
    """Fan-out for the SSE stream, one id per event and a window of them kept for a reconnect.

    A slow client drops events instead of blocking the agent. Every event also leaves with a
    sequence number and a copy stays in the replay window, because a tab that blips its
    connection comes back a second later asking for what it missed — and the frames it missed
    are the ones it was waiting for.
    """

    CLIENT_QUEUE = 256
    REPLAY_WINDOW = 512

    def __init__(self, replay_limit: int = REPLAY_WINDOW) -> None:
        self._clients: list[queue.Queue] = []
        self._lock = threading.Lock()
        self._replay: deque = deque(maxlen=max(0, int(replay_limit)))
        self._seq = 0
        self._detach: list = []

    def subscribe(self, last_id: int = 0) -> queue.Queue:
        """A new listener, seeded with whatever it missed since `last_id` if it asked for that.

        The replay is handed out under the same lock that publishes take, so an event that lands
        between the two cannot be both buffered and live — delivered once, or not at all.
        """
        client: queue.Queue = queue.Queue(maxsize=self.CLIENT_QUEUE)
        with self._lock:
            if last_id > 0:
                for event_id, payload in self._replay:
                    if event_id <= last_id:
                        continue
                    try:
                        client.put_nowait((event_id, payload))
                    except queue.Full:
                        break            # missed more than a queue holds; the snapshot covers the rest
            self._clients.append(client)
        return client

    def unsubscribe(self, client: queue.Queue) -> None:
        with self._lock:
            if client in self._clients:
                self._clients.remove(client)

    def publish(self, event) -> int:
        """Send one event — a controller's dict, or a typed agent event — to every listener."""
        payload = event if isinstance(event, dict) else ux.coerce(event).to_dict()
        with self._lock:
            self._seq += 1
            event_id = self._seq
            if self._replay.maxlen:
                self._replay.append((event_id, payload))
            clients = list(self._clients)
        for client in clients:
            try:
                client.put_nowait((event_id, payload))
            except queue.Full:
                pass
        return event_id

    def attach(self, bus=None, min_level: "ux.EventLevel | None" = None):
        """Stream the agent's own events through this hub until the returned call detaches it.

        The engine's bus is a second producer on the same wire as the controller's `_emit`, and it
        is the one that knows what a run is doing. The subscription is unplugged on shutdown, so a
        window that has closed is not a listener the next run still pays for.
        """
        bus = ux.global_bus if bus is None else bus
        level = min_level if min_level is not None else _verbosity_level()
        bus.subscribe(self.publish, min_level=level)

        def detach() -> None:
            bus.unsubscribe(self.publish)

        self._detach.append(detach)
        return detach

    def detach_all(self) -> None:
        """Unplug every bus this hub was pointed at; safe to call more than once."""
        for detach in self._detach:
            detach()
        self._detach = []


def _verbosity_level() -> "ux.EventLevel":
    """How much of the agent's notebook the browser is sent, the same dial the CLI reads."""
    verbosity = (os.environ.get("AGENT_VERBOSITY") or "").strip().lower()
    if verbosity in ("verbose", "debug"):
        return ux.EventLevel(verbosity)
    return ux.EventLevel.NORMAL


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
        if path == "/api/memory":
            # The compass panel: both layers, and the block they build. Asked for on the click for the
            # same reason as the project drawer — it reads the memory files and re-measures the block,
            # and a window watching a run has no use for either until somebody opens the tab.
            try:
                return self._json(self.controller.memory_panel(query.get("chat", [""])[0]))
            except (AgentError, OSError, ValueError) as exc:
                return self._fail(exc, 400)
        if path == "/api/events":
            return self._stream(query)
        return self._text("Unknown endpoint.", 404)

    def _last_event_id(self, query: dict) -> int:
        """What the listener says it has already seen.

        A reconnect the *browser* makes names the last id it read in a header. The reconnect the
        page makes itself is a new `EventSource`, which sends no header, so it names the same number
        in the URL instead — either way, a 1.5 s blip costs no events. Neither is required: a tab
        that says nothing gets the live stream and the snapshot it asks for.
        """
        raw = self.headers.get("last-event-id") or query.get("last", [""])[0]
        try:
            return max(0, int(str(raw).strip()))
        except (TypeError, ValueError):
            return 0

    def _stream(self, query: dict):
        client = self.hub.subscribe(self._last_event_id(query))
        self.send_response(200)
        self._safe()
        self.send_header("content-type", "text/event-stream; charset=utf-8")
        self.send_header("cache-control", "no-store")
        self.send_header("x-accel-buffering", "no")
        self.end_headers()
        try:
            self._sse(b"retry: 1500\n\n")
            while True:
                try:
                    event_id, payload = client.get(timeout=15)
                    data = json.dumps(payload, ensure_ascii=False, default=str)
                    chunk = f"id: {event_id}\ndata: {data}\n\n".encode("utf-8")
                except queue.Empty:
                    chunk = b": keep-alive\n\n"
                self._sse(chunk)
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass
        finally:
            self.hub.unsubscribe(client)

    def _sse(self, chunk: bytes) -> None:
        """One frame, out now: a buffered event stream is a page that stopped listening."""
        self.wfile.write(chunk)
        self.wfile.flush()

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


class _Server(ThreadingHTTPServer):
    """The listener, and the one place that knows the session's hub has to be unplugged.

    Stopping the window ends the socket server; it must also end the bus subscription the hub took
    on its behalf, or every run in this process keeps publishing into a window nobody is looking at.
    """

    daemon_threads = True
    hub: Hub | None = None

    def shutdown(self) -> None:
        if self.hub is not None:
            self.hub.detach_all()
        super().shutdown()


def serve(controller, host: str = "127.0.0.1", port: int = 0, bus=None):
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
    hub = Hub()
    bound = type("SessionHandler", (Handler,), {"token": token, "controller": controller, "hub": hub})
    server = _Server((host, port), bound)
    server.hub = hub
    actual = server.server_address[1]
    # The Host check compares against the port this process actually owns, which is only knowable
    # after binding: a caller that asked for port 0 gets a random one.
    bound.port = actual
    hub.attach(bus)
    threading.Thread(target=server.serve_forever, name="ui-http", daemon=True).start()
    return server, f"http://{host}:{actual}/?t={token}", token
