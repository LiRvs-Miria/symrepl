"""Chaos tests (M2-e): the session must fail cleanly with a
``terminated`` event — never a hang — when the service child dies
mid-session or sends malformed frames (ADR-0003 decision 8)."""

from __future__ import annotations

import sys
import time

import pytest

from test_dap_server import (
    _drain_until_response,
    _recv_until_event,
    running_server,
)


@pytest.fixture
def ktest_file(tmp_path):
    from symrepl.ktest import KTest, dump

    path = tmp_path / "chaos.ktest"
    path.write_bytes(
        dump(KTest(version=3, args=(), sym_argvs=0, sym_argv_len=0,
                   objects=()))
    )
    return str(path)


def test_child_crash_mid_session_delivers_terminated(ktest_file):
    with running_server("midcrash") as harness:
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
        # The launch response is withheld until configurationDone
        # (decision 4) — send it before draining replies.
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
        # The child dies right here: the client still sees terminated.
        # (The stopped(entry) event may sit in events or on the wire —
        # the chaos contract is terminated delivery, never a hang.)
        terminated = _recv_until_event(harness.client, "terminated")
        assert terminated["body"] == {}
        harness.client.close()  # hang up: the server winds down

        code = harness.join()
        assert code is not None and code != 0
        child = harness.server.child
        assert child is not None and child.poll() is not None


def test_in_flight_requests_fail_fast_after_child_death(ktest_file):
    """Decision 8: pending internal requests resolve with an error —
    a request after the child's death gets a fast, honest failure
    instead of hanging."""
    with running_server("midcrash") as harness:
        events: list = []
        wire: list = []

        orig_send_client = harness.server._send_client
        orig_child_request = harness.server._child_request

        def traced_send_client(payload):
            wire.append(
                ("S→C", payload.get("type"),
                 payload.get("event") or payload.get("command"))
            )
            orig_send_client(payload)

        def traced_child_request(cmd, params=None, timeout=10.0):
            wire.append(("S→child", cmd))
            result = orig_child_request(cmd, params, timeout)
            wire.append(("child→S ok", cmd))
            return result

        harness.server._send_client = traced_send_client
        harness.server._child_request = traced_child_request

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
        # The launch response is withheld until configurationDone
        # (decision 4) — send it, then collect replies and events.
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
        terminated = _recv_until_event(harness.client, "terminated")

        # Post-death requests fail fast (the child is gone): a direct
        # internal request errors immediately with child_gone — no
        # timeout burn (decision 8).
        started = time.monotonic()
        with pytest.raises(RuntimeError, match="child_gone"):
            harness.server._child_request("threads", {}, timeout=5.0)
        assert time.monotonic() - started < 2.0

        harness.client.close()  # hang up: the server winds down
        # The session ended abnormally (the child died mid-run).
        assert harness.join() != 0


def test_child_request_fails_fast_after_child_death(ktest_file):
    """Decision 8 (direct): after the child's death an internal request
    fails immediately with ``child_gone`` — no timeout burn."""
    with running_server("midcrash") as harness:
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
        _drain_until_response(harness.client, events)
        _recv_until_event(harness.client, "terminated")

        started = time.monotonic()
        with pytest.raises(RuntimeError, match="child_gone"):
            harness.server._child_request("threads", {}, timeout=5.0)
        assert time.monotonic() - started < 2.0

        harness.client.close()
        assert harness.join() != 0


def test_garbled_child_frame_delivers_terminated():
    with running_server("garble") as harness:
        terminated = _recv_until_event(harness.client, "terminated")
        assert terminated["body"] == {}
        harness.client.close()  # hang up: the server winds down
        code = harness.join()
        assert code is not None and code != 0
        child = harness.server.child
        assert child is not None and child.poll() is not None
