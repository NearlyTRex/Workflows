import json
import os
import signal
import sys
from pathlib import Path

import pytest

from script_helpers import load_script

ci_cpp_coverage = load_script("actions/build-env/ci_cpp_coverage.py")

# Stands in for llvm-profdata and llvm-cov: logs its arguments as one JSON line. As
# llvm-profdata it writes the -o file; as llvm-cov it prints $FAKE_EXPORT. Either fails when
# $FAKE_FAIL names it.
FAKE_LLVM = """\
import json, os, sys
name = os.path.basename(sys.argv[0])
with open(os.environ["FAKE_LOG"], "a") as log:
    log.write(json.dumps([name] + sys.argv[1:]) + "\\n")
if os.environ.get("FAKE_FAIL") == name:
    print(f"{name}: broken", file=sys.stderr)
    sys.exit(1)
if name == "llvm-profdata":
    open(sys.argv[sys.argv.index("-o") + 1], "w").close()
else:
    print(open(os.environ["FAKE_EXPORT"]).read())
"""

# The tests: writes a profile where LLVM_PROFILE_FILE says, unless told to write none.
WRITE_PROFILE = ("import os; p = os.environ['LLVM_PROFILE_FILE'].replace('%m', 'm').replace('%p', str(os.getpid()));"
                 " open(p, 'w').close()")


def counts(covered, count):
    return {"covered": covered, "count": count}


def summary(lines=(1, 1), functions=(1, 1), branches=(1, 1), regions=(1, 1)):
    return {"lines": counts(*lines), "functions": counts(*functions), "branches": counts(*branches),
            "regions": counts(*regions)}


@pytest.fixture
def llvm(tmp_path, monkeypatch):
    """Fake llvm tools, a repo to run in, and setters for what llvm-cov reports."""
    root = tmp_path / "repo"
    root.mkdir()
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name in ("llvm-profdata", "llvm-cov"):
        tool = bin_dir / name
        tool.write_text(f"#!{sys.executable}\n{FAKE_LLVM}")
        tool.chmod(0o755)
    log = tmp_path / "log"
    export = tmp_path / "export.json"
    monkeypatch.chdir(root)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("FAKE_LOG", str(log))
    monkeypatch.setenv("FAKE_EXPORT", str(export))
    monkeypatch.setenv("RUNNER_TEMP", str(tmp_path / "temp"))
    for variable in ("FAKE_FAIL", "GITHUB_STEP_SUMMARY", "LLVM_COV", "LLVM_PROFDATA"):
        monkeypatch.delenv(variable, raising=False)

    class Llvm:
        def __init__(self):
            self.root, self.profiles = root, tmp_path / "temp" / "ci-cpp-coverage"
            self.files({"src/a.cpp": summary()})

        def files(self, by_name):
            entries = [{"filename": name if name.startswith("/") else str(root / name), "summary": value}
                       for name, value in by_name.items()]
            export.write_text(json.dumps({"data": [{"files": entries}]}))

        def calls(self):
            return [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []

    return Llvm()


def run(*options, command=(sys.executable, "-c", WRITE_PROFILE)):
    return ci_cpp_coverage.main(["--object", "build/test_a", *options, "--", *command])


@pytest.mark.parametrize(("value", "expected"), [
    ("90", {"lines": 90.0, "functions": 90.0, "branches": 90.0}),
    ("regions=75.5", {"regions": 75.5}),
    ("lines=0", {"lines": 0.0}),
])
def test_a_threshold_is_every_gated_metric_or_one(value, expected):
    assert ci_cpp_coverage.threshold(value) == expected


@pytest.mark.parametrize(("value", "message"), [
    ("statements=90", "unknown metric 'statements'"),
    ("lines=most", "not a percentage: 'most'"),
    ("", "not a percentage: ''"),
])
def test_a_bad_threshold_is_rejected(value, message):
    with pytest.raises(ci_cpp_coverage.argparse.ArgumentTypeError, match=message):
        ci_cpp_coverage.threshold(value)


def test_thresholds_default_to_100_and_a_later_one_wins():
    args = ci_cpp_coverage.parse_args(["--object", "t", "--fail-under", "80", "--fail-under", "branches=50",
                                       "--fail-under", "regions=10", "--", "ctest"])
    assert args.fail_under == {"lines": 80.0, "functions": 80.0, "branches": 50.0, "regions": 10.0}
    assert args.command == ["ctest"]
    assert ci_cpp_coverage.parse_args(["--object", "t", "ctest", "-L", "unit"]).fail_under == dict.fromkeys(
        ci_cpp_coverage.GATED, 100.0)


@pytest.mark.parametrize("argv", [["--object", "t"], ["--object", "t", "--"], ["--", "ctest"]])
def test_an_object_and_a_command_are_required(argv, capsys):
    with pytest.raises(SystemExit) as exit_info:
        ci_cpp_coverage.parse_args(argv)
    assert exit_info.value.code == 2
    assert "error:" in capsys.readouterr().err


def test_full_coverage_passes_and_names_every_object(llvm, capsys):
    stale = llvm.profiles / "old.profraw"
    stale.parent.mkdir(parents=True)
    stale.touch()

    assert run("--object", "build/test_b", "--object", "build/game") == 0

    profiles = sorted(path.name for path in llvm.profiles.glob("*.profraw"))
    assert len(profiles) == 1 and profiles[0].startswith("m-") and profiles != ["old.profraw"]
    merged = llvm.profiles / "merged.profdata"
    assert llvm.calls() == [
        ["llvm-profdata", "merge", "-sparse", str(llvm.profiles / profiles[0]), "-o", str(merged)],
        ["llvm-cov", "export", "-summary-only", f"-instr-profile={merged}", "build/test_a",
         "-object", "build/test_b", "-object", "build/game"],
    ]
    out = capsys.readouterr().out
    assert "Not fully covered" not in out
    assert "    lines: 1/1 (100.00%)" in out
    assert "C++ coverage: lines 100.00%, functions 100.00%, branches 100.00%, regions 100.00%" in out


def test_the_tools_come_from_the_environment_or_the_options(llvm, monkeypatch, tmp_path):
    for name in ("llvm-profdata", "llvm-cov"):
        renamed = tmp_path / "bin" / f"{name}-18"
        renamed.write_text((tmp_path / "bin" / name).read_text())
        renamed.chmod(0o755)
    monkeypatch.setenv("LLVM_PROFDATA", "llvm-profdata-18")
    assert run("--llvm-cov", "llvm-cov-18") == 0
    assert [call[0] for call in llvm.calls()] == ["llvm-profdata-18", "llvm-cov-18"]


def test_only_files_under_the_checkout_and_not_excluded_count(llvm, capsys):
    llvm.files({
        "src/a.cpp": summary(lines=(3, 4)),
        "build/../src/b.cpp": summary(lines=(1, 2)),
        "tests/test_a.cpp": summary(lines=(0, 9)),
        "_deps/gtest/gtest.h": summary(lines=(0, 9)),
        "/usr/include/c++/vector": summary(lines=(0, 9)),
    })

    assert run("--exclude", "/tests/", "--exclude", "/_deps/", "--fail-under", "0") == 0

    out = capsys.readouterr().out
    assert "    lines: 4/6 (66.67%)" in out
    assert "  src/b.cpp: lines 1/2 (50.00%)" in out
    assert "test_a" not in out and "gtest" not in out and "vector" not in out


def test_below_a_threshold_fails_and_partly_covered_files_are_listed_most_missed_first(llvm, capsys):
    llvm.files({
        "src/a.cpp": summary(lines=(9, 10)),
        "src/b.cpp": summary(branches=(1, 2)),
        "src/c.cpp": summary(lines=(1, 10)),
        "src/d.cpp": summary(regions=(0, 1)),
    })

    assert run("--fail-under", "lines=50") == 1

    out = capsys.readouterr().out
    listed = [line.split(":")[0].strip() for line in out.splitlines() if line.startswith("  src/")]
    assert listed == ["src/c.cpp", "src/a.cpp", "src/b.cpp"]
    assert "C++ coverage: lines 54.55%, functions 100.00%, branches 80.00% (below 100%), regions 75.00%" in out
    assert "::error::C++ branches coverage is 80.00%, below 100%" in out
    assert "::error::C++ lines" not in out


def test_a_gated_region_threshold_lists_and_fails_on_regions(llvm, capsys):
    llvm.files({"src/a.cpp": summary(regions=(1, 2))})
    assert run("--fail-under", "regions=60") == 1
    out = capsys.readouterr().out
    assert "  src/a.cpp:" in out
    assert "regions 50.00% (below 60%)" in out


def test_nothing_to_cover_is_fully_covered(llvm, capsys):
    llvm.files({"src/a.cpp": summary(branches=(0, 0))})
    assert run() == 0
    assert " branches: 0/0 (100.00%)" in capsys.readouterr().out


def test_the_summary_page_gets_the_totals_and_the_files_below(llvm, tmp_path, monkeypatch):
    page = tmp_path / "summary.md"
    page.write_text("earlier\n")
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(page))
    llvm.files({"src/a.cpp": summary(lines=(1, 2))})

    assert run("--fail-under", "regions=0") == 1

    assert page.read_text() == (
        "earlier\n"
        "### C++ coverage: lines 50.00% (below 100%), functions 100.00%, branches 100.00%, regions 100.00%\n\n"
        "| Metric | Covered | Threshold |\n|---|---|---|\n"
        "| lines | 1/2 (50.00%) | 100% |\n"
        "| functions | 1/1 (100.00%) | 100% |\n"
        "| branches | 1/1 (100.00%) | 100% |\n"
        "| regions | 1/1 (100.00%) | 0% |\n"
        "\n| Not fully covered | Lines | Functions | Branches |\n|---|---|---|---|\n"
        "| `src/a.cpp` | 1/2 (50.00%) | 1/1 (100.00%) | 1/1 (100.00%) |\n"
        "\n"
    )


def test_full_coverage_puts_only_the_totals_on_the_summary_page(llvm, tmp_path, monkeypatch):
    page = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(page))
    assert run() == 0
    text = page.read_text()
    assert "| regions | 1/1 (100.00%) |  |\n\n" in text
    assert "Not fully covered" not in text


def test_the_summary_page_lists_at_most_its_limit(llvm, tmp_path, monkeypatch):
    page = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(page))
    llvm.files({f"src/f{index:02}.cpp": summary(lines=(0, 1)) for index in range(ci_cpp_coverage.SUMMARY_FILES + 3)})

    assert run() == 1

    text = page.read_text()
    assert text.count("| `src/f") == ci_cpp_coverage.SUMMARY_FILES
    assert "\nand 3 more, listed in the log.\n" in text


def test_a_failing_test_command_keeps_its_status_and_skips_the_report(llvm, capsys):
    assert run(command=(sys.executable, "-c", "raise SystemExit(3)")) == 3
    assert llvm.calls() == []
    assert "the tests failed (3); no coverage report" in capsys.readouterr().err


def test_a_test_command_killed_by_a_signal_reports_as_a_shell_would(llvm):
    kill = "import os, signal; os.kill(os.getpid(), signal.SIGTERM)"
    assert run(command=(sys.executable, "-c", kill)) == 128 + signal.SIGTERM


def test_a_missing_test_command_is_127(llvm, capsys):
    assert run(command=("no-such-test-runner",)) == 127
    assert "ci-cpp-coverage: " in capsys.readouterr().err


def test_no_profiles_means_the_build_is_not_instrumented(llvm, capsys):
    assert run(command=(sys.executable, "-c", "pass")) == 1
    assert llvm.calls() == []
    assert f"no profiles in {llvm.profiles}; build with clang's" in capsys.readouterr().err


@pytest.mark.parametrize("tool", ["llvm-profdata", "llvm-cov"])
def test_a_failing_llvm_tool_fails_with_its_error(llvm, monkeypatch, capsys, tool):
    monkeypatch.setenv("FAKE_FAIL", tool)
    assert run() == 1
    assert f"{tool}: broken" in capsys.readouterr().err


def test_a_missing_llvm_tool_fails(llvm, capsys):
    assert run("--llvm-profdata", "no-such-llvm-profdata") == 1
    assert "no-such-llvm-profdata" in capsys.readouterr().err


def test_the_profile_directory_defaults_to_the_system_temp_off_actions(monkeypatch):
    monkeypatch.delenv("RUNNER_TEMP", raising=False)
    args = ci_cpp_coverage.parse_args(["--object", "t", "--", "ctest"])
    assert args.profile_dir == Path(ci_cpp_coverage.tempfile.gettempdir(), "ci-cpp-coverage")
