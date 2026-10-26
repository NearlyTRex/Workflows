# Switch homebrew

[`cpp-build`](../cpp.md#cpp-build)'s `devkitpro: switch` runs the job in devkitPro's own
[devkitA64 image](https://hub.docker.com/r/devkitpro/devkita64), pinned by digest in
`cpp-build.yml`. It holds devkitA64, libnx, the Switch portlibs (SDL2, curl, freetype, glfw and
more) and the Switch tools, with `DEVKITPRO` set to `/opt/devkitpro` and its tools on `PATH`:
`elf2nro`, `nacptool`, `build_romfs`, `elf2nso`, `build_pfs0`, `npdmtool` and `nxlink`. It can't
be given with `container`, and any other platform name fails the job rather than building on the
runner.

A libnx Makefile, such as switchbrew's application template, builds as it does locally, since it
only needs `DEVKITPRO`. A CMake build uses devkitPro's toolchain file and its `nx_` helpers:

```cmake
add_executable(app main.cpp)
nx_generate_nacp(app.nacp NAME "Some App" AUTHOR "Someone" VERSION 1.0.0)
nx_create_nro(app NACP app.nacp ICON icon.jpg ROMFS romfs)   # app.nro
```

```yaml
with:
  devkitpro: switch
  ccache: true
  build-command: |
    cmake -S . -B build -G Ninja -DCMAKE_TOOLCHAIN_FILE="$DEVKITPRO/cmake/Switch.cmake"
    cmake --build build
  artifact: app-switch
  artifact-path: build/app.nro
```

- **`nx_create_nro(<target> ...)`:** an NRO for the homebrew menu, with `NACP`, `ICON` (libnx's
  default without one) and a `ROMFS` directory. `nx_create_exefs` makes an NSO/NSP from an NPDM
  config instead. Both are devkitPro's, documented in its
  [CMake files](https://github.com/devkitPro/pacman-packages/tree/master/cmake/switch).
- **ccache:** works inside the image as on a runner: the image is Debian, and runs as root.
- **Image updates:** Dependabot can't see an image named inside a workflow expression, so
  `self-tool-versions.yml` checks Docker Hub every week and fails, with the tag and digest to move
  to, when devkitPro has published a newer dated image.

What goes in an NRO is the caller's: the image brings the SDK and nothing else.
