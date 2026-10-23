# ADR-0004 — M3: path narrowing — the KLEE-fork subset determined by LLVM IR symbolic analysis

Date: 2026-10-23　　Status: Accepted (user ruling 2026-10-23; supersedes the
"KLEE-fork subset mapping deferred to M3" note in the M2 limitations list)

English version is authoritative. 简体中文见
[zh-CN/adr/0004-m3-path-narrowing.md](../../zh-CN/adr/0004-m3-path-narrowing.md)。

## Context

`branchTrace` records every conditional branch the inferior executes within
its scope — a dynamic **superset**. Probing (phase1, P5, 2026-10-10) showed
the noise is structural, not incidental:

- **Replay-runtime-only code**: `kleeRuntest`'s `kTest_fromFile` reads,
  object-name `strcmp` loops — this code does not exist in KLEE's world at
  all (KLEE's `klee_make_symbolic` merely marks memory; nothing reads files).
- **Concrete-condition branches**: stack checks, loop bounds — executed in
  both worlds, but KLEE never forks on them.

Meanwhile the replay semantics — *"re-run the path KLEE sampled"* — is not
observable anywhere in the product: the report mixes path decisions with
runtime noise.

Two facts bound the solution space:

1. **KLEE's fork definition lives at the LLVM bitcode layer**: a `br i1`
   whose condition depends (transitively) on memory written by
   `klee_make_symbolic` forks — both successors are executed, and a `.ktest`
   is a concrete sample down one successor. Whether a branch is symbolic is
   a **program property**, statically recoverable from the `.ll` — a public
   `clang -emit-llvm` artifact obtainable without KLEE.
2. **ADR-0001 firewall**: KLEE itself never enters the replay path; only its
   public artifacts do. The `.ll` satisfies this; installing KLEE is not
   required for this layer.

The layered capability model (user ruling, 2026-10-23) assigns scope:

| layer | capability | user gets | status |
|---|---|---|---|
| M1+M2 | record & replay the actual path | the trace superset | shipped |
| **M3** | **narrow path possibilities via LLVM IR** | **a clear path** | this ADR |
| M4 | full symbolic ↔ actual verification (`.kquery`) | a fidelity verdict | ADR-0005 |

Each earlier layer is a necessary precondition for the next; each later
layer is optional.

## Decision

1. **Scope**: M3 = static symbolic analysis over the `.ll` (method 1) +
   manually-written acceptance ground truth (method 3). The `.kquery` route
   (method 2) belongs entirely to M4 (ADR-0005).
2. **Three-way classification** (the substance of the narrowing):
   - **fork** — the branch condition is in the symbolic closure; KLEE forked
     here (the `.ktest` sampled one direction).
   - **noise-A (runtime-only)** — the site's function is not in the `.ll`
     module: code absent from KLEE's world. The judgment emerges naturally:
     `get_sign.ll` does not contain `intrinsics.c`.
   - **noise-B (concrete)** — the condition is outside the symbolic closure.
3. **Runtime surface**: an optional launch argument `ir` (path to the `.ll`).
   Absent = today's behavior, zero added frames (the M1+M2 layer); present =
   the trace report gains an annotation layer: each node carries
   `kind: "fork" | "runtime"` plus a `detail` field carrying the evidence
   (`symbolic-branch` / `function-not-in-module` / `concrete-condition` —
   the A/B distinction lives in `detail`, `kind` stays binary). A malformed
   `.ll` → launch-time `Rejected` (the ktest-refused fail-fast family).
4. **Mechanism**: a self-written lightweight `.ll` parser + dataflow —
   `klee_make_symbolic` call sites mark memory; propagation through
   `load`/`icmp` and direct-call argument→formal binding; `br` conditions
   are tested for closure membership. New pure module in `replay/` (working
   name `pathmark`), pure-core directly testable (house rule). Line-level
   join via `.ll` `!DILocation` ↔ native DWARF line/function — **two
   compilations, so instruction-level join is out of scope by construction**.
5. **Decision outcome (taken/not-taken) is not recorded in M3 v1** — the
   site sequence implies it; M4 may demand it explicitly (decide when
   encountered).
6. **Acceptance ground truth** (manual, written first): for `get_sign` —
   test1 (a=0, exit 0): forks = [L11]; test2 (a=0x01010101, exit 1) and
   test3 (a=0x80000000, exit 255): forks = [L11, L13]; everything in
   `intrinsics.c` is noise-A; the entry stop is not a decision.

## Consequences

- The report reader sees the collapsed path; M4 receives its input shape.
- Zero runtime dependencies preserved — no KLEE, no LLVM toolchain at
  runtime; the `.ll` enters as a user-supplied public artifact.
- The lightweight dataflow has known limits (pointer aliasing, indirect
  calls, array propagation) — extend when actually encountered, not before.
- The annotation is a derived layer: the 零处理 red line is untouched
  (recording stays as-observed; annotation is computed from the `.ll`).
- M3 answers a program property (static); M4 will answer an execution fact
  (dynamic) — the two layers cross-validate each other (same structure as
  the multi-net house rule).
