"""Turn the JavaScript coverage plugin on for the pytest runs in the rest of the job.

The plugin comes from pytest-testrex, which the project installs with its test
requirements as pytest-testrex[js]; installed, pytest loads it itself. Its
node --test files and its eslint rule skip when node or eslint is missing, which
in CI would pass a run that checked nothing, so this fails unless both are on
PATH. It then reads SOURCE, EXCLUDE (space-separated paths and globs), FAIL_UNDER
and BRANCH from the environment and appends their options to PYTEST_ADDOPTS in
GITHUB_ENV, keeping anything already there.
"""

import importlib.util
import os
import shlex
import shutil
import sys

REQUIREMENT = "pytest-testrex[js]"


def build_options(env):
    """The pytest options env asks for; none when SOURCE is empty."""
    sources = env.get("SOURCE", "").split()
    if not sources:
        return []
    options = [f"--js-cov-source={source}" for source in sources]
    options += [f"--js-cov-exclude={pattern}" for pattern in env.get("EXCLUDE", "").split()]
    if env.get("FAIL_UNDER"):
        options.append(f"--js-cov-fail-under={env['FAIL_UNDER']}")
    else:
        options.append("--js-cov-report")
    if env.get("BRANCH") == "true":
        options.append("--js-cov-branch")
    return options


def missing_modules(find_spec=importlib.util.find_spec):
    """The modules the plugin needs that the project's Python lacks."""
    return [name for name in ("pytest_testrex", "tree_sitter_javascript") if find_spec(name) is None]


def missing_programs(which=shutil.which):
    """The programs the JS tests and the eslint rule run that are not on PATH."""
    return [name for name in ("node", "eslint") if which(name) is None]


def main(env=None, find_spec=importlib.util.find_spec, which=shutil.which):
    """Append the options to PYTEST_ADDOPTS in GITHUB_ENV; 1 if a module or program is missing."""
    env = os.environ if env is None else env
    status = 0
    if missing := missing_modules(find_spec):
        print(f"::error::JavaScript coverage comes from {REQUIREMENT}, which this Python lacks "
              f"({', '.join(missing)} not found); add {REQUIREMENT} to the test requirements")
        status = 1
    if missing := missing_programs(which):
        print(f"::error::{', '.join(missing)} not on PATH, so the JavaScript tests or eslint would be "
              "skipped; set node-version, and list eslint in package.json's devDependencies")
        status = 1
    if status:
        return status
    options = shlex.join(build_options(env))
    if existing := env.get("PYTEST_ADDOPTS"):
        options = f"{existing} {options}".rstrip()
    with open(env["GITHUB_ENV"], "a", encoding="utf-8") as out:
        out.write(f"PYTEST_ADDOPTS={options}\n")
    print(f"PYTEST_ADDOPTS={options}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
