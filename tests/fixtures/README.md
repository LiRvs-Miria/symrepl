# Real-KLEE `.ktest` fixtures

Committed byte-for-byte artifacts produced by a real KLEE run. They pin
the parser (`symrepl.ktest`) against KLEE itself, closing the residual
risk of misreading the format sources in the same way on both sides.

## Provenance

- KLEE **3.1** (build revision `fe22b90764887ab69c20b1eccd773d47a8378b95`),
  LLVM 13.0.1, STP + MiniSat — official `klee/klee:3.1` Docker image,
  digest `sha256:05e56e17d88ed02f2872ec2ec78e7c4282d0328dc203d0a5f05cc1d458688d8f`.
- Generated 2026-10-03 by running `sh gen.sh` inside the image (see
  `gen.sh`); sources under `src/`.

## Files

- `get_sign_test00000{1,2,3}.ktest` — KLEE tutorial `get_sign` (also the
  M1 acceptance target): one symbolic object `a` (sizeof(int)), three
  paths (x == 0 / x < 0 / x > 0).
- `two_objs_test00000{1,2,3}.ktest` — two symbolic objects (`arr`[4],
  `b`[2]) in one file, exercising multi-object parsing.

## Notes

- KLEE 3.1 writes version **3** files with `sym_argvs == sym_argv_len
  == 0` and the `.ll` basename as argv[0]; `dump(parse(f)) == f` holds
  byte-exactly for all of them (no trailing bytes).
- Legacy v1 (`BOUT\n`) and v2 files are not emitted by modern KLEE; their
  layout stays covered by the hand-pinned synthetic tests in
  `tests/test_ktest.py` (ADR-0002).

To regenerate: update the image tag above if it changes, then

    docker run --rm -v "$PWD":/work -w /work klee/klee:3.1 sh gen.sh
