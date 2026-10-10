import importlib.util
import json
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("ci_clang_tidy", ROOT / "actions" / "build-env" / "ci_clang_tidy.py")
ci_clang_tidy = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ci_clang_tidy)

# Stands in for the compiler: "preprocesses" a source by printing it, and fails on one that
# asks it to.
FAKE_COMPILER = """\
import sys
source = next(a for a in sys.argv[1:] if a.endswith(".cpp"))
text = open(source).read()
if "PREPROCESSOR ERROR" in text:
    sys.exit(1)
print(text)
"""

# Stands in for clang-tidy: logs each file it checks, and reports a source containing BAD, as
# a warning-as-error does, or containing NOISE on stdout with a clean exit.
FAKE_TIDY = """\
import os, sys
if sys.argv[1:] == ["--version"]:
    print("fake clang-tidy 1")
    sys.exit(0)
source = sys.argv[-1]
with open(os.environ["TIDY_LOG"], "a") as log:
    log.write(" ".join(sys.argv[1:]) + "\\n")
text = open(source).read()
if "BAD" in text:
    print(f"{source}:2:5: error: bad thing [check-bad]")
    print(f"{os.environ['HEADER']}:1:1: warning: header thing [check-header]")
    sys.exit(1)
if "NOISE" in text:
    print("something to see")
"""


def executable(path, body):
    path.write_text(f"#!{sys.executable}\n{body}")
    path.chmod(0o755)
    return path


@pytest.fixture
def project(tmp_path, monkeypatch):
    """A source tree with a build directory holding compile_commands.json for its sources."""
    root = tmp_path / "repo"
    build = root / "build"
    (root / "src").mkdir(parents=True)
    build.mkdir()
    compiler = executable(tmp_path / "cc", FAKE_COMPILER)
    tidy = executable(tmp_path / "clang-tidy", FAKE_TIDY)
    log = tmp_path / "tidy.log"
    monkeypatch.chdir(root)
    monkeypatch.setenv("TIDY_LOG", str(log))
    monkeypatch.setenv("HEADER", str(root / "src" / "a.h"))
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)

    class Project:
        def __init__(self):
            self.root, self.build, self.tidy, self.log = root, build, tidy, log
            self.entries = []

        def source(self, name, text="int x;\n", directory=None, arguments=False):
            path = (directory or root / "src") / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text)
            command = [str(compiler), "-Iinclude", "-c", str(path), "-o", f"{name}.o"]
            entry = {"directory": str(build), "file": str(path)}
            if arguments:
                entry["arguments"] = command
            else:
                entry["command"] = " ".join(command)
            self.entries.append(entry)
            (build / "compile_commands.json").write_text(json.dumps(self.entries))
            return path

        def run(self, *extra):
            return ci_clang_tidy.main(["-p", str(build), "--clang-tidy", str(tidy), "-j", "2", *extra])

        def checked(self):
            lines = log.read_text().splitlines() if log.exists() else []
            log.unlink(missing_ok=True)
            return sorted(Path(line.split()[-1]).name for line in lines)

    return Project()


def test_checks_the_sources_but_not_the_build_tree(project, capsys):
    project.source("a.cpp")
    project.source("b.cpp", arguments=True)
    project.source("generated.cpp", directory=project.build)
    assert project.run() == 0
    assert project.checked() == ["a.cpp", "b.cpp"]
    out = capsys.readouterr().out
    assert "checked src/a.cpp" in out
    assert "clang-tidy: 2 files, 0 cached, 2 checked, 0 failed" in out


def test_files_and_exclude_choose_by_regex(project):
    project.source("a.cpp")
    project.source("skip.cpp")
    project.source("generated.cpp", directory=project.build)
    assert project.run("--files", r"\.cpp$", "--exclude", "skip") == 0
    assert project.checked() == ["a.cpp", "generated.cpp"]


def test_failures_are_listed_and_annotated_once(project, capsys, monkeypatch, tmp_path):
    summary = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    project.source("a.cpp", "BAD\n")
    project.source("b.cpp", "BAD\n")
    project.source("c.cpp", "NOISE\n")
    project.source("d.cpp")
    outside = project.source("e.cpp", "BAD\n", directory=tmp_path / "elsewhere")
    assert project.run("--files", ".") == 1
    out = capsys.readouterr().out
    assert "FAILED  src/a.cpp" in out and "FAILED  src/c.cpp" in out and "something to see" in out
    assert f"FAILED  {outside}" in out
    # The header's diagnostic comes from every failing file but is annotated once.
    assert out.count("::error file=src/a.h,line=1,col=1::header thing [check-header]") == 1
    assert "::error file=src/a.cpp,line=2,col=5::bad thing [check-bad]" in out
    assert f"::error file={outside},line=2,col=5::" in out
    assert "Failed:\n  " in out
    text = summary.read_text()
    assert "### clang-tidy: 5 files, 0 cached, 5 checked, 4 failed" in text
    assert "| Slowest | Seconds |" in text and "- `src/a.cpp`" in text


def test_no_annotations_off_github(project, capsys):
    project.source("a.cpp", "BAD\n")
    assert project.run() == 1
    assert "::error" not in capsys.readouterr().out


def test_clean_results_are_cached(project, capsys, tmp_path, monkeypatch):
    cache = tmp_path / "cache"
    project.source("a.cpp")
    b = project.source("b.cpp")
    project.source("bad.cpp", "BAD\n")
    assert project.run("--cache-dir", str(cache)) == 1
    assert project.checked() == ["a.cpp", "b.cpp", "bad.cpp"]
    assert len(list(cache.iterdir())) == 2  # failures are never kept

    capsys.readouterr()
    summary = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    b.write_text("int y;\n")
    assert project.run("--cache-dir", str(cache)) == 1
    assert project.checked() == ["b.cpp", "bad.cpp"]
    assert "1 cached, 2 checked" in capsys.readouterr().out
    # b's old entry is gone with the code it described.
    assert len(list(cache.iterdir())) == 2


def test_the_config_is_part_of_the_key(project, tmp_path):
    cache = tmp_path / "cache"
    project.source("a.cpp")
    assert project.run("--cache-dir", str(cache)) == 0
    assert project.run("--cache-dir", str(cache)) == 0
    assert project.checked() == ["a.cpp"]

    (project.root / ".clang-tidy").write_text("Checks: '*'\n")
    assert project.run("--cache-dir", str(cache)) == 0
    assert project.checked() == ["a.cpp"]

    config = tmp_path / "tidy.yaml"
    config.write_text("Checks: '-*'\n")
    assert project.run("--cache-dir", str(cache), "--config-file", str(config)) == 0
    assert project.checked() == ["a.cpp"]
    assert project.run("--cache-dir", str(cache), "--config-file", str(config)) == 0
    assert project.checked() == []


def test_the_config_file_is_passed_to_clang_tidy(project, tmp_path):
    config = tmp_path / "tidy.yaml"
    config.write_text("Checks: '-*'\n")
    project.source("a.cpp")
    assert project.run("--config-file", str(config)) == 0
    assert f"--config-file={config}" in project.log.read_text()


def test_all_cached_has_no_slowest_table(project, tmp_path, monkeypatch):
    cache = tmp_path / "cache"
    project.source("a.cpp")
    project.run("--cache-dir", str(cache))
    summary = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    assert project.run("--cache-dir", str(cache)) == 0
    assert summary.read_text() == "### clang-tidy: 1 files, 1 cached, 0 checked, 0 failed in 0s on 2 jobs\n\n"
    assert "Slowest" not in summary.read_text()


def test_a_file_that_does_not_preprocess_is_checked_uncached(project, tmp_path):
    cache = tmp_path / "cache"
    project.source("a.cpp", "PREPROCESSOR ERROR\n")
    assert project.run("--cache-dir", str(cache)) == 0
    assert project.run("--cache-dir", str(cache)) == 0
    assert project.checked() == ["a.cpp", "a.cpp"]
    assert list(cache.iterdir()) == []


def test_without_a_compile_database(project, capsys):
    assert project.run() == 2
    assert "CMAKE_EXPORT_COMPILE_COMMANDS" in capsys.readouterr().err


def test_reads_the_command_line_and_the_environment(project, monkeypatch):
    project.source("a.cpp")
    monkeypatch.setenv("CLANG_TIDY", str(project.tidy))
    monkeypatch.setattr(sys, "argv", ["ci-clang-tidy", "-p", str(project.build)])
    assert ci_clang_tidy.main() == 0
    assert project.checked() == ["a.cpp"]


def test_jobs_default_to_the_cores(monkeypatch):
    monkeypatch.setattr(os, "cpu_count", lambda: None)
    assert ci_clang_tidy.parse_args(["-p", "build"]).jobs == 1
