# DAP server mode (M2)

symrepl ships a Debug Adapter Protocol (DAP) server so replay sessions
can be driven from VS Code and any DAP client (ADR-0003).  Zero runtime
dependencies: framing is stdlib, the transport is stdio or a single TCP
connection.

## Running

    python3 src/server/schedule.py --stdio            # stdio — one client, one session
    python3 src/server/schedule.py --port 47113 --lldb-dir /usr/lib/llvm-22/bin
    # (see `python3 src/server/schedule.py --help`; --lldb-dir = toolchain
    #  dir, optional at boot — the `lldbDir` launch argument overrides it;
    #  missing in both places = clean launch-time refusal)

Lifecycle: `initialize` → `launch` (response withheld) → `initialized`
event → `setBreakpoints`/… → `configurationDone` → launch response →
`stopped`/`exited`/`terminated` events.  Debugger-layer commands
(`threads`/`stackTrace`/`scopes`/`variables`/`continue`/
`setBreakpoints`/`next`/`stepIn`/`stepOut`) self-loop: they forward to
the child without moving the adapter state machine — breakpoints may be
set in the configuration phase (standard client timing) and after any
stop; stepping is synchronous (the stop follows the step response via
the event pump, reason `step`).
Capabilities are frozen (ADR-0003 decision 4):
`supportsConfigurationDoneRequest` and `supportsTerminateRequest`;
`evaluate`/`completions` are deferred past M2.

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
  "lldbDir": "/usr/lib/llvm-22/bin",
  "scope": "get_sign"
}
```

For development against an already-running server use
`"debugServer": 47113` in place of `launch` — no extension needed
(that is how the probe suites in [`probes/`](../../probes) drive the
server); start the server manually with `--lldb-dir` in that form.

## Launch arguments

Core (`ktest`/`target`) frozen per ADR-0003 decision 10; extensions
(`lldbDir`, `scope`, `ir`) ride the same validation family, each landed
with its own probe net.

| argument       | type             | default   | meaning                                          |
| -------------- | ---------------- | --------- | ------------------------------------------------ |
| `ktest`        | string, required | —         | the `.ktest` to replay (parsed parent-side,      |
|                |                  |           | structured to the session child)                 |
| `target`       | string, required | —         | debuggee binary (linked with the kleeRuntest     |
|                |                  |           | runtime)                                         |
| `lldbDir`      | string           | boot argv | toolchain dir containing `lldb`+`lldb-server`;   |
|                |                  |           | overrides `--lldb-dir` given at boot             |
| `scope`        | string           | —         | branch-trace instrumentation scope: `"module"`   |
|                |                  |           | or comma-separated function names; absent = no   |
|                |                  |           | instrumentation, no report                       |
| `ir`           | string           | —         | path to the target's `.ll` (`clang -O0 -g -S     |
|                |                  |           | -emit-llvm`, M3); present = report nodes carry   |
|                |                  |           | fork/runtime annotations; unparsable = launch    |
|                |                  |           | rejected (fail-fast, before spawn)               |

## Branch tracing

Tracing is opt-in via `scope`: at `configurationDone` the session
auto-instruments every conditional site in scope (breakpoints by load
address); each hit emits `stopped` (reason `breakpoint`) and the
ordered report — pc, function, `file:line:column`, mnemonic, call
stack, registered symbol values with ktest byte annotations — is
pushed as an `output` event when the session ends (passive: there is
no request face for it). The entry stop is not a site.

With `ir` (M3, [ADR-0004](adr/0004-m3-path-narrowing.md)): report
nodes additionally carry `kind: "fork" | "runtime"` plus `detail`
(`symbolic-branch` / `function-not-in-module` / `concrete-condition`)
— the static fork subset computed from the `.ll` text. Without `ir`
no annotation fields are added (the M1+M2 shape is unchanged).

## Limitations (recorded honestly)

- single session per server process; multi-session deferred
- `evaluate`/`completions` (the Debug Console REPL) deferred per ADR
- the last traced hit's taken/not-taken direction stays unobserved
  (site order implies it; M3 non-goal per ADR-0004 decision 5)
- tracing without `ir` is a superset of the path decisions; the
  fork/runtime annotation narrows it statically (M3); dynamic
  input-dependence (differential replay) is M4
