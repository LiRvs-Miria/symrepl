#!/usr/bin/env python3
"""M2-a spike: validate ``SBProcess.SendAsyncInterrupt()`` under this
machine's LLDB, in the service-form shape M2 will use (ADR-0003
decision 7): one ``SBDebugger.Initialize()`` per process, async mode,
an inferior resumed with ``Continue()``, then interrupted and resumed
again.

The roadmap gates M2-a on this spike: if the interrupt does not produce
a real stop (or the process cannot be resumed afterwards), ``pause``
degrades to a documented limitation.

API realities this spike pins down for M2:

- The Python ``SBListener.WaitForEvent`` binding only accepts a
  ``uint32_t`` number of *seconds*, so a 100–200 ms ``WaitForEvent``
  poll is not expressible; the short poll is ``GetNextEvent`` (fully
  non-blocking) plus a short ``time.sleep``.
- ``lldb-server`` is not found by this LLDB build on its own;
  ``_ensure_debug_server()`` (the M1 helper) must run before launch.
- Stale events (the entry stop) are drained before each resume, so any
  process-stop event observed afterwards is the interrupt's.

One-shot diagnostic script — run from the repository root, not part of
the test suite:

    python3 scripts/spike_send_async_interrupt.py

Exit codes: ``0`` supported; ``1`` unsupported / abnormal behavior
(observations printed); ``2`` environment unavailable (no clang, or the
``lldb`` module cannot be bootstrapped).
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_SRC = _ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from symrepl.lldb_driver import (  # noqa: E402
    _ensure_debug_server,
    _ensure_lldb,
    _main_module,
)

_POLL_SECONDS = 0.05
_SETTLE_SECONDS = 0.5
_RESUME_SETTLE_SECONDS = 0.3
_WAIT_DEADLINE_SECONDS = 10.0

_C_SOURCE = """\
int main(void) {
    volatile unsigned x = 0;
    for (;;) {
        x++;
    }
    return 0;
}
"""


def _lldb_version() -> str:
    try:
        result = subprocess.run(
            ["lldb", "--version"], capture_output=True, text=True, timeout=10
        )
        return (result.stdout or result.stderr).strip() or "unknown"
    except (OSError, subprocess.TimeoutExpired):
        return "unknown"


def _drain_events(listener, lldb, max_events: int = 64) -> int:
    """Consume queued events (e.g. the entry stop) so the next stop
    event seen afterwards belongs to the interrupt, not history."""
    event = lldb.SBEvent()
    drained = 0
    while drained < max_events and listener.GetNextEvent(event):
        drained += 1
    return drained


def _resolve_stop_thread(process, event, lldb):
    """Best-effort thread for a stop: the event's own thread, then
    thread 0, then the selected thread."""
    getter = getattr(lldb.SBProcess, "GetThreadFromEvent", None)
    if getter is not None:
        thread = getter(event)
        if thread is not None and thread.IsValid():
            return thread
    if process.GetNumThreads() > 0:
        return process.GetThreadAtIndex(0)
    return process.GetSelectedThread()


def _stop_description(thread) -> str:
    try:
        return thread.GetStopDescription(120) or ""
    except Exception:  # noqa: BLE001 — diagnostics only
        return ""


def _wait_for_interrupt_stop(
    listener, process, lldb
) -> tuple[bool, str, int, str, int, str]:
    """Short-poll until the process has really stopped after the
    interrupt.

    Two signals are accepted, and which one fired is reported back as
    ``via``: a process-stop event on the listener, or — if no event
    ever arrives — the process object's own state flip to stopped.
    The process must first be observed *running*, so the entry stop
    can never masquerade as the interrupt stop (and if ``Continue``
    silently no-ops — the M1 symptom — this fails loudly instead).

    Returns ``(ok, detail, pc, function, wakeups, via)``; each poll
    iteration counts as one wakeup, so a low wakeups-per-second rate
    shows the loop sleeps instead of busy-spinning.
    """
    stopped = int(lldb.eStateStopped)
    running = int(lldb.eStateRunning)
    deadline = time.monotonic() + _WAIT_DEADLINE_SECONDS
    wakeups = 0
    resumed = False
    stop_event_seen = False
    last_stop_event = None
    while time.monotonic() < deadline:
        wakeups += 1
        state = int(process.GetState())
        if state == running:
            resumed = True
        event = lldb.SBEvent()
        if (
            listener.GetNextEvent(event)
            and lldb.SBProcess.EventIsProcessEvent(event)
            and int(lldb.SBProcess.GetStateFromEvent(event)) == stopped
        ):
            stop_event_seen = True
            last_stop_event = event
        if resumed and state == stopped:
            thread = _resolve_stop_thread(process, last_stop_event, lldb)
            if thread is not None and thread.IsValid() and thread.GetNumFrames() > 0:
                frame = thread.GetFrameAtIndex(0)
                via = (
                    "process-stop event"
                    if stop_event_seen
                    else "state poll (no stop event delivered)"
                )
                return (
                    True,
                    "",
                    int(frame.GetPC()),
                    frame.GetFunctionName() or "<unknown>",
                    wakeups,
                    via,
                )
            # Stopped but no usable thread (yet): keep polling — the
            # stop may still be materializing; the deadline aborts us.
        time.sleep(_POLL_SECONDS)
    if not resumed:
        detail = (
            "process never resumed after Continue() "
            f"(state stuck at {int(process.GetState())}) — the M1 "
            "silent-launch symptom"
        )
    else:
        stop_desc = ""
        if process.GetNumThreads() > 0:
            stop_desc = _stop_description(process.GetThreadAtIndex(0))
        detail = (
            f"no usable stop within {_WAIT_DEADLINE_SECONDS}s "
            f"(state={int(process.GetState())}, "
            f"threads={process.GetNumThreads()}, "
            f"stop_event_seen={stop_event_seen}, "
            f"stop_description={stop_desc!r})"
        )
    return False, detail, 0, "", wakeups, ""


def _pc_in_target(sb_target, target: str, pc: int, lldb) -> bool:
    """True when ``pc`` resolves into the target's own module.

    This binding has no ``SBTarget.ResolveAddress``; the ``SBAddress``
    constructor with a load address and target is the equivalent.
    """
    address = lldb.SBAddress(pc, sb_target)
    if not address.IsValid():
        return False
    module = address.GetModule()
    if not module.IsValid():
        return False
    got = module.GetFileSpec().GetFilename()
    want = _main_module(sb_target, target).GetFileSpec().GetFilename()
    if got is None or want is None:
        return False
    return got.lower() == want.lower()


def main() -> int:
    print(f"lldb version: {_lldb_version()}", flush=True)

    clang = shutil.which("clang")
    if clang is None:
        print("environment unavailable: clang not found")
        return 2
    try:
        lldb = _ensure_lldb()
    except RuntimeError as exc:
        print(f"environment unavailable: {exc}")
        return 2
    # Locate lldb-server and export LLDB_DEBUGSERVER_PATH (stdlib-only);
    # without it this LLDB build cannot launch a process at all — the
    # exact gap the M1 driver closes in run().
    _ensure_debug_server()

    with tempfile.TemporaryDirectory(prefix="symrepl-spike-") as tmp:
        work = Path(tmp)
        source = work / "spin.c"
        source.write_text(_C_SOURCE)
        target_bin = work / "spin"
        build = subprocess.run(
            [clang, "-O0", "-g", str(source), "-o", str(target_bin)],
            capture_output=True,
            text=True,
        )
        if build.returncode != 0:
            print(f"environment unavailable: clang failed: {build.stderr.strip()}")
            return 2

        stdout_path = work / "inferior-stdout"
        stderr_path = work / "inferior-stderr"

        lldb.SBDebugger.Initialize()
        debugger = lldb.SBDebugger.Create()
        process = None
        try:
            debugger.SetAsync(True)
            sb_target = debugger.CreateTarget(str(target_bin))
            if not sb_target.IsValid():
                print("FAIL: lldb could not load the spike target")
                return 1
            listener = lldb.SBListener("symrepl-spike")
            error = lldb.SBError()
            # Full-parameter Launch exactly like the M1 driver: native
            # stdio redirection to files and a stop at the entry point,
            # so the event loop below owns the whole run.
            process = sb_target.Launch(
                listener,
                [str(target_bin)],
                None,  # envp: inherit (irrelevant for this target)
                None,  # stdin
                str(stdout_path),
                str(stderr_path),
                None,  # working directory
                0,  # launch flags
                True,  # stop at entry
                error,
            )
            if not process.IsValid() or error.GetCString():
                detail = error.GetCString() or "unknown error"
                print(f"FAIL: launch failed: {detail}")
                return 1

            verdict = "SUPPORTED"
            observations: list[str] = []

            for round_no, settle in (
                (1, _SETTLE_SECONDS),
                (2, _RESUME_SETTLE_SECONDS),
            ):
                _drain_events(listener, lldb)
                process.Continue()  # async: returns immediately
                time.sleep(settle)
                process.SendAsyncInterrupt()
                t0 = time.monotonic()
                ok, detail, pc, function, wakeups, via = (
                    _wait_for_interrupt_stop(listener, process, lldb)
                )
                wall = time.monotonic() - t0
                in_target = ok and _pc_in_target(
                    sb_target, str(target_bin), pc, lldb
                )
                rate = wakeups / wall if wall > 0 else float("inf")
                observations.append(
                    f"interrupt #{round_no}: stopped={ok} "
                    f"pc_in_target={in_target} pc=0x{pc:x} "
                    f"function={function!r} via={via or '-'} "
                    f"poll_wakeups={wakeups} ({rate:.1f}/s over {wall:.2f}s)"
                    + (f" detail={detail}" if detail else "")
                )
                print(observations[-1], flush=True)

            if verdict == "SUPPORTED":
                print(
                    "VERDICT: SendAsyncInterrupt works — both interrupts "
                    "produced a real stop inside the target with a resume "
                    "in between; per-round wakeup rates above show the "
                    "GetNextEvent short poll sleeping, not busy-spinning."
                )
                return 0
            print(
                "VERDICT: SendAsyncInterrupt unusable in service form; "
                "pause degrades to a documented limitation."
            )
            return 1
        finally:
            if process is not None and process.IsValid():
                process.Kill()
            lldb.SBDebugger.Destroy(debugger)
            lldb.SBDebugger.Terminate()


if __name__ == "__main__":
    raise SystemExit(main())
