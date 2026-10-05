"""End-to-end DAP lifecycle tests against the REAL service child.

The full production chain — ``DapServer`` + real ``symrepl.lldb_service``
child + real LLDB + the get_sign target (kleeRuntest harness) — is
driven by a hand-written DAP client over a socketpair (the
``debugServer`` mode VS Code-less clients use).  Everything skips
cleanly when lldb or clang is unavailable.
"""

from __future__ import annotations

import shutil
import socket
import subprocess
import sys
import threading
import time
from contextlib import contextmanager
from pathlib import Path

import pytest

from conftest import KTESTS
from symrepl.dap_framing import FrameReader, encode_frame
from symrepl.dap_server import DapServer, _flushing, _spawn_service

pytestmark = pytest.mark.skipif(
    shutil.which("lldb") is None, reason="lldb not available"
)

# Same recorded paths as test_replay: main returns 0 / 1 / 255.
GET_SIGN_EXIT_CODES = [
    ("get_sign_test000001.ktest", 0),
    ("get_sign_test000002.ktest", 1),
    ("get_sign_test000003.ktest", 255),
]


class _Client:
    """Hand-written DAP test client (dev-only, ADR-0003 decision 15)."""

    def __init__(self, sock: socket.socket) -> None:
        sock.settimeout(20.0)
        self._sock = sock
        self._reader = FrameReader(sock.makefile("rb").read1)
        self._seq = 0
        self.events: list[dict] = []
        self.responses: list[dict] = []

    def request(self, command: str, arguments: dict | None = None) -> dict:
        """Send one request; collect events until *its* response (a
        withheld launch response surfaces under ``responses``)."""
        self._seq += 1
        self._sock.sendall(
            encode_frame(
                {
                    "seq": self._seq,
                    "type": "request",
                    "command": command,
                    "arguments": arguments or {},
                }
            )
        )
        while True:
            message = self._reader.read_frame()
            if message.get("type") == "response":
                if message.get("command") == command:
                    return message
                self.responses.append(message)  # e.g. the released launch
                continue
            self.events.append(message)

    def notify(self, command: str, arguments: dict | None = None) -> None:
        """Send a request whose response is withheld by design
        (``launch`` — released on configurationDone)."""
        self._seq += 1
        self._sock.sendall(
            encode_frame(
                {
                    "seq": self._seq,
                    "type": "request",
                    "command": command,
                    "arguments": arguments or {},
                }
            )
        )

    def wait_event(self, name: str) -> dict:
        """Return the (consumed) event, reading frames until it
        arrives — repeated waits see later events of the same name."""
        while True:
            for index, message in enumerate(self.events):
                if (
                    message.get("type") == "event"
                    and message.get("event") == name
                ):
                    return self.events.pop(index)
            message = self._reader.read_frame()
            if message.get("type") == "event":
                self.events.append(message)

    def close(self) -> None:
        try:
            self._sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        self._sock.close()


@contextmanager
def dap_session():
    client_sock, server_sock = socket.socketpair()
    rfile = server_sock.makefile("rb")
    wfile = server_sock.makefile("wb")
    server = DapServer(rfile.read1, _flushing(wfile), _spawn_service)
    thread = threading.Thread(
        target=server.run, name="dap-server-real", daemon=True
    )
    thread.start()
    client = _Client(client_sock)
    try:
        yield server, client, thread
    finally:
        client.close()
        thread.join(timeout=15)
        child = server.child
        if child is not None and child.poll() is None:
            child.kill()
            child.wait(timeout=5)
        for closer in (rfile, wfile, server_sock):
            try:
                closer.close()
            except OSError:
                pass


def _launch_get_sign(client: _Client, ktest: Path, target,
                     stop_on_entry: bool) -> None:
    reply = client.request(
        "initialize",
        {"adapterID": "symrepl"},
    )
    assert reply["success"] is True
    assert reply["body"]["supportsConfigurationDoneRequest"] is True

    client.notify(
        "launch",
        {
            "ktest": str(ktest),
            "target": str(target),
            "stopOnEntry": stop_on_entry,
            "scope": "get_sign",
        },
    )  # response withheld until configurationDone
    initialized = client.wait_event("initialized")
    assert initialized["body"] == {}


@pytest.mark.parametrize(("name", "code"), GET_SIGN_EXIT_CODES, ids=lambda v: v)
def test_dap_full_lifecycle_run_to_exit(get_sign_bin, name: str, code: int):
    """initialize → launch → configurationDone → run → exited(code)."""
    with dap_session() as (server, client, thread):
        _launch_get_sign(client, KTESTS / name, get_sign_bin,
                         stop_on_entry=False)

        reply = client.request("configurationDone")
        assert reply["success"] is True
        # The withheld launch response is released with the reply order
        # preserved (decision 4: launch response before configurationDone).
        released = [r for r in client.responses if r["command"] == "launch"]
        assert released and released[0]["success"] is True

        exited = client.wait_event("exited")
        assert exited["body"]["exitCode"] == code
        terminated = client.wait_event("terminated")
        assert terminated["body"] == {}

        reply = client.request("disconnect")
        assert reply["success"] is True
        thread.join(timeout=15)
        assert not thread.is_alive()
        assert server.exit_code == 0
        child = server.child
        assert child is not None and child.poll() is not None


def test_dap_stop_on_entry_reports_stack(get_sign_bin):
    """stopOnEntry: a reported entry stop, inspectable threads/stack,
    then continue to the recorded exit."""
    with dap_session() as (server, client, thread):
        _launch_get_sign(client, KTESTS / "get_sign_test000002.ktest",
                         get_sign_bin, stop_on_entry=True)

        reply = client.request("configurationDone")
        assert reply["success"] is True
        stopped = client.wait_event("stopped")
        assert stopped["body"]["reason"] == "entry"
        assert stopped["body"]["allThreadsStopped"] is True

        thread_id = stopped["body"]["threadId"]
        reply = client.request("threads")
        assert reply["success"] is True
        threads = reply["body"]["threads"]
        assert len(threads) == 1
        assert threads[0]["id"] == thread_id

        reply = client.request("stackTrace", {"threadId": thread_id})
        assert reply["success"] is True
        frames = reply["body"]["stackFrames"]
        assert frames, "entry stop must show at least one frame"
        for frame in frames:
            assert isinstance(frame["id"], int)
            assert frame["name"]

        reply = client.request("continue")
        assert reply["success"] is True
        assert reply["body"]["allThreadsContinued"] is True

        exited = client.wait_event("exited")
        assert exited["body"]["exitCode"] == 1
        client.wait_event("terminated")

        reply = client.request("disconnect")
        assert reply["success"] is True
        thread.join(timeout=15)
        assert server.exit_code == 0


def test_dap_launch_rejects_missing_ktest(get_sign_bin):
    """Parent-side validation (ADR-0003 decision 11) fails the launch
    before the child is involved."""
    with dap_session() as (server, client, thread):
        reply = client.request("initialize", {"adapterID": "symrepl"})
        assert reply["success"] is True
        reply = client.request(
            "launch",
            {"ktest": "/nonexistent/nope.ktest", "target": str(get_sign_bin)},
        )
        assert reply["success"] is False
        assert "ktest" in reply["message"]
        # Nothing was launched: no initialized event ever fired.
        assert not [e for e in client.events if e.get("event") == "initialized"]
        client.close()
        thread.join(timeout=15)
        assert server.exit_code == 0


def test_dap_breakpoints_variables_and_stale_reference(get_sign_bin):
    """Decision 12 in action: a resume invalidates the previous stop's
    references; breakpoint hits report hitBreakpointIds; variables at
    the breakpoint frame read the injected symbolic value."""
    with dap_session() as (server, client, thread):
        _launch_get_sign(client, KTESTS / "get_sign_test000002.ktest",
                         get_sign_bin, stop_on_entry=True)
        reply = client.request("configurationDone")
        assert reply["success"] is True
        stopped = client.wait_event("stopped")
        assert stopped["body"]["reason"] == "entry"
        thread_id = stopped["body"]["threadId"]

        # Generation-1 frame ids and references.
        reply = client.request("stackTrace", {"threadId": thread_id})
        assert reply["success"] is True
        old_frame_id = reply["body"]["stackFrames"][0]["id"]
        reply = client.request("scopes", {"frameId": old_frame_id})
        assert reply["success"] is True
        old_ref = reply["body"]["scopes"][0]["variablesReference"]

        # Breakpoint on `return 1;` — hit by the x > 0 recorded path.
        source = Path(__file__).parent / "fixtures" / "src" / "get_sign.c"
        reply = client.request(
            "setBreakpoints",
            {
                "source": {"path": str(source)},
                "breakpoints": [{"line": 15}],
            },
        )
        assert reply["success"] is True
        bp = reply["body"]["breakpoints"][0]
        assert bp["verified"] is True and bp["line"] == 15

        reply = client.request("continue")
        assert reply["success"] is True
        stopped = client.wait_event("stopped")
        assert stopped["body"]["reason"] == "breakpoint"
        assert stopped["body"]["hitBreakpointIds"]  # the bp we just set

        # New generation: frame ids re-stamped, variables resolve.
        reply = client.request("stackTrace", {"threadId": thread_id})
        new_frame_id = reply["body"]["stackFrames"][0]["id"]
        assert new_frame_id > old_frame_id  # generation advanced
        reply = client.request("scopes", {"frameId": new_frame_id})
        assert reply["success"] is True
        new_ref = reply["body"]["scopes"][0]["variablesReference"]
        reply = client.request("variables", {"variablesReference": new_ref})
        assert reply["success"] is True
        variables = {v["name"]: v for v in reply["body"]["variables"]}
        assert "x" in variables  # the symbolic argument
        assert variables["x"]["value"]  # materialized by kleeRuntest

        # The pre-resume reference is stale (decision 12).
        reply = client.request("variables", {"variablesReference": old_ref})
        assert reply["success"] is False
        assert "stale" in reply["message"].lower()

        reply = client.request("continue")
        assert reply["success"] is True
        exited = client.wait_event("exited")
        assert exited["body"]["exitCode"] == 1
        client.wait_event("terminated")

        reply = client.request("disconnect")
        assert reply["success"] is True
        thread.join(timeout=15)
        assert server.exit_code == 0


def test_dap_disconnect_while_running_kills_child(get_sign_bin):
    """Decision 8: disconnect during the run kills the inferior and the
    child; the server winds down cleanly."""
    with dap_session() as (server, client, thread):
        _launch_get_sign(client, KTESTS / "get_sign_test000002.ktest",
                         get_sign_bin, stop_on_entry=True)
        reply = client.request("configurationDone")
        assert reply["success"] is True
        client.wait_event("stopped")

        reply = client.request("disconnect")
        assert reply["success"] is True
        thread.join(timeout=15)
        assert not thread.is_alive()
        assert server.exit_code == 0
        child = server.child
        assert child is not None and child.poll() is not None
