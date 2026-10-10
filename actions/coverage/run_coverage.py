"""Run the tests under coverage.py and fail when the total is below the threshold.

Reads TEST_COMMAND (a module and its arguments, run as "coverage run -m ...") and FAIL_UNDER
from the environment, in the working directory the action gives it. Uses the repo's
.coveragerc or [tool.coverage] config when it has one; otherwise it measures every Python file
under the working directory with branch coverage, so a module no test imports still counts.
The report also goes on the run's summary page, so a failing gate shows what is missing
without opening the log.
"""

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


def main(env=None):
    env = os.environ if env is None else env
    fail_under = env["FAIL_UNDER"]
    options = [] if has_own_config() else ["--branch", "--source=."]
    tests = coverage("run", *options, "-m", *shlex.split(env["TEST_COMMAND"]))
    if tests.returncode != 0:
        return tests.returncode

    status = coverage("report", f"--fail-under={fail_under}", "--show-missing", "--skip-covered").returncode
    if step_summary := env.get("GITHUB_STEP_SUMMARY"):
        table = coverage("report", "--format=markdown", "--show-missing", "--skip-covered",
                         capture_output=True, text=True).stdout
        with open(step_summary, "a", encoding="utf-8") as out:
            out.write(f"### Coverage (minimum {fail_under}%)\n\n{table}")
    return status


if __name__ == "__main__":
    sys.exit(main())
