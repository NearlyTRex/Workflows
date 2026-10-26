# Development

## How it fits together

Reusable workflows call the composite actions in [`actions/`](../actions) with `$/actions/...`.
`$/` resolves to this repo at the commit the caller pinned
([GitHub changelog](https://github.blog/changelog/2026-07-30-reference-same-repository-actions-with-self-repository-syntax/)),
so one pin fixes the whole chain. The actions are:

| Action | Purpose |
|---|---|
| `setup-tools` | zizmor, pip-audit, ruff, shellcheck, rumdl and yamllint from [`requirements.txt`](../actions/setup-tools/requirements.txt), installed with hashes into a private venv |
| `coverage` | Runs the tests under hash-locked coverage.py and enforces the threshold |
| `shell-coverage` | Turns pytest-testrex's shell coverage and sandbox on for the job's later pytest runs |
| `version` | Reads or bumps the version; [`version.py`](../actions/version/version.py) runs locally too |
| `zizmor` | zizmor with [this config](../actions/zizmor/zizmor.yml) |
| `gitleaks`, `trivy`, `trivy-config`, `actionlint` | Container actions. Their `Dockerfile` pins the image by digest |
| `scan-range` | Works out which commits a secret scan covers: a pull request's, a push's, or the whole history |
| `publish-release` | Tags and publishes a release all or nothing, or prints what it would do (`dry-run`) |
| `tag-exists` | Whether a tag exists on GitHub, for `release-check` and `release` |
| `release-pr` | Commits the bumped version on `release/<tag>` and opens or updates its pull request |
| `diff-paths` | Stages the changes under some paths and writes them as a patch, for `scheduled-update` |
| `commit-patch` | Applies a `diff-paths` patch, commits it as `github-actions[bot]` and pushes it, by force for a pull request's branch |
| `open-pr` | Opens a pull request, or updates the one already open from the branch, for `release-pr` and `scheduled-update` |
| `wait-for-url` | Waits for a URL to answer below 400, by the clock, for `compose-test` |
| `shellcheck` | Finds tracked shell scripts by extension and shebang, and shellchecks them |
| `llvm` | An LLVM release from apt.llvm.org beside the runner's clang, for `cpp-build`'s `llvm-version` |
| `openorbis` | A pinned OpenOrbis PS4 Toolchain release, its LLVM and its CMake toolchain and helpers, for `cpp-build`'s `openorbis-version` |
| `build-env` | Parallel jobs, ccache, and `ci-phase`, `ci-clang-tidy`, `ci-cpp-coverage` and `ci-msix` on `PATH` for `cpp-build` |
| `ccache-stats` | Prints ccache's statistics and warns when `ccache-max-size` was too small |
| `windows-test` | Runs a folder of test programs on Windows under full page heap, and a failing one again under cdb, for `windows-test` |
| `check-json`, `check-toml` | Parse every tracked JSON or TOML file |
| `psscriptanalyzer` | PSScriptAnalyzer over tracked PowerShell, errors only unless the repo has settings |
| `clang-format` | Checks C and C++ formatting when the repo has a `.clang-format`, with its own hash-locked clang-format |
| `check-tracked-files` | Fails when a file that could hold a credential is tracked |
| `check-markdown-fences` | Fails on Markdown code fences that don't close, which hide text from the lint rules |
| `python-build` | Builds the sdist and wheel without isolation, from the repo's hash-locked build backend, for `python-build` |
| `pypi-publish` | Exchanges the job's OIDC token at PyPI and uploads the `python-build` artifact with hash-locked twine; a step in the caller's own job, never a reusable workflow |

Every third-party action, tool and image is pinned by hash or digest. Dependabot updates all of
them weekly, 7 days behind upstream.

## Working on this repo

```bash
python3 -m venv .venv && .venv/bin/pip install --require-hashes --no-deps -r requirements-dev.txt
.venv/bin/pip install --require-hashes --no-deps -r actions/coverage/requirements.txt
.venv/bin/python -m coverage run -m pytest -q && .venv/bin/python -m coverage report --fail-under=100
```

This repo holds itself to the same 100% gate.

The tests mirror the repository: `actions/<name>/<script>.py` is tested in
`tests/<suite>/actions/<name>/test_<script>.py`, and `scripts/` likewise. A test that runs a program
outside the process, `git` included, is in `tests/integration` and declares it with
`pytest.mark.requires_binary`; the rest are in `tests/unit`. Scripts are loaded with
`script_helpers.load_script`, and scratch repositories come from `git_helpers`.
[pytest-testrex](https://github.com/NearlyTRex/TestRex)'s meta suite, `tests/unit/meta`, holds every
test to that layout and to its quality rules, and the code to the documentation and coding
standards in `pyproject.toml`'s `[tool.testrex]`, whose baseline counts only ever go down.

`self-ci.yml` and `self-security.yml` run this repo's checks through its own reusable workflows. The
fixtures in `tests/fixtures` go through `python-ci`, `cpp-build` (the PS4 fixture with
`openorbis-version`, packaged and validated, the Switch fixture with `devkitpro`, made into an NRO,
and the UWP fixture with `msvc-uwp`, packaged by `ci-msix`), `compose-test`, `inno-setup` and
`container-scan`, and `release.yml` runs as a dry run over the C++ fixture's builds, so a change
here is tested by the same workflows callers use, release included. The C++ fixture's builds are
also attested for real, except on pull requests from forks, which can't sign. The library releases
itself from `VERSION` through `self-prepare-release.yml` and `self-release.yml`, which runs all of
`self-ci.yml` before it publishes: every repo pinned here is offered the release.

### Canary runs before a release

The fixtures are small; a real repo's size (a long history to scan, thousands of files to
compile) is where some problems show. Before merging a release PR, run real repos against the
unreleased commit:

```bash
python3 scripts/canary.py dev/some-change SomeRepo OtherRepo   # names next to this repo, or paths
python3 scripts/canary.py --cleanup SomeRepo OtherRepo          # afterwards
```

For each repo it pushes a `workflows-canary/<sha>` branch off the default branch with every
library pin moved to that commit, and opens a draft PR, so CI and Security run as they would on a
real change. When `security.yml` can be run by hand it starts that too, which scans the whole
history like the weekly run. It then waits for every run and lists how each ended, failing if any
did. It uses your `gh` login and never touches the repos' checkouts: the branch is made in a
temporary worktree. The commit must be pushed first, and release workflows don't run, since they
only run on the default branch.

Logic lives in scripts under `actions/`, called from the workflows, rather than inline in YAML,
so it has tests: Python under the 100% gate, and `analyze.ps1` through pytest. The PowerShell
tests are skipped on a machine without `pwsh`, but always run in CI.

A script is Python unless it must run where Python may not be, such as inside a tool's container.
The shell coverage and sandbox plugins, and their corpus and shfmt cross-check, live in
[TestRex](https://github.com/NearlyTRex/TestRex).

Dependabot has no Chocolatey ecosystem, and can't see the OpenOrbis release `actions/openorbis`
pins or the devkitPro images `cpp-build.yml` names inside an expression, so `self-tool-versions.yml`
checks all three every week, and fails with the version to move to when a newer one is out.

actionlint doesn't understand `$/` yet, so the `actionlint` action leaves those uses out; zizmor
does understand it.
