/* KLEE tutorial example (get_sign).
 *
 * Source of the real-KLEE `.ktest` fixtures in this directory. Also the
 * M1 acceptance target (see docs/en/roadmap.md).
 */

#include <stdio.h>
#include "klee/klee.h"

int get_sign(int x) {
  if (x == 0)
    return 0;
  if (x < 0)
    return -1;
  return 1;
}

int main() {
  int a;
  klee_make_symbolic(&a, sizeof(a), "a");
  return get_sign(a);
}
