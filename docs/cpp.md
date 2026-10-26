# C and C++

| Workflow | Inputs (defaults) | Caller grants |
|---|---|---|
| `cpp-build.yml` | `build-command`, `runs-on` (ubuntu-latest), `container`, `submodules` (false), `fetch-depth` (1), `local-tag`, `apt-packages`, `brew-packages`, `msvc` (false), `msvc-arch` (x64), `msvc-uwp` (false), `llvm-version`, `openorbis-version`, `devkitpro`, `cache-paths`, `cache-key-files`, `cache-key`, `rolling-cache-paths`, `ccache` (false), `ccache-max-size` (500M), `ccache-sloppiness` (PCH-friendly), `parallel` (true), `timeout-minutes` (90), `test-command`, `artifact`, `artifact-path` | `contents: read` |
| `windows-test.yml` | `artifact`, `runs-on` (windows-latest), `page-heap` (true), `timeout` (300), `timeout-minutes` (30) | `contents: read` |
| `inno-setup.yml` | `script`, `version`, `version-define` (AppVersion), `output-dir` (dist), `smoke-test`, `artifact`, `inno-setup-version` (6.7.1) | `contents: read` |

## `cpp-build`

`cpp-build` runs the repo's own build and test commands in bash on any runner (Git Bash on Windows),
so it fits CMake, a `Setup.py` or a packaging script alike. Around them it installs `apt-packages`
on Linux and `brew-packages` on macOS, puts MSVC on `PATH` with `msvc` (found with vswhere, so no
edition path is hard-coded), installs an LLVM release from apt.llvm.org with `llvm-version`
(versioned packages beside the runner's clang, libc++ included, so the build names `clang++-23` and
the like), restores `cache-paths` keyed on `cache-key-files`, and uploads `artifact-path` as
`artifact`. The cache is named by `cache-key`, or else the artifact name, so a CI build and a
release build of the same thing can share one. `container` runs the whole job inside an image on a
Linux runner, which is how a toolchain that ships as an image builds here. Console homebrew has
inputs of its own: `openorbis-version` for the [PS4](homebrew/ps4.md), `devkitpro` for the
[Switch](homebrew/switch.md), and `msvc-uwp` with `ci-msix` for the [Xbox](homebrew/xbox.md). For
several platforms or configurations, the caller passes a matrix:

```yaml
build:
  uses: NearlyTRex/Workflows/.github/workflows/cpp-build.yml@<sha> # vX
  permissions:
    contents: read
  strategy:
    matrix:
      os: [ubuntu-latest, windows-latest]
  with:
    runs-on: ${{ matrix.os }}
    msvc: ${{ matrix.os == 'windows-latest' }}
    build-command: cmake -S . -B build && cmake --build build --config Release
    test-command: ctest --test-dir build -C Release --output-on-failure
```

Builds use every core: `parallel` sets `CMAKE_BUILD_PARALLEL_LEVEL`, and `MAKEFLAGS=-j` off
Windows, where plain `make` would otherwise run one job at a time.

With `ccache: true` on Linux and macOS runners, it also installs ccache and routes the
compilers through it, CMake's through its launcher setting and any other build system's through
ccache's wrappers first on `PATH`, so a build only recompiles what changed since the last run.
Each run saves its ccache under a new key and the next restores the newest one, so the cache
follows the code, and the hit rate is printed after the build. A failed or cancelled run saves
too: it started from the newest cache and only added to it, and a job whose cold build outlasts
`timeout-minutes` gets further each run instead of starting cold every time.

When a build fills `ccache-max-size`, ccache evicts entries as it goes and the saved cache
holds only the end of the build, so the next run misses on the rest. The stats step then
warns, with the size to raise.

By default ccache refuses compiles that use a precompiled header, so `ccache-sloppiness`
defaults to `pch_defines,time_macros,include_file_mtime,include_file_ctime`, which accepts
them and ignores the fresh file times every checkout has. With clang, also build the header
with `-Xclang=-fno-pch-timestamp`: otherwise each rebuilt `.pch` differs from the last, and
every file that includes it misses. The stats list any calls still uncacheable, and why.

`rolling-cache-paths` gives other directories the same treatment as the ccache: saved after
every run, and the newest restored. `cache-paths` is restored only while
`cache-key-files` are unchanged and saved only by a passing run, so it suits fetched
dependencies; `rolling-cache-paths` suits caches that follow the code.

The build and test commands also have these commands on `PATH` (Linux and macOS; `ci-phase` on
Windows too, and `ci-msix`, described on the [Xbox](homebrew/xbox.md#ci-msix) page, on Windows
only):

- **`ci-phase NAME COMMAND...`** runs one phase of the step as a collapsible log group, and adds
  NAME and its time to a table on the run's summary page, so a slow build shows where the time
  went. A failing phase is marked and keeps its exit status.
- **`ci-clang-tidy -p BUILD_DIR`** runs clang-tidy over `compile_commands.json`: by default every
  file under the checkout and outside BUILD_DIR, or those `--files` matches, less `--exclude`
  (regexes), with `--config-file` for one config throughout. Each file prints as it finishes,
  every diagnostic is an error annotation, and the run's summary gets the totals and the slowest
  files. With `--cache-dir` in `rolling-cache-paths`, a file is skipped while its preprocessed
  source (comments kept, so a `NOLINT` edit counts), command line, config and the clang-tidy
  version are what an earlier clean run saw, so only what a change touches is checked again.
  Failures are never cached.
- **`ci-cpp-coverage --object BINARY... -- COMMAND...`** is the C and C++ counterpart of
  `python-ci`'s coverage gate, for a build made with clang's
  `-fprofile-instr-generate -fcoverage-mapping`. It runs COMMAND, usually `ctest`, with every
  process writing a profile, merges them, and counts lines, functions, branches and regions over
  the code in each `--object`.
  - **Objects:** name the test binaries. An instrumented binary no test runs, such as the
    application itself, can be named too: its code then counts as not run, so the total covers
    the whole program rather than only what the tests link.
  - **Files:** only files under the checkout count, less those an `--exclude` regex matches in
    the absolute path, such as `/tests/` or `/_deps/`.
  - **Gate:** `--fail-under PERCENT` sets lines, functions and branches, which default to 100;
    `--fail-under METRIC=PERCENT` sets one, regions included. `0` only reports, which suits
    recording a baseline to raise from. A metric below its threshold is an error annotation and
    fails the step, and failing tests keep their own exit status.
  - **Report:** the log lists every file short of 100% in a gated metric, most missed lines
    first, and the run's summary gets the totals and the first 50 of those files.
  - **Tools:** `llvm-cov` and `llvm-profdata` from `PATH` (Ubuntu's `llvm` package), or
    `--llvm-cov` and `--llvm-profdata`, or `LLVM_COV` and `LLVM_PROFDATA`. They must match the
    clang that built the binaries.

```yaml
with:
  apt-packages: cmake clang clang-tidy llvm ninja-build
  rolling-cache-paths: .tidy-cache
  build-command: |
    ci-phase Configure cmake -S . -B build -G Ninja -DCMAKE_EXPORT_COMPILE_COMMANDS=ON \
      -DCMAKE_CXX_COMPILER=clang++ "-DCMAKE_CXX_FLAGS=-fprofile-instr-generate -fcoverage-mapping" \
      -DCMAKE_EXE_LINKER_FLAGS=-fprofile-instr-generate
    ci-phase Build cmake --build build
    ci-phase Tidy ci-clang-tidy -p build --cache-dir .tidy-cache
  test-command: |
    ci-phase Coverage ci-cpp-coverage --object build/app_tests --exclude /tests/ \
      -- ctest --test-dir build --output-on-failure
```

A release build that stamps `git describe` into the binary should pass `local-tag:` with
`release-check`'s `tag` output. `release.yml` only tags the commit once the builds are done, so
otherwise the binary names a bare commit instead of the release. The tag is created in the
build's checkout only and never pushed.

## `windows-test`

`windows-test` runs test programs built somewhere else on Windows itself. A mingw-w64 cross build
otherwise only ever runs under wine, whose loader and heap are not Windows', so a fault that crashes
it for a player can pass there.

- **Input:** `artifact` names an uploaded folder of programs with the DLLs and data files they
  need beside them, usually from a `cpp-build` job's `artifact` input. Every `.exe` in it runs
  with the folder as its working directory, for `timeout` seconds at most.
- **Page heap:** with `page-heap`, gflags gives each program full page heap first: every
  allocation ends against an inaccessible page, so an overrun or a use after free faults at the
  instruction that did it rather than corrupting a neighbour.
- **Report:** a program that fails runs again under cdb, which prints the fault and the stack.
  Each program is a log group and each failure an error annotation, and the run's summary gets a
  row per program.

```yaml
build-windows-tests:
  uses: NearlyTRex/Workflows/.github/workflows/cpp-build.yml@<commit sha> # vX.Y.Z
  with:
    apt-packages: cmake clang ninja-build g++-mingw-w64-x86-64
    build-command: cmake --preset windows-cross && cmake --build --preset windows-cross --target test-bundle
    artifact: windows-tests
    artifact-path: build/windows-cross/test-bundle
windows-test:
  needs: build-windows-tests
  uses: NearlyTRex/Workflows/.github/workflows/windows-test.yml@<commit sha> # vX.Y.Z
  with:
    artifact: windows-tests
```

## `inno-setup`

`inno-setup` compiles a Windows installer on `windows-latest`, passing `version` as
`/DAppVersion=...`, then runs the repo's `smoke-test` PowerShell and, with `artifact`, uploads the
`.exe` for `release.yml` to attach. Run it in CI with a placeholder version so a broken script fails
the pull request rather than the release. Installing Inno Setup from Chocolatey is retried, since
the community feed sometimes rate-limits or times out.
