# PS4 homebrew

[`cpp-build`](../cpp.md#cpp-build)'s `openorbis-version` installs that
[OpenOrbis PS4 Toolchain](https://github.com/OpenOrbis/OpenOrbis-PS4-Toolchain) release on a
Linux runner, checked against the sha256 [`actions/openorbis`](../../actions/openorbis) pins for it,
with the LLVM release it was built with (18 for 0.5.4). The build gets `OO_PS4_TOOLCHAIN`, the
SDK's `create-fself`, `create-gp4`, `PkgTool.Core` and `readoelf` on `PATH`, and a CMake
toolchain file as `OPENORBIS_TOOLCHAIN_FILE`. The SDK's 0.5.4 header declaring a 32-bit
`mode_t` is fixed in the installed copy, so `struct stat` matches libkernel's. A Makefile build
can use the variables and tools directly; a CMake build gets the toolchain and helpers:

```cmake
include(OpenOrbis)
add_executable(app main.cpp)
add_library(helper SHARED helper.c)          # becomes helper.prx
openorbis_add_pkg(app_pkg ALL
  EBOOT app LIBRARIES helper
  TITLE "Some App" TITLE_ID BREW00001 CONTENT_ID IV0000-BREW00001_00-SOMEAPP000000000
  VERSION 01.00 ICON sce_sys/icon0.png
  FILES README.txt README.txt                 # <source> <path in package> pairs
  DIRECTORIES assets assets)
```

```yaml
with:
  openorbis-version: 0.5.4
  build-command: |
    cmake -S . -B build -G Ninja -DCMAKE_TOOLCHAIN_FILE="$OPENORBIS_TOOLCHAIN_FILE"
    cmake --build build
  artifact: app-ps4
  artifact-path: build/IV0000-BREW00001_00-SOMEAPP000000000.pkg
```

- **Toolchain:** executables link with the SDK's `crt1.o` and `SHARED` libraries with its
  `crtlib.o`, by `ld.lld` itself, against `-lc -lkernel` (and `-lc++` for C++). `__ORBIS__` is
  defined, as Sony's compiler does.
- **`openorbis_add_fself(<target> [PAID <id>])`:** `eboot.bin` from an executable, or
  `<OUTPUT_NAME>.prx` from a `SHARED` library, in `<binary dir>/<target>-ps4`; the path is the
  target's `OPENORBIS_FSELF` property. `openorbis_add_pkg` calls it for its targets.
- **`openorbis_add_pkg(<name> ...)`:** builds `<CONTENT_ID>.pkg`, a fake PKG for a homebrew
  package installer, in `OUTPUT_DIRECTORY` (the binary dir). It holds the `EBOOT` target's
  `eboot.bin`, a `param.sfo` of `TITLE`, `TITLE_ID`, `CONTENT_ID`, `VERSION` (01.00), `CATEGORY`
  (gd), `ATTRIBUTE` (0) and `SYSTEM_VERSION` (0), the 512x512 `ICON`, the SDK's `right.sprx`,
  `libc.prx` and `libSceFios2.prx`, each `LIBRARIES` target's `.prx` in `sce_module/`, and
  `FILES` and `DIRECTORIES` where they are paired to go. The metadata is checked when CMake
  configures, and the package is validated with `PkgTool.Core pkg_validate` once built. `ALL`
  builds it with the default target.
- **Release updates:** Dependabot can't see the release `actions/openorbis` pins, so
  `self-tool-versions.yml` checks GitHub every week and fails when OpenOrbis has published a newer
  one, which is then added to the action's `RELEASES` with its sha256.

What goes in a package is the caller's: the action brings the SDK and nothing else.
