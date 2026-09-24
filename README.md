# Workflows
Reusable template library for github workflows

My repos call these workflows instead of carrying their own copies. A fix or tool update made here
reaches every repo through one Dependabot bump.

## Setting up a repo

```bash
python3 scripts/init_repo.py ~/Repositories/SomeRepo            # writes .github/
python3 scripts/init_repo.py ~/Repositories/SomeRepo --dry-run  # shows it first
```

It detects the stack (`python`, `cpp`, `shell` or `data`) and the version file, then writes:

| File | What it runs |
|---|---|
| `workflows/ci.yml` | `python-ci` for Python repos, and `lint` for every repo |
| `workflows/security.yml` | `security`, `codeql`, `dependency-review` (PRs) and a `container-scan` per Dockerfile. It also runs weekly |
| `workflows/prepare-release.yml` | Stage one of a release. Only written when a version file is found |
| `workflows/release.yml` | Stage two: checks, then tag and publish |
| `dependabot.yml` | Actions, plus pip, docker, npm and git submodules when the repo has them |

Every call is pinned to the commit of this library's latest release, with the tag as a comment:

```yaml
uses: NearlyTRex/Workflows/.github/workflows/lint.yml@<commit sha> # v0.1.0
```

Dependabot moves that pin forward when this library releases. Existing files are never
overwritten without `--force`. The generated files are a starting point: add `with:` inputs,
and extra jobs such as builds, as the repo needs them.

## Reusable workflows

All of them run with the least permission they need. The caller must grant at least that much.

| Workflow | Inputs (defaults) | Caller grants |
|---|---|---|
| `python-ci.yml` | `python-version` (3.12), `working-directory` (.), `lock-file` (requirements-dev.txt), `ruff` (true), `ruff-select` (E4,E7,E9,F), `commands`, `test-command` (pytest -q), `coverage` (true), `coverage-fail-under` (100) | `contents: read` |
| `lint.yml` | `shellcheck` (true), `json` (true) | `contents: read` |
| `security.yml` | `zizmor` (true), `gitleaks` (true), `pip-audit-files` | `contents: read` |
| `codeql.yml` | `languages` (["actions"]), `build-mode` (none), `build-command` | `actions: read`, `contents: read`, `security-events: write` |
| `dependency-review.yml` | `fail-on-severity` (moderate) | `contents: read` |
| `container-scan.yml` | `dockerfile` (Dockerfile), `context` (.), `severity` (HIGH,CRITICAL) | `contents: read` |
| `prepare-release.yml` | `bump`, `version-file`, `tag-prefix` (v) | `contents: write`, `pull-requests: write` |
| `release.yml` | `version-file`, `tag-prefix` (v), `title`, `draft` (false), `assets` | `contents: write` |

- **`python-ci`:**
  - **Install:** from the hash-locked `lock-file` when there is one, otherwise with
    `pip install -e ".[dev]"`.
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
- **`lint`:** shellchecks every tracked `*.sh` and `*.bash` file, and checks that every tracked
  `*.json` file parses. Each check is skipped when there's nothing to check.
- **`security`:**
  - **zizmor:** audits the workflows and requires every third-party action to be pinned to a
    commit hash.
  - **gitleaks:** scans the whole git history, so a secret that was committed and later deleted
    is still caught. It reads `.gitleaks.toml` for allowlists.
  - **pip-audit:** checks the listed hash-locked files.
- **`container-scan`:** builds the image and fails on HIGH or CRITICAL vulnerabilities that have a
  fix available.

### Python dependency locks

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

### Releases

The version file is the only place a version is written: `pyproject.toml`, `package.json`,
`CMakeLists.txt` (`project(... VERSION x.y.z)`), or a plain `VERSION` file.

1. **Actions → prepare release → Run workflow:** enter `patch`, `minor`, `major` or `x.y.z`. It
   bumps the version file on a `release/vX.Y.Z` branch and opens a PR.
2. **Merge the PR:** `release` sees an untagged version, tags the merge commit and publishes a
   GitHub Release with generated notes.

Pushes that don't change the version do nothing.

`draft: true` publishes a draft to review before announcing. The tag is still created, so the next
push doesn't start a second draft. To attach build outputs, upload them as artifacts in an earlier
job and name them with `assets`:

```yaml
jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - ...
      - uses: actions/upload-artifact@<sha> # vX
        with:
          name: dist-linux
          path: dist/*

  release:
    needs: build
    uses: NearlyTRex/Workflows/.github/workflows/release.yml@<sha> # vX
    permissions:
      contents: write
    with:
      version-file: CMakeLists.txt
      draft: true
      assets: dist-*
```

GitHub doesn't run workflows on a PR opened with the workflow token, so don't make checks required
on release PRs.

## Repo settings

Each repo that uses these workflows needs:

- **Dependency graph and Dependabot alerts** (Settings → Advanced Security). Dependency review
  fails without them:
  `gh api -X PUT repos/OWNER/REPO/vulnerability-alerts`
- **"Allow GitHub Actions to create and approve pull requests"** (Settings → Actions → General),
  for prepare-release:
  `gh api -X PUT repos/OWNER/REPO/actions/permissions/workflow -f default_workflow_permissions=read -F can_approve_pull_request_reviews=true`

`init_repo.py` prints this list after writing the files.

## How it fits together

Reusable workflows call the composite actions in [`actions/`](actions) with `$/actions/...`.
`$/` resolves to this repo at the commit the caller pinned
([GitHub changelog](https://github.blog/changelog/2026-07-30-reference-same-repository-actions-with-self-repository-syntax/)),
so one pin fixes the whole chain. The actions are:

| Action | Purpose |
|---|---|
| `setup-tools` | zizmor, pip-audit, ruff and shellcheck from [`requirements.txt`](actions/setup-tools/requirements.txt), installed with hashes into a private venv |
| `coverage` | Runs the tests under hash-locked coverage.py and enforces the threshold |
| `version` | Reads or bumps the version; [`version.py`](actions/version/version.py) runs locally too |
| `zizmor` | zizmor with [this config](actions/zizmor/zizmor.yml) |
| `gitleaks`, `trivy` | Container actions. Their `Dockerfile` pins the image by digest |
| `check-json` | Parses every tracked JSON file |

Every third-party action, tool and image is pinned by hash or digest. Dependabot updates all of
them weekly, 7 days behind upstream.

## Working on this repo

```bash
python3 -m venv .venv && .venv/bin/pip install --require-hashes --no-deps -r requirements-dev.txt
.venv/bin/pip install --require-hashes --no-deps -r actions/coverage/requirements.txt
.venv/bin/python -m coverage run -m pytest -q && .venv/bin/python -m coverage report --fail-under=100
```

This repo holds itself to the same 100% gate.

`self-ci.yml` and `self-security.yml` run this repo's checks through its own reusable workflows.
The Python fixture in `tests/fixtures/python-app` goes through `python-ci` and `container-scan`,
so a change here is tested by the same workflows callers use. The library releases itself from
`VERSION` through `self-prepare-release.yml` and `self-release.yml`.

actionlint doesn't understand `$/` yet and reports every use of it. zizmor does, and it runs in CI.
