"""Long-lived LLDB service child — one per DAP session (ADR-0003
decision 1).

The DAP server parent spawns this child (``python -m
symrepl.lldb_service``) per debug session; the child holds SBDebugger
and the inferior for the session's lifetime and is destroyed with it.
Parent and child speak the internal request/response + event protocol
over the child's stdin/stdout, using the same ``Content-Length``
framing as DAP itself (:mod:`symrepl.dap_framing`).

M2-a scope (skeleton): the versioned ``hello`` handshake, the uniform
ok/error envelope, stdio hygiene, and the lifecycle semantics.  The
LLDB-facing behavior (launch, stepping, branch tracing) arrives with
M2-b1 — this module deliberately does **not** import ``lldb`` yet,
but the single-owner thread structure is established now: from M2-b1
on, *all* SB API calls execute on the owner thread (ADR-0003
decision 7), while the reader thread only feeds the command queue.

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
            kind, payload = self._commands.get()
            if kind == "eof":
                # Parent is gone — clean close or SIGKILL; nothing to
                # clean up in M2-a.  This is the stdin-EOF suicide.
                return _EXIT_OK
            if kind == "fatal":
                self._log(f"symrepl-service: {payload}\n")
                self._try_send_event(
                    "protocol_violation", {"detail": payload}
                )
                return _EXIT_PROTOCOL
            status = self._handle(payload)
            if status is not None:
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

        if cmd == "ping":
            self._send(
                {
                    "type": "response",
                    "id": message_id,
                    "ok": True,
                    "result": {
                        "pong": True,
                        "service_version": _PROTOCOL_VERSION,
                    },
                }
            )
            return None
        if cmd == "shutdown":
            self._send({"type": "response", "id": message_id, "ok": True,
                        "result": {}})
            return _EXIT_OK

        self._send(
            {
                "type": "response",
                "id": message_id,
                "ok": False,
                "error": {
                    "code": "unknown_command",
                    "message": f"unknown command: {cmd!r}",
                },
            }
        )
        return None


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
