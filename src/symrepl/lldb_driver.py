"""Thin LLDB driver: launch a target with a `.ktest` injected via
``KTEST_FILE``, trace its conditional branches, and run it to exit,
capturing the inferior's exit code, stderr, and the ordered branch
trace with per-hit program state.

This is the only module that touches the ``lldb`` Python module.  That
module is not pip-installable — it ships with LLDB itself and is tied to
the interpreter ABI LLDB was built against — so the bootstrap first
tries a plain import and otherwise locates it via ``lldb -P``.

Isolation model: :func:`run` executes each replay in a *fresh throwaway
Python subprocess* (:func:`session_run` is the child entry point).  The
LLDB library carries fragile per-process global state that cannot be
re-initialized after ``SBDebugger.Terminate()`` and misbehaves when its
host process is reused (observed on LLDB 22.1.8: launches silently no-op
or fail with "CreateProcess() failed for gdb-remote process", and the
synchronous ``Continue()`` hung forever after a failed launch).  One
debugger lifetime per OS process makes every replay — first or fiftieth,
under pytest or embedded in another application — behave identically.
The child still drives the real LLDB Python API; nothing scrapes CLI
output (ADR-0002).

Branch tracing (ADR-0002 decision 2, with one recorded deviation): at
the entry stop the driver pre-scans the main module's code symbols for
conditional branches (``GetControlFlowKind()`` with a per-architecture
mnemonic fallback — see :mod:`symrepl.branches`), sets one breakpoint
per site, and records each stop — PC, function, arguments/locals — from
the *session side* event loop.  LLDB 22.1.8's scripted breakpoint
callbacks silently never execute (whatever naming scheme, the bound
callback auto-continues without running its body), so the record step
lives in the session instead of a scripted callback; the ADR's intent
— per-site breakpoints recording PC/outcome in order — is unchanged.

kleeRuntest contract notes that shaped this driver (KLEE
``runtime/Runtest/intrinsics.c``):

- Without ``KTEST_FILE`` the runtime prompts on stdout and blocks on
  stdin — the variable is always injected, and the environment is kept
  minimal (only ``KTEST_FILE``) so a stray parent ``KLEE_RANDOM`` can
  never switch the runtime into random-feed mode.
- Contract violations are reported on stderr with a
  ``KLEE_RUN_TEST_ERROR:`` prefix and end in ``exit(1)`` — no signal is
  raised, so the outcome is detected from the exit state and the
  captured stderr alone.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path

from . import branches as _branches
from .trace import Hit, TraceReport

#: Environment variable the parent sets for the child after locating
#: ``lldb-server`` (this LLDB build does not find it on its own).
_DEBUG_SERVER_ENV = "LLDB_DEBUGSERVER_PATH"

_ERROR_PREFIX = "KLEE_RUN_TEST_ERROR: "

#: Safety valve: breakpoint stops without a bound on resumes would let a
#: pathological target spin the event loop forever.
_MAX_RESUMES = 100_000

#: A scope that instruments more sites than this is almost certainly a
#: mistake (the report would be unreadable); fail instead.
_MAX_SITES = 5_000

_INVALID_LOAD_ADDR = 0xFFFFFFFFFFFFFFFF


@dataclass(frozen=True)
class ReplayResult:
    """The observable outcome of one replay run."""

    #: Process exit status, or ``None`` when it did not exit normally
    #: (e.g. stopped by a signal).
    exit_code: int | None
    #: Lowercase LLDB state name at the end of the run: ``"exited"``,
    #: ``"crashed"``, ``"detached"`` ...
    state: str
    #: Full inferior stderr as captured during the run.
    stderr: str
    #: ``KLEE_RUN_TEST_ERROR`` messages in output order, prefix stripped.
    #: Duplicates are kept (KLEE repeats "out of inputs" per call).
    run_test_errors: tuple[str, ...]
    #: Ordered branch trace, or ``None`` when tracing was disabled.
    trace: TraceReport | None


def _ensure_lldb():
    """Import and return the ``lldb`` module (child process only),
    bootstrapping ``sys.path`` via ``lldb -P`` when the module is not
    directly importable."""
    try:
        import lldb as module
        return module
    except ImportError:
        pass
    exe = shutil.which("lldb")
    if exe is None:
        raise RuntimeError(
            "the 'lldb' Python module is not importable and no lldb "
            "executable was found on PATH; install LLDB with Python "
            "scripting support (the module ships with LLDB, it is not "
            "pip-installable)"
        )
    result = subprocess.run([exe, "-P"], capture_output=True, text=True)
    module_path = result.stdout.strip()
    if result.returncode != 0 or not module_path:
        raise RuntimeError(
            f"'{exe} -P' failed to report its Python module path"
        )
    if module_path not in sys.path:
        sys.path.insert(0, module_path)
    try:
        import lldb as module
        return module
    except ImportError as exc:
        raise RuntimeError(
            "importing lldb failed even with the 'lldb -P' path added "
            f"({module_path!r}); this Python interpreter must match the "
            "one this LLDB was built against"
        ) from exc


def _ensure_debug_server() -> None:
    """Make sure LLDB can find its ``lldb-server`` helper.

    Preference order: an explicit ``LLDB_DEBUGSERVER_PATH`` wins; then
    the directory of the resolved ``lldb`` executable; then the
    ``/usr/lib/llvm-*/bin`` packaging directories.  The (versioned)
    symlink ``lldb-server`` itself is preferred over suffixed variants.
    Pure stdlib — safe to call in the parent process.
    """
    if os.environ.get(_DEBUG_SERVER_ENV):
        return
    search_dirs: list[Path] = []
    exe = shutil.which("lldb")
    if exe:
        search_dirs.append(Path(exe).resolve().parent)
    search_dirs.extend(sorted(Path("/usr/lib").glob("llvm-*/bin"), reverse=True))
    for directory in search_dirs:
        candidates = sorted(
            p
            for p in directory.glob("lldb-server*")
            if p.is_file() and os.access(p, os.X_OK)
        )
        if candidates:
            os.environ[_DEBUG_SERVER_ENV] = str(candidates[0])
            return


# --------------------------------------------------------------- session --
# Everything between this marker and the `run` section executes inside the
# throwaway child process.


def _main_module(sb_target, target: str):
    """The main executable's module (match by basename, fall back to
    index 0)."""
    want = os.path.basename(target).lower()
    for i in range(sb_target.GetNumModules()):
        mod = sb_target.GetModuleAtIndex(i)
        name = mod.GetFileSpec().GetFilename()
        if name and name.lower() == want:
            return mod
    return sb_target.GetModuleAtIndex(0)


def _collect_sites(
    lldb, sb_target, scope: str, target: str
) -> tuple[_branches.BranchSite, ...]:
    """Pre-scan the main module's code symbols for conditional branches.

    ``scope`` is ``"module"`` (every code symbol) or a comma-separated
    list of function names.  Must run while the process is stopped at
    the entry point: a PIE binary's load addresses only exist once the
    dynamic linker has placed it.
    """
    cond_jump = int(lldb.eInstructionControlFlowKindCondJump)
    unknown_kind = int(lldb.eInstructionControlFlowKindUnknown)
    wanted = (
        None
        if scope == "module"
        else {name.strip() for name in scope.split(",") if name.strip()}
    )

    mod = _main_module(sb_target, target)
    sites: list[_branches.BranchSite] = []
    seen: set[int] = set()
    for i in range(mod.GetNumSymbols()):
        sym = mod.GetSymbolAtIndex(i)
        if sym.GetType() != int(lldb.eSymbolTypeCode):
            continue
        name = sym.GetName() or "<unnamed>"
        if wanted is not None and name not in wanted:
            continue
        start = sym.GetStartAddress().GetLoadAddress(sb_target)
        if start in seen or start in (0, _INVALID_LOAD_ADDR):
            continue
        seen.add(start)
        instructions = sym.GetInstructions(sb_target)
        for k in range(instructions.GetSize()):
            ins = instructions.GetInstructionAtIndex(k)
            mnemonic = ins.GetMnemonic(sb_target)
            # GetControlFlowKind exists on LLDB >= 16 and is authoritative
            # for x86/x86_64; without it (or for unknown kinds) the
            # mnemonic table in symrepl.branches decides.
            if hasattr(ins, "GetControlFlowKind"):
                kind_value = int(ins.GetControlFlowKind(sb_target))
                kind: int | None = (
                    None if kind_value == unknown_kind else kind_value
                )
            else:
                kind = None
            if not _branches.is_conditional(mnemonic, kind, cond_jump):
                continue
            address = ins.GetAddress().GetLoadAddress(sb_target)
            if address in (0, _INVALID_LOAD_ADDR):
                continue
            sites.append(
                _branches.BranchSite(
                    address=address,
                    function=name,
                    mnemonic=mnemonic,
                    size=ins.GetByteSize(),
                    target=_branches.parse_branch_target(
                        ins.GetOperands(sb_target)
                    ),
                )
            )
    sites.sort(key=lambda s: s.address)
    return tuple(sites)


def session_run(
    ktest_path: str,
    target: str,
    timeout_seconds: int,
    trace: bool = True,
    scope: str = "module",
) -> dict:
    """Run ``target`` once under LLDB and return the outcome as a plain
    dict (JSON-serializable).  Child-process entry point: the process
    exits after this returns, taking the debugger with it."""
    lldb = _ensure_lldb()

    with tempfile.TemporaryDirectory(prefix="symrepl-run-") as tmp:
        stdout_path = os.path.join(tmp, "stdout")
        stderr_path = os.path.join(tmp, "stderr")

        lldb.SBDebugger.Initialize()
        debugger = lldb.SBDebugger.Create()
        try:
            debugger.SetAsync(True)
            sb_target = debugger.CreateTarget(target)
            if not sb_target.IsValid():
                raise RuntimeError(
                    f"lldb could not load target binary: {target}"
                )

            listener = lldb.SBListener("symrepl")
            error = lldb.SBError()
            # The full-parameter Launch: native stdio redirection, an
            # explicit minimal environment (only KTEST_FILE — deterministic,
            # and a parent KLEE_RANDOM can never leak into the harness),
            # and a stop at the entry point so the prescan and the event
            # loop below own the whole run.
            process = sb_target.Launch(
                listener,
                [target],  # argv
                [f"KTEST_FILE={ktest_path}"],  # envp
                None,  # stdin (never read: KTEST_FILE is always set)
                stdout_path,
                stderr_path,
                None,  # working directory
                0,  # launch flags
                True,  # stop at entry
                error,
            )
            if not process.IsValid() or error.GetCString():
                detail = error.GetCString() or "unknown error"
                raise RuntimeError(f"lldb failed to launch {target}: {detail}")

            # Prescan at the entry stop (PIE load addresses exist now),
            # then one breakpoint per conditional site.
            sites: tuple[_branches.BranchSite, ...] = ()
            site_index: dict[int, _branches.BranchSite] = {}
            if trace:
                sites = _collect_sites(lldb, sb_target, scope, target)
                if len(sites) > _MAX_SITES:
                    raise RuntimeError(
                        f"scope {scope!r} matches {len(sites)} conditional "
                        f"sites (>{_MAX_SITES}); narrow it, e.g. "
                        "--scope <function>"
                    )
                for site in sites:
                    sb_target.BreakpointCreateByAddress(site.address)
                site_index = {s.address: s for s in sites}

            terminal = frozenset(
                {
                    int(lldb.eStateExited),
                    int(lldb.eStateCrashed),
                    int(lldb.eStateDetached),
                    int(lldb.eStateInvalid),
                }
            )
            names = {
                int(lldb.eStateUnloaded): "unloaded",
                int(lldb.eStateConnected): "connected",
                int(lldb.eStateAttaching): "attaching",
                int(lldb.eStateLaunching): "launching",
                int(lldb.eStateStopped): "stopped",
                int(lldb.eStateRunning): "running",
                int(lldb.eStateStepping): "stepping",
                int(lldb.eStateCrashed): "crashed",
                int(lldb.eStateDetached): "detached",
                int(lldb.eStateExited): "exited",
                int(lldb.eStateSuspended): "suspended",
                int(lldb.eStateInvalid): "invalid",
            }

            stopped = int(lldb.eStateStopped)
            hit_pcs: list[int] = []
            hit_vars: list[tuple[tuple[str, str], ...]] = []
            resumes = 0

            process.Continue()  # async: returns immediately
            state = int(lldb.eStateInvalid)
            while True:
                event = lldb.SBEvent()
                if not listener.WaitForEvent(timeout_seconds, event):
                    raise RuntimeError(
                        f"timed out after {timeout_seconds}s waiting for "
                        f"{target} to terminate"
                    )
                # Non-process events (breakpoints, stdio, ...) can arrive
                # on the listener; only process-state events may end the
                # wait, and only a real process state does.
                if not lldb.SBProcess.EventIsProcessEvent(event):
                    continue
                state = int(lldb.SBProcess.GetStateFromEvent(event))
                if state in terminal:
                    break
                if state != stopped:
                    continue
                # A breakpoint stop: record the site, then resume.  (The
                # stale entry-stop event also lands here; its PC is not a
                # site and is simply resumed.)
                cur = int(process.GetState())
                if cur != stopped:
                    continue  # already resumed (e.g. stale event)
                thread = process.GetSelectedThread()
                if thread is not None and thread.GetNumFrames() > 0:
                    frame = thread.GetFrameAtIndex(0)
                    pc = int(frame.GetPC())
                    if pc in site_index:
                        variables = tuple(
                            (v.GetName(), v.GetValue() or "")
                            for v in frame.GetVariables(True, True, False, True)
                        )
                        hit_pcs.append(pc)
                        hit_vars.append(variables)
                resumes += 1
                if resumes > _MAX_RESUMES:
                    raise RuntimeError(
                        f"exceeded {_MAX_RESUMES} breakpoint resumes; "
                        "aborting a pathological trace"
                    )
                process.Continue()

            # The process object is the authority on the final state; the
            # last event may lag behind it.
            state = int(process.GetState())
            state_name = names.get(state, f"state-{state}")
            exit_code = (
                process.GetExitStatus()
                if state == int(lldb.eStateExited)
                else None
            )
        finally:
            lldb.SBDebugger.Destroy(debugger)
            lldb.SBDebugger.Terminate()

        stderr_text = Path(stderr_path).read_text(errors="replace")

    payload: dict = {
        "exit_code": exit_code,
        "state": state_name,
        "stderr": stderr_text,
        "run_test_errors": [
            line[len(_ERROR_PREFIX) :]
            for line in stderr_text.splitlines()
            if line.startswith(_ERROR_PREFIX)
        ],
        "trace": None,
    }
    if trace:
        # Emit the trace structure even when zero sites matched the scope —
        # "traced, found nothing" must stay distinguishable from
        # "tracing disabled" (trace=None).
        outcomes = _branches.infer_outcomes(
            site_index, hit_pcs, run_ended=state == int(lldb.eStateExited)
        )
        hits = tuple(
            Hit(
                order=i + 1,
                site=site_index[pc],
                variables=variables,
                outcome=outcomes[i],
            )
            for i, (pc, variables) in enumerate(zip(hit_pcs, hit_vars))
        )
        payload["trace"] = {
            "scope": scope,
            "sites": [asdict(s) for s in sites],
            "hits": [
                {
                    "order": h.order,
                    "address": h.site.address,
                    "variables": [list(v) for v in h.variables],
                    "outcome": h.outcome,
                }
                for h in hits
            ],
        }
    return payload


# ------------------------------------------------------------------- run --
# Parent-process orchestration: no lldb import anywhere below.


def _child_main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="symrepl-lldb-session")
    parser.add_argument("--ktest", required=True)
    parser.add_argument("--target", required=True)
    parser.add_argument("--timeout", type=int, default=60)
    parser.add_argument("--trace", action="store_true")
    parser.add_argument("--scope", default="module")
    parser.add_argument("--out", required=True, help="result JSON path")
    args = parser.parse_args(argv)
    try:
        result = session_run(
            args.ktest,
            args.target,
            args.timeout,
            trace=args.trace,
            scope=args.scope,
        )
        result["error"] = None
    except Exception as exc:  # noqa: BLE001 — reported to the parent verbatim
        result = {"error": f"{type(exc).__name__}: {exc}"}
    Path(args.out).write_text(json.dumps(result))
    return 0


def _trace_from_payload(payload: dict) -> TraceReport | None:
    """Rebuild the :class:`TraceReport` from the child's JSON payload."""
    raw = payload.get("trace")
    if raw is None:
        return None
    sites = tuple(_branches.BranchSite(**s) for s in raw["sites"])
    by_address = {s.address: s for s in sites}
    hits = tuple(
        Hit(
            order=h["order"],
            site=by_address[h["address"]],
            variables=tuple(tuple(v) for v in h["variables"]),
            outcome=h["outcome"],
        )
        for h in raw["hits"]
    )
    return TraceReport(scope=raw["scope"], sites=sites, hits=hits)


def run(
    ktest_path: str,
    target: str,
    timeout_seconds: int = 60,
    trace: bool = True,
    scope: str = "module",
) -> ReplayResult:
    """Run ``target`` to completion under LLDB with ``KTEST_FILE``
    pointing at ``ktest_path``, in an isolated child process.

    ``ktest_path`` should be an absolute path (as produced by
    :func:`symrepl.replay`).
    """
    _ensure_debug_server()  # stdlib-only; also inherited by the child

    with tempfile.TemporaryDirectory(prefix="symrepl-driver-") as tmp:
        out_path = os.path.join(tmp, "result.json")
        cmd = [
            sys.executable,
            "-m",
            "symrepl.lldb_driver",
            "--ktest",
            ktest_path,
            "--target",
            target,
            "--timeout",
            str(timeout_seconds),
        ]
        if trace:
            cmd += ["--trace", "--scope", scope]
        cmd += ["--out", out_path]
        proc = subprocess.run(
            cmd,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=timeout_seconds + 10,
        )
        if not os.path.exists(out_path):
            tail = "\n".join(proc.stderr.splitlines()[-10:])
            raise RuntimeError(
                f"lldb driver child process failed (rc={proc.returncode}): "
                f"{tail or 'no output'}"
            )
        payload = json.loads(Path(out_path).read_text())

    if err := payload.get("error"):
        raise RuntimeError(f"lldb replay of {target} failed: {err}")
    return ReplayResult(
        exit_code=payload["exit_code"],
        state=payload["state"],
        stderr=payload["stderr"],
        run_test_errors=tuple(payload["run_test_errors"]),
        trace=_trace_from_payload(payload),
    )


if __name__ == "__main__":
    raise SystemExit(_child_main(sys.argv[1:]))
