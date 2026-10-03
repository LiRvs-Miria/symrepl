# ADR-0002 — M1 input contract frozen: single `.ktest` + dynamic branch tracing

Date: 2026-10-03　　Status: Accepted

English version is authoritative. 简体中文见
[zh-CN/adr/0002-m1-input-contract.md](../../zh-CN/adr/0002-m1-input-contract.md)。

## Context

An [open question](../../zh-CN/roadmap.md) blocked M1 implementation:
how much of the path condition is recoverable from `.ktest` alone, vs.
requiring KLEE to emit a sidecar decision log? Findings from research
against klee/klee master sources and docs:

- `.ktest` (v2/v3) contains only, per symbolic object,
  `{name, numBytes, concrete bytes}` plus argv metadata — no path or
  branch decisions whatsoever. v2 and v3 have identical layout (v3 only
  changed the magic from `BOUT\n` to `KTEST`); one parser code path
  suffices.
- No existing KLEE artifact maps decisions to branch locations:
  `.kquery`/`.cvc`/`.smt2` hold logical constraints with no code
  locations, and one-side-resolved branches add no constraint;
  `.path`/`.sym.path` (the only ordered decision logs) are officially
  unmaintained, have a known bug where different paths produce
  identical files, and use a host-endian internal format.
- KLEE's official replay contract is the `-lkleeRuntest` runtime:
  bytes are fed to `klee_make_symbolic` calls in object order, with
  name/size checks (`KTEST_FILE` environment variable) — the path the
  tutorials document.
- The LLDB Python API has every needed primitive: `SBLaunchInfo` for
  argv/env/stdin file actions; `ReadInstructions` + `DoesBranch()` to
  enumerate conditional branches; address breakpoints with scripted
  callbacks that record and return `False` to auto-continue.

## Decision

Freeze the M1 input contract as "single `.ktest` + dynamic branch
tracing":

1. **The only required input is one `.ktest` (v2/v3) file.** The parser
   accepts v2/v3, tolerates `BOUT\n` (v1) on read, and rejects version
   > 3 — mirroring KLEE's `kTest_fromFile` semantics, so a future v4 is
   never silently mis-parsed.
2. **Path decisions are recovered by dynamic branch tracing of the real
   execution under LLDB**: pre-scan executed ranges for conditional
   branches (`DoesBranch()` + per-architecture mnemonic filtering), set
   a breakpoint per site, record PC/mnemonic/outcome in order from the
   scripted callback.
3. **The harness contract mirrors KLEE's official replay path**: target
   linked with `-lkleeRuntest`; symrepl injects `KTEST_FILE` and argv
   via `SBLaunchInfo`, with stdin available via file actions; object
   names/sizes are validated up front against the parser (matching
   `KLEE_RUN_TEST_ERROR` semantics).
4. **`.kquery` is an optional, never-required diagnostic** (the logical
   view of the path condition: pretty-print / validation).
5. **No custom sidecar decision log is introduced for M1.** If one is
   ever needed, its format is publicly specified in this repository per
   the ADR-0001 firewall.
6. **Scope of "path-condition-aware breakpoints" for M1**: record all
   conditional branches on the path (tracing scoped to functions or
   modules), with the report distinguishing concrete-only branches; the
   precise mapping of traced branches to the KLEE-fork "constraining"
   subset is **deferred to M3** (IR/DWARF).

## Consequences

- Works out of the box with stock KLEE: ktests are guaranteed to exist
  (`-write-ktests` defaults to true); zero change to user workflow.
- Fully compliant with the standard-format firewall (ADR-0001): the
  only consumed input format is the public `.ktest`.
- M1 limitations (recorded honestly): dynamic tracing is a superset of
  path decisions (includes input-independent concrete branches); per-
  address breakpoints cost performance on large targets — mitigated in
  M1 by function/module scoping.
- By-product: a binary-level decision trace in true execution order —
  strictly better data than any sidecar could provide; M3's IR/DWARF
  mapping can layer directly on top of it.
