# ADR-0005 — M4: path consistency verification — the symbolic path condition vs the replayed decisions

Date: 2026-10-23　　Status: Accepted (deferred execution; the layering was
ruled 2026-10-21 with the branchTrace reshape and scoped 2026-10-23 — the
`.kquery` route moved here from M3)

English version is authoritative. 简体中文见
[zh-CN/adr/0005-m4-path-consistency.md](../../zh-CN/adr/0005-m4-path-consistency.md)。

## Context

M3 (ADR-0004) narrows the trace by a **program property** — which branches
are symbolic in the `.ll`. That answers "this branch *could* be a fork"; it
does not answer whether the replayed decisions **match the path KLEE
actually constrained**. That is an **execution fact**, recorded by KLEE
itself: for each explored path it emits a path-condition formula in the
public **KQuery** format (`.kquery`).

Three prior rulings constrain this layer:

1. **Layer model** (2026-10-23): M1+M2 record & replay → M3 narrow via IR →
   M4 full symbolic ↔ actual verification. The earlier layer is the later
   layer's necessary precondition; the later layer is optional. M4 consumes
   M3's narrowed decision sequence as its input shape.
2. **Thin-bridge positioning** (2026-10-21, user ruling "方案 A"): the
   consistency checker is a standalone tool consuming the branchTrace
   report — KLEE path constraints are consumed via the **public `.kquery`
   format**; if SMT solving would break the thin-bridge / zero-dependency
   positioning, it becomes a separate library-dependency project following
   the ptrfuzz model (decided when encountered).
3. **E2 algebra pre-laid** (2026-10-21): `match_path`/`equal` in
   `replay/branches.py` compare (observed, target) site lists element-wise;
   they are **not wired into the replay path** and await their first
   consumer here.

## Decision

1. **M4 = an independent verification tool** (thin bridge). Inputs: the
   branchTrace report narrowed by M3 + the `.kquery` for the same ktest +
   the ktest itself. It is not part of the DAP server's runtime surface.
2. **`.kquery` is consumed via the public format only** (self-written
   parser). **SMT solving is not in scope by default**: checks limited to
   structural/parseable facts of the formula (constraint count, per-decision
   correspondence, variable/object binding) run without a solver; anything
   requiring a real solve (novel inputs, implication proofs) triggers the
   ptrfuzz-model split per ruling 2 above.
3. **The verification verdict** covers: every replay fork decision has a
   corresponding constraint on the KLEE side (no unexplained forks), the
   constraint sequence is consistent with the replay order, and mismatches
   are reported as values, not exceptions (the Expect-algebra house rule).
4. **E2 `match_path`/`equal` become first consumers here** — wired into this
   tool, not into the replay path (2026-10-21 ruling stands).
5. **Execution is deferred until M3 lands.** Producing `.kquery` artifacts
   requires running KLEE out-of-link (installation is a toolchain concern,
   not a symrepl dependency); artifacts enter via the public format.
6. **Decision directions (taken/not-taken)**: M3 v1 does not record them; if
   formula-level correspondence requires them, the direction inference from
   site ordering (native pc monotonicity) lands in M3's annotation at that
   point (decide when encountered).

## Consequences

- The fidelity claim — *"the replay follows the path KLEE sampled"* —
  becomes **checkable**, not asserted: for the paper (§7 artifact), the
  defensive story upgrades from "a replay debugger" to "a replay debugger
  whose path fidelity is verifiable".
- Two layers cross-validate: M3 states a program property (static), M4
  checks an execution fact (dynamic) — same structure as the multi-net
  house rule.
- Zero-dependency is preserved at the default depth; the solver escape
  hatch is pre-priced (ptrfuzz model) instead of silently growing a
  dependency.
- The tool is optional and offline: sessions that never invoke it behave
  exactly as ADR-0004 leaves them.
