# DAP server mode (M2)

symrepl ships a Debug Adapter Protocol (DAP) server so replay sessions
can be driven from VS Code and any DAP client (ADR-0003).  Zero runtime
dependencies: framing is stdlib, the transport is stdio or a single TCP
connection.

## Running

    python3 src/server/schedule.py --stdio            # stdio — one client, one session
    python3 src/server/schedule.py --port 47113 --lldb-dir /usr/lib/llvm-22/bin
    # (see `python3 src/server/schedule.py --help`; --lldb-dir = toolchain dir)

Lifecycle: `initialize` → `launch` (response withheld) → `initialized`
event → `setBreakpoints`/… → `configurationDone` → launch response →
`stopped`/`exited`/`terminated` events.  Capabilities are frozen
(ADR-0003 decision 4): `supportsConfigurationDoneRequest` and
`supportsTerminateRequest`; `evaluate`/`completions` are deferred past
M2.

## VS Code

Install the zero-code extension in [`vscode/`](../../vscode)
(declarative `contributes.debuggers`, Linux-first — ADR-0003 decision
14), then:

```json
{
  "type": "symrepl",
  "request": "launch",
  "name": "replay get_sign",
  "ktest": "third_party/klee/ktest/get_sign_test000002.ktest",
  "target": "${workspaceFolder}/get_sign",
  "stopOnEntry": true,
  "scope": "get_sign"
}
```

For development against an already-running server use
`"debugServer": 47113` in place of `launch` — no extension needed
(that is how the test suite and
[`scripts/acceptance_dap.py`](../../scripts/acceptance_dap.py) drive
the server).

## Launch arguments (frozen, ADR-0003 decision 10)

| argument       | type             | default   | meaning                                          |
| -------------- | ---------------- | --------- | ------------------------------------------------ |
| `ktest`        | string, required | —         | the `.ktest` to replay (validated parent-side)   |
| `target`       | string, required | —         | debuggee binary (linked with `-lkleeRuntest`)    |
| `stopOnEntry`  | boolean          | `false`   | stop at the entry point                          |
| `stopOnBranch` | boolean          | `false`   | stop at every traced branch (else M1 parity)     |
| `scope`        | string           | `"module"`| tracing scope: `"module"` or function names      |
| `timeout`      | number           | off       | seconds before the run is aborted (decision 9)   |

## Branch tracing

Tracing is always on within `scope`: traced sites record-and-continue
(M1 parity), and the ordered report — sites, hits, outcomes, per-site
summary — is pushed to the Debug Console (`output`) at session end and
available on demand via the custom request `replay.branchTrace`.
`"stopOnBranch": true` instead stops at every site
(`stopped`, reason `breakpoint`), exposing live variables.

## Limitations (recorded honestly)

- single session per server process; multi-session deferred
- `evaluate`/`completions` (the Debug Console REPL) deferred past M2
- the last traced hit's outcome is unobservable (M1 blind spot; M3)
- tracing is a superset of the recorded path decisions — mapping the
  KLEE-fork constraining subset needs IR/DWARF (M3)
