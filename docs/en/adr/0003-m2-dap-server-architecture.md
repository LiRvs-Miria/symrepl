# ADR-0003 — M2 architecture: DAP server over the parent–child topology

Date: 2026-10-04　　Status: Accepted (architectural review 2026-10-04; findings incorporated)

English version is authoritative. 简体中文见
[zh-CN/adr/0003-m2-dap-server-architecture.md](../../zh-CN/adr/0003-m2-dap-server-architecture.md)。

## Context

M1 delivered one-shot replay: `symrepl <ktest> --target <bin>` spawns a
throwaway child (`python -m symrepl.lldb_driver`) that holds SBDebugger
and the inferior, runs to exit, and returns a JSON result. Three facts
from M1 constrain M2:

1. LLDB's embedded global state is fragile in long-lived hosts
   (re-initialization after `SBDebugger.Terminate()` breaks launches;
   the synchronous `Continue()` hung; scripted callbacks silently never
   ran) — hence the throwaway-child isolation, which M2 must preserve.
2. The harness contract (kleeRuntest) and branch tracing (per-site
   breakpoints, session-side recording) are session semantics that any
   interactive mode must reproduce, not replace.
3. The repository carries zero runtime dependencies, and the M1 child
   already redirects the inferior's stdio to files.

M2's goal (roadmap): replay sessions usable from VS Code and any DAP
client. Research against the DAP specification, `lldb-dap`,
`microsoft/debugpy`, and `vscode-mock-debug` (2026-10-04) established:

- The wire protocol is `Content-Length`-framed UTF-8 JSON, identical
  over stdio and TCP; the envelope is request/response/event with a
  per-actor `seq` starting at 1.
- Lifecycle: `initialize` → capabilities response → `initialized`
  event → `setBreakpoints`/… → `configurationDone`; the `launch`
  response must be withheld until `configurationDone` is answered.
- `threads` must be implemented even for single-threaded targets.
- Small adapters (`vscode-mock-debug`) implement ~25 requests;
  stepping (`next/stepIn/stepOut/pause`) is baseline, and results are
  expressed via `stopped(reason, …)` events, not request responses.
- VS Code requires a `debuggers` contribution (an extension) to offer
  a debug type, but a declarative, zero-code `package.json` suffices;
  `"debugServer": N` connects to an already-running adapter without
  any extension.
- stdio hygiene is mandatory: debugpy nulls `sys.stderr` at startup in
  stdio mode; lldb-dap has a dedicated output redirector — protocol
  channels must be exclusive.
- Prior-art process models: debugpy separates adapter / launcher /
  debug-server processes with per-component JSON channels; lldb-dap
  runs per-session runloops with a dedicated LLDB event thread and
  clears variables references on continue.

## Decision

1. **Topology**: the DAP server is the `symrepl` parent process; each
   debug session gets one **long-lived child**
   (`python -m symrepl.lldb_service`) that holds SBDebugger and the
   inferior for the session's lifetime and is destroyed with it. The
   parent speaks DAP to the client; parent and child speak an internal
   request/response + event protocol.
2. **Internal protocol**: `Content-Length`-framed UTF-8 JSON — the
   same framing module used for DAP — over the child's stdin/stdout.
   Rules: a `hello` message carries a protocol **version** (cheap
   insurance; the roadmap's ptrfuzz integration may one day drive this
   channel); every response carries a uniform **ok/error envelope**;
   `Content-Length` is capped (64 MiB) and counted in **encoded
   bytes**; a **malformed frame kills the session** (no resync).
3. **Transport**: default single-session stdio;
   `symrepl dap --port N` listens for a single client connection.
   Multi-session in one process is deferred.
4. **DAP subset** (capabilities declared honestly, mock-debug
   aligned): `initialize`, `launch`, `setBreakpoints`,
   `setExceptionBreakpoints`, `configurationDone`, `threads`,
   `stackTrace`, `scopes`, `variables`, `continue`, `disconnect`,
   `terminate`, and stepping (`next`, `stepIn`, `stepOut`, `pause`).
   `evaluate` and `completions` are deferred past M2. `threads`
   returns a single thread. The `launch` response is withheld until
   `configurationDone` (a bad client that never sends it is handled by
   decision 11's timeout rule). **Event vocabulary**:
   `initialized`, `process`, `thread`, `stopped`, `exited`,
   `terminated`, `output`. **Capabilities frozen now**:
   `supportsConfigurationDoneRequest: true` (the withheld-launch
   design depends on it), `supportsTerminateRequest: true`;
   `supportsTerminateDebuggee` is **not** declared (decision 8).
   **Ordering invariant**: responses and events share one output
   queue through a single writer, so a `continue` response is always
   written before the `stopped` event it causes.
5. **Branch tracing UX**: default is M1 parity — branch-site
   breakpoints auto-continue and the ordered report is available via
   the custom request `replay.branchTrace` (custom requests use the
   `replay.*` prefix; unknown commands fail gracefully per spec).
   The `launch` argument `"stopOnBranch": true` instead stops at every
   site (`stopped`, reason `breakpoint`), exposing live variables.
   Because a zero-code extension cannot invoke custom requests, the
   session end **pushes `TraceReport.render()` to the Debug Console
   via an `output` event** — the default VS Code experience is not a
   black hole.
6. **Stepping**: `next`/`stepIn`/`stepOut`/`pause` map to SBThread
   step operations (with `SendAsyncInterrupt` for `pause`); completion
   is reported as `stopped(reason: "step")`.
7. **Concurrency & thread ownership**: threads + queues + a single
   writer per channel (reader threads never write); no asyncio (the
   SB API is a blocking C-shaped surface). **Inside the child, one
   single-owner thread executes all SB API calls**: the LLDB event
   thread runs `WaitForEvent` as a 100–200 ms short poll, yielding to
   internal commands injected through a queue; `pause` uses
   `process.SendAsyncInterrupt()`. The parent runs a client-reader
   thread and a child-event reader thread, multiplexed by the main
   loop. An **M2-a spike** validates `SendAsyncInterrupt` in the
   service-form child before the skeleton lands.
8. **Lifecycle & failure semantics** (the shutdown ladder):
   - The child watches its **stdin**: on EOF it kills the inferior and
     exits — the only parent-death signal reliable against SIGKILL.
   - If the child dies mid-session (stdout EOF / nonzero exit), the
     parent translates it into a DAP `terminated` event, fails all
     pending internal requests, and cleans up the session.
   - `disconnect`/`terminate` (and client transport EOF) escalate:
     SIGTERM → 2–5 s grace → SIGKILL → `waitpid`. Graceful exit is
     never assumed (M1 evidence).
   - `disconnect`/`terminate` **always kill** the inferior and child
     (replay leaves nothing worth detaching);
     `supportsTerminateDebuggee` stays undeclared.
9. **Interactive timeouts**: interactive sessions have **no global
   DAP-level timeout** — the user may sit watching a stopped process.
   Event waits use short polls purely to yield CPU and check shutdown
   flags. M1's hard default remains only in the one-shot CLI path.
10. **Launch arguments schema, frozen now** (names leak into user
    `launch.json` and the extension manifest): `ktest` (required),
    `target` (required), `stopOnBranch` (bool, default `false`),
    `stopOnEntry` (bool, default `false`), `scope` (string, default
    `"module"`), `timeout` (number, optional — off by default per
    decision 9).
11. **ktest injection**: the parent validates the user's ktest with
    `symrepl.ktest`, then the child sets `KTEST_FILE` to the **user's
    original path** — M1's temp-file re-serialization and its
    cross-process lifecycle are dropped.
12. **State references**: variables references and stack frame ids are
    per-stop **generation ids**; any resume invalidates the current
    generation, and stale references get an error response (the
    classic DAP adapter pitfall, decided up front).
13. **Backpressure**: `output` events are **droppable** (bounded queue,
    truncation marker); control events (`stopped`/`terminated`/
    `exited`) are **never dropped**. This split is explicit because
    the two classes have opposite failure semantics.
14. **VS Code integration**: a declarative, zero-code extension
    (`contributes.debuggers`, type `symrepl`, adapter script via
    `program`) ships at M2 end. Development and tests use
    `"debugServer"` / raw socket clients, so no extension is needed
    for any non-VS-Code DAP client. Linux-first: the Windows adapter
    launcher story is documented later.
15. **Testing without LLDB**: the parent's DAP logic is tested against
    a mock child speaking the internal protocol; the framing layer has
    pure unit tests (frame splitting/coalescing, encoded-byte lengths,
    two-frames-in-one-read). A hand-written ~40 line Python DAP test
    client (dev-only) drives integration tests in socket mode.
    M2-e adds chaos tests (child crash mid-session, malformed frames)
    and an LLDB smoke path in the acceptance-script style.
16. **Rejected alternative**: building on `lldb-dap`. It is a generic
    C++ adapter: ktest injection, harness validation, and branch-site
    semantics cannot be embedded, and its process model conflicts with
    the child-isolation contract. It remains the protocol-mapping
    reference.

## Consequences

- VS Code and any DAP client can drive replay sessions; symrepl gains
  an interactive debugger surface without new dependencies (framing is
  ~40 lines of stdlib).
- M1's isolation contract is preserved verbatim: one throwaway child
  per session; LLDB never runs in the long-lived parent. The child's
  lifecycle semantics (stdin-EOF suicide, shutdown ladder) close the
  parent-death and mid-session-crash gaps the one-shot model never
  had.
- M1's semantics — ktest injection per the kleeRuntest contract,
  harness-violation surfacing, the ordered branch trace with its
  `not observed` honesty — carry over unchanged. DAP adds a transport,
  not new semantics.
- The internal parent↔child protocol is private but now carries a
  versioned handshake, so the roadmap's ptrfuzz integration can bind
  to it deliberately instead of by accident.
- Limitations, recorded honestly: single session per server process;
  `evaluate`/completions (the debug-console REPL) deferred; branch-site
  tracing still reports the last hit's outcome as unobserved (M1's
  blind spot; M3 will address it); multi-session and `runInTerminal`
  deferred; Windows adapter launcher deferred (Linux-first).
- M2 slices live in the roadmap: M2-a (skeleton + lifecycle semantics
  + interrupt spike), M2-b1 (DAP lifecycle + threads/stackTrace/
  continue + test client), M2-b2 (scopes/variables + source
  breakpoints), M2-c (branch tracing over DAP), M2-d (stepping),
  M2-e (extension, docs, acceptance scenario, chaos tests).
