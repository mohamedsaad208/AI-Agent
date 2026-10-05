"""The event wire: ids, replay on reconnect, and the agent's own bus reaching the browser.

``tests/test_webapp.py`` covers who may talk to the server; this covers what a listener gets
once it is talking. Those guarantees live in the socket — a frame a browser cannot number is a
frame it cannot ask again for — so the transport runs for real against a loopback port, with a
bus of the test's own rather than the process-wide one.
"""
import json
import os
from pathlib import Path
import queue
import socket
import struct
import sys
import unittest
import urllib.parse
import urllib.request

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ai_code_engineer import events as ux
from ai_code_engineer.webapp import server as server_module


class Stub:
    """Nothing but the surface the stream touches: these tests are about the wire, not the agent."""

    def snapshot(self):
        return {"ok": True, "busy": False}

    def action(self, type_, payload, emit):
        return {"echo": type_}

    def list_dir(self, path, want_files=None):
        return {"path": str(path), "dirs": [], "files": [], "parent": None}

    def project_info(self, key):
        return {"key": key}

    def set_reply(self, request_id, reply):
        pass


class FrameReader:
    """One browser's view of `/api/events`: the header block, then `id:` + `data:` frames.

    Keep-alive comments and the opening `retry:` line carry no payload, so they are skipped — a
    test asks for the next event and gets the next event.
    """

    def __init__(self, base, token, last_id=None, via_query=False):
        parsed = urllib.parse.urlparse(base + "api/events")
        host, port = parsed.hostname, parsed.port
        path = "/api/events?t=" + urllib.parse.quote(token)
        header = []
        if last_id is not None:
            if via_query:
                path += "&last=%s" % last_id
            else:
                header.append("Last-Event-ID: %s" % last_id)
        request = ["GET %s HTTP/1.1" % path,
                   "Host: %s:%d" % (host, port),
                   "Accept: text/event-stream",
                   "Connection: close"] + header
        self.sock = socket.create_connection((host, port), timeout=15)
        self.sock.sendall(("\r\n".join(request) + "\r\n\r\n").encode())
        self.buf = ""
        self.preface = ""
        self.head = self._read_head()

    def close(self):
        if self.sock is None:
            return
        # An ordinary close lets Windows hand the descriptor to the next connection while the
        # dying handler thread is still writing into it, so one test's frames appear on another
        # test's stream. Aborting releases it immediately and makes a late write fail as a reset
        # on the peer instead — which is what a closed tab really looks like.
        try:
            self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER,
                                 struct.pack("ii", 1, 0))
        except OSError:
            pass
        self.sock.close()
        self.sock = None

    def _read_head(self):
        """Everything up to the blank line, keeping whatever body bytes arrived with it."""
        got = b""
        while b"\r\n\r\n" not in got:
            block = self.sock.recv(65536)
            if not block:
                raise AssertionError("the stream closed before its header")
            got += block
        head, _, rest = got.partition(b"\r\n\r\n")
        self.buf = rest.decode("utf-8", "replace")
        return head.decode("latin-1")

    def frame(self):
        """The next (id, payload) pair, waiting for it as long as the socket will.

        Frames that carry no payload — the opening `retry:` and the keep-alive comments — are kept
        in `preface` instead of dropped: a test that cares about them asks for the text, and one
        that does not simply never looks. A chunk may hold a payload-less piece *and* a frame,
        because the socket coalesces writes: the piece is kept either way, or `retry:` disappears
        from a stream that sent it first on every connection.
        """
        while True:
            while "\n\n" not in self.buf:
                self.buf += self._recv()
            chunk, self.buf = self.buf.split("\n\n", 1)
            event_id, payload = None, None
            noise = []
            for line in chunk.splitlines():
                if line.startswith("id:"):
                    event_id = int(line[3:].strip() or 0)
                elif line.startswith("data:"):
                    payload = json.loads(line[5:].strip())
                else:
                    noise.append(line)
            if noise:
                self.preface += "\n".join(noise) + "\n\n"
            if payload is not None:
                return event_id, payload

    def _recv(self):
        block = self.sock.recv(65536)
        if not block:
            raise AssertionError("the stream closed before the next frame")
        return block.decode("utf-8", "replace")

    def until(self, matches, limit=12):
        """Read frames until one satisfies `matches`, and return everything consumed.

        A closed client socket hands its descriptor to the next connection while the dying
        handler thread still holds a reference to it, so that thread's next write can land on
        somebody else's stream — a test that reads exactly one frame may be reading a stray.
        This is a socket-lifetime artefact of tearing down loopback connections as fast as a
        test class does, not a wire guarantee; a browser does not re-dial that fast.
        """
        seen = []
        for _ in range(limit):
            try:
                seen.append(self.frame())
            except (socket.timeout, AssertionError):
                break
            if matches(seen[-1]):
                return seen
        return seen

    def drain(self, limit=8):
        """Frames already in flight, stopping at the first one that has not arrived."""
        out = []
        self.sock.settimeout(0.5)
        try:
            for _ in range(limit):
                out.append(self.frame())
        except (socket.timeout, OSError):
            pass
        finally:
            self.sock.settimeout(15)
        return out


def queued(client):
    """Everything a subscriber queue holds right now, oldest first."""
    out = []
    while True:
        try:
            out.append(client.get_nowait())
        except queue.Empty:
            return out


# --------------------------------- the hub, on its own ---------------------------------

class HubNumberingAndReplay(unittest.TestCase):
    """Every event leaves with a number, and a window of them stays askable-for."""

    def setUp(self):
        self.hub = server_module.Hub(replay_limit=4)

    def test_events_leave_in_the_order_they_were_published(self):
        client = self.hub.subscribe()
        ids = [self.hub.publish({"kind": "log", "n": n}) for n in (1, 2, 3)]
        self.assertEqual(ids, [1, 2, 3])
        self.assertEqual([event_id for event_id, _ in queued(client)], ids)

    def test_a_reconnect_is_handed_only_what_it_missed(self):
        for n in range(4):
            self.hub.publish({"kind": "log", "n": n})
        client = self.hub.subscribe(last_id=2)
        replayed = queued(client)
        self.assertEqual([event_id for event_id, _ in replayed], [3, 4])
        self.assertEqual([payload["n"] for _, payload in replayed], [2, 3])

    def test_a_listener_that_asks_for_nothing_is_not_handed_the_past(self):
        """A brand-new tab reads the bootstrap snapshot for its state; replaying history into it
        would redraw a run that has already finished as though it were live."""
        self.hub.publish({"kind": "log", "n": 0})
        self.assertEqual(queued(self.hub.subscribe()), [])

    def test_the_replay_window_is_bounded(self):
        """A long run streams thousands of events, and a window nobody is watching must not keep
        one dict per event of them."""
        for n in range(20):
            self.hub.publish({"kind": "log", "n": n})
        self.assertEqual(len(self.hub._replay), 4)
        self.assertEqual([payload["n"] for _, payload in queued(self.hub.subscribe(last_id=15))],
                         [16, 17, 18, 19])

    def test_an_id_older_than_the_window_replays_what_survives(self):
        for n in range(20):
            self.hub.publish({"kind": "log", "n": n})
        self.assertEqual(len(queued(self.hub.subscribe(last_id=1))), 4)

    def test_a_listener_that_cannot_keep_up_drops_events_instead_of_the_agent(self):
        """Publishing runs on the thread doing the work, so a wedged tab must never become a run
        that stops — and the numbering must not lie about what was sent."""
        slow = server_module.Hub()
        slow.CLIENT_QUEUE = 4
        client = slow.subscribe()
        ids = [slow.publish({"kind": "log", "n": n}) for n in range(10)]
        self.assertEqual(ids[-1], 10)
        self.assertEqual([event_id for event_id, _ in queued(client)], [1, 2, 3, 4])

    def test_a_published_controller_dict_is_carried_unchanged(self):
        """The window's own events already have the shape the page switches on, so the hub's job
        here is to number them, not to translate them."""
        client = self.hub.subscribe()
        sent = {"kind": "state", "data": {"busy": True}, "ts": "00:00:00"}
        self.hub.publish(sent)
        self.assertEqual(queued(client)[0][1], sent)

    def test_a_typed_agent_event_arrives_in_the_shared_dialect(self):
        client = self.hub.subscribe()
        self.hub.publish(ux.StageChanged(stage="PLANNING", previous_stage="UNDERSTANDING",
                                         message="planning"))
        _id, payload = client.get_nowait()
        self.assertEqual(payload["kind"], "stage_changed")
        self.assertEqual(payload["stage"], "PLANNING")
        self.assertIn("at", payload)

    def test_a_step_carries_its_id_so_the_page_can_answer_for_it(self):
        client = self.hub.subscribe()
        self.hub.publish(ux.StepUpdated(step_id="step-7", action="read_file", status="done"))
        payload = client.get_nowait()[1]
        self.assertEqual((payload["id"], payload["step_id"]), ("step-7", "step-7"))

    def test_a_replayed_event_is_the_dictionary_that_was_sent(self):
        """The window is a cache: what it hands a reconnecting tab has to go straight to a socket
        with no second translation to fail."""
        client = self.hub.subscribe()
        self.hub.publish(ux.make(ux.EventKind.TOOL_CALL, tool_name="read_file"))
        self.hub.publish(ux.make(ux.EventKind.TOOL_CALL, tool_name="search_code"))
        live = [payload for _id, payload in queued(client)]
        replayed = [payload for _id, payload in queued(self.hub.subscribe(last_id=1))]
        self.assertEqual(replayed, live[1:])


# --------------------------------- the bus, bridged ---------------------------------

class BusBridge(unittest.TestCase):
    """What the engine broadcasts is what the window sees — at the level the window asked for."""

    def setUp(self):
        self.bus = ux.EventBus()
        self.hub = server_module.Hub()
        self.client = self.hub.subscribe()
        self.addCleanup(self.bus.clear)
        # The level a stream defaults to is read from the environment, so these tests say what
        # theirs is rather than inheriting whatever shell the suite happened to run in.
        saved = os.environ.pop("AGENT_VERBOSITY", None)

        def restore():
            if saved is not None:
                os.environ["AGENT_VERBOSITY"] = saved
        self.addCleanup(restore)

    def test_an_event_the_controller_never_touched_reaches_the_listener(self):
        """The engine writes its notebook on the bus; the browser is not allowed to learn about a
        run only when the window's own code happens to forward a line."""
        detach = self.hub.attach(self.bus)
        self.addCleanup(detach)
        self.bus.emit(ux.StageChanged(stage="EXECUTING"))
        self.assertEqual([payload["kind"] for _id, payload in queued(self.client)],
                         ["stage_changed"])

    def test_a_normal_listener_is_not_shown_the_agent_shorthand(self):
        detach = self.hub.attach(self.bus)
        self.addCleanup(detach)
        self.bus.emit(ux.ToolCall(tool_name="read_file", args={"path": "pom.xml"}))
        self.assertEqual(queued(self.client), [])

    def test_a_louder_listener_is(self):
        detach = self.hub.attach(self.bus, min_level=ux.EventLevel.VERBOSE)
        self.addCleanup(detach)
        self.bus.emit(ux.ToolCall(tool_name="read_file"))
        self.assertEqual(len(queued(self.client)), 1)

    def test_the_environment_raises_the_floor_of_every_stream(self):
        saved = os.environ.get("AGENT_VERBOSITY")
        os.environ["AGENT_VERBOSITY"] = "verbose"
        try:
            detach = self.hub.attach(self.bus)
            self.bus.emit(ux.ToolCall(tool_name="read_file"))
        finally:
            detach()
            if saved is None:
                os.environ.pop("AGENT_VERBOSITY", None)
            else:
                os.environ["AGENT_VERBOSITY"] = saved
        self.assertEqual(len(queued(self.client)), 1)

    def test_detaching_stops_the_flow_and_survives_being_said_twice(self):
        """A window that closes mid-run must not stay on the bus for the rest of the process."""
        detach = self.hub.attach(self.bus)
        detach()
        detach()
        self.hub.detach_all()
        self.bus.emit(ux.StageChanged(stage="EXECUTING"))
        self.assertEqual(queued(self.client), [])
        self.assertEqual(self.bus._subscribers, [])

    def test_an_event_of_a_kind_nobody_has_met_still_travels(self):
        """The bus swallows a subscriber's failure, so one that raises mid-run would take the
        web half of the event stream down quietly."""
        detach = self.hub.attach(self.bus)
        self.addCleanup(detach)
        self.bus.emit({"kind": "made_up", "at": "now", "value": 1})
        self.assertEqual([payload["kind"] for _id, payload in queued(self.client)], ["made_up"])


# --------------------------------- the wire, for real ---------------------------------

class StreamOverHttp(unittest.TestCase):
    """Frames on a socket: the id a browser needs in order to reconnect, and what comes back."""

    @classmethod
    def setUpClass(cls):
        cls.bus = ux.EventBus()
        cls.server, cls.url, cls.token = server_module.serve(Stub(), bus=cls.bus)
        cls.addClassCleanup(cls.server.server_close)
        cls.addClassCleanup(cls.server.shutdown)
        cls.base = cls.url.rsplit("?", 1)[0]
        cls.hub = cls.server.RequestHandlerClass.hub

    def _open(self, last_id=None, via_query=False):
        reader = FrameReader(self.base, self.token, last_id, via_query)
        self.addCleanup(reader.close)
        return reader

    def _ask(self, reader, text):
        """Publish until this connection answers with the frame it was published for.

        Re-asking on the same subscription is enough: the hub numbers every frame and the
        queue holds whatever this reader has not read yet. Returns the frames read, oldest
        first, so a test can assert on the payload of the one it was waiting for.
        """
        for _ in range(5):
            sent = self.hub.publish({"kind": "log", "entry": {"text": text}})
            seen = reader.until(lambda frame: frame[0] == sent)
            if seen and seen[-1][0] == sent:
                return sent, seen
        self.fail("no connection answered for its own frame in five asks")

    def test_the_stream_opens_as_event_stream_and_says_how_soon_to_come_back(self):
        """`retry:` is how long the browser waits before it re-dials at all, so it has to be the
        first thing on the wire — asked for as part of the first real frame, never on its own:
        an idle stream is the one thing a test cannot wait on inside a second or two."""
        seen_retry = False
        for _ in range(3):
            reader = self._open()
            self.assertIn("200", reader.head.split("\r\n", 1)[0])
            self.assertIn("text/event-stream", reader.head.lower())
            self._ask(reader, "first")
            if "retry: 1500" in reader.preface:
                seen_retry = True
                break
            reader.close()
        self.assertTrue(seen_retry,
                        "a fresh stream that answers its own frame never said `retry:` first")

    def test_a_frame_carries_the_id_the_hub_gave_it(self):
        reader = self._open()
        _sent, seen = self._ask(reader, "one")
        self.assertEqual(seen[-1][1]["kind"], "log")

    def test_an_agent_event_reaches_the_page_unasked(self):
        reader = self._open()
        self.bus.emit(ux.StepUpdated(step_id="step-3", action="propose", status="done"))
        _id, payload = reader.frame()
        self.assertEqual((payload["kind"], payload["id"]), ("step_updated", "step-3"))

    def test_a_reconnect_names_its_last_id_and_is_given_the_ones_it_missed(self):
        """The whole point of numbering frames: a tab's own state is the last id it read, and
        saying it out loud is what turns a 1.5 s blip into no lost events."""
        reader = self._open()
        self.hub.publish({"kind": "log", "entry": {"text": "a"}})
        second = self.hub.publish({"kind": "log", "entry": {"text": "b"}})
        reader.until(lambda frame: frame[1].get("entry", {}).get("text") == "b")
        reader.close()

        missed = self.hub.publish({"kind": "log", "entry": {"text": "c"}})
        back = self._open(last_id=second)
        replayed = back.until(lambda frame: frame[0] == missed)
        self.assertGreater(missed, second, "the frame to replay has to post-date the named id")
        self.assertIn((missed, "c"), [(event_id, payload["entry"]["text"])
                                      for event_id, payload in replayed],
                      "the frame this tab missed while away was not handed back to it")

    def test_a_reconnect_without_an_id_is_not_handed_the_past(self):
        reader = self._open()
        self.hub.publish({"kind": "log", "entry": {"text": "before"}})
        reader.frame()
        reader.close()

        fresh = self._open()
        later = self.hub.publish({"kind": "log", "entry": {"text": "after"}})
        seen = fresh.until(lambda frame: frame[0] == later)
        self.assertTrue(seen and seen[-1][0] == later,
                        "a new tab did not get the frame it was opened for")
        self.assertNotIn("before", [payload.get("entry", {}).get("text") for _id, payload in seen[:-1]],
                         "a new tab replayed a frame it never missed")

    def test_an_id_the_client_cannot_parse_opens_a_clean_stream(self):
        """The header is client-supplied text on a local port; a bad one has to degrade to "nothing
        was missed" rather than close the stream or throw the handler."""
        reader = self._open(last_id="not-a-number")
        sent = self.hub.publish({"kind": "log", "entry": {"text": "live"}})
        self.assertEqual(reader.frame()[0], sent)

    def test_the_query_form_of_the_id_serves_a_client_that_cannot_set_headers(self):
        """`EventSource` will not send a header it has not been given, and the page re-dials with a
        new one — so the id has to travel in the URL, which is how the live window asks for replay."""
        anchor = self.hub.publish({"kind": "log", "entry": {"text": "seen before the blip"}})
        for _ in range(5):
            self.hub.publish({"kind": "log", "entry": {"text": "missed me"}})
            reader = self._open(last_id=anchor, via_query=True)
            seen = reader.until(lambda frame: frame[1].get("entry", {}).get("text") == "missed me")
            if seen and seen[-1][1]["entry"]["text"] == "missed me":
                self.assertGreater(seen[-1][0], anchor, "a replay handed back a frame it had seen")
                reader.close()
                return
        self.fail("the URL form of the id never served a replay")

    def test_two_windows_on_one_session_get_the_same_numbered_event(self):
        first, second = self._open(), self._open()
        sent = self.hub.publish({"kind": "log", "entry": {"text": "both"}})
        self.assertEqual(first.frame()[0], sent)
        self.assertEqual(second.frame()[0], sent)

    def test_a_tab_that_vanishes_mid_frame_does_not_stop_the_next_one(self):
        """A closed tab is the normal way this endpoint ends; the next one is the page the user
        is actually looking at, and it must be served by the same session."""
        gone = self._open()
        gone.close()
        live = self._open()
        self.hub.publish({"kind": "log", "entry": {"text": "x"}})
        self.bus.emit(ux.StageChanged(stage="EXECUTING"))
        self.assertEqual(sorted(payload["kind"] for _id, payload in [live.frame(), live.frame()]),
                         ["log", "stage_changed"])
        self.assertEqual(live.drain(), [])
        with urllib.request.urlopen(self.base + "api/bootstrap?t=" + urllib.parse.quote(self.token),
                                    timeout=10) as response:
            self.assertEqual(response.status, 200)


class BridgeLifetime(unittest.TestCase):
    """`serve()` puts the session on the run's bus, and only the shutdown takes it off again."""

    def test_a_closed_window_is_not_still_fed_by_the_runs_that_follow_it(self):
        bus = ux.EventBus()
        server, _url, _token = server_module.serve(Stub(), bus=bus)
        self.addCleanup(server.server_close)
        client = server.hub.subscribe()
        bus.emit(ux.StageChanged(stage="EXECUTING"))
        self.assertEqual([payload["kind"] for _id, payload in queued(client)], ["stage_changed"])

        server.shutdown()
        bus.emit(ux.StageChanged(stage="COMPLETED"))
        self.assertEqual(queued(client), [])
        self.assertEqual(bus._subscribers, [])

    def test_a_window_with_no_bus_of_its_own_shares_the_process_one(self):
        """The launcher passes no bus, because the engine runs in this process and defaults to the
        same one — the bridge has to be on it without being told to be."""
        server, _url, _token = server_module.serve(Stub())
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        client = server.hub.subscribe()
        ux.global_bus.emit(ux.StageChanged(stage="VALIDATING"))
        self.assertEqual([payload["kind"] for _id, payload in queued(client)], ["stage_changed"])


class ClientAsksForWhatItMissed(unittest.TestCase):
    """A replay window is only worth having if the page says which id it stopped at.

    `EventSource` sends `Last-Event-ID` on the reconnects the browser makes itself, and on none of
    the ones this page makes — so the number has to travel in the URL it re-dials with.
    """

    def source(self):
        folder = Path(__file__).resolve().parents[1] / "src/ai_code_engineer/webapp/static"
        return (folder / "ui-core.js").read_text(encoding="utf-8")

    def test_the_page_remembers_the_id_of_every_frame_it_read(self):
        self.assertIn("lastEventId = row.lastEventId", self.source())

    def test_the_page_names_that_id_when_it_dials_again(self):
        source = self.source()
        self.assertRegex(source, r"'&last=' \+ encodeURIComponent\(lastEventId\)")


if __name__ == "__main__":
    unittest.main()
