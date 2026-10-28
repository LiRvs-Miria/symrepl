# symrepl

English | [简体中文](README.zh-CN.md)

Replay symbolic-execution counterexamples under a real debugger.

symrepl takes the artifacts produced by a symbolic executor (KLEE test
cases with their recorded inputs) and replays them under LLDB: setting
breakpoints along the recorded path, injecting inputs step by step, and
dumping program state at each decision point.

Symbolic execution answers *which input violates the property*.
symrepl answers the question every engineer asks next: *what exactly
happened, step by step?*

## Is this for you?

- You ran KLEE on a parser and it hands you `test000001.ktest` — a concrete
  input that trips an assertion 40 branches deep. Today you would open GDB,
  parse the binary `.ktest` format by hand, and rebuild the input. With
  symrepl: one command opens LLDB with the input injected and breakpoints
  set along the recorded path.
- Your symbolic-execution or fuzzing pipeline produced 200 unique crashes,
  and you must decide which are real. symrepl replays each under a debugger
  and dumps the path state, making triage scriptable and comparable.
- You teach or study symbolic execution and want to *see* how a path
  condition steers execution, branch by branch.

New to symbolic execution, or the terms above? Read the
[five-minute background](docs/en/background.md) — no prior KLEE experience
assumed.

## Why

KLEE ships `klee-replay`, a minimal GDB-based replayer. Once a
counterexample involves a nontrivial path condition, "run it and watch"
is not enough: you want breakpoints at the branch points that constrain
the path, and the debugger's view of the state aligned with the
execution path that produced the failure.

symrepl closes that gap. It is a thin, well-defined bridge between
symbolic execution and interactive debugging — not a new symbolic
engine.

## Scope and architecture rule

symrepl consumes **public, standard formats only**:

- KLEE `.ktest` files
- LLVM IR / bitcode
- DWARF debug info
- Debug Adapter Protocol (DAP)

Anything that only makes sense with knowledge of a particular private
codebase or dialect does not belong here (see
[docs/adr/0001-scope-and-ip-firewall.md](docs/en/adr/0001-scope-and-ip-firewall.md)).

## Ecosystem

[ptrfuzz](https://github.com/LiRvs-Miria/ptrfuzz) — a coverage-guided
fuzzing platform for targets without sanitizer runtimes, built on symrepl's
debugger-side infrastructure. ptrfuzz is to symrepl what Clang is to LLVM:
from ptrfuzz M2, symrepl becomes a library dependency.

## Status

Replay is delivered over the DAP server: ktest parsing, breakpoints
along the traced scope, ordered branch reports with per-site state, and
interactive stepping (`next`/`stepIn`/`stepOut`) in VS Code. With the
optional `ir` launch argument (M3, delivered 2026-10-24 —
[ADR-0004](docs/en/adr/0004-m3-path-narrowing.md)), report nodes carry
statically-computed fork/runtime annotations that narrow the trace to
the path decisions. The original M1 CLI was retired 2026-10-22; there
is no CLI — the DAP server is the product surface.

Next: M4 analysis overlays (coverage, input-dependence via differential
replay, path-consistency checking; ADR-0005/0006).  See the
[roadmap](docs/en/roadmap.md), the M2 architecture contract
([ADR-0003](docs/en/adr/0003-m2-dap-server-architecture.md)), and DAP
usage: [docs/en/dap.md](docs/en/dap.md).

## Requirements

- LLDB with Python scripting support
- Python >= 3.10

## License

Apache-2.0 WITH LLVM-exception — the same license as the LLVM project,
since symrepl sits squarely in that ecosystem.
