#!/bin/sh
# Regenerate the real-KLEE `.ktest` fixtures committed in
# third_party/klee/ktest/.
#
# Usage (from tests/fixtures/, with Docker):
#   docker run --rm -v "$PWD":/work -w /work klee/klee:3.1 sh gen.sh
#
# The fresh test*.ktest files land in this directory; move them to
# third_party/klee/ktest/ (updating the sha256 table in
# third_party/klee/README.md) if they change.
#
# The image matches the KLEE version recorded in
# third_party/klee/README.md; regenerate and update that note whenever
# the image tag changes.
set -eu

run_case() {
  name=$1
  src=$2
  dir=$(mktemp -d)
  (
    cd "$dir"
    clang -I /home/klee/klee_src/include \
      -emit-llvm -c -g -O0 -Xclang -disable-O0-optnone \
      -o "$name.ll" "/work/src/$src"
    klee "$name.ll"
    for f in klee-out-*/test*.ktest; do
      cp "$f" "/work/${name}_$(basename "$f")"
    done
  )
  rm -rf "$dir"
}

run_case get_sign get_sign.c
run_case two_objs two_objs.c
