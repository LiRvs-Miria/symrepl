"""Long-lived LLDB service child — one per DAP session (ADR-0003
decision 1).

The DAP server parent spawns this child (``python -m
symrepl.lldb_service``) per debug session; the child holds SBDebugger
and the inferior for the session's lifetime and is destroyed with it.
Parent and child speak the internal request/response + event protocol
over the child's stdin/stdout, using the same ``Content-Length``
framing as DAP itself (:mod:`symrepl.dap_framing`).

M2-a established the versioned ``hello`` handshake, the uniform
ok/error envelope, stdio hygiene, and the lifecycle semantics.  M2-b1
adds the interactive debug surface on the same owner thread (ADR-0003
decision 7): every SB API call executes there, while the reader thread
only feeds the command queue.  The LLDB session itself lives in
:mod:`symrepl.service_session`; this module is protocol plumbing.

Internal commands: ``ping``, ``launch`` (ktest/target/stop_on_entry/
scope/timeout), ``config_done``, ``continue``, ``threads``,
``stack_trace`` (thread_id, levels), ``terminate``, ``shutdown``.
Session events (``stopped`` / ``exited`` / ``terminated``) flow to the
parent as protocol events, in wire order.

Wire contract (frozen):

- child → parent, immediately on startup:
  ``{"type": "hello", "version": 1}``
- parent → child:  ``{"type": "request", "id": <int>, "cmd": <str>,
  "params": {...}}``
- child → parent:  ``{"type": "response", "id": <id>, "ok": true,
  "result": {...}}`` or ``{"type": "response", "id": <id>,
  "ok": false, "error": {"code": <str>, "message": <str>}}``
- child → parent events: ``{"type": "event", "event": <str>,
  "params": {...}}``

Lifecycle semantics (ADR-0003 decisions 2 and 8):

- **stdin-EOF suicide**: a clean EOF on stdin exits with status 0 —
  the only parent-death signal reliable against SIGKILL.
- **A malformed frame kills the session** (no resync): any framing
  error, or a well-framed message that is not a valid request, makes
  the child emit a ``protocol_violation`` event (best effort) and
  exit with status 3.

stdio hygiene: the protocol channel owns fd 1 exclusively from the
earliest entry point.  ``sys.stdout`` and ``sys.stderr`` are re-pointed
at the ``--log`` file before any protocol byte is written, so a stray
``print`` from any code path can never corrupt the framing.
"""

from __future__ import annotations

import argparse
import os
import queue
import sys
import threading

from .dap_framing import FrameReader, FramingError, encode_frame
from .service_session import POLL_SECONDS, LldbSession, SessionError

_PROTOCOL_VERSION = 1

#: Exit status on the clean paths: shutdown request, stdin EOF.
_EXIT_OK = 0

#: Exit status when the protocol was violated (malformed frame or a
#: non-request message) — always fatal, never resynced.
_EXIT_PROTOCOL = 3


class _Service:
    """Owner thread + reader thread; ``_send`` is the single writer."""

    def __init__(self, protocol_out, log) -> None:
        self._out = protocol_out
        self._log = log
        self._commands: queue.Queue = queue.Queue()
        self._stop = threading.Event()
        self._session: LldbSession | None = None
        self._pending_events: list[dict] = []

    # ------------------------------------------------------------- io --

    def _send(self, obj: dict) -> None:
        """Write one protocol frame.  Owner thread only — the single
        writer keeps the byte order on the wire authoritative."""
        self._out.write(encode_frame(obj))
        self._out.flush()

    def _try_send_event(self, event: str, params: dict) -> None:
        """Best-effort event; used on the failure path where the
        channel itself may be broken."""
        try:
            self._send({"type": "event", "event": event, "params": params})
        except (OSError, ValueError, FramingError):
            pass  # the log already has the detail

    # --------------------------------------------------------- reader --

    def _read_loop(self) -> None:
        """Reader thread: parse frames off stdin and queue them.

        Never writes protocol bytes (ADR-0003 decision 7: reader
        threads never write).  ``read1`` because ``read`` on a
        BufferedReader blocks until the full chunk arrives — small
        frames would sit undelivered (see :mod:`symrepl.dap_framing`).
        """
        reader = FrameReader(sys.stdin.buffer.read1)  # type: ignore[attr-defined]  # runtime object is a BufferedReader
        while not self._stop.is_set():
            try:
                message = reader.read_frame()
            except EOFError:
                self._commands.put(("eof", None))
                return
            except FramingError as exc:
                self._commands.put(("fatal", f"malformed frame: {exc}"))
                return
            except (OSError, ValueError):
                # stdin closed under us (process teardown) — same as EOF.
                self._commands.put(("eof", None))
                return
            self._commands.put(("message", message))

    # ----------------------------------------------------------- run --

    def _close_session(self) -> None:
        """Tear the LLDB session down; safe to call repeatedly."""
        if self._session is not None:
            self._session.close()
            self._session = None

    def run(self) -> int:
        """Serve until EOF, a shutdown request, or a protocol violation.

        Returns the process exit status.
        """
        self._send({"type": "hello", "version": _PROTOCOL_VERSION})
        reader = threading.Thread(
            target=self._read_loop, name="service-reader", daemon=True
        )
        reader.start()
        while True:
            # Forward session state first, so a response sent below can
            # never overtake the events it caused.
            if self._session is not None:
                for event in self._session.poll_events():
                    self._send({"type": "event", **event})
                if self._session.terminal:
                    # Resources are released, but the session object
                    # stays — the trace report must remain retrievable
                    # via replay.branchTrace afterwards.
                    self._session.close()
            if self._pending_events:
                pending, self._pending_events = self._pending_events, []
                for event in pending:
                    self._send({"type": "event", **event})
            try:
                kind, payload = self._commands.get(timeout=POLL_SECONDS)
            except queue.Empty:
                continue
            if kind == "eof":
                # Parent is gone — clean close or SIGKILL; this is the
                # stdin-EOF suicide (ADR-0003 decision 8).
                self._close_session()
                return _EXIT_OK
            if kind == "fatal":
                self._log(f"symrepl-service: {payload}\n")
                self._close_session()
                self._try_send_event(
                    "protocol_violation", {"detail": payload}
                )
                return _EXIT_PROTOCOL
            status = self._handle(payload)
            if status is not None:
                self._close_session()
                return status

    def _handle(self, message: dict) -> int | None:
        """Handle one queued message; a returned int ends the session."""
        if not isinstance(message, dict) or message.get("type") != "request":
            detail = f"not a request message: {message!r}"
            self._log(f"symrepl-service: {detail}\n")
            self._try_send_event("protocol_violation", {"detail": detail})
            return _EXIT_PROTOCOL

        message_id = message.get("id")
        # bool is an int subclass; a bool id cannot be correlated safely.
        if not isinstance(message_id, int) or isinstance(message_id, bool):
            detail = f"request without an integer id: {message!r}"
            self._log(f"symrepl-service: {detail}\n")
            self._try_send_event("protocol_violation", {"detail": detail})
            return _EXIT_PROTOCOL

        cmd = message.get("cmd")
        if not isinstance(cmd, str):
            self._send(
                {
                    "type": "response",
                    "id": message_id,
                    "ok": False,
                    "error": {
                        "code": "bad_request",
                        "message": f"cmd must be a string, got {cmd!r}",
                    },
                }
            )
            return None

        if cmd == "shutdown":
            self._send({"type": "response", "id": message_id, "ok": True,
                        "result": {}})
            return _EXIT_OK

        try:
            result = self._dispatch(cmd, message.get("params") or {})
        except SessionError as exc:
            self._send(
                {
                    "type": "response",
                    "id": message_id,
                    "ok": False,
                    "error": {"code": exc.code, "message": exc.message},
                }
            )
            return None
        except Exception as exc:  # noqa: BLE001 — the protocol error
            # envelope is the only safe failure mode: a crashing command
            # must never take the service process down with it.
            self._log(
                f"symrepl-service: {cmd} raised "
                f"{type(exc).__name__}: {exc}\n"
            )
            self._send(
                {
                    "type": "response",
                    "id": message_id,
                    "ok": False,
                    "error": {
                        "code": "internal_error",
                        "message": f"{type(exc).__name__}: {exc}",
                    },
                }
            )
            return None
        self._send(
            {
                "type": "response",
                "id": message_id,
                "ok": True,
                "result": result,
            }
        )
        return None

    # ------------------------------------------------- session commands --

    def _require_session(self, action: str) -> LldbSession:
        if self._session is None:
            raise SessionError("not_launched", f"no session to {action}")
        return self._session

    def _dispatch(self, cmd: str, params: dict) -> dict:
        """Interactive session commands; returns the ok-result payload.
        Session state failures raise :class:`SessionError` with a
        machine-readable code."""
        if cmd == "ping":
            return {"pong": True, "service_version": _PROTOCOL_VERSION}
        if cmd == "launch":
            return self._cmd_launch(params)
        if cmd == "config_done":
            self._pending_events.extend(
                self._require_session("config_done").config_done()
            )
            return {}
        if cmd == "continue":
            self._pending_events.extend(
                self._require_session("continue").continue_()
            )
            return {"all_threads_continued": True}
        if cmd == "threads":
            return {"threads": self._require_session("threads").threads()}
        if cmd == "stack_trace":
            return self._cmd_stack_trace(params)
        if cmd == "terminate":
            self._pending_events.extend(
                self._require_session("terminate").terminate()
            )
            return {}
        if cmd == "branch_trace":
            return self._require_session("branch_trace").branch_trace()
        if cmd == "scopes":
            return {
                "scopes": self._require_session("scopes").scopes(
                    params.get("frame_id", 0)
                )
            }
        if cmd == "variables":
            return {
                "variables": self._require_session("variables").variables(
                    params.get("variables_reference", 0)
                )
            }
        if cmd == "set_breakpoints":
            return self._cmd_set_breakpoints(params)
        raise SessionError("unknown_command", f"unknown command: {cmd!r}")

    def _cmd_set_breakpoints(self, params: dict) -> dict:
        session = self._require_session("set_breakpoints")
        path = params.get("path")
        if not isinstance(path, str):
            raise SessionError("bad_request", "path must be a string")
        lines = params.get("lines")
        if not isinstance(lines, list):
            raise SessionError("bad_request", "lines must be a list")
        return session.set_breakpoints(path, lines)

    def _cmd_launch(self, params: dict) -> dict:
        if self._session is not None:
            raise SessionError(
                "already_launched", "a session is already running"
            )
        ktest = params.get("ktest")
        target = params.get("target")
        if not isinstance(ktest, str) or not isinstance(target, str):
            raise SessionError(
                "bad_request", "launch requires ktest and target strings"
            )
        stop_on_entry = bool(params.get("stop_on_entry", False))
        stop_on_branch = bool(params.get("stop_on_branch", False))
        scope = params.get("scope", "module")
        if not isinstance(scope, str):
            raise SessionError("bad_request", "scope must be a string")
        timeout = params.get("timeout")
        if timeout is not None and (
            not isinstance(timeout, (int, float))
            or isinstance(timeout, bool)
            or timeout <= 0
        ):
            raise SessionError(
                "bad_request", "timeout must be a positive number"
            )
        self._session = LldbSession()
        try:
            return self._session.launch(
                ktest,
                target,
                stop_on_entry=stop_on_entry,
                stop_on_branch=stop_on_branch,
                scope=scope,
                timeout=timeout,
            )
        except SessionError:
            self._session = None  # a failed launch leaves nothing behind
            raise

    def _cmd_stack_trace(self, params: dict) -> dict:
        session = self._require_session("stack_trace")
        thread_id = params.get("thread_id", 0)
        if not isinstance(thread_id, int) or isinstance(thread_id, bool):
            raise SessionError(
                "bad_request", "thread_id must be an integer"
            )
        levels = params.get("levels", 20)
        if not isinstance(levels, int) or isinstance(levels, bool) or levels < 1:
            levels = 20
        return session.stack_trace(thread_id, levels)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="symrepl-lldb-service",
        description=(
            "Long-lived LLDB service child speaking the symrepl internal "
            "protocol on stdin/stdout (spawned by the DAP server; not "
            "meant for direct use)."
        ),
    )
    parser.add_argument(
        "--log",
        default=os.devnull,
        metavar="PATH",
        help="file that captures everything that is NOT protocol traffic "
        "(default: discarded)",
    )
    args = parser.parse_args(argv)

    # stdio hygiene, at the earliest entry point: from here on fd 1
    # carries protocol frames and nothing else.
    protocol_out = sys.stdout.buffer
    log = open(args.log, "a", buffering=1, errors="replace")
    sys.stdout = log
    sys.stderr = log

    return _Service(protocol_out, log).run()


if __name__ == "__main__":
    raise SystemExit(main())
