#include "calc.h"

int weighted(int a, int b) {
  if (a > b) {
    return a * 2 - b;
  }
  return b - a;
}

int clamp_low(int value, int floor) {
  if (value < floor) {
    return floor;
  }
  return value;
}

struct pair make_pair(int a, int b) {
  struct pair p;
  p.first = a;
  p.second = b;
  return p;
}
