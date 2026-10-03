# ADR-0001 — Scope and IP firewall

Date: 2026-09-30　　Status: Accepted

English version is authoritative. 简体中文见
[zh-CN/adr/0001-scope-and-ip-firewall.md](../../zh-CN/adr/0001-scope-and-ip-firewall.md)。

## Context

The author's day job is on an industrial compiler team (closed-source:
in-house MLIR dialects, lowering pipelines, and the accompanying debug
systems). This repository is a personal open-source project. It is
related to that work in **domain** (program analysis / symbolic
execution / debuggers), but must **not** be related in terms of its
code, dialects, or pipeline knowledge.

## Decision

1. **Format firewall (architectural, permanent constraint)**: symrepl
   consumes public standard formats only — KLEE `.ktest`, LLVM
   IR/bitcode, DWARF, DAP. The company stack exists only outside the
   repository boundary, as a *producer* of these formats. Any code that
   only makes sense with knowledge of a particular private codebase or
   dialect does not belong here.
2. **Not a symbolic engine**: symrepl is a thin bridge between symbolic
   execution and interactive debugging. KLEE does the counterexample
   search; symrepl turns a counterexample into a debuggable session —
   automatic breakpoints, step-by-step input injection, state export.
3. **MVP uses the LLDB Python API + pure Python**: fast iteration, and
   a direct reuse of the author's DAP/lldb experience; performance-
   critical parts (M3+ if ever needed) may drop to C++ later.
4. **License: Apache-2.0 WITH LLVM-exception**, consistent with the
   LLVM ecosystem.

## Consequences

- The repository carries zero IP risk from its first line; day-job IP
  negotiations stay decoupled from this repo (see personal plan §5.1,
  risk 1).
- Features are constrained by the "standard formats" rule: if a
  KLEE-side path condition cannot be recovered from standard
  artifacts, it is extended via a **publicly specified sidecar format**
  (see the roadmap's open questions), never by introducing private
  information.
- The repo's growth narrative matches the author's public identity
  line: positioning violation (symrepl) → construction guarantee
  (analysis framework) → machine proof (translation validation).
