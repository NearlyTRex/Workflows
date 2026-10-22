"""Turn the shell coverage plugins on for the pytest runs in the rest of the job.

The plugins come from pytest-testrex, which the project installs with its test
requirements as pytest-testrex[shell]; installed, pytest loads them itself. This
checks they are there, then reads SOURCE, EXCLUDE (space-separated paths and globs),
FAIL_UNDER, BRANCH and SANDBOX from the environment and appends their options to
PYTEST_ADDOPTS in GITHUB_ENV. pytest reads PYTEST_ADDOPTS whatever command starts
it, so coverage run -m pytest picks them up too. Anything already there is kept.
"""

import importlib.util
import os
import shlex
import sys

REQUIREMENT = "pytest-testrex[shell]"


def build_options(env):
    options = []
    sources = env.get("SOURCE", "").split()
    if sources:
        options += [f"--shell-cov-source={source}" for source in sources]
        options += [f"--shell-cov-exclude={pattern}" for pattern in env.get("EXCLUDE", "").split()]
        if env.get("FAIL_UNDER"):
            options.append(f"--shell-cov-fail-under={env['FAIL_UNDER']}")
        else:
            options.append("--shell-cov-report")
        if env.get("BRANCH") == "true":
            options.append("--shell-cov-branch")
    if env.get("SANDBOX") == "true":
        options.append("--shell-sandbox-required")
    return options


def missing_modules(env, find_spec=importlib.util.find_spec):
    """The modules the asked-for plugins need that the project's Python lacks."""
    needed = ["pytest_testrex"]
    if env.get("SOURCE", "").split():
        needed.append("tree_sitter_bash")
    return [name for name in needed if find_spec(name) is None]


def main(env=None, find_spec=importlib.util.find_spec):
    env = os.environ if env is None else env
    missing = missing_modules(env, find_spec)
    if missing:
        print(f"::error::shell coverage and the shell sandbox come from {REQUIREMENT}, which this Python "
              f"lacks ({', '.join(missing)} not found); add {REQUIREMENT} to the test requirements")
        return 1
    options = shlex.join(build_options(env))
    if existing := env.get("PYTEST_ADDOPTS"):
        options = f"{existing} {options}"
    with open(env["GITHUB_ENV"], "a", encoding="utf-8") as out:
        out.write(f"PYTEST_ADDOPTS={options}\n")
    print(f"PYTEST_ADDOPTS={options}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
