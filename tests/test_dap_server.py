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

from symrepl.dap_framing import FrameReader, encode_frame
from symrepl.dap_server import DapServer, _flushing

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

if mode == "crash":
    sys.exit(1)

if mode == "badversion":
    send(sys.stdout.buffer, {"type": "hello", "version": 99})
    sys.stdin.buffer.read()  # block until the shutdown ladder kills us
    sys.exit(2)

send(sys.stdout.buffer, {"type": "hello", "version": 1})
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
                "command": "initialize",
                "arguments": {"adapterID": "symrepl"},
            }
        )
        reply = harness.client.recv()
        assert reply["type"] == "response"
        assert reply["request_seq"] == 1
        assert reply["success"] is False
        assert reply["command"] == "initialize"
        assert "M2-b1" in reply["message"]
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
        code = harness.join()
        assert code is not None and code != 0


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
