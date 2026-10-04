#!/bin/sh
# M1 acceptance (docs/en/roadmap.md): the full loop on the KLEE tutorial
# example — `klee` run -> symrepl -> breakpoints hit in order -> state
# report.
#
# Usage (from anywhere):
#   sh scripts/acceptance.sh
#   KLEE_IMAGE=klee/klee:3.1 sh scripts/acceptance.sh          (default)
#   KLEE_IMAGE=docker.1ms.run/klee/klee:3.1 sh scripts/acceptance.sh
#
# Only the .ktest generation is containerized (official KLEE image);
# the symrepl replay runs on the host and needs host clang/clang++ and
# LLDB with Python scripting (the vendored kleeRuntest runtime from
# tests/fixtures/klee-runtime is compiled fresh — no KLEE install).
set -eu

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
KLEE_IMAGE=${KLEE_IMAGE:-klee/klee:3.1}
PY=${PYTHON:-python3}
WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT
mkdir -p "$WORK/ktest" "$WORK/build"

fail() { printf 'acceptance: FAIL: %s\n' "$1" >&2; exit 1; }
pass() { printf 'acceptance: ok: %s\n' "$1"; }

# --- 1. klee run: generate fresh .ktest files inside the official image --
docker run --rm \
  -v "$ROOT/tests/fixtures/src":/src:ro \
  -v "$WORK/ktest":/out \
  "$KLEE_IMAGE" /bin/sh -c '
    set -eu
    cd "$(mktemp -d)"
    clang -I /home/klee/klee_src/include \
      -emit-llvm -c -g -O0 -Xclang -disable-O0-optnone \
      -o get_sign.ll /src/get_sign.c
    klee get_sign.ll
    cp klee-out-*/test*.ktest /out/
  '
KTEST_COUNT=$(ls "$WORK/ktest"/*.ktest 2>/dev/null | wc -l)
[ "$KTEST_COUNT" -eq 3 ] || fail "expected 3 fresh ktests, got $KTEST_COUNT"
pass "klee ($KLEE_IMAGE) generated 3 fresh test cases"

# --- 2. host build: replay target linked against the vendored runtime ---
CLANG=${CLANG:-clang}
CLANGXX=${CLANGXX:-clang++}
INC="$ROOT/tests/fixtures/klee-runtime/include"
"$CLANG" -O0 -g -c "$ROOT/tests/fixtures/klee-runtime/intrinsics.c" \
  -I"$INC" -o "$WORK/build/intrinsics.o"
"$CLANGXX" -O0 -g -std=c++17 -c "$ROOT/tests/fixtures/klee-runtime/KTest.cpp" \
  -I"$INC" -o "$WORK/build/KTest.o"
"$CLANG" -O0 -g "$ROOT/tests/fixtures/src/get_sign.c" -I"$INC" \
  "$WORK/build/intrinsics.o" "$WORK/build/KTest.o" -o "$WORK/build/get_sign"
pass "built replay target linked with vendored kleeRuntest runtime"

# --- 3. symrepl: replay every fresh ktest, assert ordered trace ---------
PYTHONPATH="$ROOT/src${PYTHONPATH:+:$PYTHONPATH}"
export PYTHONPATH

CODES=""
for KT in "$WORK/ktest"/*.ktest; do
  set +e
  OUT=$("$PY" -m symrepl.cli "$KT" --target "$WORK/build/get_sign" \
        --scope get_sign 2>&1)
  CODE=$?
  set -e
  printf -- '--- %s -> exit %d\n' "$(basename "$KT")" "$CODE"
  printf '%s\n' "$OUT" | sed 's/^/    /'
  CODES="$CODES $CODE"
  printf '%s\n' "$OUT" | grep -q 'branch trace:' \
    || fail "$(basename "$KT"): no branch trace in report"
  printf '%s\n' "$OUT" | grep -q '<get_sign>' \
    || fail "$(basename "$KT"): no get_sign breakpoint hit"
  printf '%s\n' "$OUT" | grep -q '^  #1 ' \
    || fail "$(basename "$KT"): no ordered hit record"
done

# The three KLEE paths replay to three distinct exit statuses (the
# tutorial's return values: x == 0 -> 0, x > 0 -> 1, x < 0 -> 255).
for c in 0 1 255; do
  case " $CODES " in *" $c "*) ;; *) fail "missing exit status $c (got:$CODES)";; esac
done
pass "all 3 paths replayed with distinct statuses (0/1/255) and ordered traces"

printf 'acceptance: PASS: M1 full loop (klee -> symrepl -> ordered trace -> state report)\n'
