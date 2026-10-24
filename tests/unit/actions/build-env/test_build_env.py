"""build_env.py, given the runner's variables as the action passes them."""

import os

import pytest

from script_helpers import ROOT, load_script

ACTION = ROOT / "actions" / "build-env"
build_env = load_script("actions/build-env/build_env.py")


def read_env(path):
    return dict(line.split("=", 1) for line in path.read_text().splitlines()) if path.exists() else {}


def run(tmp_path, capsys, **env):
    github_env, github_path = tmp_path / "env", tmp_path / "path"
    base = {"GITHUB_ENV": str(github_env), "GITHUB_PATH": str(github_path), "GITHUB_WORKSPACE": "/work",
            "NUMBER_OF_PROCESSORS": "4", "RUNNER_TEMP": str(tmp_path / "temp")}
    assert build_env.main({**base, **env}) == 0
    path = github_path.read_text().splitlines() if github_path.exists() else []
    return read_env(github_env), path, capsys.readouterr().out


CCACHE = {"CCACHE": "true", "CCACHE_DIR": "/tmp/cc", "MAX_SIZE": "1G", "SLOPPINESS": "time_macros"}


@pytest.mark.parametrize("os_name,expected", [
    ("Linux", {"CMAKE_BUILD_PARALLEL_LEVEL": "4", "MAKEFLAGS": "-j4"}),
    ("macOS", {"CMAKE_BUILD_PARALLEL_LEVEL": "4", "MAKEFLAGS": "-j4"}),
    ("Windows", {"CMAKE_BUILD_PARALLEL_LEVEL": "4"}),       # nmake rejects -j
])
def test_parallel(tmp_path, capsys, os_name, expected):
    env, path, out = run(tmp_path, capsys, PARALLEL="true", RUNNER_OS=os_name)
    assert env == expected and path == []
    assert "Building with 4 jobs" in out


def test_parallel_keeps_what_the_caller_set(tmp_path, capsys):
    env, _, _ = run(tmp_path, capsys, PARALLEL="true", RUNNER_OS="Linux", MAKEFLAGS="-j2",
                    CMAKE_BUILD_PARALLEL_LEVEL="3")
    assert env == {}


def test_parallel_falls_back_to_the_core_count(tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(os, "cpu_count", lambda: 6)
    env, _, _ = run(tmp_path, capsys, PARALLEL="true", RUNNER_OS="Linux", NUMBER_OF_PROCESSORS="")
    assert env["CMAKE_BUILD_PARALLEL_LEVEL"] == "6"
    monkeypatch.setattr(os, "cpu_count", lambda: None)
    (tmp_path / "env").unlink()
    env, _, _ = run(tmp_path, capsys, PARALLEL="true", RUNNER_OS="Linux", NUMBER_OF_PROCESSORS="")
    assert env["CMAKE_BUILD_PARALLEL_LEVEL"] == "2"


def test_nothing_when_all_are_off(tmp_path, capsys):
    env, path, out = run(tmp_path, capsys, PARALLEL="false", CCACHE="false", RUNNER_OS="Linux")
    assert env == {} and path == [] and out == ""


def test_ccache_on_linux(tmp_path, capsys):
    env, path, _ = run(tmp_path, capsys, RUNNER_OS="Linux", **CCACHE)
    assert env == {
        "CCACHE_DIR": "/tmp/cc", "CCACHE_BASEDIR": "/work", "CCACHE_MAXSIZE": "1G",
        "CCACHE_SLOPPINESS": "time_macros", "CCACHE_COMPRESS": "true",
        "CMAKE_C_COMPILER_LAUNCHER": "ccache", "CMAKE_CXX_COMPILER_LAUNCHER": "ccache",
    }
    assert path == ["/usr/lib/ccache"]


def test_ccache_on_macos(tmp_path, capsys):
    env, path, _ = run(tmp_path, capsys, RUNNER_OS="macOS", **CCACHE)
    assert env["CMAKE_CXX_COMPILER_LAUNCHER"] == "ccache" and env["CCACHE_DIR"] == "/tmp/cc"
    assert path == ["/opt/homebrew/opt/ccache/libexec"]


@pytest.mark.parametrize("os_name", ["Linux", "macOS"])
def test_ccache_wrappers_can_be_elsewhere(tmp_path, capsys, os_name):
    _, path, _ = run(tmp_path, capsys, RUNNER_OS=os_name, CCACHE_WRAPPERS="/opt/ccache", **CCACHE)
    assert path == ["/opt/ccache"]


@pytest.mark.parametrize("os_name,named", [("Windows", "Windows"), ("", "this runner")])
def test_ccache_is_skipped_off_linux_and_macos(tmp_path, capsys, os_name, named):
    env, path, out = run(tmp_path, capsys, RUNNER_OS=os_name, **CCACHE)
    assert env == {} and path == []
    assert f"Linux and macOS runners only; not on {named}" in out


def test_tools_are_put_on_path(tmp_path, capsys):
    env, path, out = run(tmp_path, capsys, TOOLS=str(ACTION), RUNNER_OS="Linux")
    bin_dir = tmp_path / "temp" / "ci-tools"
    assert env == {} and path == [str(bin_dir)]
    assert f"ci-phase, ci-clang-tidy and ci-cpp-coverage in {bin_dir}" in out
    assert sorted(launcher.name for launcher in bin_dir.iterdir()) == ["ci-clang-tidy", "ci-cpp-coverage", "ci-phase"]


def test_reads_the_process_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("GITHUB_ENV", str(tmp_path / "env"))
    monkeypatch.setenv("PARALLEL", "true")
    monkeypatch.setenv("NUMBER_OF_PROCESSORS", "3")
    monkeypatch.delenv("CMAKE_BUILD_PARALLEL_LEVEL", raising=False)
    monkeypatch.delenv("TOOLS", raising=False)
    monkeypatch.delenv("CCACHE", raising=False)
    assert build_env.main() == 0
    assert read_env(tmp_path / "env")["CMAKE_BUILD_PARALLEL_LEVEL"] == "3"
