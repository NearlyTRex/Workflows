"""Export the build environment for a C/C++ build's later steps.

Reads the action's inputs from the environment and writes to $GITHUB_ENV and $GITHUB_PATH;
runs on every runner.

PARALLEL=true   build on every core: CMAKE_BUILD_PARALLEL_LEVEL, and MAKEFLAGS=-j off Windows
                (nmake reads MAKEFLAGS too, and rejects -j). A value the caller already set is
                left alone.
CCACHE=true     route compilers through ccache (Linux and macOS): CMake reads the launcher
                variables when it configures, and other build systems call compilers by name, so
                ccache's wrappers go first on PATH as well. ccache notices when it is both and
                caches each compile once. CCACHE_BASEDIR makes paths relative, so a hit doesn't
                depend on where the runner checked out. Uses CCACHE_DIR, MAX_SIZE, SLOPPINESS and
                CCACHE_WRAPPERS (default /usr/lib/ccache on Linux, Homebrew's libexec on macOS).
TOOLS=DIR       put ci-phase, ci-clang-tidy, ci-cpp-coverage and ci-msix on PATH: bash launchers
                in $RUNNER_TEMP/ci-tools that run DIR/ci_phase.py, DIR/ci_clang_tidy.py,
                DIR/ci_cpp_coverage.py and DIR/ci_msix.py with this Python.
"""

import os
import sys
from pathlib import Path

TOOLS = ("ci_phase", "ci_clang_tidy", "ci_cpp_coverage", "ci_msix")

# Where each runner's ccache keeps its compiler-named wrappers. On macOS the
# action passes CCACHE_WRAPPERS from `brew --prefix`; this is Apple silicon's.
CCACHE_WRAPPERS = {"Linux": "/usr/lib/ccache", "macOS": "/opt/homebrew/opt/ccache/libexec"}


def append(env, variable, lines):
    with open(env[variable], "a", encoding="utf-8") as out:
        out.writelines(f"{line}\n" for line in lines)


def install_tools(env):
    tools = env["TOOLS"]
    bin_dir = Path(env.get("RUNNER_TEMP", "/tmp")) / "ci-tools"
    bin_dir.mkdir(parents=True, exist_ok=True)
    for tool in TOOLS:
        launcher = bin_dir / tool.replace("_", "-")
        launcher.write_text(f'#!/usr/bin/env bash\nexec "{sys.executable}" "{Path(tools, tool)}.py" "$@"\n',
                            encoding="utf-8")
        launcher.chmod(0o755)
    append(env, "GITHUB_PATH", [str(bin_dir)])
    print(f"ci-phase, ci-clang-tidy, ci-cpp-coverage and ci-msix in {bin_dir}")


def set_parallel(env):
    cores = env.get("NUMBER_OF_PROCESSORS") or str(os.cpu_count() or 2)
    lines = []
    if not env.get("CMAKE_BUILD_PARALLEL_LEVEL"):
        lines.append(f"CMAKE_BUILD_PARALLEL_LEVEL={cores}")
    if env.get("RUNNER_OS") != "Windows" and not env.get("MAKEFLAGS"):
        lines.append(f"MAKEFLAGS=-j{cores}")
    append(env, "GITHUB_ENV", lines)
    print(f"Building with {cores} jobs")


def set_ccache(env):
    runner = env.get("RUNNER_OS")
    if runner not in CCACHE_WRAPPERS:
        print(f"ccache is set up on Linux and macOS runners only; not on {runner or 'this runner'}")
        return
    append(env, "GITHUB_ENV", [
        f"CCACHE_DIR={env['CCACHE_DIR']}",
        f"CCACHE_BASEDIR={env['GITHUB_WORKSPACE']}",
        f"CCACHE_MAXSIZE={env['MAX_SIZE']}",
        f"CCACHE_SLOPPINESS={env['SLOPPINESS']}",
        "CCACHE_COMPRESS=true",
        "CMAKE_C_COMPILER_LAUNCHER=ccache",
        "CMAKE_CXX_COMPILER_LAUNCHER=ccache",
    ])
    append(env, "GITHUB_PATH", [env.get("CCACHE_WRAPPERS") or CCACHE_WRAPPERS[runner]])
    print(f"ccache: {env['CCACHE_DIR']}, up to {env['MAX_SIZE']}, sloppiness {env['SLOPPINESS']}")


def main(env=None):
    env = os.environ if env is None else env
    if env.get("TOOLS"):
        install_tools(env)
    if env.get("PARALLEL") == "true":
        set_parallel(env)
    if env.get("CCACHE") == "true":
        set_ccache(env)
    return 0


if __name__ == "__main__":
    sys.exit(main())
