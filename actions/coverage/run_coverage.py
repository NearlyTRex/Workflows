"""Run the tests under coverage.py and fail when the total is below the threshold.

Reads TEST_COMMAND (a module and its arguments, run as "coverage run -m ...") and FAIL_UNDER
from the environment, in the working directory the action gives it. Uses the repo's
.coveragerc or [tool.coverage] config when it has one; otherwise it measures every Python file
under the working directory with branch coverage, so a module no test imports still counts.
The report also goes on the run's summary page, so a failing gate shows what is missing
without opening the log. When coverage measured nothing but test files, which always run, the
total says nothing about the code; that only warns, since it can't tell a mistake from a repo
whose code really is all tests.
"""

import json
import os
import re
import shlex
import subprocess
import sys
from pathlib import Path


def has_own_config():
    if Path(".coveragerc").is_file():
        return True
    pyproject = Path("pyproject.toml")
    return pyproject.is_file() and re.search(r"(?m)^\[tool\.coverage", pyproject.read_text()) is not None


def coverage(*arguments, **options):
    return subprocess.run([sys.executable, "-m", "coverage", *arguments], check=False, **options)


def is_test_file(path):
    parts = Path(path).parts
    name = parts[-1]
    return ("tests" in parts[:-1] or "test" in parts[:-1] or name.startswith("test_")
            or name.endswith("_test.py") or name == "conftest.py")


def only_tests_warning():
    """A workflow warning when the data covers no file but tests, else None."""
    report = coverage("json", "-o", "-", "--quiet", capture_output=True, text=True)
    files = list(json.loads(report.stdout).get("files", {})) if report.returncode == 0 else []
    if any(not is_test_file(path) for path in files):
        return None
    return ("::warning title=Coverage may not measure your code::Coverage measured "
            + ("no files" if not files else "only test files")
            + ", so its total says nothing about the code under test. Code the tests run in a subprocess "
              "is only measured when coverage follows it: set [run] source to the code, and add "
              "parallel = true and patch = subprocess to the coverage config.")


def main(env=None):
    env = os.environ if env is None else env
    fail_under = env["FAIL_UNDER"]
    options = [] if has_own_config() else ["--branch", "--source=."]
    tests = coverage("run", *options, "-m", *shlex.split(env["TEST_COMMAND"]))
    if tests.returncode != 0:
        return tests.returncode

    status = coverage("report", f"--fail-under={fail_under}", "--show-missing", "--skip-covered").returncode
    if warning := only_tests_warning():
        print(warning)
    if step_summary := env.get("GITHUB_STEP_SUMMARY"):
        table = coverage("report", "--format=markdown", "--show-missing", "--skip-covered",
                         capture_output=True, text=True).stdout
        with open(step_summary, "a", encoding="utf-8") as out:
            out.write(f"### Coverage (minimum {fail_under}%)\n\n{table}")
    return status


if __name__ == "__main__":
    sys.exit(main())
