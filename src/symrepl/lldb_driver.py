"""Thin LLDB driver: launch a target with a `.ktest` injected via
``KTEST_FILE`` and run it to exit, capturing the inferior's exit code
and stderr.

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

Scope (M1-a): the replay *path* only — launch, run to exit/stop, collect
the outcome.  Branch tracing and state dumps arrive in M1-c on top of
the same primitives (see docs/en/roadmap.md).
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

#: Environment variable the parent sets for the child after locating
#: ``lldb-server`` (this LLDB build does not find it on its own).
_DEBUG_SERVER_ENV = "LLDB_DEBUGSERVER_PATH"

_ERROR_PREFIX = "KLEE_RUN_TEST_ERROR: "


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


def _ensure_lldb():
    """Import and return the ``lldb`` module (child process only),
    bootstrapping ``sys.path`` via ``lldb -P`` when the module is not
    directly importable."""
    try:
        import lldb  # noqa: F401 — re-exported via return value
        return sys.modules["lldb"]
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
        import lldb  # noqa: F401
        return sys.modules["lldb"]
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


def session_run(ktest_path: str, target: str, timeout_seconds: int) -> dict:
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
            # and a stop at the entry point so the event loop below owns
            # the whole run.
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

            terminal = frozenset(
                {
                    int(lldb.eStateExited),
                    int(lldb.eStateCrashed),
                    int(lldb.eStateDetached),
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

            process.Continue()  # async: returns immediately
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
                if state == int(lldb.eStateStopped):
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

    return {
        "exit_code": exit_code,
        "state": state_name,
        "stderr": stderr_text,
        "run_test_errors": [
            line[len(_ERROR_PREFIX) :]
            for line in stderr_text.splitlines()
            if line.startswith(_ERROR_PREFIX)
        ],
    }


# ------------------------------------------------------------------- run --
# Parent-process orchestration: no lldb import anywhere below.


def _child_main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="symrepl-lldb-session")
    parser.add_argument("--ktest", required=True)
    parser.add_argument("--target", required=True)
    parser.add_argument("--timeout", type=int, default=60)
    parser.add_argument("--out", required=True, help="result JSON path")
    args = parser.parse_args(argv)
    try:
        result = session_run(args.ktest, args.target, args.timeout)
        result["error"] = None
    except Exception as exc:  # noqa: BLE001 — reported to the parent verbatim
        result = {"error": f"{type(exc).__name__}: {exc}"}
    Path(args.out).write_text(json.dumps(result))
    return 0


def run(ktest_path: str, target: str, timeout_seconds: int = 60) -> ReplayResult:
    """Run ``target`` to completion under LLDB with ``KTEST_FILE``
    pointing at ``ktest_path``, in an isolated child process.

    ``ktest_path`` should be an absolute path (as produced by
    :func:`symrepl.replay`).
    """
    _ensure_debug_server()  # stdlib-only; also inherited by the child

    with tempfile.TemporaryDirectory(prefix="symrepl-driver-") as tmp:
        out_path = os.path.join(tmp, "result.json")
        proc = subprocess.run(
            [
                sys.executable,
                "-m",
                "symrepl.lldb_driver",
                "--ktest",
                ktest_path,
                "--target",
                target,
                "--timeout",
                str(timeout_seconds),
                "--out",
                out_path,
            ],
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
    )


if __name__ == "__main__":
    raise SystemExit(_child_main(sys.argv[1:]))
