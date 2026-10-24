"""ci_cpp_coverage.py against real clang and llvm tools, on the C++ fixture."""

import shutil
import subprocess

import pytest

from script_helpers import ROOT, load_script

ci_cpp_coverage = load_script("actions/build-env/ci_cpp_coverage.py")

pytestmark = pytest.mark.requires_binary("clang++", "llvm-cov", "llvm-profdata")

INSTRUMENT = ["-fprofile-instr-generate", "-fcoverage-mapping"]

# A program no test runs, standing in for an application whose code should still count.
UNRUN = """\
#include "fixture.hpp"

int main(int argc, char**) { return argc > 1 && newer("2.0.0", "1.0.0") ? 0 : 1; }
"""


@pytest.fixture
def built(tmp_path, monkeypatch):
    """The fixture's test and an unrun program, compiled instrumented in a copy of its sources."""
    shutil.copytree(ROOT / "tests" / "fixtures" / "cpp-app", tmp_path, dirs_exist_ok=True)
    (tmp_path / "app.cpp").write_text(UNRUN)
    for binary, source in (("fixture_test", "fixture_test.cpp"), ("app", "app.cpp")):
        subprocess.run(["clang++", *INSTRUMENT, "-o", binary, source, "fixture.cpp"], cwd=tmp_path, check=True)
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
    return tmp_path


def test_the_tested_code_is_fully_covered(built, capsys):
    status = ci_cpp_coverage.main(["--object", "fixture_test", "--exclude", r"_test\.cpp$",
                                   "--fail-under", "regions=100", "--profile-dir", str(built / "profiles"),
                                   "--", "./fixture_test"])
    out = capsys.readouterr().out
    assert status == 0, out
    assert "C++ coverage: lines 100.00%, functions 100.00%, branches 100.00%, regions 100.00%" in out


def test_an_unrun_object_counts_against_the_total(built, capsys):
    argv = ["--object", "fixture_test", "--object", "app", "--exclude", r"_test\.cpp$",
            "--profile-dir", str(built / "profiles"), "--", "./fixture_test"]

    assert ci_cpp_coverage.main(argv) == 1
    out = capsys.readouterr().out
    assert "  app.cpp: lines 0/1 (0.00%), functions 0/1 (0.00%)" in out
    assert "fixture.cpp:" not in out

    assert ci_cpp_coverage.main(["--fail-under", "0", *argv]) == 0
