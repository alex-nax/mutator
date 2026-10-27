#ifndef CALC_H
#define CALC_H

struct pair {
  int first;
  int second;
};

int weighted(int a, int b);
int clamp_low(int value, int floor);
struct pair make_pair(int a, int b);

#endif
