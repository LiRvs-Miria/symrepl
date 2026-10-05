"""Parent-side DAP server: one client, one service child.

Topology (ADR-0003 decisions 1, 3): this process *is* the DAP server —
it speaks DAP to the client over stdio (default) or a single TCP
connection (``--port``), and drives one long-lived
``symrepl.lldb_service`` child per session over the internal framed
protocol.

M2-a scope: transport, the child lifecycle (shutdown ladder, decision
8), and the single-writer output ordering invariant (decision 4: all
client-bound frames are written by the run loop, so their order on the
wire is authoritative).  Every DAP request gets an honest
``success: false`` "not implemented until M2-b1" response — request
semantics (initialize/launch/...) land with M2-b1, as does the
translation of a mid-session child death into a ``terminated`` event;
in M2-a that death simply fails the server with a nonzero exit.

Threads (decision 7): a client-reader and a child-reader pump frames
into one tagged event queue; the run loop (single owner) multiplexes
it and is the only writer toward the client.  Reader threads never
write client frames.

Lifecycle (decision 8): a clean client EOF or *any* failure path runs
the shutdown ladder — SIGTERM, a grace window, then SIGKILL and
``waitpid``.  A graceful child exit is never assumed.
"""

from __future__ import annotations

import argparse
import os
import queue
import socket
import subprocess
import sys
import tempfile
import threading
from collections.abc import Callable

from .dap_framing import FrameReader, FramingError, encode_frame

_PROTOCOL_VERSION = 1

#: SIGTERM → grace → SIGKILL (ADR-0003 decision 8).
_SHUTDOWN_GRACE_SECONDS = 3.0

#: How long ``ping_child`` waits for a response.
_PING_TIMEOUT_SECONDS = 10.0

_EXIT_OK = 0
_EXIT_FAILURE = 1


class _PendingRequest:
    """One in-flight internal request awaiting its response."""

    def __init__(self) -> None:
        self.done = threading.Event()
        self.ok = False
        self.result: dict | None = None
        self.error: dict | None = None


class DapServer:
    """DAP server core; streams and child spawning are injectable for
    tests (the production ``main`` wires stdio/socket and the real
    service child)."""

    def __init__(
        self,
        reader: Callable[[int], bytes],
        writer: Callable[[bytes], None],
        child_spawner: Callable[[], subprocess.Popen],
    ) -> None:
        self._client_read = reader
        self._client_write = writer
        self._child_spawner = child_spawner

        self._events: "queue.Queue[tuple[str, object]]" = queue.Queue()
        self._child: subprocess.Popen | None = None
        self._child_lock = threading.Lock()  # guards writes to child stdin
        self._pending: dict[int, _PendingRequest] = {}
        self._pending_lock = threading.Lock()
        self._client_seq = 0
        self._request_id = 0
        self._child_ready = threading.Event()
        self.exit_code: int | None = None

    # ------------------------------------------------------------ child --

    @property
    def child(self) -> subprocess.Popen | None:
        """The spawned service child (observability, tests)."""
        return self._child

    def _spawn_child(self) -> None:
        self._child = self._child_spawner()
        self._child_ready.set()

    def _send_child(self, payload: dict) -> None:
        child = self._child
        if child is None or child.stdin is None:
            raise RuntimeError("service child is not running")
        data = encode_frame(payload)
        with self._child_lock:
            child.stdin.write(data)
            child.stdin.flush()

    def ping_child(self, timeout: float = _PING_TIMEOUT_SECONDS) -> dict:
        """Send an internal ``ping`` request and return its result.

        Usable from any thread once :meth:`run` has spawned the child;
        responses are resolved by the child-reader thread.
        """
        if not self._child_ready.wait(timeout):
            raise TimeoutError("service child did not start")
        request = _PendingRequest()
        with self._pending_lock:
            self._request_id += 1
            request_id = self._request_id
            self._pending[request_id] = request
        try:
            self._send_child(
                {
                    "type": "request",
                    "id": request_id,
                    "cmd": "ping",
                    "params": {},
                }
            )
        except (OSError, RuntimeError, ValueError) as exc:
            with self._pending_lock:
                self._pending.pop(request_id, None)
            raise RuntimeError(f"service child unreachable: {exc}") from exc
        if not request.done.wait(timeout):
            with self._pending_lock:
                self._pending.pop(request_id, None)
            raise TimeoutError(
                f"service child ping timed out after {timeout}s"
            )
        if not request.ok:
            error = request.error or {}
            raise RuntimeError(
                "service child ping failed: "
                f"{error.get('code', 'unknown')}: "
                f"{error.get('message', '')}"
            )
        return request.result or {}

    # ------------------------------------------------------------- run --

    def run(self) -> int:
        """Drive one session to completion; returns the process exit
        code (0 clean, 1 failure)."""
        self.exit_code = None
        try:
            self._spawn_child()
        except OSError as exc:
            return self._fail(f"failed to spawn service child: {exc}")

        child_pump = self._start_pump(self._pump_child, "dap-child-reader")
        client_pump = self._start_pump(
            self._pump_client, "dap-client-reader"
        )

        code = self._loop()
        self.exit_code = code
        self._terminate_child()
        # The child pump unblocks on kill (stdout EOF); the client pump
        # only when the client end closed — daemon threads, best effort.
        child_pump.join(timeout=2.0)
        if code == _EXIT_OK:
            client_pump.join(timeout=2.0)
        return code

    def _loop(self) -> int:
        while True:
            kind, payload = self._events.get()
            if kind == "client-eof":
                return _EXIT_OK  # clean shutdown; ladder runs in run()
            if kind == "client-error":
                return self._fail(f"malformed client frame: {payload}")
            if kind == "child-eof":
                return self._fail(
                    "service child exited unexpectedly "
                    f"(code={payload})"
                )
            if kind == "child-error":
                return self._fail(f"malformed child frame: {payload}")
            if kind == "client-frame":
                assert isinstance(payload, dict)
                problem = self._handle_client_message(payload)
                if problem is not None:
                    return self._fail(problem)
            elif kind == "child-frame":
                assert isinstance(payload, dict)
                if not self._handle_child_message(payload):
                    return self._fail("service child protocol violation")

    # ----------------------------------------------------------- pumps --

    def _start_pump(
        self, body: Callable[[], None], name: str
    ) -> threading.Thread:
        thread = threading.Thread(target=body, name=name, daemon=True)
        thread.start()
        return thread

    def _pump_client(self) -> None:
        reader = FrameReader(self._client_read)
        while True:
            try:
                frame = reader.read_frame()
            except EOFError:
                self._events.put(("client-eof", None))
                return
            except (FramingError, OSError, ValueError) as exc:
                self._events.put(("client-error", str(exc)))
                return
            self._events.put(("client-frame", frame))

    def _pump_child(self) -> None:
        assert self._child is not None and self._child.stdout is not None
        # type ignore: runtime object is a BufferedReader (has read1),
        # subprocess stubs only promise IO[Any].
        reader = FrameReader(self._child.stdout.read1)  # type: ignore[attr-defined]
        while True:
            try:
                frame = reader.read_frame()
            except EOFError:
                self._events.put(("child-eof", self._child_exit_code()))
                return
            except (FramingError, OSError, ValueError) as exc:
                self._events.put(("child-error", str(exc)))
                return
            self._events.put(("child-frame", frame))

    def _child_exit_code(self) -> int | None:
        return self._child.poll() if self._child is not None else None

    # -------------------------------------------------------- messages --

    def _handle_client_message(self, message: dict) -> str | None:
        """Answer one DAP message; returns a failure detail or None.

        M2-a: every request gets the honest not-implemented response;
        stray non-request messages are logged and ignored.
        """
        kind = message.get("type")
        if kind == "request":
            self._send_client(
                {
                    "type": "response",
                    "request_seq": message.get("seq"),
                    "success": False,
                    "command": str(message.get("command", "")),
                    "message": "not implemented until M2-b1",
                }
            )
            return None
        print(
            f"symrepl-dap: ignoring unexpected DAP message type {kind!r}",
            file=sys.stderr,
        )
        return None

    def _handle_child_message(self, message: dict) -> bool:
        """Route one internal-protocol message; False on violation."""
        kind = message.get("type")
        if kind == "hello":
            return message.get("version") == _PROTOCOL_VERSION
        if kind == "response":
            self._resolve_pending(message)
            return True
        if kind == "event":
            event = message.get("event")
            detail = (message.get("params") or {}).get("detail", "")
            print(
                f"symrepl-dap: child event {event!r}: {detail}",
                file=sys.stderr,
            )
            return True
        print(
            f"symrepl-dap: ignoring unexpected child message type {kind!r}",
            file=sys.stderr,
        )
        return True

    def _resolve_pending(self, message: dict) -> None:
        request_id = message.get("id")
        # bool is an int subclass; a bool id was never allocated.
        if not isinstance(request_id, int) or isinstance(request_id, bool):
            return
        with self._pending_lock:
            request = self._pending.pop(request_id, None)
        if request is None:
            return
        request.ok = message.get("ok") is True
        request.result = message.get("result")
        request.error = message.get("error")
        request.done.set()

    # ---------------------------------------------------------- output --

    def _send_client(self, payload: dict) -> None:
        """The only client writer: the run loop (ordering invariant,
        ADR-0003 decision 4 — a response is always on the wire before
        any event it causes)."""
        self._client_seq += 1
        payload["seq"] = self._client_seq
        self._client_write(encode_frame(payload))

    # ------------------------------------------------------- lifecycle --

    def _fail(self, detail: str) -> int:
        print(f"symrepl-dap: {detail}", file=sys.stderr)
        return _EXIT_FAILURE

    def _terminate_child(self) -> None:
        """The shutdown ladder: SIGTERM → grace → SIGKILL → waitpid
        (ADR-0003 decision 8).  A graceful exit is never assumed."""
        child = self._child
        if child is None or child.poll() is not None:
            return
        try:
            child.terminate()  # SIGTERM on POSIX
        except OSError:
            pass
        try:
            child.wait(timeout=_SHUTDOWN_GRACE_SECONDS)
            return
        except subprocess.TimeoutExpired:
            pass
        try:
            child.kill()
        except OSError:
            pass
        try:
            child.wait(timeout=_SHUTDOWN_GRACE_SECONDS)
        except subprocess.TimeoutExpired:
            print(
                "symrepl-dap: service child ignored SIGKILL?!",
                file=sys.stderr,
            )


# -------------------------------------------------------------- wiring --


def _flushing(buffer) -> Callable[[bytes], None]:
    """Wrap a raw buffer into the writer callable DapServer expects
    (write + flush per frame — buffered endpoints must not sit on
    frames)."""

    def write(data: bytes) -> None:
        buffer.write(data)
        buffer.flush()

    return write


def _spawn_service() -> subprocess.Popen:
    """Production child spawner: the real ``symrepl.lldb_service``
    with its own diagnostics log; its stderr is discarded because the
    child owns its hygiene."""
    fd, log_path = tempfile.mkstemp(
        prefix="symrepl-service-", suffix=".log"
    )
    os.close(fd)
    return subprocess.Popen(
        [sys.executable, "-m", "symrepl.lldb_service", "--log", log_path],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="symrepl dap",
        description=(
            "DAP server mode (ADR-0003): one client, one service "
            "child per session."
        ),
    )
    parser.add_argument(
        "--port",
        type=int,
        default=None,
        metavar="N",
        help="serve a single client on 127.0.0.1:N (default: stdio)",
    )
    args = parser.parse_args(argv)

    if args.port is None:
        # read1: a plain read() would block until a full 64 KiB chunk
        # arrives — small DAP frames would sit undelivered.
        return DapServer(
            sys.stdin.buffer.read1,  # type: ignore[attr-defined]  # runtime object is a BufferedReader
            _flushing(sys.stdout.buffer),
            _spawn_service,
        ).run()

    with socket.create_server(("127.0.0.1", args.port)) as server_sock:
        connection, _addr = server_sock.accept()
        with connection:
            rfile = connection.makefile("rb")
            wfile = connection.makefile("wb")
            try:
                return DapServer(
                    rfile.read1, _flushing(wfile), _spawn_service
                ).run()
            finally:
                rfile.close()
                wfile.close()


if __name__ == "__main__":
    raise SystemExit(main())
