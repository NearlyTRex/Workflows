"""pytest_options.py, which hands the shell coverage plugins to the job's later pytest runs."""

import importlib.util
import os
import shlex
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PLUGIN_DIR = ROOT / "actions" / "shell-coverage"
spec = importlib.util.spec_from_file_location("pytest_options", PLUGIN_DIR / "pytest_options.py")
pytest_options = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pytest_options)


def written(path):
    return dict(line.split("=", 1) for line in path.read_text().splitlines())


def test_nothing_on_adds_nothing():
    assert pytest_options.build_options({}) == []


def test_every_option():
    assert pytest_options.build_options({
        "SOURCE": "scripts  install.sh", "EXCLUDE": "vendor/* */gradlew", "FAIL_UNDER": "90",
        "BRANCH": "true", "SANDBOX": "true",
    }) == [
        "-p", "pytest_shell_cov", "--shell-cov-source=scripts", "--shell-cov-source=install.sh",
        "--shell-cov-exclude=vendor/*", "--shell-cov-exclude=*/gradlew", "--shell-cov-fail-under=90",
        "--shell-cov-branch", "-p", "pytest_shell_sandbox", "--shell-sandbox-required",
    ]


def test_without_a_floor_it_only_reports():
    options = pytest_options.build_options({"SOURCE": "scripts", "FAIL_UNDER": "", "BRANCH": "false"})
    assert options == ["-p", "pytest_shell_cov", "--shell-cov-source=scripts", "--shell-cov-report"]


def test_the_sandbox_alone():
    assert pytest_options.build_options({"SANDBOX": "true"}) == [
        "-p", "pytest_shell_sandbox", "--shell-sandbox-required"]


def test_writes_the_job_environment(tmp_path, capsys):
    github_env = tmp_path / "env"
    assert pytest_options.main({"GITHUB_ENV": str(github_env), "SOURCE": "my scripts/x.sh"}) == 0
    values = written(github_env)
    assert values["PYTHONPATH"] == str(PLUGIN_DIR)
    assert shlex.split(values["PYTEST_ADDOPTS"]) == [
        "-p", "pytest_shell_cov", "--shell-cov-source=my", "--shell-cov-source=scripts/x.sh", "--shell-cov-report"]
    assert "PYTEST_ADDOPTS=" in capsys.readouterr().out


def test_keeps_what_the_job_already_set(tmp_path, monkeypatch, capsys):
    github_env = tmp_path / "env"
    monkeypatch.setenv("GITHUB_ENV", str(github_env))
    monkeypatch.setenv("PYTHONPATH", "/mine")
    monkeypatch.setenv("PYTEST_ADDOPTS", "-x")
    monkeypatch.setenv("SANDBOX", "true")
    monkeypatch.delenv("SOURCE", raising=False)
    monkeypatch.setattr(sys, "argv", ["pytest_options.py"])
    assert pytest_options.main() == 0
    values = written(github_env)
    assert values["PYTHONPATH"] == f"{PLUGIN_DIR}{os.pathsep}/mine"
    assert values["PYTEST_ADDOPTS"] == "-x -p pytest_shell_sandbox --shell-sandbox-required"
