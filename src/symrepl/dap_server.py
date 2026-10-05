"""Parent-side DAP server: one client, one service child.

Topology (ADR-0003 decisions 1, 3): this process *is* the DAP server —
it speaks DAP to the client over stdio (default) or a single TCP
connection (``--port``), and drives one long-lived
``symrepl.lldb_service`` child per session over the internal framed
protocol.

M2-b1 implements the lifecycle surface on that transport: initialize
(capabilities frozen per decision 4), launch (response withheld until
configurationDone), threads/stackTrace/continue, and
terminate/disconnect (always kill, decision 8).  Anything else still
answers ``success: false`` "not implemented until M2-c"; DAP event
semantics for child death (``terminated`` translation) are live as of
M2-b1.

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
from .ktest import KTestError, load as _load_ktest

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


class _ChildRequestError(RuntimeError):
    """The service child answered an internal request with an error
    envelope; ``code`` stays machine-readable for the caller."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


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
        # DAP lifecycle (ADR-0003 decision 4): the in-flight launch's
        # request seq — its response stays withheld until
        # configurationDone — and the session wind-down flag.
        self._pending_launch: int | None = None
        self._closing = False
        self._handlers = {
            "initialize": self._dap_initialize,
            "launch": self._dap_launch,
            "configurationDone": self._dap_configuration_done,
            "threads": self._dap_threads,
            "stackTrace": self._dap_stack_trace,
            "scopes": self._dap_scopes,
            "variables": self._dap_variables,
            "setBreakpoints": self._dap_set_breakpoints,
            "continue": self._dap_continue,
            "terminate": self._dap_terminate,
            "disconnect": self._dap_disconnect,
        }

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

    def _child_request(
        self,
        cmd: str,
        params: dict | None = None,
        timeout: float = _PING_TIMEOUT_SECONDS,
    ) -> dict:
        """Send one internal request; return the child's result payload.

        Usable from any thread once :meth:`run` has spawned the child;
        responses are resolved by the child-reader thread.  Raises
        :class:`TimeoutError` when the child does not answer and
        :class:`_ChildRequestError` when it answers with an error.
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
                    "cmd": cmd,
                    "params": params or {},
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
                f"service child request {cmd!r} timed out after {timeout}s"
            )
        if not request.ok:
            error = request.error or {}
            raise _ChildRequestError(
                str(error.get("code", "unknown")),
                str(error.get("message", "")),
            )
        return request.result or {}

    def ping_child(self, timeout: float = _PING_TIMEOUT_SECONDS) -> dict:
        """Internal ``ping`` round trip (handshake sanity check)."""
        return self._child_request("ping", {}, timeout)

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
                if self._closing:
                    return _EXIT_OK  # disconnect/terminate wind-down
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
            if isinstance(frame, dict) and frame.get("type") == "response":
                # Resolve in the pump thread: internal requests are sent
                # from the run loop itself (launch/threads/...), so the
                # loop cannot be its own resolver — that deadlock is why
                # responses never route through the event queue.
                self._resolve_pending(frame)
            else:
                self._events.put(("child-frame", frame))

    def _child_exit_code(self) -> int | None:
        return self._child.poll() if self._child is not None else None

    # -------------------------------------------------------- messages --

    def _handle_client_message(self, message: dict) -> str | None:
        """Answer one DAP message; returns a failure detail or None.

        M2-b1 implements the lifecycle surface (initialize, launch,
        configurationDone, threads, stackTrace, continue, terminate,
        disconnect); everything else answers not-implemented honestly.
        Stray non-request messages are logged and ignored.
        """
        if message.get("type") != "request":
            print(
                f"symrepl-dap: ignoring unexpected DAP message type "
                f"{message.get('type')!r}",
                file=sys.stderr,
            )
            return None
        command = str(message.get("command", ""))
        handler = self._handlers.get(command)
        if handler is None:
            self._send_response(
                message.get("seq"),
                command,
                success=False,
                message="not implemented until M2-c",
            )
            return None
        return handler(message.get("seq"), message.get("arguments") or {})

    # --------------------------------------------------- DAP lifecycle --

    def _dap_initialize(self, seq, _arguments: dict) -> str | None:
        """Capabilities are frozen in ADR-0003 decision 4."""
        self._send_response(
            seq,
            "initialize",
            success=True,
            body={
                "supportsConfigurationDoneRequest": True,
                "supportsTerminateRequest": True,
            },
        )
        return None

    def _dap_launch(self, seq, arguments: dict) -> str | None:
        """Validate parent-side (decision 11), forward to the child,
        then fire ``initialized`` — the launch response stays withheld
        until ``configurationDone`` (decision 4)."""
        if self._pending_launch is not None:
            self._send_response(
                seq,
                "launch",
                success=False,
                message="launch is already in flight",
            )
            return None
        try:
            params = self._validate_launch(arguments)
        except ValueError as exc:
            self._send_response(seq, "launch", success=False, message=str(exc))
            return None
        try:
            result = self._child_request("launch", params, timeout=60.0)
        except (TimeoutError, RuntimeError) as exc:
            self._send_response(
                seq,
                "launch",
                success=False,
                message=f"service child launch failed: {exc}",
            )
            return None
        self._send_event(
            "process",
            {
                "systemProcessId": result.get("pid"),
                "isLocalProcess": True,
                "startMethod": "launch",
                "name": params["target"],
            },
        )
        self._send_event("initialized", {})
        self._pending_launch = seq if isinstance(seq, int) else 0
        return None

    def _validate_launch(self, arguments: dict) -> dict:
        """Freeze-check the launch schema (ADR-0003 decision 10) and
        validate the ktest parent-side (decision 11: the child later
        points ``KTEST_FILE`` at the user's original path)."""
        if not isinstance(arguments, dict):
            raise ValueError("launch arguments must be an object")
        ktest = arguments.get("ktest")
        target = arguments.get("target")
        if not isinstance(ktest, str) or not ktest:
            raise ValueError("launch requires a 'ktest' path string")
        if not isinstance(target, str) or not target:
            raise ValueError("launch requires a 'target' path string")
        try:
            _load_ktest(ktest)
        except KTestError as exc:
            raise ValueError(f"invalid .ktest ({ktest}): {exc}") from exc
        except OSError as exc:
            raise ValueError(f"unreadable .ktest ({ktest}): {exc}") from exc
        if not os.path.exists(target):
            raise ValueError(f"target binary not found: {target}")
        params: dict = {"ktest": ktest, "target": target}
        # stopOnBranch is accepted (frozen schema) and surfaces in M2-c.
        for name, internal in (
            ("stopOnBranch", "stop_on_branch"),
            ("stopOnEntry", "stop_on_entry"),
        ):
            value = arguments.get(name, False)
            if not isinstance(value, bool):
                raise ValueError(f"{name} must be a boolean")
            params[internal] = value
        scope = arguments.get("scope", "module")
        if not isinstance(scope, str) or not scope:
            raise ValueError("scope must be a non-empty string")
        params["scope"] = scope
        timeout = arguments.get("timeout")
        if timeout is not None:
            if (
                not isinstance(timeout, (int, float))
                or isinstance(timeout, bool)
                or timeout <= 0
            ):
                raise ValueError("timeout must be a positive number")
            params["timeout"] = timeout
        return params

    def _dap_configuration_done(self, seq, _arguments: dict) -> str | None:
        """Release the withheld launch response, then confirm."""
        if self._pending_launch is None:
            self._send_response(
                seq,
                "configurationDone",
                success=False,
                message="no launch in flight",
            )
            return None
        launch_seq = self._pending_launch
        self._pending_launch = None
        try:
            self._child_request("config_done", {}, timeout=30.0)
        except (TimeoutError, RuntimeError) as exc:
            detail = f"service child failed on configurationDone: {exc}"
            self._send_response(
                launch_seq, "launch", success=False, message=detail
            )
            self._send_response(
                seq, "configurationDone", success=False, message=detail
            )
            return None
        self._send_response(launch_seq, "launch", success=True, body={})
        self._send_response(seq, "configurationDone", success=True, body={})
        return None

    def _dap_threads(self, seq, _arguments: dict) -> str | None:
        try:
            result = self._child_request("threads", {}, timeout=10.0)
        except (TimeoutError, RuntimeError) as exc:
            self._send_response(
                seq, "threads", success=False, message=f"service child: {exc}"
            )
            return None
        self._send_response(
            seq,
            "threads",
            success=True,
            body={"threads": result.get("threads", [])},
        )
        return None

    def _dap_stack_trace(self, seq, arguments: dict) -> str | None:
        params = {
            "thread_id": arguments.get("threadId", 0),
            "levels": arguments.get("levels", 20),
        }
        try:
            result = self._child_request("stack_trace", params, timeout=10.0)
        except (TimeoutError, RuntimeError) as exc:
            self._send_response(
                seq,
                "stackTrace",
                success=False,
                message=f"service child: {exc}",
            )
            return None
        self._send_response(
            seq,
            "stackTrace",
            success=True,
            body={
                "stackFrames": result.get("stack_frames", []),
                "totalFrames": result.get("total_frames", 0),
            },
        )
        return None

    def _dap_scopes(self, seq, arguments: dict) -> str | None:
        try:
            result = self._child_request(
                "scopes",
                {"frame_id": arguments.get("frameId", 0)},
                timeout=10.0,
            )
        except (TimeoutError, RuntimeError) as exc:
            self._send_response(
                seq, "scopes", success=False, message=f"service child: {exc}"
            )
            return None
        self._send_response(
            seq,
            "scopes",
            success=True,
            body={
                "scopes": [
                    {
                        "name": s["name"],
                        "variablesReference": s["variables_reference"],
                        "expensive": False,
                    }
                    for s in result.get("scopes", [])
                ],
            },
        )
        return None

    def _dap_variables(self, seq, arguments: dict) -> str | None:
        try:
            result = self._child_request(
                "variables",
                {
                    "variables_reference": arguments.get(
                        "variablesReference", 0
                    )
                },
                timeout=10.0,
            )
        except (TimeoutError, RuntimeError) as exc:
            self._send_response(
                seq,
                "variables",
                success=False,
                message=f"service child: {exc}",
            )
            return None
        self._send_response(
            seq,
            "variables",
            success=True,
            body={
                "variables": [
                    {
                        "name": v["name"],
                        "value": v["value"],
                        "type": v.get("type", ""),
                        "variablesReference": v.get("variables_reference", 0),
                    }
                    for v in result.get("variables", [])
                ],
            },
        )
        return None

    def _dap_set_breakpoints(self, seq, arguments: dict) -> str | None:
        source = arguments.get("source") or {}
        params = {
            "path": source.get("path"),
            "lines": [
                bp.get("line")
                for bp in arguments.get("breakpoints", [])
                if isinstance(bp, dict)
            ],
        }
        try:
            result = self._child_request(
                "set_breakpoints", params, timeout=10.0
            )
        except (TimeoutError, RuntimeError) as exc:
            self._send_response(
                seq,
                "setBreakpoints",
                success=False,
                message=f"service child: {exc}",
            )
            return None
        self._send_response(
            seq,
            "setBreakpoints",
            success=True,
            body={
                "breakpoints": [
                    {
                        "verified": bp.get("verified", False),
                        "line": bp.get("line"),
                    }
                    for bp in result.get("breakpoints", [])
                ],
            },
        )
        return None

    def _dap_continue(self, seq, _arguments: dict) -> str | None:
        try:
            self._child_request("continue", {}, timeout=10.0)
        except (TimeoutError, RuntimeError) as exc:
            self._send_response(
                seq, "continue", success=False, message=f"service child: {exc}"
            )
            return None
        self._send_response(
            seq, "continue", success=True, body={"allThreadsContinued": True}
        )
        return None

    def _dap_terminate(self, seq, _arguments: dict) -> str | None:
        """Decision 8: terminate always kills; the ``terminated`` event
        arrives from the child right after the response."""
        try:
            self._child_request("terminate", {}, timeout=15.0)
        except _ChildRequestError as exc:
            if exc.code != "not_launched":
                self._send_response(
                    seq,
                    "terminate",
                    success=False,
                    message=f"service child: {exc}",
                )
                return None
            # Session already dead — terminate stays idempotent-success.
        except (TimeoutError, RuntimeError) as exc:
            self._send_response(
                seq,
                "terminate",
                success=False,
                message=f"service child: {exc}",
            )
            return None
        self._send_response(seq, "terminate", success=True, body={})
        return None

    def _dap_disconnect(self, seq, _arguments: dict) -> str | None:
        """Decision 8: disconnect always kills the inferior and the
        child; the ``terminated`` event goes out before the session
        ends."""
        try:
            self._child_request("terminate", {}, timeout=15.0)
        except _ChildRequestError as exc:
            if exc.code != "not_launched":
                print(
                    f"symrepl-dap: disconnect terminate failed: {exc}",
                    file=sys.stderr,
                )
        except (TimeoutError, RuntimeError) as exc:
            print(
                f"symrepl-dap: disconnect terminate failed: {exc}",
                file=sys.stderr,
            )
        self._send_response(seq, "disconnect", success=True, body={})
        self._closing = True
        try:
            self._child_request("shutdown", {}, timeout=5.0)
        except (TimeoutError, RuntimeError, _ChildRequestError):
            pass  # the ladder in run() finishes the job
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
            return self._translate_child_event(message)
        print(
            f"symrepl-dap: ignoring unexpected child message type {kind!r}",
            file=sys.stderr,
        )
        return True

    def _translate_child_event(self, message: dict) -> bool:
        """Map internal session events onto the DAP event vocabulary
        (ADR-0003 decision 4): stopped → stopped; exited → exited +
        terminated (DAP convention); terminated → terminated."""
        event = message.get("event")
        params = message.get("params") or {}
        if event == "stopped":
            body: dict = {
                "reason": params.get("reason", "exception"),
                "threadId": params.get("thread_id", 0),
                "allThreadsStopped": bool(
                    params.get("all_threads_stopped", True)
                ),
            }
            if params.get("description"):
                body["description"] = params["description"]
            if params.get("breakpoint_ids"):
                body["hitBreakpointIds"] = params["breakpoint_ids"]
            self._send_event("stopped", body)
            return True
        if event == "exited":
            self._send_event(
                "exited", {"exitCode": params.get("exit_code", 0)}
            )
            # DAP convention: terminated follows exited.
            self._send_event("terminated", {})
            return True
        if event == "terminated":
            self._send_event("terminated", {})
            return True
        print(
            f"symrepl-dap: ignoring child event {event!r}: {params}",
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
        any event it causes).

        A client that disconnects mid-forward is a normal end-of-session
        race, not a failure: the write is dropped and the closing flag
        routes the wind-down (the client-eof from the pump ends the
        loop; the shutdown ladder in run() reaps the child).
        """
        try:
            self._client_seq += 1
            payload["seq"] = self._client_seq
            self._client_write(encode_frame(payload))
        except (BrokenPipeError, OSError) as exc:
            print(
                f"symrepl-dap: client transport broken: {exc}",
                file=sys.stderr,
            )
            self._closing = True

    def _send_response(
        self,
        request_seq,
        command: str,
        success: bool,
        body: dict | None = None,
        message: str | None = None,
    ) -> None:
        payload: dict = {
            "type": "response",
            "request_seq": request_seq,
            "success": success,
            "command": command,
        }
        if body is not None:
            payload["body"] = body
        if message is not None:
            payload["message"] = message
        self._send_client(payload)

    def _send_event(self, event: str, body: dict | None = None) -> None:
        payload: dict = {"type": "event", "event": event}
        if body is not None:
            payload["body"] = body
        self._send_client(payload)

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
