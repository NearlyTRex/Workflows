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
| `workflows/ci.yml` | `python-ci` for Python repos, `cpp-build` on Linux and Windows for CMake repos, `compose-test` per compose file, `inno-setup` per `.iss` script, and `lint` for every repo |
| `workflows/security.yml` | `security`, `codeql`, `dependency-review` (PRs) and a `container-scan` per Dockerfile. It also runs weekly |
| `workflows/prepare-release.yml` | Stage one of a release. Only written when a version file is found |
| `workflows/release.yml` | Stage two: checks, then tag and publish, building and attaching any Inno Setup installers at the released version |
| `dependabot.yml` | Actions, plus pip, docker, docker-compose, npm and git submodules when the repo has them |

Every call is pinned to the commit of this library's latest release, with the tag as a comment:

```yaml
uses: NearlyTRex/Workflows/.github/workflows/lint.yml@<commit sha> # v0.1.0
```

Dependabot moves that pin forward when this library releases.

`ci.yml` and `security.yml` cancel a superseded run on a pull request, but let every run on
`main` finish: each push's secret scan covers only that push's commits, so a cancelled one would
leave them unscanned until the weekly full scan. `release.yml` runs its checks only when a
release is pending; any other push to `main` has already been tested by CI. Existing files are never
overwritten without `--force`. The generated files are a starting point: add `with:` inputs,
and extra jobs such as builds, as the repo needs them.

## Reusable workflows

All of them run with the least permission they need. The caller must grant at least that much.

| Workflow | Inputs (defaults) | Caller grants |
|---|---|---|
| `python-ci.yml` | `python-version` (3.12), `working-directory` (.), `lock-file` (requirements-dev.txt), `ruff` (true), `ruff-select` (E4,E7,E9,F), `commands`, `test-command` (pytest -q), `coverage` (true), `coverage-fail-under` (100) | `contents: read` |
| `lint.yml` | `shellcheck` (true), `markdown` (true), `json` (true), `yaml` (true), `actionlint` (true) | `contents: read` |
| `security.yml` | `zizmor` (true), `gitleaks` (true), `pip-audit-files`, `tracked-files` (true), `tracked-files-patterns` | `contents: read` |
| `codeql.yml` | `languages` (["actions"]), `build-mode` (none), `build-command` | `actions: read`, `contents: read`, `security-events: write` |
| `dependency-review.yml` | `fail-on-severity` (moderate) | `contents: read` |
| `container-scan.yml` | `dockerfile` (Dockerfile), `context` (.), `severity` (HIGH,CRITICAL), `config-scan` (false) | `contents: read` |
| `prepare-release.yml` | `bump`, `version-file`, `tag-prefix` (v) | `contents: write`, `pull-requests: write` |
| `release-check.yml` | `version-file`, `tag-prefix` (v). Outputs `version`, `tag`, `pending` | `contents: read` |
| `release.yml` | `version-file`, `tag-prefix` (v), `title`, `draft` (false), `assets`, `checksums` (false), `notes-file`, `dry-run` (false) | `contents: write` |
| `compose-test.yml` | `compose-file` (compose.yml), `health-url`, `timeout` (180), `wait` (true), `commands` | `contents: read` |
| `cpp-build.yml` | `build-command`, `runs-on` (ubuntu-latest), `submodules` (false), `fetch-depth` (1), `local-tag`, `apt-packages`, `msvc` (false), `msvc-arch` (x64), `cache-paths`, `cache-key-files`, `cache-key`, `ccache` (false), `ccache-max-size` (500M), `ccache-sloppiness` (PCH-friendly), `parallel` (true), `timeout-minutes` (90), `test-command`, `artifact`, `artifact-path` | `contents: read` |
| `inno-setup.yml` | `script`, `version`, `version-define` (AppVersion), `output-dir` (dist), `smoke-test`, `artifact`, `inno-setup-version` (6.7.1) | `contents: read` |

- **`python-ci`:**
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
- **`lint`:** shellchecks every tracked shell script, found by its `*.sh` or `*.bash` extension
  or by a `sh`, `bash`, `dash` or `ksh` shebang, checks that every tracked
  `*.json` file parses, and lints Markdown with [rumdl](https://github.com/rvben/rumdl), which
  implements the markdownlint rules. Each check is skipped when there's nothing to check.
  - **Markdown fences:** `markdown` also fails on a code fence with text after it, or a code
    block that never closes. Under CommonMark neither ends the block, so the prose after it is
    read as code and the markdownlint rules, which skip code, never see it.
  - **Markdown config:** rumdl reads the repo's `.markdownlint.json`/`.yaml`, `.rumdl.toml` or
    `[tool.rumdl]` in `pyproject.toml`, so a repo sets line length and exclusions there. Without
    one it uses the markdownlint defaults, including 80-column lines. Gitignored files are skipped.
  - **YAML:** lints every tracked `*.yml` and `*.yaml` file with
    [yamllint](https://github.com/adrienverge/yamllint). With the repo's `.yamllint` config it
    reports whatever that asks for; without one, only real errors such as invalid syntax,
    duplicate keys and trailing spaces.
  - **actionlint:** checks the workflows for expression typos, bad inputs, unknown runner labels
    and YAML mistakes, and shellchecks their `run:` scripts.
- **`security`:**
  - **zizmor:** audits the workflows and requires every third-party action to be pinned to a
    commit hash.
  - **gitleaks:** on pull requests and pushes, scans only the new commits, since everything before
    them was scanned when it landed. Scheduled and manual runs scan the whole history, so a
    secret that was committed and later deleted is still caught, as is anything a newer rule
    recognises. A push it can't place, such as a new branch or a force push, also gets the full
    scan. It reads `.gitleaks.toml` for allowlists.
  - **pip-audit:** checks the listed hash-locked files.
  - **tracked-files:** fails if git tracks a file that could hold a credential: `.env` files,
    private keys and certificates. It complements gitleaks, which reads contents and so can miss a
    binary file such as a database an app keeps a token in. `tracked-files-patterns` adds patterns
    (with a `/` they match the path, otherwise the file name); `!pattern` makes an exception, and
    `*.example`, `*.sample` and `*.template` files are always allowed.
- **`container-scan`:** builds the image and fails on HIGH or CRITICAL vulnerabilities that have a
  fix available.
  - **`config-scan`:** also fails on misconfigurations in the Dockerfile's directory, such as a
    container that runs as root. A deliberate exception goes in the repo's `.trivyignore`, with a
    comment saying why. Off by default so existing callers don't go red on a library bump;
    `init_repo.py` turns it on for new repos.
- **`compose-test`:** builds and starts a Compose stack, waits for every service with a
  healthcheck to report healthy (and for `health-url`, when given), then runs `commands` against
  it. Logs are printed on failure and the stack is always removed, volumes included.
  - **One-shot services:** compose counts a container that runs and exits, such as a migration
    or seed job, as a failure to start. For a stack with one, set `wait: false` and give
    `health-url` instead.
- **`cpp-build`:** runs the repo's own build and test commands in bash on any runner (Git Bash on
  Windows), so it fits CMake, a `Setup.py` or a packaging script alike. Around them it installs
  `apt-packages`, puts MSVC on `PATH` with `msvc` (found with vswhere, so no edition path is
  hard-coded), restores `cache-paths` keyed on `cache-key-files`, and uploads `artifact-path` as
  `artifact`. The cache is named by `cache-key`, or else the artifact name, so a CI build and a
  release build of the same thing can share one. For several platforms or configurations, the
  caller passes a matrix:

  ```yaml
  build:
    uses: NearlyTRex/Workflows/.github/workflows/cpp-build.yml@<sha> # vX
    permissions:
      contents: read
    strategy:
      matrix:
        os: [ubuntu-latest, windows-latest]
    with:
      runs-on: ${{ matrix.os }}
      msvc: ${{ matrix.os == 'windows-latest' }}
      build-command: cmake -S . -B build && cmake --build build --config Release
      test-command: ctest --test-dir build -C Release --output-on-failure
  ```

  Builds use every core: `parallel` sets `CMAKE_BUILD_PARALLEL_LEVEL`, and `MAKEFLAGS=-j` off
  Windows, where plain `make` would otherwise run one job at a time.

  With `ccache: true` on Linux runners, it also installs ccache and routes the compilers through
  it, CMake's through its launcher setting and any other build system's through ccache's
  wrappers first on `PATH`, so a build only recompiles what changed since the last run. Each run
  saves its ccache under a new key and the next restores the newest one, so the cache follows the
  code, and the hit rate is printed after the build.

  By default ccache refuses compiles that use a precompiled header, so `ccache-sloppiness`
  defaults to `pch_defines,time_macros,include_file_mtime,include_file_ctime`, which accepts
  them and ignores the fresh file times every checkout has. With clang, also build the header
  with `-Xclang=-fno-pch-timestamp`: otherwise each rebuilt `.pch` differs from the last, and
  every file that includes it misses. The stats list any calls still uncacheable, and why.

  A release build that stamps `git describe` into the binary should pass `local-tag:` with
  `release-check`'s `tag` output. `release.yml` only tags the commit once the builds are done, so
  otherwise the binary names a bare commit instead of the release. The tag is created in the
  build's checkout only and never pushed.
- **`inno-setup`:** compiles a Windows installer on `windows-latest`, passing `version` as
  `/DAppVersion=...`, then runs the repo's `smoke-test` PowerShell and, with `artifact`, uploads the
  `.exe` for `release.yml` to attach. Run it in CI with a placeholder version so a broken script
  fails the pull request rather than the release. Installing Inno Setup from Chocolatey is
  retried, since the community feed sometimes rate-limits or times out.

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

1. **Actions → Prepare Release → Run workflow:** enter `patch`, `minor`, `major` or `x.y.z`. It
   bumps the version file on a `release/vX.Y.Z` branch and opens a PR.
2. **Merge the PR:** Release sees an untagged version, tags the merge commit and publishes a
   GitHub Release with generated notes.

Pushes that don't change the version do nothing.

`draft: true` publishes a draft to review before announcing. The tag is still created, so the next
push doesn't start a second draft.

Publishing is all or nothing. The tag is what marks a version released, so if creating the
release fails, for example on an asset upload, the partial release and the tag are removed again,
and re-running the workflow retries the whole release.

- **`notes-file`:** a Markdown file, such as install instructions, placed above the generated
  notes. `{version}`, `{tag}` and `{repository}` in it are filled in.
- **`checksums`:** also attaches `SHA256SUMS.txt` covering every asset.
- **`dry-run`:** does everything except create the tag and the release, even for a version
  that is already tagged: it downloads the assets, renders the notes, writes the checksums and
  prints the `gh` commands it would run. For testing a release setup without releasing.

To attach build outputs, build them in an earlier job that uploads them as artifacts, and name them
with `assets`. `release-check` says whether a release is pending and at which version, so the build
only runs when there is something to publish, and is stamped with the version being released:

```yaml
jobs:
  check:
    uses: NearlyTRex/Workflows/.github/workflows/release-check.yml@<sha> # vX
    permissions:
      contents: read
    with:
      version-file: pyproject.toml

  installer:
    needs: check
    if: needs.check.outputs.pending == 'true'
    uses: NearlyTRex/Workflows/.github/workflows/inno-setup.yml@<sha> # vX
    permissions:
      contents: read
    with:
      script: packaging/app.iss
      version: ${{ needs.check.outputs.version }}
      artifact: installer

  release:
    needs: installer
    uses: NearlyTRex/Workflows/.github/workflows/release.yml@<sha> # vX
    permissions:
      contents: write
    with:
      version-file: pyproject.toml
      assets: installer
      checksums: true
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

  ```bash
  gh api -X PUT repos/OWNER/REPO/actions/permissions/workflow -f default_workflow_permissions=read -F can_approve_pull_request_reviews=true
  ```

`init_repo.py` prints this list after writing the files.

## How it fits together

Reusable workflows call the composite actions in [`actions/`](actions) with `$/actions/...`.
`$/` resolves to this repo at the commit the caller pinned
([GitHub changelog](https://github.blog/changelog/2026-07-30-reference-same-repository-actions-with-self-repository-syntax/)),
so one pin fixes the whole chain. The actions are:

| Action | Purpose |
|---|---|
| `setup-tools` | zizmor, pip-audit, ruff, shellcheck, rumdl and yamllint from [`requirements.txt`](actions/setup-tools/requirements.txt), installed with hashes into a private venv |
| `coverage` | Runs the tests under hash-locked coverage.py and enforces the threshold |
| `version` | Reads or bumps the version; [`version.py`](actions/version/version.py) runs locally too |
| `zizmor` | zizmor with [this config](actions/zizmor/zizmor.yml) |
| `gitleaks`, `trivy`, `trivy-config`, `actionlint` | Container actions. Their `Dockerfile` pins the image by digest |
| `scan-range` | Works out which commits a secret scan covers: a pull request's, a push's, or the whole history |
| `publish-release` | Tags and publishes a release all or nothing, or prints what it would do (`dry-run`) |
| `shellcheck` | Finds tracked shell scripts by extension and shebang, and shellchecks them |
| `build-env` | Parallel jobs and ccache for `cpp-build` |
| `check-json` | Parses every tracked JSON file |
| `check-tracked-files` | Fails when a file that could hold a credential is tracked |
| `check-markdown-fences` | Fails on Markdown code fences that don't close, which hide text from the lint rules |

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
The fixtures in `tests/fixtures` go through `python-ci`, `cpp-build`, `compose-test`,
`inno-setup` and `container-scan`, and `release.yml` runs as a dry run over the C++ fixture's
builds, so a change here is tested by the same workflows callers use, release included. The
library releases itself from `VERSION` through `self-prepare-release.yml` and `self-release.yml`.

Logic lives in scripts under `actions/`, called from the workflows, rather than inline in YAML,
so it has tests: Python under the 100% gate, and `build_env.sh`, which also runs on Windows
runners, through pytest.

Dependabot has no Chocolatey ecosystem, so `self-tool-versions.yml` checks the Inno Setup version
`inno-setup.yml` installs every week, and fails with the version to move to when a newer one is
out.

actionlint doesn't understand `$/` yet, so the `actionlint` action leaves those uses out; zizmor
does understand it.
