# Xbox homebrew

Xbox homebrew runs in Dev Mode as a UWP app, installed from the console's Device Portal as a signed
MSIX. [`cpp-build`](../cpp.md#cpp-build)'s `msvc-uwp: true` sets MSVC up for that on a Windows
runner: vcvarsall's `uwp` mode, so builds link the store CRT (`vcruntime140_app.dll` and the rest),
and `LINK` adds `WindowsApp.lib` to every link, a build system's compiler checks included, since the
store CRT starts with `RoInitialize`. It also puts the Windows SDK's C++/WinRT headers on `INCLUDE`,
and `makeappx`, `signtool`, `makepri`, `dumpbin` and `lib` on `PATH`. It implies `msvc`, and fails
when the runner's Visual Studio has no store libraries for `msvc-arch`, rather than linking the
desktop CRT.

A UWP executable is an ordinary MSVC build with `WINAPI_FAMILY=WINAPI_FAMILY_APP`, linked with
`/APPCONTAINER` against `WindowsApp.lib`. C++/WinRT needs C++20: before it, it uses MSVC's
experimental coroutines, which current MSVC refuses with error STL1011.

```cmake
set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_LIBRARIES "WindowsApp.lib")
add_executable(app WIN32 app.cpp)
target_compile_definitions(app PRIVATE WINAPI_FAMILY=WINAPI_FAMILY_APP UNICODE _UNICODE)
target_link_options(app PRIVATE /APPCONTAINER)
```

```yaml
with:
  runs-on: windows-latest
  msvc-uwp: true
  build-command: |
    cmake -S . -B build -G Ninja -DCMAKE_BUILD_TYPE=Release
    cmake --build build
    mkdir -p build/stage/Assets && cp build/app.exe build/stage/ && cp packaging/Assets/* build/stage/Assets/
    ci-msix --dir build/stage --manifest packaging/Package.appxmanifest --out build/app.msix \
      --version "1.0.$GITHUB_RUN_NUMBER" --publisher "CN=Someone" --vclibs --check-imports
  artifact: app-xbox
  artifact-path: |
    build/app.msix
    build/app.cer
```

## `ci-msix`

`ci-msix` is on `PATH` in every `cpp-build` job, beside `ci-phase`. It packages a directory the
build has staged, laid out as the package holds it, with the repo's `Package.appxmanifest`:

- **Manifest:** written into the stage as `AppxManifest.xml`, with its `Identity`'s `Version` and
  `Publisher` replaced by `--version` (up to four numbers, `1.2.3` becoming `1.2.3.0`) and
  `--publisher` when given. Its `Executable`, and every image it names, as is or as a `scale-` or
  `targetsize-` qualified variant, must be in the stage.
- **`--vclibs`:** copies the C++ runtime DLLs from the Windows SDK's `Microsoft.VCLibs` framework
  package, for the manifest's `ProcessorArchitecture`, into the stage. Without them, or a
  dependency on that framework package installed beside the app, the app dies at launch with no
  message.
- **`--check-imports`:** fails, listing them, when an `.exe` or `.dll` in the stage imports a DLL
  that is neither packaged nor the system's (one `WindowsApp.lib` or `ucrt.lib` links to, the
  latter being the Universal CRT's `api-ms-win-crt-*` sets), which is that same silent death,
  usually a desktop CRT or a library built for desktop. `--allow-import DLL` passes one the
  device provides anyway.
- **Signing:** `--certificate PFX` signs with the caller's certificate, whose subject must be the
  manifest's `Publisher`, and whose password is `MSIX_CERTIFICATE_PASSWORD` (from a secret).
  Without one, a new self-signed certificate for the `Publisher` signs it, and is exported beside
  the package as a `.cer`, which a PC needs to trust before installing; Device Portal installs a
  Dev Mode package without it.

What goes in a package is the caller's: the runner brings the SDK and nothing else.
