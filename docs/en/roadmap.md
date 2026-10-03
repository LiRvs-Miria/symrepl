# Roadmap

English | [简体中文](../zh-CN/roadmap.md)

> The English version is authoritative; both are updated in the same commit.

Milestones are deliberately small: each is 2–3 weekend blocks.

## M0 — Scaffold (done)

- Repository, license (Apache-2.0 WITH LLVM-exception), CI, and the
  scope/IP firewall documented in [ADR-0001](adr/0001-scope-and-ip-firewall.md).

## M1 — ktest replay (MVP)

Input contract frozen: [ADR-0002](adr/0002-m1-input-contract.md).

- `.ktest` parser (v2/v3 — identical layout; `BOUT\n` tolerated on read,
  version > 3 rejected), pure Python, zero dependencies, byte layout
  verified against the KLEE sources.
- Replay driver: launch the target (linked with `-lkleeRuntest`) under
  LLDB via its Python API and inject `KTEST_FILE` via `SBLaunchInfo`,
  with per-object name/size validated up front — the harness contract
  mirrors KLEE's official replay path.
- Branch tracing: scripted breakpoints on the conditional branches of
  executed ranges record PC/outcome in order; state dumps at each hit.
  Precise mapping to the KLEE-fork "constraining" subset is deferred
  to M3 (IR/DWARF).
- Optional diagnostic (never required): `.kquery` pretty-print /
  validation of the logical path condition.
- Acceptance: on a KLEE tutorial example (e.g. `get_sign`), the full
  loop works — `klee` run → symrepl → breakpoints hit in order → state
  report.

## M2 — DAP server mode

- Expose replay sessions over the Debug Adapter Protocol; replay
  becomes usable from VS Code and any DAP client.

## M3 — LLVM IR / DWARF mapping

- Map path decisions back to IR locations and source lines via DWARF.

## M4 — Analysis overlays

- Coverage and taint overlays on the replay trace.

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
