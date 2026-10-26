# Setting up a repo

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
| `workflows/release.yml` | Stage two: checks, then tag and publish, building, attesting and attaching any Inno Setup installers at the released version. With `--pypi`, it also builds the sdist and wheel, attaches them, and publishes them to PyPI (see [Publishing to PyPI](python.md#publishing-to-pypi)) |
| `dependabot.yml` | Actions, plus pip, docker, docker-compose, npm and git submodules when the repo has them. Pull requests are assigned to the owner of the repo's GitHub origin, or to `--assignee USER` (repeatable); `--no-assignee` for none |

It also copies this library's `.markdownlint.yaml`, so `lint` wraps prose at 100 columns there as
it does here, rather than at rumdl's default 80.

It also writes the files GitHub's community standards look for, from `templates/community`, with
links filled in from the repo's GitHub `origin`:

| File | What it holds |
|---|---|
| `CODE_OF_CONDUCT.md` | Contributor Covenant 2.1; conduct is reported with GitHub's **Report content**, so no address is published |
| `CONTRIBUTING.md` | Issues and pull requests welcome on a best-effort basis, and how to propose a change |
| `SECURITY.md` | Private reporting through the repo's **Report a vulnerability** form |
| `.github/ISSUE_TEMPLATE/` | Bug report and feature request forms; blank issues off, and security reports pointed to private reporting |
| `.github/pull_request_template.md` | Summary, testing and a short checklist |

Any of these a repo already has is kept, even with `--force`, since those are usually written for
the repo. For a repo set up before these existed, `--community` writes only the missing ones and
leaves its workflows alone:

```bash
python3 scripts/init_repo.py ~/Repositories/SomeRepo --community --dry-run
python3 scripts/init_repo.py ~/Repositories/SomeRepo --community
```

`SECURITY.md`'s report form needs private vulnerability reporting, which `--settings` turns on.

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

## Repo settings

Each repo that uses these workflows needs:

- **Dependency graph and Dependabot alerts** (Settings → Advanced Security). Dependency review
  fails without them:
  `gh api -X PUT repos/OWNER/REPO/vulnerability-alerts`
- **"Allow GitHub Actions to create and approve pull requests"** (Settings → Actions → General),
  for prepare-release, scheduled-update and license-year:

  ```bash
  gh api -X PUT repos/OWNER/REPO/actions/permissions/workflow -f default_workflow_permissions=read -F can_approve_pull_request_reviews=true
  ```

- **Private vulnerability reporting** (Settings → Advanced Security), for the report form
  `SECURITY.md` links to:

  ```bash
  gh api -X PUT repos/OWNER/REPO/private-vulnerability-reporting
  ```

- **A "Protect main" ruleset** (Settings → Rules → Rulesets) on the default branch: every change
  arrives by pull request, with no approving review required since a sole maintainer can't approve
  their own, and the branch can't be force-pushed or deleted. Nobody bypasses it. Tags are
  untouched, so `release.yml` still tags releases. `--settings` updates a ruleset of that name
  rather than adding a second one.

`init_repo.py` prints this list after writing the files. `init_repo.py TARGET --settings` turns
them on with `gh`, reading the repo from TARGET's `origin`; add `--dry-run` to see the calls first.
It needs admin rights on the repo.
