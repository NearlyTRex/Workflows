#include "fixture.hpp"

#include <cstdio>

int main() {
    int failures = 0;
    if (!newer("1.10.0", "1.9.0")) { std::puts("FAIL 1.10.0 > 1.9.0"); ++failures; }
    if (newer("1.0.0", "1.0.0")) { std::puts("FAIL 1.0.0 > 1.0.0"); ++failures; }
    if (newer("0.9.9", "1.0.0")) { std::puts("FAIL 0.9.9 > 1.0.0"); ++failures; }
    std::puts(failures ? "fixture tests failed" : "fixture tests passed");
    return failures;
}
