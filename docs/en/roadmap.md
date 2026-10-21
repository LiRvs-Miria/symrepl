# Roadmap

English | [简体中文](../zh-CN/roadmap.md)

> The English version is authoritative; both are updated in the same commit.

Milestones are deliberately small: each is 2–3 weekend blocks.

## M0 — Scaffold (done)

- Repository, license (Apache-2.0 WITH LLVM-exception), CI, and the
  scope/IP firewall documented in [ADR-0001](adr/0001-scope-and-ip-firewall.md).

## M1 — ktest replay (MVP) — done 2026-10-04

Input contract frozen: [ADR-0002](adr/0002-m1-input-contract.md)
(editorially revised 2026-10-04 — per-hit recording moved session-side;
decision unchanged).

- `.ktest` parser (v2/v3 — identical layout; `BOUT\n` tolerated on read,
  version > 3 rejected), pure Python, zero dependencies, byte layout
  verified against the KLEE sources and pinned by real-KLEE fixtures.
- Replay driver: launch the target (linked with `-lkleeRuntest`) under
  LLDB via its Python API and inject `KTEST_FILE`, surfacing per-object
  harness violations from `KLEE_RUN_TEST_ERROR` — the contract mirrors
  KLEE's official replay path.
- Branch tracing: per-site breakpoints on the conditional branches of
  the traced scope record PC/outcome in order, with arguments/locals
  dumped at each hit. Precise mapping to the KLEE-fork "constraining"
  subset is deferred to M3 (IR/DWARF).
- Optional diagnostic (never required): `.kquery` pretty-print /
  validation of the logical path condition — not provided; deferred.
- Acceptance: on a KLEE tutorial example (`get_sign`), the full loop
  works — `klee` run → symrepl → breakpoints hit in order → state
  report. Runnable end-to-end via `sh scripts/acceptance.sh`.

## M2 — DAP server mode (done 2026-10-05)

Architecture contract: [ADR-0003](adr/0003-m2-dap-server-architecture.md)
(parent DAP adapter + long-lived LLDB service child; stdio/--port;
mock-debug-aligned DAP subset; branch tracing via `stopOnBranch` and the
`replay.branchTrace` custom request).

Implementation mapping (planning pass 2026-10-05):

- **Reused verbatim**: `branches.py`, `trace.py` (`TraceReport.render()`
  becomes the output-event push), `ktest.py` (parent-side validation,
  user's original path — ADR-0003 decision 11), the LLDB bootstrap
  helpers and `_collect_sites` in `lldb_driver.py`, the file-redirected
  inferior stdio (the child's own stdout is idle today and becomes the
  protocol channel), and the `get_sign_bin` fixture.
- **Restructured**: the `session_run` kernel becomes the interactive
  session loop — the entry stop waits on internal commands instead of
  running to exit, and `SBDebugger.Terminate()` moves to child exit.
  One child process per DAP session: the M1 isolation contract with the
  lifetime extended from "one replay" to "one session" (still exactly
  one Initialize and one launch per process).
- **Untouched**: the one-shot CLI replay path (`replay.py` +
  `lldb_driver.run()`); DAP adds a transport, not new semantics.
- **New**: `dap_framing.py` (~40 lines of stdlib), `lldb_service.py`
  (the long-lived child), the parent DAP server, and a dev-only DAP
  test client.

Slices — strictly linear (each builds on the previous protocol
surface); the framing unit tests, the test client, and the extension
manifest skeleton may be pulled ahead:

- **M2-a skeleton** — the `SendAsyncInterrupt` spike **passed**
  (2026-10-05, LLDB 22.1.8, `scripts/spike_send_async_interrupt.py`):
  `pause` is confirmed viable; the spike also pinned the service-loop
  event primitive — `GetNextEvent` plus a short sleep, since the
  `WaitForEvent` binding only accepts whole seconds — plus
  drain-before-every-resume and `_ensure_debug_server()` before launch.
  Then: the framing module (encoded-byte counting, 64 MiB cap,
  malformed frame kills the session; unit-tested for
  splitting/coalescing), `lldb_service.py` (versioned `hello`
  handshake, ok/error envelope, stdin-EOF suicide, single-owner LLDB
  thread with a `GetNextEvent` short poll plus a command queue; stdio
  hygiene at the earliest entry — protocol channel owns stdout, stderr
  redirected to a file, LLDB logging kept off fd 1), the parent server
  (stdio + `--port`, single-writer output queue, shutdown ladder), a
  `symrepl dap` subcommand (bare-replay CLI and its exit codes
  unchanged), and mock-child tests. **Accept**: handshake over stdio
  and socket; malformed frame kills the session; stdin EOF or parent
  SIGKILL → child self-terminates.
- **M2-b1 DAP lifecycle** — initialize/launch/configurationDone (the
  launch response withheld), threads/stackTrace/continue,
  terminate/disconnect (including disconnect-while-running); the frozen
  launch schema (ktest, target, stopOnBranch, stopOnEntry, scope,
  timeout) with parent-side ktest validation. **Accept**: a full
  initialize→launch→run→exit session driven by the hand-written test
  client in socket mode; disconnect while running leaves no orphan
  inferior or child.
- **M2-b2 state surface** — scopes/variables with per-stop generation
  ids (resume invalidates; stale references error), source breakpoints.
  **Accept**: variables readable at a stop; stale-reference error after
  continue; breakpoints hit and reported.
- **M2-c branch tracing over DAP** — branch-site breakpoints,
  `stopOnBranch` (`stopped`, reason `breakpoint`), `replay.branchTrace`,
  end-of-session `TraceReport.render()` push via `output`, inferior
  output forwarding (droppable bounded queue with a truncation marker;
  control events never dropped). **Accept**: default M1 parity — the
  DAP report matches the CLI trace on the same fixture; `stopOnBranch`
  exposes live variables at each site.
- **M2-d stepping** — next/stepIn/stepOut/pause mapped to SBThread step
  operations (+ `SendAsyncInterrupt` for `pause`), completion reported
  as `stopped(reason: "step")`; no global interactive timeout.
  **Accept**: each step kind stops within the expected function on the
  fixture.
- **M2-e wrap-up** — declarative VS Code extension (zero-code), en/zh
  docs, DAP acceptance scenario (acceptance.sh style), chaos tests
  (child crash mid-session, malformed frames, client EOF mid-run), and
  an LLDB smoke path. **Accept**: the full loop runs from VS Code via
  `"debugServer"`; chaos tests fail cleanly with `terminated`, never a
  hang.

Known risks (planning pass 2026-10-05):

- **stdio hygiene** is the sharpest edge: any stray write to fd 1 from
  a library or LLDB internals corrupts framing; the redirect must run
  at the earliest entry, and LLDB log routing needs an explicit test.
- **Output ordering invariant** (`continue` response before its
  `stopped` event) is enforced structurally by the single-writer queue,
  but pinned by a dedicated byte-order test.
- **Backpressure split** (droppable `output` vs never-dropped control
  events) must be two explicit queue classes, not post-hoc filtering.
- **The one-shot CLI path stays frozen**; migrating it onto the service
  child is explicitly out of M2 scope (revisit at M3).

## branchTrace reshape, cuts and deferrals (2026-10-21, rulings during
## the cleanroom rebuild)

> Since 2026-10-16 the cleanroom rebuild re-implements the M2 service
> surface; the rebuild made the following substantive rulings on the
> original M2 slices (rationale in
> `local-cleanroom/server/design-notes.md`):

- **stepping deferred** (the original M2-d keeps its slot, to be
  implemented after the M2 wrap-up): the replay core = pre-scan
  instrumentation + bp + continue driving + report production;
  interactive stepping is the **user's debugging strategy after a
  breakpoint stop** and is not load-bearing for the current phase.
  DAP house rule "advertise only what exists" — unimplemented means
  unadvertised. The M2-a `SendAsyncInterrupt` spike results stay
  archived; resuming this work needs a long-running target fixture
  (get_sign exits too fast to expose a running window).
- **pause/terminate not implemented for now** (part of the original
  M2-b1): teardown = the single `disconnect` path, which also covers
  pause's rescue role; `terminate` replies `unknown` if it arrives.
  Revisit when a real need shows up.
- **`replay.branchTrace` command cut** (part of the original M2-c):
  the trace report became an automatic end-of-session summary pushed
  via `output` (purely passive, no new command); branchTrace is
  reshaped into **pre-scan guided replay** — the pre-scanned site set
  (`is_conditional`) is the target side's sole duty (the basis for
  placing breakpoints); the actual side is recorded verbatim at each
  breakpoint hit (pc/function/mnemonic/call stack/symbol values); the
  report contains the path line + per-node details (including
  consistency annotations of symbol values against the ktest solution
  samples).
- **Two-layer verification split**: value consistency (symbol values
  vs the ktest solution samples) is produced with the replay report;
  path consistency verification (modeling/solving) is part of M4
  trace analysis — an independent analysis layer consuming the report
  (see M4).

## M3 — LLVM IR / DWARF mapping

- Map path decisions back to IR locations and source lines via DWARF.

## M4 — Analysis overlays

- Coverage and taint overlays on the replay trace.
- **Path consistency verification** (added 2026-10-21, user ruling):
  consumes the branchTrace report for path modeling, solving and
  consistency checking — part of trace analysis. KLEE path constraints
  are consumed via the public `.kquery` format; if SMT solving would
  break the "thin bridge / zero-dependency" positioning, it becomes a
  separate library-dependency project following the ptrfuzz model
  (to be decided then). The comparison algebra pre-laid inside the
  replayer (`local-cleanroom/replay/branches.py` `equal`/`match_path`)
  is consumed by this layer and is not wired into the replay path.

## Ecosystem

[ptrfuzz](https://github.com/LiRvs-Miria/ptrfuzz) builds a coverage-guided
fuzzing platform on top of symrepl's debugger-side infrastructure — the
Clang to symrepl's LLVM. From ptrfuzz M2, symrepl becomes a library
dependency of ptrfuzz; the standard-format firewall (ADR-0001) applies to
both. ptrfuzz development starts after symrepl M2.

## Open questions

- ~~**Path-condition contract (blocks M1 implementation)**~~ —
  resolved 2026-10-03, frozen as [ADR-0002](adr/0002-m1-input-contract.md):
  the M1 input is exactly one `.ktest` (v2/v3) file; path decisions are
  recovered by dynamic branch tracing under LLDB. No KLEE sidecar is
  required; `.kquery` may be consumed as an optional diagnostic.
