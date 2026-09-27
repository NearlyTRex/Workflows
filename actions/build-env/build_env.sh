#!/usr/bin/env bash
# Exports the build environment for a C/C++ build's later steps.
#
# PARALLEL=true   build on every core: CMAKE_BUILD_PARALLEL_LEVEL, and MAKEFLAGS=-j
#                 off Windows (nmake reads MAKEFLAGS too, and rejects -j). A value
#                 the caller already set is left alone.
# CCACHE=true     route compilers through ccache (Linux only): CMake reads the
#                 launcher variables when it configures, and other build systems
#                 call compilers by name, so ccache's wrappers go first on PATH as
#                 well. ccache notices when it is both and caches each compile once.
#                 CCACHE_BASEDIR makes paths relative, so a hit doesn't depend on
#                 where the runner checked out. Uses CCACHE_DIR, MAX_SIZE,
#                 SLOPPINESS and CCACHE_WRAPPERS (default /usr/lib/ccache).
#
# Writes to $GITHUB_ENV and $GITHUB_PATH; runs in bash on every runner.

set -euo pipefail

if [ "${PARALLEL:-false}" = "true" ]; then
    cores="${NUMBER_OF_PROCESSORS:-$(getconf _NPROCESSORS_ONLN 2>/dev/null || echo 2)}"
    if [ -z "${CMAKE_BUILD_PARALLEL_LEVEL:-}" ]; then
        echo "CMAKE_BUILD_PARALLEL_LEVEL=$cores" >> "$GITHUB_ENV"
    fi
    if [ "${RUNNER_OS:-}" != "Windows" ] && [ -z "${MAKEFLAGS:-}" ]; then
        echo "MAKEFLAGS=-j$cores" >> "$GITHUB_ENV"
    fi
    echo "Building with $cores jobs"
fi

if [ "${CCACHE:-false}" = "true" ]; then
    if [ "${RUNNER_OS:-}" != "Linux" ]; then
        echo "ccache is set up on Linux runners only; not on ${RUNNER_OS:-this runner}"
    else
        {
            echo "CCACHE_DIR=$CCACHE_DIR"
            echo "CCACHE_BASEDIR=$GITHUB_WORKSPACE"
            echo "CCACHE_MAXSIZE=$MAX_SIZE"
            echo "CCACHE_SLOPPINESS=$SLOPPINESS"
            echo "CCACHE_COMPRESS=true"
            echo "CMAKE_C_COMPILER_LAUNCHER=ccache"
            echo "CMAKE_CXX_COMPILER_LAUNCHER=ccache"
        } >> "$GITHUB_ENV"
        echo "${CCACHE_WRAPPERS:-/usr/lib/ccache}" >> "$GITHUB_PATH"
        echo "ccache: $CCACHE_DIR, up to $MAX_SIZE, sloppiness $SLOPPINESS"
    fi
fi
