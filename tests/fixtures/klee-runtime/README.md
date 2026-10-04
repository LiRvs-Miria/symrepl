# Vendored kleeRuntest runtime sources

The reference implementation of the `.ktest` replay harness contract
(KLEE's official replay path, ADR-0002), vendored as **source** and
compiled by the test suite with the local `clang` — no binary blobs, no
KLEE installation required.

## Files

| File | Upstream (klee/klee, tag `3.1`) | sha256 |
|---|---|---|
| `intrinsics.c` | `runtime/Runtest/intrinsics.c` | `a5dfbde8627602a64225c735ce74fc2fb62dccd480de52b088714fbe1fac1ba3` |
| `KTest.cpp` | `lib/Basic/KTest.cpp` | `23c89d94bde19dd257e48b4ea63dc9edccfdcc660c558ddd70a3419986550426` |
| `include/klee/klee.h` | `include/klee/klee.h` | `456285c131c6f6e4c324a3fa97432752dfe04b4f6765ad0ed3957a9fa908bb9b` |
| `include/klee/ADT/KTest.h` | `include/klee/ADT/KTest.h` | `4e525b7b92b825855fc64fd134799804851546800b1987e7b66127a0d2bcb9aa` |

Extracted 2026-10-04 from the official `klee/klee:3.1` Docker image
(digest `sha256:05e56e17d88ed02f2872ec2ec78e7c4282d0328dc203d0a5f05cc1d458688d8f`,
same image as the `.ktest` fixtures in the parent directory) at
`/tmp/klee_src` (source tree) and `/home/klee/klee_src/include` (headers).

## License

All files are © the KLEE project, distributed under the University of
Illinois Open Source License (NCSA) — license notices retained in each
file. The notices are compatible with this repository's Apache-2.0 WITH
LLVM-exception licensing.

## How the tests use this

`tests/test_replay.py` compiles `intrinsics.c` + `KTest.cpp` into the
kleeRuntest runtime, links it against a target from `src/` (e.g.
`get_sign.c`), and replays the parent directory's `.ktest` fixtures
under LLDB via `symrepl.replay`. Nothing here is imported at runtime by
the `symrepl` package itself.
