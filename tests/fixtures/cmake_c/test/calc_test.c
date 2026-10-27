#include "calc.h"

/* weighted is pinned on both sides of its boundary. clamp_low never sees
   value == floor, so `<` -> `<=` survives. */
int main(void) {
  if (weighted(3, 1) != 5) return 1;
  if (weighted(1, 3) != 2) return 1;
  if (weighted(2, 2) != 0) return 1;
  if (clamp_low(5, 2) != 5) return 1;
  if (clamp_low(-3, 2) != 2) return 1;
  if (make_pair(1, 2).first != 1) return 1;
  return 0;
}
