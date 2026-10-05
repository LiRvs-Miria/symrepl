"""Interactive LLDB session owned by the service child (M2-b1).

Where :mod:`symrepl.lldb_driver` runs a *one-shot* replay to exit, this
module keeps SBDebugger and the inferior alive across DAP requests:
launch, stop at entry, then an owner-driven loop of continue /
threads / stack traces with state events flowing back to the parent
(ADR-0003 decisions 1, 7, 9, 10).

M1 semantics carry over unchanged (ADR-0003 decision 2 + 11):

- the harness contract: minimal environment, exactly ``KTEST_FILE``,
  entry stop so the session owns the whole run, inferior stdio
  redirected to files (output forwarding over DAP is M2-c);
- the parent validates the ktest; this session points ``KTEST_FILE``
  at the user's **original path** — no temp-file re-serialization.

Concurrency (ADR-0003 decision 7, spike-verified on LLDB 22.1.8): the
owner thread is the only caller of every method here; LLDB events are
drained with non-blocking ``GetNextEvent`` between commands, and the
pre-resume drain rule holds — queued stale events (the entry stop)
are consumed before every resume so history is never mistaken for
news.  ``SBDebugger.Initialize`` happens exactly once per process.

States: ``entry`` → ``entry_stopped`` (silent, awaiting
configurationDone) → ``running`` ↔ ``stopped`` → ``dead``; a launch
argument of ``stopOnEntry`` turns the post-configurationDone stop into
a reported ``stopped(reason: "entry")`` instead of a resume.
"""

from __future__ import annotations

import os
import tempfile
import time
from typing import Any

from .lldb_driver import _ensure_debug_server, _ensure_lldb

#: Owner-loop poll cadence (ADR-0003 decision 7: short polls purely to
#: yield CPU and check shutdown flags).
POLL_SECONDS = 0.1

_ENTRY_STOP_WAIT_SECONDS = 10.0


class SessionError(RuntimeError):
    """A session-level failure with a machine-readable code."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class LldbSession:
    """One debug session: SBDebugger + inferior, driven by the owner
    thread of :mod:`symrepl.lldb_service`.

    Methods return protocol-neutral event dicts (``{"event": ...,
    "params": {...}}``) where a reply is synchronous; asynchronous
    process-state events are collected by :meth:`poll_events` and must
    be forwarded by the caller in order.
    """

    def __init__(self) -> None:
        # The lldb types live in LLDB's own module (not pip-installable),
        # so the handles are Any: _ensure_lldb() binds them at launch.
        self._lldb: Any = None
        self._debugger: Any = None
        self._target: Any = None
        self._process: Any = None
        self._listener: Any = None
        self._phase = "entry"  # entry → entry_stopped → running ⇄ stopped → dead
        self._stop_on_entry = False
        self._scope = "module"
        self._deadline: float | None = None
        self._terminal_emitted = False
        self._io_dir = tempfile.TemporaryDirectory(prefix="symrepl-session-")
        self._closed = False

    # ------------------------------------------------------------ state --

    @property
    def terminal(self) -> bool:
        """True once the session has emitted its terminal events."""
        return self._phase == "dead" and self._terminal_emitted

    def _require_phase(self, *phases: str, code: str, action: str) -> None:
        if self._phase not in phases:
            raise SessionError(
                code,
                f"cannot {action} in phase {self._phase!r} "
                f"(expected one of {phases})",
            )

    # ----------------------------------------------------------- launch --

    def launch(
        self,
        ktest: str,
        target: str,
        stop_on_entry: bool = False,
        scope: str = "module",
        timeout: float | None = None,
    ) -> dict:
        """Launch the target with ``KTEST_FILE`` injected; the inferior
        stops at the entry point (always — later phases depend on it).

        Returns ``{"pid": <system process id>}``.  Raises
        :class:`SessionError` on any launch failure.
        """
        self._require_phase("entry", code="already_launched", action="launch")
        self._stop_on_entry = stop_on_entry
        self._scope = scope
        self._lldb = _ensure_lldb()
        _ensure_debug_server()
        self._lldb.SBDebugger.Initialize()
        self._debugger = self._lldb.SBDebugger.Create()
        try:
            self._debugger.SetAsync(True)
            self._target = self._debugger.CreateTarget(target)
            if not self._target.IsValid():
                raise SessionError(
                    "launch_failed",
                    f"lldb could not load target binary: {target}",
                )
            self._listener = self._lldb.SBListener("symrepl-service")
            error = self._lldb.SBError()
            stdout_path = os.path.join(self._io_dir.name, "stdout")
            stderr_path = os.path.join(self._io_dir.name, "stderr")
            # The M1 launch shape, verbatim: native stdio redirection,
            # a minimal environment (only KTEST_FILE — deterministic,
            # and a stray parent KLEE_RANDOM can never switch the
            # runtime into random-feed mode), and a stop at entry.
            self._process = self._target.Launch(
                self._listener,
                [target],
                [f"KTEST_FILE={ktest}"],
                None,
                stdout_path,
                stderr_path,
                None,
                0,
                True,
                error,
            )
            if not self._process.IsValid() or error.GetCString():
                detail = error.GetCString() or "unknown error"
                raise SessionError(
                    "launch_failed", f"lldb failed to launch {target}: {detail}"
                )
            self._wait_entry_stop()
        except SessionError:
            self._teardown_debugger()
            raise

        if timeout is not None:
            self._deadline = time.monotonic() + float(timeout)
        return {"pid": int(self._process.GetProcessID())}

    def _wait_entry_stop(self) -> None:
        """Block until the inferior is stopped at its entry point.

        The process object is the authority (M1 evidence); events for
        this stop stay queued and are silently consumed by the poll
        loop while in the entry phases.
        """
        stopped = int(self._lldb.eStateStopped)
        deadline = time.monotonic() + _ENTRY_STOP_WAIT_SECONDS
        while time.monotonic() < deadline:
            if int(self._process.GetState()) == stopped:
                self._phase = "entry_stopped"
                return
            time.sleep(0.02)
        raise SessionError(
            "launch_failed",
            f"inferior did not reach its entry stop within "
            f"{_ENTRY_STOP_WAIT_SECONDS}s",
        )

    # ------------------------------------------------------- commands --

    def config_done(self) -> list[dict]:
        """Client finished configuring: report the entry stop
        (``stopOnEntry``) or resume toward program ``main``."""
        self._require_phase(
            "entry_stopped", code="not_launched", action="finish configuration"
        )
        if self._stop_on_entry:
            self._phase = "stopped"
            return [self._stopped_event("entry")]
        self._resume()
        return []

    def continue_(self) -> list[dict]:
        """Resume a reported stop; completion arrives as events."""
        self._require_phase(
            "stopped", code="not_stopped", action="continue"
        )
        self._resume()
        return []

    def _resume(self) -> None:
        # Spike rule: queued stale events are drained before every
        # resume so history is never mistaken for news.
        event = self._lldb.SBEvent()
        while self._listener.GetNextEvent(event):
            pass
        self._process.Continue()  # async: returns immediately
        self._phase = "running"

    def threads(self) -> list[dict]:
        """The (single) thread of the inferior, by harness contract."""
        process = self._process
        if process is None or not process.IsValid():
            raise SessionError("no_process", "inferior is not running")
        count = process.GetNumThreads()
        if count == 0:
            raise SessionError("no_process", "inferior has no threads")
        thread = process.GetThreadAtIndex(0)
        name = thread.GetName() or f"thread-{int(thread.GetThreadID())}"
        return [{"id": int(thread.GetThreadID()), "name": name}]

    def stack_trace(self, thread_id: int, levels: int = 20) -> dict:
        """Frames of one thread; requires a reported stop."""
        self._require_phase(
            "stopped",
            "entry_stopped",
            code="not_stopped",
            action="read the stack",
        )
        process = self._process
        thread = process.GetThreadByID(int(thread_id))
        if not thread.IsValid():
            raise SessionError(
                "no_thread", f"no thread with id {thread_id}"
            )
        frames = []
        for index in range(min(max(int(levels), 1), thread.GetNumFrames())):
            frame = thread.GetFrameAtIndex(index)
            name = frame.GetFunctionName() or f"0x{int(frame.GetPC()):x}"
            entry = frame.GetLineEntry()
            line = column = 0
            path = ""
            if entry.IsValid():
                line = int(entry.GetLine())
                column = int(entry.GetColumn())
                filespec = entry.GetFileSpec()
                if filespec.IsValid():
                    path = filespec.fullpath or filespec.GetFilename() or ""
            frames.append(
                {
                    "id": index,
                    "name": name,
                    "line": line,
                    "column": column,
                    "path": path,
                }
            )
        return {"stack_frames": frames, "total_frames": thread.GetNumFrames()}

    def terminate(self) -> list[dict]:
        """Kill the inferior now; the ``terminated`` event arrives via
        :meth:`poll_events` (or immediately when already dead)."""
        process = self._process
        if process is None or self._phase == "dead":
            self._phase = "dead"
            return [self._terminated_event()]
        try:
            process.Kill()
        except (OSError, RuntimeError):
            pass  # already dead; the poll loop confirms
        self._phase = "terminating"
        return []

    # ----------------------------------------------------------- polls --

    def poll_events(self) -> list[dict]:
        """Drain queued LLDB events and translate them per the current
        phase.  Non-blocking; the listener queues while we are busy."""
        events: list[dict] = []
        if self._phase == "dead" or self._process is None:
            return events

        if (
            self._phase == "running"
            and self._deadline is not None
            and time.monotonic() > self._deadline
        ):
            self._deadline = None
            self.terminate()

        event = self._lldb.SBEvent()
        while self._listener.GetNextEvent(event):
            events.extend(self._translate(event))
        return events

    def _translate(self, event) -> list[dict]:
        if not self._lldb.SBProcess.EventIsProcessEvent(event):
            return []
        state = int(self._lldb.SBProcess.GetStateFromEvent(event))
        stopped = int(self._lldb.eStateStopped)
        exited = int(self._lldb.eStateExited)

        if state == exited:
            code = int(self._process.GetExitStatus() or 0)
            self._phase = "dead"
            return [
                {"event": "exited", "params": {"exit_code": code}},
                self._terminated_event(),
            ]

        if self._phase in ("entry", "entry_stopped"):
            # The launch entry stop (and any stragglers before
            # configurationDone) is consumed silently.
            if state == stopped:
                self._phase = "entry_stopped"
            return []

        if self._phase == "terminating":
            if state in self._terminal_states():
                self._phase = "dead"
                return [self._terminated_event()]
            return []

        if self._phase == "running":
            if state == stopped:
                self._phase = "stopped"
                return [self._crash_or_stop_event()]
            if state in self._terminal_states():
                self._phase = "dead"
                return [self._terminated_event()]
            return []

        # stopped / dead stragglers: nothing to report.
        return []

    def _terminal_states(self) -> frozenset[int]:
        lldb = self._lldb
        return frozenset(
            {
                int(lldb.eStateDetached),
                int(lldb.eStateInvalid),
            }
        )

    def _stopped_thread(self):
        thread = self._process.GetSelectedThread()
        if thread is None or not thread.IsValid() or thread.GetNumFrames() == 0:
            if self._process.GetNumThreads() > 0:
                thread = self._process.GetThreadAtIndex(0)
        return thread

    def _crash_or_stop_event(self) -> dict:
        thread = self._stopped_thread()
        reason = "exception"
        description = ""
        if thread is not None and thread.IsValid():
            raw = int(thread.GetStopReason())
            exceptions = (
                self._lldb.eStopReasonSignal,
                self._lldb.eStopReasonException,
            )
            if raw == int(self._lldb.eStopReasonBreakpoint):
                reason = "breakpoint"  # M2-b2/M2-c surfaces these
            elif raw == int(self._lldb.eStopReasonTrace):
                reason = "step"  # M2-d surfaces these
            elif raw not in exceptions:
                reason = "exception"
            description = thread.GetStopDescription(256) or ""
        params = {
            "reason": reason,
            "thread_id": int(thread.GetThreadID()) if thread is not None else 0,
            "all_threads_stopped": True,
        }
        if description:
            params["description"] = description
        return {"event": "stopped", "params": params}

    def _stopped_event(self, reason: str) -> dict:
        thread = self._stopped_thread()
        return {
            "event": "stopped",
            "params": {
                "reason": reason,
                "thread_id": (
                    int(thread.GetThreadID()) if thread is not None else 0
                ),
                "all_threads_stopped": True,
            },
        }

    def _terminated_event(self) -> dict:
        self._terminal_emitted = True
        return {"event": "terminated", "params": {}}

    # ---------------------------------------------------------- close --

    def close(self) -> None:
        """Release the inferior (if any) and tear the debugger down.
        Idempotent; called on every session end path."""
        if self._closed:
            return
        self._closed = True
        self._teardown_debugger()
        try:
            self._io_dir.cleanup()
        except OSError:
            pass

    def _teardown_debugger(self) -> None:
        if self._debugger is not None and self._lldb is not None:
            try:
                self._lldb.SBDebugger.Destroy(self._debugger)
                self._lldb.SBDebugger.Terminate()
            except (OSError, RuntimeError, ValueError):
                pass
            self._debugger = None
