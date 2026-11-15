"""pytest_options.py, which turns pytest-testrex's JavaScript coverage on for the job's later pytest runs."""

import shlex
import sys

from script_helpers import finds, load_script, written

pytest_options = load_script("actions/js-coverage/pytest_options.py")

MODULES = ("pytest_testrex", "tree_sitter_javascript")
PROGRAMS = ("node", "eslint")


def test_no_source_adds_nothing():
    assert pytest_options.build_options({"FAIL_UNDER": "100", "BRANCH": "true"}) == []


def test_every_option():
    assert pytest_options.build_options({
        "SOURCE": "lib  main.js", "EXCLUDE": "vendor/* *.min.js", "FAIL_UNDER": "90", "BRANCH": "true",
    }) == [
        "--js-cov-source=lib", "--js-cov-source=main.js",
        "--js-cov-exclude=vendor/*", "--js-cov-exclude=*.min.js", "--js-cov-fail-under=90",
        "--js-cov-branch",
    ]


def test_without_a_floor_it_only_reports():
    options = pytest_options.build_options({"SOURCE": "lib", "FAIL_UNDER": "", "BRANCH": "false"})
    assert options == ["--js-cov-source=lib", "--js-cov-report"]


def test_names_the_missing_modules_and_programs():
    assert pytest_options.missing_modules(finds()) == list(MODULES)
    assert pytest_options.missing_modules(finds("pytest_testrex")) == ["tree_sitter_javascript"]
    assert pytest_options.missing_programs(finds("node")) == ["eslint"]
    assert pytest_options.missing_programs(finds(*PROGRAMS)) == []


def test_writes_the_job_environment(tmp_path, capsys):
    github_env = tmp_path / "env"
    env = {"GITHUB_ENV": str(github_env), "SOURCE": "my lib/x.js"}
    assert pytest_options.main(env, finds(*MODULES), finds(*PROGRAMS)) == 0
    assert shlex.split(written(github_env)["PYTEST_ADDOPTS"]) == [
        "--js-cov-source=my", "--js-cov-source=lib/x.js", "--js-cov-report"]
    assert "PYTEST_ADDOPTS=" in capsys.readouterr().out


def test_keeps_what_the_job_already_set(tmp_path, monkeypatch, capsys):
    github_env = tmp_path / "env"
    monkeypatch.setenv("GITHUB_ENV", str(github_env))
    monkeypatch.setenv("PYTEST_ADDOPTS", "--shell-sandbox-required")
    monkeypatch.setenv("SOURCE", "lib")
    monkeypatch.setenv("FAIL_UNDER", "100")
    monkeypatch.delenv("BRANCH", raising=False)
    monkeypatch.delenv("EXCLUDE", raising=False)
    monkeypatch.setattr(sys, "argv", ["pytest_options.py"])
    assert pytest_options.main(find_spec=finds(*MODULES), which=finds(*PROGRAMS)) == 0
    assert written(github_env)["PYTEST_ADDOPTS"] == (
        "--shell-sandbox-required --js-cov-source=lib --js-cov-fail-under=100")
    capsys.readouterr()


def test_a_project_without_the_plugin_is_told_what_to_add(tmp_path, capsys):
    github_env = tmp_path / "env"
    env = {"GITHUB_ENV": str(github_env), "SOURCE": "lib"}

    assert pytest_options.main(env, finds(), finds(*PROGRAMS)) == 1

    out = capsys.readouterr().out
    assert "pytest_testrex, tree_sitter_javascript not found" in out and "add pytest-testrex[js]" in out
    assert not github_env.exists()


def test_a_missing_eslint_fails_instead_of_skipping(tmp_path, capsys):
    github_env = tmp_path / "env"
    env = {"GITHUB_ENV": str(github_env), "SOURCE": "lib"}

    assert pytest_options.main(env, finds(*MODULES), finds("node")) == 1

    out = capsys.readouterr().out
    assert "eslint not on PATH" in out and "devDependencies" in out
    assert "not found" not in out
    assert not github_env.exists()
