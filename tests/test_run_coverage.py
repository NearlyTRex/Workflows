"""run_coverage.py over small projects, with the real coverage.py."""

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("run_coverage", ROOT / "actions" / "coverage" / "run_coverage.py")
run_coverage = importlib.util.module_from_spec(spec)
spec.loader.exec_module(run_coverage)

COVERED = "def double(x):\n    return x * 2\n"
HALF = "def sign(x):\n    if x < 0:\n        return -1\n    return 1\n"


def project(tmp_path, monkeypatch, module, test, **extra):
    for name, text in {"app.py": module, "test_app.py": test, **extra}.items():
        (tmp_path / name).write_text(text)
    monkeypatch.chdir(tmp_path)


def run(env_extra=None, summary=None):
    env = {"TEST_COMMAND": "pytest -q -p no:cacheprovider", "FAIL_UNDER": "100"}
    if summary:
        env["GITHUB_STEP_SUMMARY"] = str(summary)
    return run_coverage.main({**env, **(env_extra or {})})


def test_a_fully_covered_project_passes(tmp_path, monkeypatch, capfd):
    project(tmp_path, monkeypatch, COVERED, "from app import double\n\ndef test():\n    assert double(2) == 4\n")
    summary = tmp_path / "summary.md"
    assert run(summary=summary) == 0
    assert summary.read_text().startswith("### Coverage (minimum 100%)\n\n|")


def test_without_config_every_file_counts_with_branches(tmp_path, monkeypatch, capfd):
    project(tmp_path, monkeypatch, HALF, "from app import sign\n\ndef test():\n    assert sign(1) == 1\n",
            **{"unimported.py": "x = 1\n"})
    summary = tmp_path / "summary.md"
    assert run(summary=summary) == 2
    text = summary.read_text()
    assert "app.py" in text and "unimported.py" in text


def test_the_projects_own_config_is_used(tmp_path, monkeypatch, capfd):
    # Measures only app.py, so the unimported module doesn't count.
    project(tmp_path, monkeypatch, COVERED, "from app import double\n\ndef test():\n    assert double(1) == 2\n",
            **{"unimported.py": "x = 1\n", ".coveragerc": "[run]\ninclude = app.py\n"})
    assert run() == 0


def test_pyproject_config_is_used(tmp_path, monkeypatch, capfd):
    project(tmp_path, monkeypatch, COVERED, "from app import double\n\ndef test():\n    assert double(1) == 2\n",
            **{"unimported.py": "x = 1\n", "pyproject.toml": "[tool.coverage.run]\ninclude = ['app.py']\n"})
    assert run_coverage.has_own_config()
    assert run() == 0


def test_a_pyproject_without_coverage_config(tmp_path, monkeypatch):
    project(tmp_path, monkeypatch, COVERED, "", **{"pyproject.toml": "[project]\nname = 'x'\n"})
    assert not run_coverage.has_own_config()


def test_failing_tests_fail_before_the_report(tmp_path, monkeypatch, capfd):
    project(tmp_path, monkeypatch, COVERED, "def test():\n    assert False\n")
    summary = tmp_path / "summary.md"
    assert run(summary=summary) == 1
    assert not summary.exists()


def test_the_threshold_and_the_process_environment(tmp_path, monkeypatch, capfd):
    project(tmp_path, monkeypatch, HALF, "from app import sign\n\ndef test():\n    assert sign(1) == 1\n")
    monkeypatch.setenv("TEST_COMMAND", "pytest -q -p no:cacheprovider")
    monkeypatch.setenv("FAIL_UNDER", "10")
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
    monkeypatch.setattr(sys, "argv", ["run_coverage.py"])
    assert run_coverage.main() == 0
