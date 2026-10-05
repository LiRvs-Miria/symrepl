"""Parent DAP server tests against a hand-rolled mock service child.

No LLDB anywhere.  The mock child reimplements the Content-Length
framing inline *on purpose* — it validates the wire protocol, not
shared code — and the client side is a raw socketpair, mirroring the
``debugServer`` mode DAP clients use without VS Code.  The mock speaks
the internal contract frozen for M2-a (see
:mod:`symrepl.lldb_service`): versioned hello, ping/shutdown commands,
nonzero exit on protocol violations.
"""

from __future__ import annotations

import json
import socket
import subprocess
import sys
import threading
from contextlib import contextmanager

import pytest

from symrepl.dap_framing import FrameReader, encode_frame
from symrepl.dap_server import DapServer, _flushing
from symrepl.ktest import KTest, dump

_MOCK_CHILD_CODE = r"""
import json
import sys


def read_frame(stream):
    headers = {}
    line = b""
    while True:
        byte = stream.read(1)
        if not byte:
            raise EOFError
        line += byte
        if line.endswith(b"\r\n"):
            if line.strip() == b"":
                break
            name, _, value = line[:-2].partition(b":")
            headers[name.strip().lower()] = value.strip()
            line = b""
    length = int(headers[b"content-length"])
    body = b""
    while len(body) < length:
        chunk = stream.read(length - len(body))
        if not chunk:
            raise EOFError
        body += chunk
    return json.loads(body.decode("utf-8"))


def send(stream, payload):
    data = json.dumps(payload).encode("utf-8")
    stream.write(b"Content-Length: %d\r\n\r\n" % len(data) + data)
    stream.flush()


mode = sys.argv[1] if len(sys.argv) > 1 else "ok"
stdin = sys.stdin.buffer
stdout = sys.stdout.buffer

if mode == "crash":
    sys.exit(1)
if mode == "midcrash":
    # Die right after the entry stop — mid-session child death.
    send(stdout, {"type": "hello", "version": 1})
    while True:
        try:
            message = read_frame(stdin)
        except EOFError:
            sys.exit(0)
        if message.get("type") != "request":
            continue
        if message.get("cmd") == "launch":
            send(stdout, {"type": "response", "id": message["id"],
                          "ok": True, "result": {"pid": 4242}})
        elif message.get("cmd") == "config_done":
            send(stdout, {"type": "response", "id": message["id"],
                          "ok": True, "result": {}})
            send(stdout, {"type": "event", "event": "stopped",
                          "params": {"reason": "entry", "thread_id": 1,
                                     "all_threads_stopped": True}})
            sys.exit(1)
        else:
            send(stdout, {
                "type": "response", "id": message["id"], "ok": False,
                "error": {"code": "unknown_command", "message": "mock"},
            })
if mode == "garble":
    # A mid-session malformed frame: the session must die cleanly.
    send(stdout, {"type": "hello", "version": 1})
    stdout.write(b"THIS-IS-NOT-A-FRAME\r\n\r\n")
    stdout.flush()
    stdin.read()  # block until the shutdown ladder kills us
    sys.exit(2)
if mode == "badversion":
    send(stdout, {"type": "hello", "version": 99})
    stdin.read()  # block until the shutdown ladder kills us
    sys.exit(2)

send(stdout, {"type": "hello", "version": 1})

if mode == "lifecycle":
    # M2-b1/b2 session surface: lifecycle + state surface.  Reference
    # generations are simulated: gen starts at 1 and bumps on a
    # breakpoint hit, so pre-hit references go stale afterwards.
    gen = 1
    bps = False
    bp_hit = False
    while True:
        try:
            message = read_frame(stdin)
        except EOFError:
            sys.exit(0)
        if message.get("type") != "request":
            continue
        command = message.get("cmd")
        if command == "launch":
            send(stdout, {
                "type": "response", "id": message["id"], "ok": True,
                "result": {"pid": 4242},
            })
        elif command == "config_done":
            send(stdout, {"type": "response", "id": message["id"],
                          "ok": True, "result": {}})
            send(stdout, {"type": "event", "event": "output",
                          "params": {"category": "stderr",
                                     "text": "mock inferior wrote this"}})
            send(stdout, {"type": "event", "event": "stopped",
                          "params": {"reason": "entry", "thread_id": 1,
                                     "all_threads_stopped": True}})
        elif command == "continue":
            send(stdout, {
                "type": "response", "id": message["id"], "ok": True,
                "result": {"all_threads_continued": True},
            })
            if bps and not bp_hit:
                bp_hit = True
                gen += 1
                send(stdout, {
                    "type": "event", "event": "stopped",
                    "params": {"reason": "breakpoint", "thread_id": 1,
                               "all_threads_stopped": True,
                               "breakpoint_ids": [1]},
                })
            else:
                send(stdout, {"type": "event", "event": "exited",
                              "params": {"exit_code": 7}})
                send(stdout, {"type": "event", "event": "terminated",
                              "params": {}})
        elif command == "threads":
            send(stdout, {
                "type": "response", "id": message["id"], "ok": True,
                "result": {"threads": [{"id": 1, "name": "main"}]},
            })
        elif command == "stack_trace":
            send(stdout, {
                "type": "response", "id": message["id"], "ok": True,
                "result": {"stack_frames": [
                    {"id": 0, "name": "main", "line": 3, "column": 5,
                     "path": "/x/get_sign.c"}],
                    "total_frames": 1},
            })
        elif command == "terminate":
            send(stdout, {"type": "response", "id": message["id"],
                          "ok": True, "result": {}})
            send(stdout, {"type": "event", "event": "terminated",
                          "params": {}})
        elif command == "branch_trace":
            send(stdout, {
                "type": "response", "id": message["id"], "ok": True,
                "result": {"trace": "branch trace: scope=get_sign, "
                                    "2 conditional site(s) instrumented, "
                                    "2 hit(s) in execution order (mock)"},
            })
        elif command == "scopes":
            send(stdout, {
                "type": "response", "id": message["id"], "ok": True,
                "result": {"scopes": [
                    {"name": "Locals",
                     "variables_reference": gen * 1000 + 1}]},
            })
        elif command == "variables":
            ref = (message.get("params") or {}).get("variables_reference", 0)
            if ref == gen * 1000 + 1:
                send(stdout, {
                    "type": "response", "id": message["id"], "ok": True,
                    "result": {"variables": [
                        {"name": "x", "value": "42", "type": "int",
                         "variables_reference": 0}]},
                })
            else:
                send(stdout, {
                    "type": "response", "id": message["id"], "ok": False,
                    "error": {"code": "stale_reference",
                              "message": f"reference {ref} is stale"},
                })
        elif command == "set_breakpoints":
            bps = True
            send(stdout, {
                "type": "response", "id": message["id"], "ok": True,
                "result": {"breakpoints": [{"line": 5, "verified": True}]},
            })
        elif command == "step":
            send(stdout, {"type": "response", "id": message["id"],
                          "ok": True, "result": {}})
            send(stdout, {"type": "event", "event": "stopped",
                          "params": {"reason": "step", "thread_id": 1,
                                     "all_threads_stopped": True}})
        elif command == "pause":
            send(stdout, {"type": "response", "id": message["id"],
                          "ok": True, "result": {}})
            send(stdout, {"type": "event", "event": "stopped",
                          "params": {"reason": "pause", "thread_id": 1,
                                     "all_threads_stopped": True}})
        elif command == "shutdown":
            send(stdout, {"type": "response", "id": message["id"],
                          "ok": True, "result": {}})
            sys.exit(0)
        else:
            send(stdout, {
                "type": "response", "id": message["id"], "ok": False,
                "error": {"code": "unknown_command", "message": "mock"},
            })

while True:
    try:
        message = read_frame(sys.stdin.buffer)
    except EOFError:
        sys.exit(0)
    if message.get("type") != "request":
        continue
    command = message.get("cmd")
    if command == "ping":
        send(sys.stdout.buffer, {
            "type": "response",
            "id": message["id"],
            "ok": True,
            "result": {"pong": True, "service_version": 1},
        })
    elif command == "shutdown":
        send(sys.stdout.buffer, {
            "type": "response",
            "id": message["id"],
            "ok": True,
            "result": {},
        })
        sys.exit(0)
    else:
        send(sys.stdout.buffer, {
            "type": "response",
            "id": message["id"],
            "ok": False,
            "error": {"code": "unknown_command", "message": "mock"},
        })
"""


class _Client:
    """Test-side DAP client over the raw socketpair."""

    def __init__(self, sock: socket.socket) -> None:
        # Timeout must be set BEFORE makefile — SocketIO snapshots it.
        sock.settimeout(10.0)
        self._sock = sock
        self._reader = FrameReader(sock.makefile("rb").read1)

    def send(self, payload: dict) -> None:
        self._sock.sendall(encode_frame(payload))

    def send_raw(self, data: bytes) -> None:
        self._sock.sendall(data)

    def recv(self) -> dict:
        return self._reader.read_frame()

    def close(self) -> None:
        # shutdown() delivers FIN even though the makefile created in
        # __init__ still holds a reference — a bare socket.close() only
        # decrements it and the peer would wait forever for EOF.
        try:
            self._sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        self._sock.close()


class _Harness:
    """``DapServer`` on a thread; the client end of the socketpair is
    the test's DAP client."""

    def __init__(self, mode: str) -> None:
        client_sock, server_sock = socket.socketpair()
        rfile = server_sock.makefile("rb")
        wfile = server_sock.makefile("wb")
        self.        server = DapServer(
            reader=rfile.read1,
            writer=_flushing(wfile),
            child_spawner=lambda: subprocess.Popen(
                [sys.executable, "-c", _MOCK_CHILD_CODE, mode],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
            ),
        )
        self.client = _Client(client_sock)
        self._rfile = rfile
        self._wfile = wfile
        self._server_sock = server_sock
        self._thread = threading.Thread(
            target=self.server.run, name="dap-server-under-test", daemon=True
        )
        self._thread.start()

    def join(self, timeout: float = 15.0) -> int | None:
        """Wait for the server to exit; asserts it did not hang."""
        self._thread.join(timeout)
        assert not self._thread.is_alive(), "server run() hung"
        return self.server.exit_code

    def cleanup(self) -> None:
        # Close the client end FIRST: EOF unblocks the server's client
        # pump so later closes cannot deadlock on the buffer lock.
        self.client.close()
        self._thread.join(timeout=10)
        child = self.server.child
        if child is not None and child.poll() is None:
            child.kill()
            child.wait(timeout=5)
        for closer in (self._rfile, self._wfile, self._server_sock):
            try:
                closer.close()
            except OSError:
                pass
        try:
            self._server_sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass


@contextmanager
def running_server(mode: str = "ok"):
    harness = _Harness(mode)
    try:
        yield harness
    finally:
        harness.cleanup()


def test_dap_request_answers_not_implemented():
    with running_server() as harness:
        harness.client.send(
            {
                "seq": 1,
                "type": "request",
                # evaluate/completions stay deferred past M2 (ADR-0003
                # decision 4) — the honest not-implemented surface.
                "command": "evaluate",
                "arguments": {"expression": "1+1", "frameId": 0},
            }
        )
        reply = harness.client.recv()
        assert reply["type"] == "response"
        assert reply["request_seq"] == 1
        assert reply["success"] is False
        assert reply["command"] == "evaluate"
        assert "not implemented" in reply["message"]
        assert isinstance(reply["seq"], int) and reply["seq"] >= 1
        harness.client.close()  # clean session end
        assert harness.join() == 0


def test_ping_child_round_trip():
    with running_server() as harness:
        result = harness.server.ping_child(timeout=10.0)
        assert result["pong"] is True
        assert result["service_version"] == 1
        harness.client.close()
        assert harness.join() == 0


def test_client_eof_shuts_down_cleanly():
    with running_server() as harness:
        harness.client.send(
            {
                "seq": 2,
                "type": "request",
                "command": "continue",
                "arguments": {},
            }
        )
        reply = harness.client.recv()
        assert reply["request_seq"] == 2
        harness.client.close()
        assert harness.join() == 0
        child = harness.server.child
        assert child is not None and child.poll() is not None  # reaped


def test_child_crash_fails_server():
    with running_server("crash") as harness:
        terminated = _recv_until_event(harness.client, "terminated")
        assert terminated["body"] == {}
        harness.client.close()  # hang up: the server winds down
        code = harness.join()
        assert code is not None and code != 0
        child = harness.server.child
        assert child is not None and child.poll() is not None


def test_bad_hello_version_fails_server():
    with running_server("badversion") as harness:
        code = harness.join()
        assert code is not None and code != 0
        child = harness.server.child
        assert child is not None and child.poll() is not None  # ladder reaped


def test_malformed_client_frame_fails_server():
    with running_server() as harness:
        harness.client.send_raw(b"not a frame at all\r\n\r\n{}")
        code = harness.join()
        assert code is not None and code != 0
        child = harness.server.child
        assert child is not None and child.poll() is not None  # ladder reaped


# ------------------------------------------- DAP lifecycle (M2-b1, mock) --


def _drain_until_response(client: _Client, events: list) -> dict:
    """Collect event frames until a response arrives; return it."""
    while True:
        message = client.recv()
        if message.get("type") == "response":
            return message
        events.append(message)


def _recv_until_event(client: _Client, name: str) -> dict:
    """Read frames until the named DAP event arrives; responses in
    between (e.g. continue's own reply) are skipped."""
    while True:
        message = client.recv()
        if message.get("type") == "event" and message.get("event") == name:
            return message


@pytest.fixture
def ktest_file(tmp_path):
    """A minimal valid .ktest — the parent validates it at launch."""
    path = tmp_path / "lifecycle.ktest"
    path.write_bytes(
        dump(KTest(version=3, args=(), sym_argvs=0, sym_argv_len=0,
                   objects=()))
    )
    return str(path)


def test_initialize_returns_frozen_capabilities():
    with running_server() as harness:
        harness.client.send(
            {
                "seq": 1,
                "type": "request",
                "command": "initialize",
                "arguments": {"adapterID": "symrepl"},
            }
        )
        reply = harness.client.recv()
        assert reply["success"] is True
        assert reply["body"]["supportsConfigurationDoneRequest"] is True
        assert reply["body"]["supportsTerminateRequest"] is True
        # Decision 8: supportsTerminateDebuggee stays undeclared.
        assert "supportsTerminateDebuggee" not in reply["body"]
        harness.client.close()
        assert harness.join() == 0


def test_launch_response_withheld_until_configuration_done(ktest_file):
    with running_server("lifecycle") as harness:
        events: list = []
        harness.client.send(
            {
                "seq": 1,
                "type": "request",
                "command": "initialize",
                "arguments": {},
            }
        )
        reply = _drain_until_response(harness.client, events)
        assert reply["command"] == "initialize" and reply["success"]

        harness.client.send(
            {
                "seq": 2,
                "type": "request",
                "command": "launch",
                "arguments": {"ktest": ktest_file, "target": sys.executable},
            }
        )
        # A probe request proves launch is withheld: the threads answer
        # comes back first, while launch has produced no response.
        harness.client.send(
            {"seq": 3, "type": "request", "command": "threads",
             "arguments": {}}
        )
        reply = _drain_until_response(harness.client, events)
        assert reply["command"] == "threads" and reply["success"]
        assert reply["body"]["threads"] == [{"id": 1, "name": "main"}]
        kinds = [event.get("event") for event in events]
        assert "process" in kinds and "initialized" in kinds

        harness.client.send(
            {
                "seq": 4,
                "type": "request",
                "command": "configurationDone",
                "arguments": {},
            }
        )
        launch_reply = _drain_until_response(harness.client, events)
        assert launch_reply["command"] == "launch"
        assert launch_reply["success"] is True
        config_reply = _drain_until_response(harness.client, events)
        assert config_reply["command"] == "configurationDone"
        assert config_reply["success"] is True
        # Ordering invariant: launch response precedes configurationDone.
        assert launch_reply["seq"] < config_reply["seq"]

        # The mock reports the entry stop on configurationDone.
        stopped_frame = _recv_until_event(harness.client, "stopped")
        assert stopped_frame["body"]["reason"] == "entry"

        harness.client.send(
            {"seq": 5, "type": "request", "command": "continue",
             "arguments": {}}
        )
        # No breakpoints set: the scripted run goes to exit(7).
        exited_frame = _recv_until_event(harness.client, "exited")
        assert exited_frame["body"]["exitCode"] == 7
        terminated_frame = _recv_until_event(harness.client, "terminated")
        assert terminated_frame.get("event") == "terminated"

        harness.client.close()
        assert harness.join() == 0


def test_launch_validation_failure_reports_error():
    with running_server("lifecycle") as harness:
        harness.client.send(
            {
                "seq": 1,
                "type": "request",
                "command": "initialize",
                "arguments": {},
            }
        )
        _drain_until_response(harness.client, [])
        harness.client.send(
            {
                "seq": 2,
                "type": "request",
                "command": "launch",
                "arguments": {"target": sys.executable},  # ktest missing
            }
        )
        events: list = []
        reply = _drain_until_response(harness.client, events)
        assert reply["command"] == "launch" and reply["success"] is False
        assert "ktest" in reply["message"]
        # The failure happened parent-side: no initialized event leaked.
        assert not [e for e in events if e.get("event") == "initialized"]
        harness.client.close()
        assert harness.join() == 0


def test_threads_stack_trace_continue_terminate_lifecycle(ktest_file):
    with running_server("lifecycle") as harness:
        events: list = []
        harness.client.send(
            {"seq": 1, "type": "request", "command": "initialize",
             "arguments": {}}
        )
        _drain_until_response(harness.client, events)
        harness.client.send(
            {
                "seq": 2,
                "type": "request",
                "command": "launch",
                "arguments": {"ktest": ktest_file, "target": sys.executable},
            }
        )
        harness.client.send(
            {
                "seq": 3,
                "type": "request",
                "command": "configurationDone",
                "arguments": {},
            }
        )
        launch_reply = _drain_until_response(harness.client, events)
        assert launch_reply["command"] == "launch"
        config_reply = _drain_until_response(harness.client, events)
        assert config_reply["command"] == "configurationDone"

        harness.client.send(
            {"seq": 4, "type": "request", "command": "threads",
             "arguments": {}}
        )
        reply = _drain_until_response(harness.client, events)
        assert reply["body"]["threads"] == [{"id": 1, "name": "main"}]

        harness.client.send(
            {"seq": 5, "type": "request", "command": "stackTrace",
             "arguments": {"threadId": 1}}
        )
        reply = _drain_until_response(harness.client, events)
        frames = reply["body"]["stackFrames"]
        assert frames and frames[0]["name"] == "main"

        harness.client.send(
            {"seq": 6, "type": "request", "command": "continue",
             "arguments": {}}
        )
        reply = _drain_until_response(harness.client, events)
        assert reply["body"]["allThreadsContinued"] is True

        # Terminate after the (scripted) exit is idempotent success.
        harness.client.send(
            {"seq": 7, "type": "request", "command": "terminate",
             "arguments": {}}
        )
        reply = _drain_until_response(harness.client, events)
        assert reply["command"] == "terminate" and reply["success"]

        harness.client.close()
        assert harness.join() == 0


def test_scopes_variables_round_trip(ktest_file):
    """scopes at the reported entry stop → Locals ref → variables."""
    with running_server("lifecycle") as harness:
        events: list = []
        harness.client.send(
            {"seq": 1, "type": "request", "command": "initialize",
             "arguments": {}}
        )
        _drain_until_response(harness.client, events)
        harness.client.send(
            {
                "seq": 2,
                "type": "request",
                "command": "launch",
                "arguments": {"ktest": ktest_file, "target": sys.executable},
            }
        )
        harness.client.send(
            {
                "seq": 3,
                "type": "request",
                "command": "configurationDone",
                "arguments": {},
            }
        )
        launch_reply = _drain_until_response(harness.client, events)
        assert launch_reply["command"] == "launch"
        config_reply = _drain_until_response(harness.client, events)
        assert config_reply["command"] == "configurationDone"
        stopped_frame = _recv_until_event(harness.client, "stopped")
        assert stopped_frame["body"]["reason"] == "entry"

        harness.client.send(
            {"seq": 4, "type": "request", "command": "scopes",
             "arguments": {"frameId": 0}}
        )
        reply = _drain_until_response(harness.client, events)
        scopes = reply["body"]["scopes"]
        assert scopes[0]["name"] == "Locals"
        assert scopes[0]["expensive"] is False
        ref = scopes[0]["variablesReference"]
        assert ref == 1001  # generation 1

        harness.client.send(
            {"seq": 5, "type": "request", "command": "variables",
             "arguments": {"variablesReference": ref}}
        )
        reply = _drain_until_response(harness.client, events)
        assert reply["body"]["variables"] == [
            {"name": "x", "value": "42", "type": "int",
             "variablesReference": 0}
        ]

        harness.client.send(
            {"seq": 6, "type": "request", "command": "continue",
             "arguments": {}}
        )
        exited_frame = _recv_until_event(harness.client, "exited")
        assert exited_frame.get("event") == "exited"
        harness.client.close()
        assert harness.join() == 0


def test_breakpoint_hit_and_stale_reference(ktest_file):
    """Decision 12 over the mock: a breakpoint hit starts a new
    reference generation; pre-hit references go stale with an error,
    fresh ones resolve."""
    with running_server("lifecycle") as harness:
        events: list = []
        harness.client.send(
            {"seq": 1, "type": "request", "command": "initialize",
             "arguments": {}}
        )
        _drain_until_response(harness.client, events)
        harness.client.send(
            {
                "seq": 2,
                "type": "request",
                "command": "launch",
                "arguments": {"ktest": ktest_file, "target": sys.executable},
            }
        )
        harness.client.send(
            {
                "seq": 3,
                "type": "request",
                "command": "configurationDone",
                "arguments": {},
            }
        )
        launch_reply = _drain_until_response(harness.client, events)
        assert launch_reply["command"] == "launch"
        config_reply = _drain_until_response(harness.client, events)
        assert config_reply["command"] == "configurationDone"
        stopped_entry = _recv_until_event(harness.client, "stopped")
        assert stopped_entry["body"]["reason"] == "entry"

        harness.client.send(
            {
                "seq": 4,
                "type": "request",
                "command": "setBreakpoints",
                "arguments": {
                    "source": {"path": "/x/get_sign.c"},
                    "breakpoints": [{"line": 5}],
                },
            }
        )
        reply = _drain_until_response(harness.client, events)
        assert reply["body"]["breakpoints"] == [{"verified": True, "line": 5}]

        # Generation-1 references.
        harness.client.send(
            {"seq": 5, "type": "request", "command": "scopes",
             "arguments": {"frameId": 0}}
        )
        reply = _drain_until_response(harness.client, events)
        old_ref = reply["body"]["scopes"][0]["variablesReference"]
        assert old_ref == 1001

        harness.client.send(
            {"seq": 6, "type": "request", "command": "continue",
             "arguments": {}}
        )
        stopped_bp = _recv_until_event(harness.client, "stopped")
        assert stopped_bp["body"]["reason"] == "breakpoint"
        assert stopped_bp["body"]["hitBreakpointIds"] == [1]

        # Pre-hit reference: stale — an error, never a mis-read.
        harness.client.send(
            {"seq": 7, "type": "request", "command": "variables",
             "arguments": {"variablesReference": old_ref}}
        )
        reply = _drain_until_response(harness.client, events)
        assert reply["success"] is False
        assert "stale_reference" in reply["message"]

        # New generation: fresh scopes and variables resolve.
        harness.client.send(
            {"seq": 8, "type": "request", "command": "scopes",
             "arguments": {"frameId": 0}}
        )
        reply = _drain_until_response(harness.client, events)
        new_ref = reply["body"]["scopes"][0]["variablesReference"]
        assert new_ref == 2001  # generation 2
        harness.client.send(
            {"seq": 9, "type": "request", "command": "variables",
             "arguments": {"variablesReference": new_ref}}
        )
        reply = _drain_until_response(harness.client, events)
        assert reply["body"]["variables"][0]["name"] == "x"

        harness.client.send(
            {"seq": 10, "type": "request", "command": "continue",
             "arguments": {}}
        )
        exited_frame = _recv_until_event(harness.client, "exited")
        assert exited_frame.get("event") == "exited"
        harness.client.close()
        assert harness.join() == 0


def test_stepping_and_pause_over_mock(ktest_file):
    """M2-d over the mock: next reports a step stop; pause reports a
    pause stop."""
    with running_server("lifecycle") as harness:
        events: list = []
        harness.client.send(
            {"seq": 1, "type": "request", "command": "initialize",
             "arguments": {}}
        )
        _drain_until_response(harness.client, events)
        harness.client.send(
            {
                "seq": 2,
                "type": "request",
                "command": "launch",
                "arguments": {"ktest": ktest_file, "target": sys.executable},
            }
        )
        harness.client.send(
            {
                "seq": 3,
                "type": "request",
                "command": "configurationDone",
                "arguments": {},
            }
        )
        launch_reply = _drain_until_response(harness.client, events)
        assert launch_reply["command"] == "launch"
        config_reply = _drain_until_response(harness.client, events)
        assert config_reply["command"] == "configurationDone"
        stopped_entry = _recv_until_event(harness.client, "stopped")
        assert stopped_entry["body"]["reason"] == "entry"

        harness.client.send(
            {"seq": 4, "type": "request", "command": "next",
             "arguments": {"threadId": 1}}
        )
        reply = _drain_until_response(harness.client, events)
        assert reply["command"] == "next" and reply["success"]
        stepped = _recv_until_event(harness.client, "stopped")
        assert stepped["body"]["reason"] == "step"

        harness.client.send(
            {"seq": 5, "type": "request", "command": "pause",
             "arguments": {"threadId": 1}}
        )
        reply = _drain_until_response(harness.client, events)
        assert reply["command"] == "pause" and reply["success"]
        paused = _recv_until_event(harness.client, "stopped")
        assert paused["body"]["reason"] == "pause"

        harness.client.close()
        assert harness.join() == 0
