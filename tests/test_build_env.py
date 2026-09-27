"""build_env.sh, run as the action runs it: bash with the runner's variables."""

import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "actions" / "build-env" / "build_env.sh"


def read_env(path):
    return dict(line.split("=", 1) for line in path.read_text().splitlines()) if path.exists() else {}


def run(tmp_path, **env):
    github_env, github_path = tmp_path / "env", tmp_path / "path"
    base = {"PATH": "/usr/bin:/bin", "GITHUB_ENV": str(github_env), "GITHUB_PATH": str(github_path),
            "GITHUB_WORKSPACE": "/work", "NUMBER_OF_PROCESSORS": "4"}
    result = subprocess.run(["bash", str(SCRIPT)], env={**base, **env}, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    return read_env(github_env), github_path.read_text().splitlines() if github_path.exists() else [], result.stdout


CCACHE = {"CCACHE": "true", "CCACHE_DIR": "/tmp/cc", "MAX_SIZE": "1G", "SLOPPINESS": "time_macros"}


@pytest.mark.parametrize("os_name,expected", [
    ("Linux", {"CMAKE_BUILD_PARALLEL_LEVEL": "4", "MAKEFLAGS": "-j4"}),
    ("macOS", {"CMAKE_BUILD_PARALLEL_LEVEL": "4", "MAKEFLAGS": "-j4"}),
    ("Windows", {"CMAKE_BUILD_PARALLEL_LEVEL": "4"}),       # nmake rejects -j
])
def test_parallel(tmp_path, os_name, expected):
    env, path, out = run(tmp_path, PARALLEL="true", RUNNER_OS=os_name)
    assert env == expected and path == []
    assert "Building with 4 jobs" in out


def test_parallel_keeps_what_the_caller_set(tmp_path):
    env, _, _ = run(tmp_path, PARALLEL="true", RUNNER_OS="Linux", MAKEFLAGS="-j2",
                    CMAKE_BUILD_PARALLEL_LEVEL="3")
    assert env == {}


def test_parallel_falls_back_to_getconf(tmp_path):
    github_env = tmp_path / "env"
    subprocess.run(["bash", str(SCRIPT)], check=True, env={
        "PATH": "/usr/bin:/bin", "GITHUB_ENV": str(github_env), "PARALLEL": "true", "RUNNER_OS": "Linux"})
    cores = subprocess.run(["getconf", "_NPROCESSORS_ONLN"], capture_output=True, text=True).stdout.strip()
    assert f"CMAKE_BUILD_PARALLEL_LEVEL={cores}" in github_env.read_text()


def test_nothing_when_both_are_off(tmp_path):
    env, path, out = run(tmp_path, PARALLEL="false", CCACHE="false", RUNNER_OS="Linux")
    assert env == {} and path == [] and out == ""


def test_ccache_on_linux(tmp_path):
    env, path, _ = run(tmp_path, RUNNER_OS="Linux", **CCACHE)
    assert env == {
        "CCACHE_DIR": "/tmp/cc", "CCACHE_BASEDIR": "/work", "CCACHE_MAXSIZE": "1G",
        "CCACHE_SLOPPINESS": "time_macros", "CCACHE_COMPRESS": "true",
        "CMAKE_C_COMPILER_LAUNCHER": "ccache", "CMAKE_CXX_COMPILER_LAUNCHER": "ccache",
    }
    assert path == ["/usr/lib/ccache"]


def test_ccache_is_skipped_off_linux(tmp_path):
    env, path, out = run(tmp_path, RUNNER_OS="Windows", **CCACHE)
    assert env == {} and path == []
    assert "Linux runners only" in out
