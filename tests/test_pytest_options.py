"""pytest_options.py, which turns pytest-testrex's shell plugins on for the job's later pytest runs."""

import importlib.util
import shlex
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("pytest_options", ROOT / "actions" / "shell-coverage" / "pytest_options.py")
pytest_options = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pytest_options)


def written(path):
    return dict(line.split("=", 1) for line in path.read_text().splitlines())


def finds(*present):
    return lambda name: object() if name in present else None


def test_nothing_on_adds_nothing():
    assert pytest_options.build_options({}) == []


def test_every_option():
    assert pytest_options.build_options({
        "SOURCE": "scripts  install.sh", "EXCLUDE": "vendor/* */gradlew", "FAIL_UNDER": "90",
        "BRANCH": "true", "SANDBOX": "true",
    }) == [
        "--shell-cov-source=scripts", "--shell-cov-source=install.sh",
        "--shell-cov-exclude=vendor/*", "--shell-cov-exclude=*/gradlew", "--shell-cov-fail-under=90",
        "--shell-cov-branch", "--shell-sandbox-required",
    ]


def test_without_a_floor_it_only_reports():
    options = pytest_options.build_options({"SOURCE": "scripts", "FAIL_UNDER": "", "BRANCH": "false"})
    assert options == ["--shell-cov-source=scripts", "--shell-cov-report"]


def test_the_sandbox_alone():
    assert pytest_options.build_options({"SANDBOX": "true"}) == ["--shell-sandbox-required"]


def test_the_parser_is_needed_only_for_coverage():
    assert pytest_options.missing_modules({"SANDBOX": "true"}, finds()) == ["pytest_testrex"]
    assert pytest_options.missing_modules({"SOURCE": "scripts"}, finds("pytest_testrex")) == ["tree_sitter_bash"]
    assert pytest_options.missing_modules({"SOURCE": "scripts"}, finds("pytest_testrex", "tree_sitter_bash")) == []


def test_writes_the_job_environment(tmp_path, capsys):
    github_env = tmp_path / "env"
    env = {"GITHUB_ENV": str(github_env), "SOURCE": "my scripts/x.sh"}
    assert pytest_options.main(env, finds("pytest_testrex", "tree_sitter_bash")) == 0
    values = written(github_env)
    assert "PYTHONPATH" not in values
    assert shlex.split(values["PYTEST_ADDOPTS"]) == [
        "--shell-cov-source=my", "--shell-cov-source=scripts/x.sh", "--shell-cov-report"]
    assert "PYTEST_ADDOPTS=" in capsys.readouterr().out


def test_keeps_what_the_job_already_set(tmp_path, monkeypatch, capsys):
    github_env = tmp_path / "env"
    monkeypatch.setenv("GITHUB_ENV", str(github_env))
    monkeypatch.setenv("PYTEST_ADDOPTS", "-x")
    monkeypatch.setenv("SANDBOX", "true")
    monkeypatch.delenv("SOURCE", raising=False)
    monkeypatch.setattr(sys, "argv", ["pytest_options.py"])
    assert pytest_options.main(find_spec=finds("pytest_testrex")) == 0
    assert written(github_env)["PYTEST_ADDOPTS"] == "-x --shell-sandbox-required"
    capsys.readouterr()


def test_a_project_without_the_plugins_is_told_what_to_add(tmp_path, capsys):
    github_env = tmp_path / "env"
    env = {"GITHUB_ENV": str(github_env), "SOURCE": "scripts"}

    assert pytest_options.main(env, finds()) == 1

    out = capsys.readouterr().out
    assert "pytest_testrex, tree_sitter_bash not found" in out and "add pytest-testrex[shell]" in out
    assert not github_env.exists()
