# Python

| Workflow | Inputs (defaults) | Caller grants |
|---|---|---|
| `python-ci.yml` | `python-version` (3.12), `working-directory` (.), `lock-file` (requirements-dev.txt), `ruff` (true), `ruff-select` (E4,E7,E9,F), `commands`, `test-command` (pytest -q), `coverage` (true), `coverage-fail-under` (100), `shell-coverage-source`, `shell-coverage-exclude`, `shell-coverage-fail-under` (100), `shell-coverage-branch` (false), `shell-sandbox` (false) | `contents: read` |
| `python-build.yml` | `python-version` (3.12), `working-directory` (.), `lock-file` (requirements-build.txt), `artifact-name` (dist) | `contents: read` |

## `python-ci`

- **Install:** from the hash-locked `lock-file` when there is one, otherwise with
  `pip install -e ".[dev]"`. Downloads are cached between runs, keyed on `pyproject.toml` and
  the `requirements*.txt` files.
- **Ruff:** uses the repo's own `[tool.ruff]` or `ruff.toml` when present. Otherwise it checks
  `ruff-select`: syntax errors, undefined names, unused imports.
- **Commands:** `commands` runs extra checks between install and tests.
- **Coverage:** the tests run under coverage.py and fail below `coverage-fail-under`, which
  is 100 by default.
  - **Without config:** the gate measures every Python file under the working directory, with
    branch coverage, so a module no test imports still counts.
  - **With config:** a repo's `.coveragerc` or `[tool.coverage]` replaces that, for example to
    omit a generated file.
  - **Test command:** with coverage on, `test-command` must be a Python module and its
    arguments, like `pytest -q`.
  - **Run page:** the coverage table, with missing lines, is also written to the job summary.
  - **Only tests measured:** when coverage measured nothing but test files, it warns: the code
    probably runs in a subprocess coverage isn't following. Set `[run] source` to the code and
    add `parallel = true` and `patch = subprocess` to the coverage config.
  - **Parallel runs:** the per-process data files that `parallel = true` leaves, from
    subprocesses or pytest-xdist workers (`test-command: pytest -n auto`), are combined before
    the report.
- **Shell coverage:** `shell-coverage-source` names scripts, or directories searched for `*.sh`
  and `*.bash`, that the pytest tests must run. The tests run them with
  `subprocess.run(["bash", "scripts/x.sh", ...])`; every bash they start is traced, and the run
  fails below `shell-coverage-fail-under`, 100 by default, or empty to only report. It and the
  shell sandbox are [pytest-testrex](https://github.com/NearlyTRex/TestRex)'s plugins, so the
  repo lists `pytest-testrex[shell]` in its hash-locked test requirements; the job fails, saying
  so, when it doesn't.

  ```yaml
  with:
    shell-coverage-source: scripts install.sh
  ```

  - **Statements:** the unit is a statement, read from each script's syntax tree by
    tree-sitter-bash. A command over several lines counts once. The report lists each script's
    missed lines, then the source of each missed statement.
  - **Branches:** `cmd || { echo failed; exit 1; }` counts as run whenever `cmd` ran. With
    `shell-coverage-branch`, the right-hand side of each `&&` and `||` is a statement of its
    own, so the handler needs a test where `cmd` fails. Only right-hand sides with a literal
    command word are counted, not ones like `a && $cmd`.
  - **Exclusions:** a script the parser can't read fails the run, naming it. Leave vendored or
    generated ones out with `shell-coverage-exclude`, globs matched against the path from the
    repo root, where `*` crosses directories: `vendor/* */gradlew`.
  - **Limits:** only bash 4.1 or newer is traced, and only under pytest, since the plugin loads
    through `PYTEST_ADDOPTS`. `sh` doesn't read `BASH_ENV`. A test that builds its own
    environment (`env=...`) takes it from the `shell_traced_env` fixture to stay traced.
- **Shell sandbox:** `shell-sandbox` gives the tests a `shell_sandbox` fixture for scripts
  written for a fresh server. Each run is root in an unprivileged user namespace, with no
  network, and `/etc`, `/var`, `/home`, `/tmp` and the like replaced by empty directories the
  test owns. `PATH` holds recording stubs for `systemctl`, `apt-get`, `curl`, `git`, `docker`
  and others, and a short list of harmless real tools, so an unplanned command fails with
  "command not found" instead of running.

  ```python
  def test_install_enables_the_service(shell_sandbox):
      shell_sandbox.write("/etc/myapp/conf", "port=80\n")
      shell_sandbox.stub("curl", stdout="ok")
      result = shell_sandbox.run("scripts/setup.sh", "install")  # relative to the repo root
      assert result.returncode == 0
      assert shell_sandbox.ran("systemctl", "enable", "myapp")
  ```

  - **Helpers:** arrange with `write`, `add_user` and `stub(name, stdout, code, script)`, run
    with `run` or `run_command`, and read back with `read`, `exists`, `host_path`, `calls` and
    `ran`. Override the `shell_sandbox_options` fixture for `extra_stubs` or `extra_tools`.
  - **Runner:** the workflow lets user namespaces through on runners that restrict them, and
    fails sandbox tests rather than skipping them if they're still unavailable. Locally they
    skip where namespaces are unavailable, such as on macOS.
  - **Locally:** with the test requirements installed, run
    `pytest --shell-cov-source scripts --shell-cov-fail-under 100`.

## Dependency locks

Dependabot only regenerates a pip-compile lock that was compiled from a `.in` file and is named
after it: `requirements.in` becomes `requirements.txt`, and `requirements-dev.in` becomes
`requirements-dev.txt`.

- **Other setups go stale:** a lock compiled from `pyproject.toml`, or named `*.lock`, never gets
  updated. Dependabot bumps ranges in the source file instead.
- **Warnings:** `init_repo.py` warns about both.

The layout that keeps everything current:

```text
requirements.in        runtime dependencies; pyproject.toml reads them with
                       dynamic = ["dependencies"] and
                       [tool.setuptools.dynamic] dependencies = { file = ["requirements.in"] }
requirements-dev.in    -r requirements.in, the test tools, and setuptools for the editable install
requirements.txt       pip-compile --generate-hashes --allow-unsafe --strip-extras -o requirements.txt requirements.in
requirements-dev.txt   same, from requirements-dev.in
```

With `versioning-strategy: lockfile-only`, Dependabot's PRs change only the `.txt` files and keep
their hashes. `init_repo.py` sets that for repos with hash-locked files.

## Publishing to PyPI

`init_repo.py TARGET --pypi` writes a `release.yml` that runs `python-build.yml` after the checks,
attaches the sdist and wheel to the GitHub Release, then publishes them to PyPI under the
`[project]` name in `pyproject.toml`.

The publish job stays in the repo's own `release.yml`, calling the `pypi-publish` action as a step.
PyPI's trusted publishing checks the workflow file that runs the job and does not accept a
reusable workflow from another repository, so a `python-publish.yml` here could never publish.
Building has no such limit, so it is a reusable workflow like the rest.

`pypi-publish` makes the trusted publishing exchange itself rather than wrapping
`pypa/gh-action-pypi-publish`: that action picks its Docker image from the action that calls it,
which inside another repository's composite action is the wrong one
([actions/runner#2473](https://github.com/actions/runner/issues/2473)). It asks GitHub for the job's
OIDC token, exchanges it at PyPI for an upload token that lasts minutes, and uploads with
hash-locked `twine` after `twine check --strict`. It makes no PEP 740 attestations; the GitHub
Release's `SHA256SUMS.txt` covers the same files.

The repo needs:

- **`requirements-build.txt`**, hash-locked, listing `build` and its build backend. The build runs
  without isolation, so nothing unpinned is downloaded:

  ```bash
  printf 'build\nsetuptools\n' > requirements-build.in
  pip-compile --generate-hashes --allow-unsafe --strip-extras -o requirements-build.txt requirements-build.in
  ```

- **A trusted publisher on PyPI** (Account → Publishing; a pending one before the first release)
  naming the repo, workflow `release.yml` and environment `pypi`.
- **A `pypi` environment** in the repo (Settings → Environments).
