"""Turn the shell coverage plugins on for the pytest runs in the rest of the job.

Reads SOURCE, EXCLUDE (space-separated paths and globs), FAIL_UNDER, BRANCH and
SANDBOX from the environment, and appends to GITHUB_ENV: PYTHONPATH with this
directory first, so pytest can load the plugins, and PYTEST_ADDOPTS with the
plugin options. pytest reads PYTEST_ADDOPTS whatever command starts it, so
coverage run -m pytest picks them up too. Anything already in either variable
is kept.
"""

import os
import shlex
import sys

PLUGIN_DIR = os.path.dirname(os.path.abspath(__file__))


def build_options(env):
    options = []
    sources = env.get("SOURCE", "").split()
    if sources:
        options += ["-p", "pytest_shell_cov"]
        options += [f"--shell-cov-source={source}" for source in sources]
        options += [f"--shell-cov-exclude={pattern}" for pattern in env.get("EXCLUDE", "").split()]
        if env.get("FAIL_UNDER"):
            options.append(f"--shell-cov-fail-under={env['FAIL_UNDER']}")
        else:
            options.append("--shell-cov-report")
        if env.get("BRANCH") == "true":
            options.append("--shell-cov-branch")
    if env.get("SANDBOX") == "true":
        options += ["-p", "pytest_shell_sandbox", "--shell-sandbox-required"]
    return options


def main(env=None):
    env = os.environ if env is None else env
    options = shlex.join(build_options(env))
    if existing := env.get("PYTEST_ADDOPTS"):
        options = f"{existing} {options}"
    path = os.pathsep.join(part for part in (PLUGIN_DIR, env.get("PYTHONPATH")) if part)
    with open(env["GITHUB_ENV"], "a", encoding="utf-8") as out:
        out.write(f"PYTHONPATH={path}\nPYTEST_ADDOPTS={options}\n")
    print(f"PYTEST_ADDOPTS={options}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
