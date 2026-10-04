# Vendored KLEE artifacts (`klee/klee:3.1`)

Everything in this directory comes byte-for-byte from the official
KLEE 3.1 Docker image — the same image that generated the `.ktest`
fixtures. Nothing here is written by hand; the only maintenance is
re-extracting if the pinned image changes. `tests/` and
`scripts/acceptance.sh` reference these artifacts but own none of them.

- `runtime/` — the kleeRuntest replay-runtime sources and public
  headers (the `.ktest` harness contract of ADR-0002), compiled fresh
  by `tests/conftest.py` and `scripts/acceptance.sh`. No KLEE install
  is required.
- `ktest/` — six `.ktest` files produced by a real KLEE run
  (regeneration: `tests/fixtures/gen.sh`), pinning the
  `symrepl.ktest` parser against KLEE itself.

## Provenance

- KLEE **3.1** (build revision `fe22b90764887ab69c20b1eccd773d47a8378b95`),
  LLVM 13.0.1, STP + MiniSat — official `klee/klee:3.1` Docker image,
  digest `sha256:05e56e17d88ed02f2872ec2ec78e7c4282d0328dc203d0a5f05cc1d458688d8f`.
- `runtime/` extracted 2026-10-04 via `docker cp` from the image's
  source tree (`/tmp/klee_src`) and headers
  (`/home/klee/klee_src/include`).
- `ktest/` generated 2026-10-03 by running `tests/fixtures/gen.sh`
  inside the image.

## Files

`runtime/`:

| File | Upstream (`klee/klee`, tag `3.1`) | sha256 |
|---|---|---|
| `intrinsics.c` | `runtime/Runtest/intrinsics.c` | `a5dfbde8627602a64225c735ce74fc2fb62dccd480de52b088714fbe1fac1ba3` |
| `KTest.cpp` | `lib/Basic/KTest.cpp` | `23c89d94bde19dd257e48b4ea63dc9edccfdcc660c558ddd70a3419986550426` |
| `include/klee/klee.h` | `include/klee/klee.h` | `456285c131c6f6e4c324a3fa97432752dfe04b4f6765ad0ed3957a9fa908bb9b` |
| `include/klee/ADT/KTest.h` | `include/klee/ADT/KTest.h` | `4e525b7b92b825855fc64fd134799804851546800b1987e7b66127a0d2bcb9aa` |

`ktest/`:

| File | sha256 |
|---|---|
| `get_sign_test000001.ktest` | `a1be91e1cb7bae21a14c7102350f9ca3288156fb5076fb281525277a920c9a32` |
| `get_sign_test000002.ktest` | `9f280b5e617c93cfa1df3b44588ea2ffc9f5e54b2c235b95233edd4860a673b9` |
| `get_sign_test000003.ktest` | `9ca0e930643d7c3a5ab0a29df3381fa00a80f5d036310d93237ce23a79bd9d54` |
| `two_objs_test000001.ktest` | `f6c77496e38e73e6d3a51077d7b2235c7093490428b878efd553b8994f7f9b37` |
| `two_objs_test000002.ktest` | `e3995b4a18c67470d9804f53357aa0669d064272e1bdbd788c0939b458a62f1b` |
| `two_objs_test000003.ktest` | `94ee9cd8ad53259961e12cb796cdb6920c10c2b6d9be16bd84d6590e8a8793fb` |

## Runtime semantics worth knowing

- `KTEST_FILE` must be set; without it the runtime prompts on stdout
  and **blocks on stdin**.
- Contract violations print `KLEE_RUN_TEST_ERROR: ...` to stderr and
  end in `exit(1)` (non-fatal continuation via
  `KLEE_RUN_TEST_ERRORS_NON_FATAL`).
- Objects are consumed strictly in `klee_make_symbolic` call order;
  names are checked but do not index.

## License

All files are © the KLEE project, distributed under the University of
Illinois Open Source License (NCSA) — license notices retained in each
file. The notices are compatible with this repository's Apache-2.0
WITH LLVM-exception licensing.
