# Test targets and fixture generation

Everything third-party lives in [`third_party/klee/`](../../third_party/klee/README.md)
(the vendored kleeRuntest runtime and the committed `.ktest` fixtures —
provenance, sha256 table, and image digest there). This directory holds
only what we wrote:

- `src/get_sign.c` — KLEE tutorial example; the replay/trace acceptance
  target (built against the vendored runtime by `tests/conftest.py` and
  `scripts/acceptance.sh`).
- `src/two_objs.c` — minimal target producing a multi-object `.ktest`
  (two symbolic objects: `arr`[4], `b`[2]).
- `gen.sh` — regenerates the `.ktest` fixtures inside the official KLEE
  image:

  ```
  cd tests/fixtures
  docker run --rm -v "$PWD":/work -w /work klee/klee:3.1 sh gen.sh
  ```

  The fresh `test*.ktest` files land in this directory; move them to
  `third_party/klee/ktest/` (and update the sha256 table in
  `third_party/klee/README.md`) if they change.
