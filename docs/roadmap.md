# Roadmap

English | [简体中文](roadmap.zh-CN.md)

> The English version is authoritative; both are updated in the same commit.

Milestones are deliberately small: each is 2–3 weekend blocks.

## M0 — Scaffold (done)

- Repository, license (Apache-2.0 WITH LLVM-exception), CI, and the
  scope/IP firewall documented in [ADR-0001](adr/0001-scope-and-ip-firewall.md).

## M1 — ktest replay (MVP)

- `.ktest` parser (versions 2/3), pure Python, zero dependencies,
  byte layout verified against the KLEE sources.
- Replay driver: launch the target under LLDB via its Python API and
  inject the recorded inputs per the harness contract.
- Path-condition-aware breakpoints: break at the branch points that
  constrain the recorded path, dump variables/registers at each hit.
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

- **Path-condition contract (blocks M1 implementation):** how much of
  the path condition is recoverable from `.ktest` objects alone, vs.
  requiring KLEE to also emit a sidecar decision log? Investigate
  KLEE's output options, then freeze the M1 input contract. If a
  sidecar is needed, its format must be publicly specified in this
  repository (see ADR-0001).
