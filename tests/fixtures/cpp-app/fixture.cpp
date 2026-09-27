#include "fixture.hpp"

#include <cstdio>

bool newer(const char* a, const char* b) {
    int x[3] = {0, 0, 0};
    int y[3] = {0, 0, 0};
    std::sscanf(a, "%d.%d.%d", &x[0], &x[1], &x[2]);
    std::sscanf(b, "%d.%d.%d", &y[0], &y[1], &y[2]);
    for (int i = 0; i < 3; ++i) {
        if (x[i] != y[i]) {
            return x[i] > y[i];
        }
    }
    return false;
}
