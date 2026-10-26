// Runs on a Switch as the fixture's NRO: prints the line its romfs holds, and
// exits on +.
#include <switch.h>

#include <cstdio>
#include <fstream>
#include <string>

int main() {
    consoleInit(nullptr);
    padConfigureInput(1, HidNpadStyleSet_NpadStandard);
    PadState pad;
    padInitializeDefault(&pad);

    std::string line = "romfs:/message.txt is missing";
    if (R_SUCCEEDED(romfsInit())) {
        std::ifstream message("romfs:/message.txt");
        std::getline(message, line);
        romfsExit();
    }
    std::printf("%s\nPress + to exit.\n", line.c_str());

    while (appletMainLoop()) {
        padUpdate(&pad);
        if (padGetButtonsDown(&pad) & HidNpadButton_Plus) {
            break;
        }
        consoleUpdate(nullptr);
    }
    consoleExit(nullptr);
    return 0;
}
