import os
import subprocess

import pytest

from script_helpers import load_script

run_windows_tests = load_script("actions/windows-test/run_windows_tests.py")

# An NTSTATUS for an access violation, as Windows reports a program's exit
ACCESS_VIOLATION = 0xC0000005


class FakeRun:
    """Stands in for subprocess.run: records each command and answers by program name."""

    def __init__(self):
        self.calls = []
        self.statuses = {}
        self.timeouts = set()

    def __call__(self, command, **kwargs):
        self.calls.append((list(command), kwargs))
        name = os.path.basename(command[-1])
        if command[0].endswith("gflags.exe"):
            return subprocess.CompletedProcess(command, 0, "", "")
        if name in self.timeouts:
            raise subprocess.TimeoutExpired(command, kwargs["timeout"])
        if command[0].endswith("cdb.exe"):
            return subprocess.CompletedProcess(command, 0, f"stack of {name}\n", "")
        return subprocess.CompletedProcess(command, self.statuses.get(name, 0), f"ran {name}\n", "")

    def commands(self):
        """Each command with its program reduced to a file name."""
        return [[os.path.basename(command[0]), *command[1:]] for command, _ in self.calls]


@pytest.fixture
def fake(monkeypatch):
    fake = FakeRun()
    monkeypatch.setattr(subprocess, "run", fake)
    monkeypatch.setattr(run_windows_tests.shutil, "which", lambda name: None)
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
    return fake


def make_bundle(tmp_path, programs=("a_test.exe", "b_test.exe"), tools=("gflags.exe", "cdb.exe")):
    """A bundle of programs and a DLL, and a debuggers folder holding the named tools."""
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    for name in programs:
        (bundle / name).write_bytes(b"MZ")
    (bundle / "runtime.dll").write_bytes(b"MZ")
    debuggers = tmp_path / "debuggers"
    debuggers.mkdir()
    for name in tools:
        (debuggers / name).write_bytes(b"MZ")
    return bundle, debuggers


def test_every_program_runs_in_the_bundle(tmp_path, fake, capsys):
    bundle, debuggers = make_bundle(tmp_path)
    assert run_windows_tests.main([str(bundle), "--debuggers", str(debuggers)]) == 0
    assert [command for command, _ in fake.calls] == [[str(bundle / "a_test.exe")], [str(bundle / "b_test.exe")]]
    assert all(kwargs["cwd"] == str(bundle) and kwargs["timeout"] == 300 for _, kwargs in fake.calls)
    out = capsys.readouterr().out
    assert "::group::a_test.exe\nran a_test.exe\n::endgroup::" in out
    assert out.endswith("Windows tests: 2 of 2 passed\n")


def test_page_heap_is_on_for_each_program_while_it_runs_and_off_after(tmp_path, fake, capsys):
    bundle, debuggers = make_bundle(tmp_path, programs=("a_test.exe",))
    assert run_windows_tests.main([str(bundle), "--page-heap", "--debuggers", str(debuggers)]) == 0
    assert fake.commands() == [
        ["gflags.exe", "/p", "/enable", "a_test.exe", "/full"],
        ["a_test.exe"],
        ["gflags.exe", "/p", "/disable", "a_test.exe"],
    ]
    assert "passed under full page heap" in capsys.readouterr().out


def test_a_failing_program_is_run_again_under_cdb_and_annotated(tmp_path, fake, capsys):
    bundle, debuggers = make_bundle(tmp_path)
    fake.statuses["a_test.exe"] = ACCESS_VIOLATION
    assert run_windows_tests.main([str(bundle), "--debuggers", str(debuggers), "--timeout", "9"]) == 1
    assert fake.commands()[1] == ["cdb.exe", "-G", "-lines", "-c", run_windows_tests.CDB_COMMANDS,
                                  str(bundle / "a_test.exe")]
    assert fake.calls[1][1]["timeout"] == 9
    out = capsys.readouterr().out
    assert "--- a_test.exe again under cdb\nstack of a_test.exe\n" in out
    assert "::error::a_test.exe failed: exception 0xc0000005" in out
    assert out.endswith("Windows tests: 1 of 2 passed\n  failed: a_test.exe\n")


def test_without_cdb_a_failure_is_reported_without_a_stack(tmp_path, fake, capsys):
    bundle, debuggers = make_bundle(tmp_path, programs=("a_test.exe",), tools=())
    fake.statuses["a_test.exe"] = 3
    assert run_windows_tests.main([str(bundle), "--debuggers", str(debuggers)]) == 1
    assert fake.commands() == [["a_test.exe"]]
    captured = capsys.readouterr()
    assert "no cdb.exe, so a failure gets no stack" in captured.err
    assert "::error::a_test.exe failed: exit status 3" in captured.out


def test_a_program_that_hangs_is_a_failure(tmp_path, fake, capsys):
    bundle, debuggers = make_bundle(tmp_path, programs=("a_test.exe",))
    fake.timeouts.add("a_test.exe")
    assert run_windows_tests.main([str(bundle), "--debuggers", str(debuggers), "--timeout", "5"]) == 1
    out = capsys.readouterr().out
    assert "timed out after 5s" in out
    assert "::error::a_test.exe failed: timed out" in out


def test_the_tools_on_path_are_preferred(tmp_path, fake, monkeypatch):
    bundle, debuggers = make_bundle(tmp_path, programs=("a_test.exe",), tools=())
    monkeypatch.setattr(run_windows_tests.shutil, "which", lambda name: os.path.join("on", "path", name))
    fake.statuses["a_test.exe"] = 1
    assert run_windows_tests.main([str(bundle), "--page-heap", "--debuggers", str(debuggers)]) == 1
    assert [command[0] for command, _ in fake.calls] == [
        os.path.join("on", "path", "gflags.exe"), str(bundle / "a_test.exe"),
        os.path.join("on", "path", "cdb.exe"), os.path.join("on", "path", "gflags.exe")]


def test_page_heap_without_gflags_is_refused_before_anything_runs(tmp_path, fake, capsys):
    bundle, debuggers = make_bundle(tmp_path, tools=("cdb.exe",))
    assert run_windows_tests.main([str(bundle), "--page-heap", "--debuggers", str(debuggers)]) == 2
    assert not fake.calls
    assert "--page-heap needs gflags.exe" in capsys.readouterr().err


def test_an_empty_bundle_is_an_error(tmp_path, fake, capsys):
    bundle, debuggers = make_bundle(tmp_path, programs=())
    assert run_windows_tests.main([str(bundle), "--debuggers", str(debuggers)]) == 2
    assert not fake.calls
    assert f"no programs in {bundle}" in capsys.readouterr().err


def test_the_summary_page_gets_a_row_per_program(tmp_path, fake, monkeypatch):
    bundle, debuggers = make_bundle(tmp_path)
    page = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(page))
    fake.statuses["b_test.exe"] = ACCESS_VIOLATION
    assert run_windows_tests.main([str(bundle), "--page-heap", "--debuggers", str(debuggers)]) == 1
    assert page.read_text(encoding="utf-8") == (
        "### Windows tests: 1 of 2 passed under full page heap\n\n"
        "| Program | Result |\n|---|---|\n"
        "| `a_test.exe` | passed |\n"
        "| `b_test.exe` | exception 0xc0000005 |\n\n")
