"""The shell coverage plugin's statement finder, cross-checked against shfmt.

shfmt is required in CI (a missing one fails the run, so the check can't quietly
vanish); locally the tests skip without it. Install it, or point SHFMT at it.
"""

import importlib.util
import os
from pathlib import Path

import pytest

import shfmt_oracle

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location(
    "pytest_shell_cov", ROOT / "actions" / "shell-coverage" / "pytest_shell_cov.py")
shell_cov = importlib.util.module_from_spec(spec)
spec.loader.exec_module(shell_cov)

CORPUS = sorted((ROOT / "tests" / "shell_corpus").glob("*.corpus"))


@pytest.fixture(scope="module")
def shfmt():
    found = shfmt_oracle.find_shfmt()
    if not found:
        if os.environ.get("CI"):
            pytest.fail("shfmt is required in CI: the statement finder is cross-checked against it")
        pytest.skip("shfmt is not installed")
    return found


@pytest.mark.parametrize("path", CORPUS, ids=lambda path: path.stem)
def test_the_corpus_agrees_with_a_real_parser(shfmt, path):
    findings = shfmt_oracle.cross_check(
        path.read_text(), shell_cov.find_statements, shfmt, shfmt_oracle.plugin_branches(shell_cov))
    assert findings == {"dropped": [], "phantom": [], "split": [], "merged": [], "branch": []}


# The oracle has to be able to see each kind of mistake, or agreeing proves nothing

SCRIPT = "echo one\\\n  two\nif true; then\n  echo three\nfi\n"


@pytest.mark.parametrize("statements, kind", [
    ([(1, 2), (3, 3)], "dropped"),                       # `echo three` is in no statement
    ([(1, 2), (3, 3), (4, 4), (5, 5)], "phantom"),       # `fi` runs nothing
    ([(1, 1), (3, 3), (4, 4)], "split"),                 # `echo one \ two` cut in two
    ([(1, 4)], "merged"),                                # four lines, three commands, one statement
])
def test_the_oracle_sees_each_kind_of_mistake(shfmt, statements, kind):
    tree, _ = shfmt_oracle.parse(SCRIPT, shfmt)
    findings = shfmt_oracle.compare(statements, shfmt_oracle.find_units(tree))
    assert findings[kind], findings


def test_the_oracle_reports_a_script_shfmt_cannot_parse(shfmt):
    assert shfmt_oracle.cross_check("if then fi (\n", shell_cov.find_statements, shfmt) is None


def test_the_oracle_cli(shfmt, tmp_path, capsys):
    good = tmp_path / "good.sh"
    good.write_text("echo ok\n")
    assert shfmt_oracle.main(["x", str(good)]) == 0
    assert "0 finding(s)" in capsys.readouterr().out
    assert shfmt_oracle.main(["x"]) == 2


def test_the_oracle_cli_flags_a_mismatch(shfmt, tmp_path, capsys):
    bad = tmp_path / "bad.sh"
    bad.write_text("echo a\necho b\n")
    assert shfmt_oracle.main(["x", str(bad)], find_statements=lambda source: [(1, 2)]) == 1
    assert "merged" in capsys.readouterr().out


def test_the_oracle_cli_says_when_shfmt_cannot_parse_a_script(shfmt, tmp_path, capsys):
    bad = tmp_path / "bad.sh"
    bad.write_text("if then fi (\n")
    assert shfmt_oracle.main(["x", str(bad)]) == 0
    assert "shfmt cannot parse it" in capsys.readouterr().out


# --- branches ---------------------------------------------------------------

OPERANDS = """\
a && b || c
[[ x ]] && local y=1
f && {
  g
}
h || (cd /tmp)
i && ! j
k || x=1
l && (( 1 ))
m && [[ n ]]
o && "$cmd"
p && $cmd
q | r && s | t
"""


def test_the_oracle_finds_the_command_each_right_hand_side_starts_with(shfmt):
    tree, _ = shfmt_oracle.parse(OPERANDS, shfmt)
    assert shfmt_oracle.find_right_hand_sides(tree) == {
        (1, "b"), (1, "c"), (2, "local"), (4, "g"), (6, "cd"), (7, "j"), (8, "x="), (9, "(("), (10, "[["),
        (13, "s"),
    }


def test_the_plugin_tracks_a_subset_of_the_real_operands(shfmt):
    tree, _ = shfmt_oracle.parse(OPERANDS, shfmt)
    real = shfmt_oracle.find_right_hand_sides(tree)
    tracked = shfmt_oracle.plugin_branches(shell_cov)(OPERANDS)
    wrong, skipped = shfmt_oracle.compare_branches(tracked, real)
    assert wrong == []
    assert skipped == 1   # the group's body is measured by its own statements


def test_the_oracle_sees_a_branch_the_plugin_invents(shfmt):
    tree, _ = shfmt_oracle.parse("a && b\n", shfmt)
    real = shfmt_oracle.find_right_hand_sides(tree)
    assert shfmt_oracle.compare_branches([(1, "b"), (1, "zzz"), (2, "b")], real) == ([(1, "zzz"), (2, "b")], 0)


def test_the_oracle_cli_flags_an_invented_branch(shfmt, tmp_path, capsys):
    bad = tmp_path / "bad.sh"
    bad.write_text("a && b\n")
    code = shfmt_oracle.main(["x", str(bad)], find_statements=lambda source: [(1, 1)],
                             find_branches=lambda source: [(1, "nope")])
    assert code == 1
    assert "branch" in capsys.readouterr().out
