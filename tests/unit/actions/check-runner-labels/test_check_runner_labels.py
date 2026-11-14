"""check_runner_labels.py, over workflow files written here."""

import runpy
import sys

import pytest

from script_helpers import ROOT, load_script

check_runner_labels = load_script("actions/check-runner-labels/check_runner_labels.py")

WORKFLOW = """\
# ubuntu-latest in a comment is fine
jobs:
  build:
    runs-on: ubuntu-latest
    strategy:
      matrix:
        os: [ubuntu-24.04, macos-latest-xlarge]  # windows-latest here too
    with:
      msvc: ${{ matrix.os == 'windows-latest' }}
      label: issue#windows-latest
"""


def test_finds_each_label_outside_comments():
    assert check_runner_labels.latest_labels(WORKFLOW) == [
        (4, 14, "ubuntu-latest"), (7, 28, "macos-latest-xlarge"), (9, 31, "windows-latest"),
        (10, 20, "windows-latest")]


def test_annotates_every_label_and_fails(tmp_path, capsys):
    (tmp_path / "ci.yml").write_text(WORKFLOW, encoding="utf-8")
    (tmp_path / "pinned.yaml").write_text("runs-on: ubuntu-24.04\n", encoding="utf-8")
    (tmp_path / "notes.md").write_text("runs-on: ubuntu-latest\n", encoding="utf-8")
    assert check_runner_labels.main([str(tmp_path)]) == 1
    lines = capsys.readouterr().out.splitlines()
    assert len(lines) == 4
    assert lines[0] == (f"::error file={tmp_path}/ci.yml,line=4,col=14::ubuntu-latest moves to a new image "
                        "without a commit; name a version, such as ubuntu-24.04, windows-2025 or macos-15")


def test_pinned_workflows_pass(tmp_path, monkeypatch, capsys):
    workflows = tmp_path / ".github" / "workflows"
    workflows.mkdir(parents=True)
    (workflows / "ci.yml").write_text("runs-on: ubuntu-24.04\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    assert check_runner_labels.main([]) == 0
    assert capsys.readouterr().out == "No -latest runner labels in .github/workflows\n"


def test_reads_its_arguments_when_run_as_a_script(tmp_path, monkeypatch):
    (tmp_path / "ci.yml").write_text("runs-on: windows-latest\n", encoding="utf-8")
    monkeypatch.setattr(sys, "argv", ["check_runner_labels.py", str(tmp_path)])
    with pytest.raises(SystemExit) as exit_info:
        runpy.run_path(str(ROOT / "actions" / "check-runner-labels" / "check_runner_labels.py"), run_name="__main__")
    assert exit_info.value.code == 1
