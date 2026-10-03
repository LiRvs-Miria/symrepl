# Background — five minutes

English | [简体中文](../zh-CN/background.md)

This page assumes nothing. If you already run KLEE daily, skim the
glossary and jump back to the [README](../../README.md).

## What symbolic execution gives you

A symbolic executor (KLEE) explores a program's paths *symbolically*:
instead of feeding it one concrete input, it treats inputs as variables
and reasons about all the paths their values could take. When it finds a
path that violates a property — an assertion failure, a memory error —
it emits a **test case**: concrete input bytes that are guaranteed to
reach that failure. In KLEE this lands on disk as a `.ktest` file, one
per violating path.

So after a KLEE run you might be holding 200 `.ktest` files. Each one is
an answer to "which input breaks it?" — and the beginning of a much
harder question.

## The gap after the counterexample

"Which input violates the property" is not what an engineer needs next.
They need:

- **Triage** — of those 200 counterexamples, which are real bugs, which
  are harness-modeling mistakes, which are duplicates of the same root
  cause?
- **Understanding** — for the real one: why did execution take this path?
  What were the variables at each branch? Where exactly does the
  invariant break?

Today this is done by hand: open GDB, parse the binary `.ktest` format
yourself, reconstruct the input file the harness expects, re-type the
branch conditions as breakpoints. Every counterexample, from scratch.
It is slow, error-prone, and does not scale to triage across hundreds of
findings.

## What symrepl does

One command:

```
symrepl test000042.ktest --target ./parser
```

- parses the `.ktest` file and injects the recorded input per the
  harness contract (argv / stdin / files),
- opens an LLDB session on the target,
- sets breakpoints along the branches that constrain the recorded path,
- dumps program state at each decision point, so the path condition and
  the debugger's view of the machine stay aligned.

You stay in a normal debugger session — symrepl just makes the
counterexample navigable.

## Who is this for

- **Security engineers** triaging output from symbolic execution and
  fuzzing pipelines (the gap between "found" and "confirmed" is the
  expensive part of vulnerability analysis).
- **Developers** whose CI runs KLEE and who receive `.ktest` failures
  they need to reproduce locally.
- **Teachers and researchers** demonstrating how path conditions steer
  execution, one branch at a time.

## Glossary

| Term | Meaning |
|---|---|
| counterexample (反例) | The concrete input a symbolic executor found that violates the checked property |
| path condition (路径条件) | The conjunction of branch decisions that constrains one specific path |
| `.ktest` | KLEE's binary test-case format: one file per counterexample, holding one concrete value per symbolic variable |
| triage (分诊) | Deciding whether a reported failure is a real, reachable bug — and which ones share a root cause |
| harness (测试皮) | The small adapter that feeds a target's inputs from files/stdin so tools like KLEE can drive it |
