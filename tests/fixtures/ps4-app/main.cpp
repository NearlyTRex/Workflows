// Runs on a PS4 as the fixture's eboot.bin: libc++ and the SDK's C headers
// together, and the struct stat layout libkernel fills in.
#include <sys/stat.h>

#include <string>
#include <vector>

// 120 bytes only with the 16-bit mode_t actions/openorbis fixes the SDK's
// header to declare.
static_assert(sizeof(struct stat) == 120, "struct stat doesn't match libkernel's");

int main() {
    std::vector<std::string> words{"fixture", "eboot"};
    struct stat status {};
    return stat("/app0/README.txt", &status) == 0 && words.size() == 2 ? 0 : 1;
}
