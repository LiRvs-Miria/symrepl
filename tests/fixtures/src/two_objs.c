/* Minimal target producing a multi-object `.ktest` fixture:
 * two symbolic objects ("arr", "b") in one test case.
 */

#include "klee/klee.h"

int main(void) {
  unsigned char arr[4], b[2];
  klee_make_symbolic(arr, sizeof(arr), "arr");
  klee_make_symbolic(b, sizeof(b), "b");
  if (arr[0] == 0x41 && b[1] == 0x00)
    return 0;
  return 1;
}
