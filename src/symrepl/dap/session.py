"""Interactive LLDB session owned by the service child (M2-b1).

Where :mod:`symrepl.replay.driver` runs a *one-shot* replay to exit, this
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

M2-b2 adds the state surface: scopes/variables over a stopped frame
and source breakpoints (create/replace per DAP semantics, hits
reported via ``stopped(reason: "breakpoint")``).  References are
**per-stop generation ids** (ADR-0003 decision 12): every reported
stop and every resume starts a new generation; frame ids embed the
generation, variable references are registered against it, and stale
ids fail with a ``stale_reference`` error instead of mis-resolving.
"""

from __future__ import annotations

import os
import tempfile
import time
from typing import Any

from ..replay import branches as _branches
from ..replay.driver import (
    _MAX_RESUMES,
    _MAX_SITES,
    _collect_sites,
    _ensure_debug_server,
    _ensure_lldb,
)
from ..replay.trace import Hit, TraceReport

#: Owner-loop poll cadence (ADR-0003 decision 7: short polls purely to
#: yield CPU and check shutdown flags).
POLL_SECONDS = 0.1

#: Per-poll cap per stream when forwarding inferior stdio (decision 13:
#: droppable, bounded — excess bytes are dropped with a truncation
#: marker instead of stalling the session).
_OUTPUT_CHUNK = 8192

_ENTRY_STOP_WAIT_SECONDS = 10.0


class SessionError(RuntimeError):
    """A session-level failure with a machine-readable code."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class LldbSession:
    """One debug session: SBDebugger + inferior, driven by the owner
    thread of :mod:`symrepl.dap.service`.

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
        # Per-stop reference generations (ADR-0003 decision 12).
        self._generation = 0
        self._var_refs: dict[int, tuple[int, int]] = {}  # ref → (gen, frame)
        self._ref_counter = 0
        self._own_breakpoint_ids: list[int] = []
        # Branch tracing (M1 semantics, session-side recording; ADR-0003
        # decision 5).
        self._stop_on_branch = False
        self._sites: tuple = ()
        self._site_index: dict = {}
        self._hit_pcs: list[int] = []
        self._hit_vars: list[tuple[tuple[str, str], ...]] = []
        self._trace_enabled = False
        self._resume_count = 0
        self._exit_code: int | None = None
        self._pause_pending = False
        # Inferior output forwarding (decision 13: droppable, bounded).
        self._io_offsets = {"stdout": 0, "stderr": 0}
        self._dropped = {"stdout": 0, "stderr": 0}

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
        stop_on_branch: bool = False,
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
        self._stop_on_branch = stop_on_branch
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
            self._install_branch_sites(target)
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

    def _install_branch_sites(self, target_path: str) -> None:
        """M1 parity: one breakpoint per conditional site in scope
        (ADR-0002 decision 2, replayed session-side)."""
        sites = _collect_sites(
            self._lldb, self._target, self._scope, target_path
        )
        if len(sites) > _MAX_SITES:
            raise SessionError(
                "launch_failed",
                f"scope {self._scope!r} matches {len(sites)} conditional "
                f"sites (>{_MAX_SITES}); narrow it, e.g. scope a function",
            )
        for site in sites:
            self._target.BreakpointCreateByAddress(site.address)
        self._sites = sites
        self._site_index = {site.address: site for site in sites}
        self._trace_enabled = True

    def _record_hit(self, frame, pc: int) -> None:
        """Session-side recording of one traced-site stop (the LLDB
        22.1.8 scripted-callback workaround from M1)."""
        variables = tuple(
            (v.GetName(), v.GetValue() or "")
            for v in frame.GetVariables(True, True, False, True)
        )
        self._hit_pcs.append(pc)
        self._hit_vars.append(variables)

    def _trace_report_text(self, run_ended: bool) -> str | None:
        """The ordered M1 report — ``None`` when tracing is off."""
        if not self._trace_enabled:
            return None
        outcomes = _branches.infer_outcomes(
            self._site_index, self._hit_pcs, run_ended=run_ended
        )
        hits = tuple(
            Hit(
                order=i + 1,
                site=self._site_index[pc],
                variables=variables,
                outcome=outcomes[i],
            )
            for i, (pc, variables) in enumerate(
                zip(self._hit_pcs, self._hit_vars)
            )
        )
        report = TraceReport(scope=self._scope, sites=self._sites, hits=hits)
        return report.render()

    def branch_trace(self) -> dict:
        """The ``replay.branchTrace`` surface: the ordered report so far
        (``{"trace": None}`` when tracing is off)."""
        run_ended = self._phase == "dead" and self._exit_code is not None
        return {"trace": self._trace_report_text(run_ended)}

    def _poll_output(self) -> list[dict]:
        """Forward inferior stdio (decision 13: droppable, bounded — a
        poll cadence slower than the writer drops bytes and inserts a
        truncation marker instead of stalling the session)."""
        events: list[dict] = []
        for stream in ("stdout", "stderr"):
            path = os.path.join(self._io_dir.name, stream)
            offset = self._io_offsets[stream]
            try:
                size = os.path.getsize(path)
            except OSError:
                continue
            if size <= offset:
                continue
            with open(path, "rb") as handle:
                handle.seek(offset)
                chunk = handle.read(_OUTPUT_CHUNK)
            new_offset = offset + len(chunk)
            if size > new_offset:
                self._dropped[stream] += size - new_offset
                new_offset = size
            self._io_offsets[stream] = new_offset
            text = chunk.decode(errors="replace")
            if self._dropped[stream] > 0:
                text += (
                    f"\n[symrepl: {self._dropped[stream]} byte(s) truncated]"
                )
                self._dropped[stream] = 0
            events.append(
                {
                    "event": "output",
                    "params": {"category": stream, "text": text},
                }
            )
        return events

    # ------------------------------------------------------- commands --

    def config_done(self) -> list[dict]:
        """Client finished configuring: report the entry stop
        (``stopOnEntry``) or resume toward program ``main``."""
        self._require_phase(
            "entry_stopped", code="not_launched", action="finish configuration"
        )
        if self._stop_on_entry:
            self._begin_reported_stop()
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

    def step(self, kind: str, thread_id: int) -> list[dict]:
        """One stepping operation (M2-d); the completion arrives as a
        ``stopped(reason: "step")`` event."""
        self._require_phase(
            "stopped", code="not_stopped", action="step"
        )
        thread = self._process.GetThreadByID(int(thread_id))
        if not thread.IsValid():
            raise SessionError("no_thread", f"no thread with id {thread_id}")
        operations = {
            "next": thread.StepOver,
            "stepIn": thread.StepInto,
            "stepOut": thread.StepOut,
        }
        operation = operations.get(kind)
        if operation is None:
            raise SessionError("bad_request", f"unknown step kind: {kind!r}")
        # Spike rule: drain queued events before issuing the operation.
        event = self._lldb.SBEvent()
        while self._listener.GetNextEvent(event):
            pass
        # Decision 12: a step is a resume.
        self._generation += 1
        self._var_refs.clear()
        operation()  # async: the completion arrives as a stop event
        self._phase = "running"
        return []

    def pause(self) -> list[dict]:
        """Async-interrupt the running inferior (the spike-verified
        ``SendAsyncInterrupt`` path, decision 7); the completion arrives
        as a ``stopped(reason: "pause")`` event."""
        self._require_phase(
            "running", code="not_stopped", action="pause"
        )
        self._pause_pending = True
        self._process.SendAsyncInterrupt()
        return []

    def _resume(self) -> None:
        # Spike rule: queued stale events are drained before every
        # resume so history is never mistaken for news.
        event = self._lldb.SBEvent()
        while self._listener.GetNextEvent(event):
            pass
        # Decision 12: any resume invalidates the current reference
        # generation — stale ids must error, never mis-resolve.
        self._generation += 1
        self._var_refs.clear()
        self._resume_count += 1
        if self._resume_count > _MAX_RESUMES:
            # M1's safety valve: a pathological trace must not spin the
            # session forever.
            print(
                f"symrepl-service: exceeded {_MAX_RESUMES} resumes; "
                "aborting a pathological trace",
                flush=True,
            )
            self.terminate()
            return
        self._process.Continue()  # async: returns immediately
        self._phase = "running"

    def _begin_reported_stop(self) -> None:
        """A user-visible stop starts a fresh reference generation
        (ADR-0003 decision 12)."""
        self._generation += 1
        self._var_refs.clear()

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
                    # Frame ids carry the generation (decision 12): a
                    # resume invalidates them along with variable refs.
                    "id": self._generation * 1000 + index,
                    "name": name,
                    "line": line,
                    "column": column,
                    "path": path,
                }
            )
        return {"stack_frames": frames, "total_frames": thread.GetNumFrames()}

    # ------------------------------------------------- state surface --

    def scopes(self, frame_id: int) -> list[dict]:
        """Variable scopes of one frame; requires a reported stop."""
        self._require_phase(
            "stopped", code="not_stopped", action="read scopes"
        )
        generation, frame_index = self._decode_frame_id(frame_id)
        self._frame_at(frame_index)  # validates the frame exists
        ref = self._new_reference(generation)
        self._var_refs[ref] = (generation, frame_index)
        return [{"name": "Locals", "variables_reference": ref}]

    def variables(self, variables_reference: int) -> list[dict]:
        """The variables behind a scope reference; requires a reported
        stop and a reference from the current generation."""
        self._require_phase(
            "stopped", code="not_stopped", action="read variables"
        )
        _, frame_index = self._decode_reference(variables_reference)
        frame = self._frame_at(frame_index)
        variables = []
        for value in frame.GetVariables(True, True, False, True):
            variables.append(
                {
                    "name": value.GetName() or "<unnamed>",
                    "value": value.GetValue() or value.GetSummary() or "",
                    "type": value.GetTypeName() or "",
                    "variables_reference": 0,  # flat surface: no children
                }
            )
        return variables

    def set_breakpoints(self, path: str, lines: list[int]) -> dict:
        """Replace the breakpoints of one source file (DAP semantics:
        setBreakpoints is a full replacement per source).  Creation
        works on a launched target, stopped or running."""
        if self._process is None:
            raise SessionError(
                "not_launched", "no session to place breakpoints in"
            )
        if not isinstance(path, str) or not path:
            raise SessionError("bad_request", "source path must be a string")
        for bp_id in self._own_breakpoint_ids:
            self._target.BreakpointDelete(bp_id)
        self._own_breakpoint_ids = []
        breakpoints = []
        for line in lines:
            if (
                not isinstance(line, int)
                or isinstance(line, bool)
                or line < 1
            ):
                raise SessionError(
                    "bad_request", f"invalid breakpoint line: {line!r}"
                )
            bp = self._target.BreakpointCreateByLocation(path, line)
            if not bp.IsValid():
                breakpoints.append({"line": line, "verified": False})
                continue
            self._own_breakpoint_ids.append(int(bp.GetID()))
            resolved = line
            if bp.GetNumLocations() > 0:
                # SBBreakpointLocation has no GetLineInfo; the resolved
                # line comes through the location's address.
                address = bp.GetLocationAtIndex(0).GetAddress()
                if address.IsValid():
                    entry = address.GetLineEntry()
                    if entry.IsValid():
                        resolved = int(entry.GetLine())
            breakpoints.append({"line": resolved, "verified": True})
        return {"breakpoints": breakpoints}

    def _decode_frame_id(self, frame_id: int) -> tuple[int, int]:
        if (
            not isinstance(frame_id, int)
            or isinstance(frame_id, bool)
            or frame_id < 0
        ):
            raise SessionError(
                "bad_request", "frameId must be a non-negative integer"
            )
        generation, frame_index = divmod(frame_id, 1000)
        if generation != self._generation:
            raise SessionError(
                "stale_reference",
                f"frame id from generation {generation}, "
                f"current generation is {self._generation}",
            )
        return generation, frame_index

    def _decode_reference(self, ref: int) -> tuple[int, int]:
        if not isinstance(ref, int) or isinstance(ref, bool):
            raise SessionError(
                "bad_request", "variablesReference must be an integer"
            )
        entry = self._var_refs.get(ref)
        if entry is None or entry[0] != self._generation:
            raise SessionError(
                "stale_reference",
                f"variables reference {ref} is not from the current stop",
            )
        return entry

    def _new_reference(self, generation: int) -> int:
        self._ref_counter += 1
        return generation * 100000 + self._ref_counter

    def _frame_at(self, frame_index: int):
        process = self._process
        thread = process.GetThreadAtIndex(0)
        if (
            thread is None
            or not thread.IsValid()
            or frame_index >= thread.GetNumFrames()
        ):
            raise SessionError("no_frame", f"no frame {frame_index}")
        return thread.GetFrameAtIndex(frame_index)

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
            self._exit_code = code
            self._phase = "dead"
            events = self._poll_output()  # final stdio before the news
            events.append(
                {"event": "exited", "params": {"exit_code": code}}
            )
            events.append(self._terminated_event())
            trace = self._trace_report_text(run_ended=True)
            if trace is not None:
                events.append(
                    {"event": "trace_report", "params": {"text": trace}}
                )
            return events

        if self._phase in ("entry", "entry_stopped"):
            # The launch entry stop (and any stragglers before
            # configurationDone) is consumed silently.
            if state == stopped:
                self._phase = "entry_stopped"
            return []

        if self._phase == "terminating":
            if state in self._terminal_states():
                self._phase = "dead"
                return self._dead_tail(run_ended=False)
            return []

        if self._phase == "running":
            if state == stopped:
                return self._on_stop()
            if state in self._terminal_states():
                self._phase = "dead"
                return self._dead_tail(run_ended=False)
            return []

        # stopped / dead stragglers: nothing to report.
        return []

    def _dead_tail(self, run_ended: bool) -> list[dict]:
        """Terminal events of a non-exit death + the trace report."""
        events = self._poll_output()
        events.append(self._terminated_event())
        trace = self._trace_report_text(run_ended=run_ended)
        if trace is not None:
            events.append(
                {"event": "trace_report", "params": {"text": trace}}
            )
        return events

    def _on_stop(self) -> list[dict]:
        """A stop while running: a traced site (record-and-continue in
        M1 parity, reported under ``stopOnBranch``) or a real stop."""
        thread = self._stopped_thread()
        frame = (
            thread.GetFrameAtIndex(0)
            if thread is not None and thread.GetNumFrames() > 0
            else None
        )
        pc = int(frame.GetPC()) if frame is not None else 0
        raw = (
            int(thread.GetStopReason())
            if thread is not None and thread.IsValid()
            else 0
        )
        if self._pause_pending:
            # A pause beats a traced site's auto-continue: the user
            # asked for the inferior to stop, so it stops.
            self._pause_pending = False
            self._begin_reported_stop()
            self._phase = "stopped"
            return [self._stopped_event("pause")]
        if (
            raw == int(self._lldb.eStopReasonBreakpoint)
            and pc in self._site_index
        ):
            self._record_hit(frame, pc)
            if self._stop_on_branch:
                site = self._site_index[pc]
                self._begin_reported_stop()
                self._phase = "stopped"
                event = self._stopped_event("breakpoint")
                event["params"]["description"] = (
                    f"{site.function} {site.mnemonic}"
                )
                return [event]
            # M1 parity: record and auto-continue.
            self._resume()
            return []
        events = self._poll_output()
        self._phase = "stopped"
        events.append(self._crash_or_stop_event())
        return events

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
        self._begin_reported_stop()
        thread = self._stopped_thread()
        reason = "exception"
        description = ""
        breakpoint_ids: list[int] = []
        if thread is not None and thread.IsValid():
            raw = int(thread.GetStopReason())
            exceptions = (
                self._lldb.eStopReasonSignal,
                self._lldb.eStopReasonException,
            )
            if raw == int(self._lldb.eStopReasonBreakpoint):
                reason = "breakpoint"
                breakpoint_ids = [
                    int(thread.GetStopReasonDataAtIndex(i))
                    for i in range(thread.GetStopReasonDataCount())
                ]
            elif raw in (
                int(self._lldb.eStopReasonTrace),
                int(self._lldb.eStopReasonPlanComplete),
            ):
                # eStopReasonTrace is instruction-level stepping;
                # PlanComplete is what StepOver/StepInto/StepOut report
                # on completion (M2-d).
                reason = "step"
            elif raw not in exceptions:
                reason = "exception"
            description = thread.GetStopDescription(256) or ""
        params = {
            "reason": reason,
            "thread_id": int(thread.GetThreadID()) if thread is not None else 0,
            "all_threads_stopped": True,
        }
        if breakpoint_ids:
            params["breakpoint_ids"] = breakpoint_ids
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
