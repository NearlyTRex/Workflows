"""run_coverage.py over small projects, with the real coverage.py."""

import subprocess
import sys

from script_helpers import load_script

run_coverage = load_script("actions/coverage/run_coverage.py")

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


def test_a_parallel_run_is_combined_before_the_report(tmp_path, monkeypatch, capfd):
    # The code runs in a subprocess coverage follows, which writes data files of its own
    project(tmp_path, monkeypatch, HALF,
            "import subprocess, sys\n\ndef test():\n"
            "    subprocess.run([sys.executable, '-c', 'import app; app.sign(1); app.sign(-1)'], check=True)\n",
            **{".coveragerc": "[run]\nbranch = true\nparallel = true\npatch = subprocess\ninclude = app.py\n"})
    assert run() == 0
    assert not list(tmp_path.glob(".coverage.*"))


def test_data_that_will_not_combine_fails_the_run(monkeypatch, capfd):
    calls = []

    def fake(*arguments, **options):
        calls.append(arguments[0])
        output = "Couldn't combine data file .coverage.x\n" if arguments[0] == "combine" else ""
        return subprocess.CompletedProcess(arguments, 0 if arguments[0] == "run" else 1, output, "")

    monkeypatch.setattr(run_coverage, "coverage", fake)
    assert run() == 1
    assert calls == ["run", "combine"]
    assert "Couldn't combine data file" in capfd.readouterr().out


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


def test_coverage_of_only_the_tests_warns(tmp_path, monkeypatch, capfd):
    # The code runs in a subprocess coverage isn't following, so only the test is measured
    project(tmp_path, monkeypatch, COVERED,
            "import subprocess, sys\n\ndef test():\n    subprocess.run([sys.executable, 'app.py'], check=True)\n",
            **{".coveragerc": "[run]\nomit = app.py\n"})
    (tmp_path / "test_app.py").rename(tmp_path / "test_cli.py")
    assert run() == 0
    assert "::warning title=Coverage may not measure your code::Coverage measured only test files" in capfd.readouterr().out


def test_measuring_no_files_warns(tmp_path, monkeypatch, capfd):
    project(tmp_path, monkeypatch, COVERED, "def test():\n    pass\n", **{".coveragerc": "[run]\ninclude = nothing.py\n"})
    run()
    assert "Coverage measured no files" in capfd.readouterr().out


def test_measured_code_does_not_warn(tmp_path, monkeypatch, capfd):
    project(tmp_path, monkeypatch, COVERED, "from app import double\n\ndef test():\n    assert double(2) == 4\n")
    assert run() == 0
    assert "::warning" not in capfd.readouterr().out


def test_what_counts_as_a_test_file():
    assert all(run_coverage.is_test_file(path) for path in
               ["tests/helpers.py", "test/x.py", "test_app.py", "app_test.py", "src/conftest.py"])
    assert not any(run_coverage.is_test_file(path) for path in ["app.py", "src/testing.py", "contest.py"])
