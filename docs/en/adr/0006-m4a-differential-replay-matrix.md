# ADR-0006 — M4-a: the differential replay matrix — foundation of the taint overlay

Date: 2026-10-26　　Status: Accepted (contract settled 2026-10-26; first
brick of M4, moved from #16/M3-c — issue #19)

English version is authoritative. 简体中文见
[zh-CN/adr/0006-m4a-differential-replay-matrix.md](../../zh-CN/adr/0006-m4a-differential-replay-matrix.md)。

## Context

M4's three pillars (2026-10-24) are: coverage overlay, taint overlay, and
path-consistency verification (ADR-0005). The differential replay matrix
opens the **taint pillar**: perturb the symbolic objects of a `.ktest`,
replay N variants, and collect a per-site **outcome set**.

Two prior rulings constrain this layer:

1. **Layer model** (ADR-0005): M4 is an offline, independent tool — outside
   the replay loop, not part of the DAP server's runtime surface. The
   matrix is engine-side and consumes the one-shot subprocess replay chain.
2. **Static half already in place** (ADR-0004): `pathmark.classify` states
   a program property — *this site could be a fork*. A static-only claim
   cannot say a site **is** input-dependent; a perturbation supplies the
   execution fact. The two halves cross-validate, the same structure as the
   multi-net house rule.

## Decision

1. **Perturbation strategy: bit-flip baseline** (user ruling, 2026-10-26).
   Attribution lands at bit granularity, which is what "locate the driving
   bytes" requires; large objects tier by size with **deterministic**
   down-sampling (same input, same plan — no randomness); exceeding the
   replay cap degrades to an explicit "not analyzed" mark, never a silent
   truncation.
2. **"Direction" v1 = hit-set differential** (user ruling, 2026-10-26). Per
   variant, record the set of hit `(function, line)` sites plus the exit
   code; a site whose hit-membership varies across variants is
   **input-dependent**. ADR-0004 ruling 5 (no taken/not-taken recording)
   stands; `server/` keeps a zero diff.
3. **The ktest write side is admitted** (user ruling, 2026-10-26; explicitly
   reverses the E1 ruling "read-only, no dump"). `dump` is a pure function
   `KTest -> bytes` following the frozen ADR-0002 layout, writing the single
   current magic (`KTEST`; `BOUT\n` remains read-only legacy), pinned by the
   round trip `parse(dump(k)) == k`. Variants must land as real `.ktest`
   files — the runtime reads only real files (`KTEST_FILE`).
4. **Confidence is honest by construction.** Flipping is *necessary, not
   sufficient*: a constant outcome set is labeled "no dependency seen within
   this observation", never "input-independent"; capped analyses are labeled
   as capped.
5. **Cross-validation, three arms** (static `pathmark` × dynamic flips):
   static fork ∧ flip ⇒ strong assertion; static concrete ∧ flip ⇒
   propagation-boundary alarm (the runtime self-check mirror); static fork ∧
   constant ⇒ insufficient coverage. Every site falls in exactly one arm.
6. **One subprocess per variant.** The driver follows the one-child-per-
   session house rule (`_created` gate); the process cost is small next to
   an LLDB load. M1+M3 form regressions must not break.
7. **Acceptance** (issue #19): get_sign sites {11, 13} (`x==0` / `x<0`) with
   all three baselines (exit 0/1/255) reachable by flips and the driving
   bytes located; the two_objs mismatch branch triggered independently by
   flipping either object; M1+M3 regressions green.
8. **IR precision is an optional parameter** (user ruling, 2026-10-26). The
   matrix runs pure-dynamic without the `.ll` and stands as its own report;
   providing it enables the static cross-validation arms of Decision 5.
   House-rule mirror of the launch `ir` parameter: **absence adds zero**.
   The report states whether cross-validation ran.
9. **Always-evaluated sites are honestly blind** (user ruling, 2026-10-26).
   The hit-set differential structurally cannot see sites that execute on
   every path (get_sign L11, the first `if`); such sites are reported "no
   dependency seen within this observation" plus the insufficient-coverage
   arm — the per-site verdict does not absorb program-level evidence.
   Acceptance reading fixed accordingly: "three sites" = the three exits
   (0/1/255) all reachable by flips; fork site L13 is marked
   input-dependent, L11 walks the blind-spot arm.

## Consequences

- The taint overlay gets its foundation: site × input-byte dependency is
  measurable, and the ptrfuzz ecosystem gains a library-level dependency
  (packaging deferred until demand is proven, the house rule).
- E1's frozen read-only scope gains a write half with its own round-trip
  net; the ADR-0002 byte layout becomes write-covered as well.
- `pathmark` gets its first runtime oracle: propagation-boundary alarms are
  live evidence of closure limits, not test fixtures.
