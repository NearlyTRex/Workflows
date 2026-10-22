"""Write the caller workflows and Dependabot config that wire a repo up to this library.

Usage:
    init_repo.py TARGET [--stack python|cpp|shell|data] [--version-file FILE | --no-release]
                        [--pypi] [--assignee USER ... | --no-assignee] [--ref REF] [--force]
                        [--dry-run]
    init_repo.py TARGET --repin [--ref REF]
    init_repo.py TARGET --settings [--dry-run]
    init_repo.py TARGET --community [--dry-run]

Writes into TARGET/.github:
    workflows/ci.yml               lint, plus python-ci for Python repos, cpp-build for CMake
                                   repos, compose-test per compose file and inno-setup per
                                   Inno Setup script
    workflows/security.yml         zizmor, gitleaks, pip-audit, CodeQL, dependency review,
                                   and a container scan per Dockerfile; also weekly
    workflows/prepare-release.yml  stage one of a release (when a version file is found)
    workflows/release.yml          stage two of a release, attesting and attaching the installers;
                                   with --pypi, building the sdist and wheel, attaching them,
                                   and publishing them to PyPI through trusted publishing
    dependabot.yml                 actions, plus pip, docker, docker-compose, npm and
                                   submodules when present; its pull requests are assigned
                                   to --assignee, or by default to the owner of TARGET's
                                   GitHub origin (--no-assignee for none, as for an
                                   organization, which can't be assigned)

Every library reference is pinned to the commit of REF (default: this repo's latest
tag) with the tag as a comment, which is what lets Dependabot bump the pin when the
library is released. Existing files are left alone unless --force is given.

--pypi is for a Python package published to PyPI under the name in its pyproject.toml.
The publish job stays in TARGET's own release.yml, since PyPI's trusted publishing does not
accept a reusable workflow; PyPI needs a trusted publisher for release.yml and the pypi
environment, and TARGET a requirements-build.txt locking build and its build backend.

--repin moves every library pin in TARGET's workflows to REF and changes nothing else,
so customized callers can be upgraded without regenerating them.

Also writes the community health files GitHub's community standards look for, from
templates/community: CODE_OF_CONDUCT.md, CONTRIBUTING.md, SECURITY.md, issue forms and a
pull request template, linked to TARGET's GitHub origin; and this library's
.markdownlint.yaml, so the lint job wraps prose at 100 columns there too. Any of these
TARGET already has is kept, even with --force, since those are usually written for the repo.

--community writes only those files, so a repo set up before they existed can take them
without regenerating its workflows.

--settings turns on, with gh, the GitHub settings TARGET's workflows need: Dependabot
alerts (and with them the dependency graph), letting Actions open pull requests when it
has prepare-release.yml, and private vulnerability reporting when it has SECURITY.md.
It also protects the default branch with a ruleset: changes arrive by pull request, and
it can't be force-pushed or deleted. A ruleset of the same name is updated, not doubled.
It changes nothing in TARGET itself.
"""

import argparse
import importlib.util
import json
import re
import shlex
import subprocess
import sys
import tomllib
from pathlib import Path

LIBRARY_ROOT = Path(__file__).resolve().parent.parent
COMMUNITY_TEMPLATES = LIBRARY_ROOT / "templates" / "community"
# The library's own lint configs, copied as they are, so every repo's lint job judges by
# the same rules: prose wraps at 100 columns rather than the linter's default 80
SHARED_CONFIGS = [".markdownlint.yaml"]
DEFAULT_LIBRARY = "NearlyTRex/Workflows"

_spec = importlib.util.spec_from_file_location("version", LIBRARY_ROOT / "actions" / "version" / "version.py")
version_tool = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(version_tool)


class InitError(Exception):
    pass


def git(repo, *args):
    result = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise InitError(f"git {' '.join(args)} failed in {repo}: {result.stderr.strip()}")
    return result.stdout.strip()


def github_slug(url):
    """OWNER/REPO from a GitHub remote URL, or None for any other host."""
    match = re.search(r"github\.com[:/](.+?/.+?)(?:\.git)?$", url)
    return match.group(1) if match else None


def origin_slug(target):
    """OWNER/REPO of target's GitHub origin, or None without one."""
    try:
        return github_slug(git(target, "remote", "get-url", "origin"))
    except InitError:
        return None


def origin_owner(target):
    """The owner of target's GitHub origin, or None without one."""
    slug = origin_slug(target)
    return slug.split("/")[0] if slug else None


def community_files(slug):
    """The community health files from templates/community, filled in for the GitHub repo
    OWNER/REPO: the code of conduct, contributing guide, security policy, and issue and pull
    request templates; plus the shared lint configs."""
    files = {}
    for path in sorted(COMMUNITY_TEMPLATES.rglob("*")):
        if path.is_file():
            text = path.read_text().replace("{{repo}}", slug).replace("{{name}}", slug.split("/")[1])
            files[path.relative_to(COMMUNITY_TEMPLATES).as_posix()] = text
    for name in SHARED_CONFIGS:
        files[name] = (LIBRARY_ROOT / name).read_text()
    return files


def write_community_files(target, slug, dry_run=False):
    """Write the community files target lacks, leaving any it has alone, since those are
    usually written for the repo. Returns (written, kept)."""
    written, kept = [], []
    for name, text in community_files(slug).items():
        path = target / name
        if path.exists():
            kept.append(name)
            continue
        written.append(name)
        if dry_run:
            print(f"\n--- {name}\n{text}")
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    return written, kept


def library_name():
    try:
        url = git(LIBRARY_ROOT, "remote", "get-url", "origin")
    except InitError:
        return DEFAULT_LIBRARY
    return github_slug(url) or DEFAULT_LIBRARY


def latest_tag():
    """The highest vX.Y.Z tag, after fetching so a stale clone doesn't pin an old release."""
    try:
        git(LIBRARY_ROOT, "fetch", "--quiet", "--tags", "origin")
    except InitError as e:
        print(f"warning: could not fetch tags, using local ones ({e})", file=sys.stderr)
    tags = git(LIBRARY_ROOT, "tag", "--list", "v[0-9]*", "--sort=-v:refname").split()
    if not tags:
        raise InitError("the library has no release tag yet; release it first or pass --ref")
    return tags[0]


def resolve_ref(ref):
    """Return (sha, label) for the library commit that callers pin."""
    ref = ref or latest_tag()
    sha = git(LIBRARY_ROOT, "rev-list", "-n", "1", ref)
    return sha, ref


def tracked_files(target):
    try:
        return git(target, "ls-files").splitlines()
    except InitError:
        return [str(p.relative_to(target)) for p in target.rglob("*") if p.is_file() and ".git" not in p.parts]


def detect_stack(files):
    names = {Path(f).name for f in files}
    if "pyproject.toml" in names:
        return "python"
    sources = sum(Path(f).suffix in (".c", ".cc", ".cpp", ".cxx", ".h", ".hh", ".hpp") for f in files)
    if "CMakeLists.txt" in names or sources > len(files) / 4:
        return "cpp"
    if sum(f.endswith(".json") for f in files) > len(files) / 2:
        return "data"
    return "shell"


def detect_version_file(target, stack):
    candidates = {"python": ["pyproject.toml"], "cpp": ["CMakeLists.txt"]}.get(stack, [])
    for name in candidates + ["VERSION", "package.json"]:
        path = target / name
        if path.is_file():
            try:
                version_tool.read(path)
                return name
            except version_tool.VersionError:
                continue
    return None


def hashed_requirement_files(target, files):
    found = []
    for f in files:
        name = Path(f).name
        if (name.endswith(".lock") or (name.startswith("requirements") and name.endswith(".txt"))) \
                and "--hash=" in (target / f).read_text(errors="ignore"):
            found.append(f)
    return sorted(found)


def lock_warnings(target, requirement_files):
    """Hash-locked files Dependabot can't regenerate: it maintains only a requirements*.txt
    pip-compiled from the .in file it is named after, so a lock compiled from pyproject.toml,
    or named *.lock, goes stale while its ranges are bumped."""
    warnings = []
    for name in requirement_files:
        header = (target / name).read_text(errors="ignore")[:2000]
        if name.endswith(".lock"):
            warnings.append(f"{name}: Dependabot never updates *.lock files; pip-compile it to a "
                            "requirements*.txt named after its .in file instead")
        elif re.search(r"#\s+pip-compile .*\bpyproject\.toml\s*$", header, re.MULTILINE):
            warnings.append(f"{name}: compiled from pyproject.toml, which Dependabot can't regenerate; "
                            "compile it from a requirements .in file instead")
    return warnings


def dockerfiles(files):
    return sorted(f for f in files if Path(f).name == "Dockerfile")


COMPOSE_FILES = {"compose.yml", "compose.yaml", "docker-compose.yml", "docker-compose.yaml"}


def compose_files(files):
    return sorted(f for f in files if Path(f).name in COMPOSE_FILES)


def installer_scripts(files):
    return sorted(f for f in files if f.endswith(".iss"))


def directory_of(path):
    parent = str(Path(path).parent)
    return "/" if parent == "." else "/" + parent


class Renderer:
    def __init__(self, library, sha, label, target):
        self.target = target
        self.library = library
        self.sha = sha
        self.label = label

    def uses(self, workflow):
        return f"{self.library}/.github/workflows/{workflow}.yml@{self.sha} # {self.label}"

    def uses_action(self, action):
        return f"{self.library}/actions/{action}@{self.sha} # {self.label}"

    def ci(self, stack, files=()):
        names = {Path(f).name for f in files}
        jobs = []
        if stack == "python":
            jobs.append(f"""  python:
    name: Python
    uses: {self.uses("python-ci")}
    permissions:
      contents: read
    # with:
    #   commands: |
    #     extra checks that run before the tests""")
        if stack == "cpp" and "CMakeLists.txt" in files:
            submodules = "\n      submodules: recursive" if ".gitmodules" in names else ""
            jobs.append(f"""  build:
    name: Build (${{{{ matrix.os }}}})
    uses: {self.uses("cpp-build")}
    permissions:
      contents: read
    strategy:
      fail-fast: false
      matrix:
        os: [ubuntu-latest, windows-latest]
    with:
      runs-on: ${{{{ matrix.os }}}}
      msvc: ${{{{ matrix.os == 'windows-latest' }}}}{submodules}
      build-command: |
        cmake -S . -B build -DCMAKE_BUILD_TYPE=Release
        cmake --build build --config Release --parallel
      test-command: ctest --test-dir build -C Release --output-on-failure""")
        for index, compose in enumerate(compose_files(files)):
            suffix = "" if len(compose_files(files)) == 1 else f"-{index + 1}"
            jobs.append(f"""  compose{suffix}:
    name: Compose ({compose})
    uses: {self.uses("compose-test")}
    permissions:
      contents: read
    with:
      compose-file: {compose}
    #   health-url: http://127.0.0.1:8080/health
    #   commands: |
    #     checks to run against the running stack""")
        for index, script in enumerate(installer_scripts(files)):
            suffix = "" if len(installer_scripts(files)) == 1 else f"-{index + 1}"
            jobs.append(f"""  installer{suffix}:
    name: Installer ({script})
    uses: {self.uses("inno-setup")}
    permissions:
      contents: read
    with:
      script: {script}
      # A placeholder: this only proves the script compiles. release.yml builds the real one.
      version: 0.0.0-ci
    #   smoke-test: .github\\scripts\\smoke-test-installer.ps1""")
        jobs.append(f"""  lint:
    name: Lint
    uses: {self.uses("lint")}
    permissions:
      contents: read""")
        return f"""name: CI

on:
  push:
    branches: [main]
  pull_request:

permissions: {{}}

concurrency:
  group: ci-${{{{ github.ref }}}}
  # Only pull requests: a newer push supersedes their run. On main every run
  # finishes, since each push's secret scan covers only that push's commits.
  cancel-in-progress: ${{{{ github.event_name == 'pull_request' }}}}

jobs:
{(chr(10) * 2).join(jobs)}
"""

    def security(self, stack, requirement_files, images):
        languages = {"python": '["python", "actions"]', "cpp": '["c-cpp", "actions"]'}.get(stack, '["actions"]')
        audit = f"""
    with:
      pip-audit-files: {" ".join(requirement_files)}""" if requirement_files else ""
        jobs = [f"""  security:
    name: Security
    uses: {self.uses("security")}
    permissions:
      contents: read{audit}""",
                f"""  codeql:
    name: CodeQL
    uses: {self.uses("codeql")}
    permissions:
      actions: read
      contents: read
      security-events: write
    with:
      languages: '{languages}'""",
                f"""  dependency-review:
    name: Dependency Review
    if: github.event_name == 'pull_request'
    uses: {self.uses("dependency-review")}
    permissions:
      contents: read"""]
        for index, dockerfile in enumerate(images):
            suffix = "" if len(images) == 1 else f"-{index + 1}"
            title = "" if len(images) == 1 else f" ({dockerfile})"
            context = str(Path(dockerfile).parent)
            jobs.append(f"""  container-scan{suffix}:
    name: Container Scan{title}
    uses: {self.uses("container-scan")}
    permissions:
      contents: read
    with:
      dockerfile: {dockerfile}
      context: {context}
      config-scan: true""")
        return f"""name: Security

on:
  push:
    branches: [main]
  pull_request:
  schedule:
    - cron: "17 6 * * 1"
  workflow_dispatch:

permissions: {{}}

concurrency:
  group: security-${{{{ github.ref }}}}
  # Only pull requests: a newer push supersedes their run. On main every run
  # finishes, since each push's secret scan covers only that push's commits.
  cancel-in-progress: ${{{{ github.event_name == 'pull_request' }}}}

jobs:
{(chr(10) * 2).join(jobs)}
"""

    def prepare_release(self, version_file):
        return f"""name: Prepare Release

# Bumps the version and opens a PR. Merging it lets release.yml tag and publish.
on:
  workflow_dispatch:
    inputs:
      bump:
        description: patch, minor, major, or an exact version like 1.4.0
        required: true
        default: patch

permissions: {{}}

jobs:
  prepare:
    name: Prepare
    uses: {self.uses("prepare-release")}
    permissions:
      contents: write
      pull-requests: write
    with:
      bump: ${{{{ inputs.bump }}}}
      version-file: {version_file}
"""

    def release(self, stack, version_file, files=(), pypi=None):
        check = "python-ci" if stack == "python" else "lint"
        scripts = installer_scripts(files)
        if scripts:
            return self.release_with_installers(check, version_file, scripts)
        if pypi:
            return self.release_to_pypi(version_file, pypi)
        return f"""name: Release

# Tags and publishes the version in {version_file} once, after the checks pass.
# Pushes that don't change the version find the tag already there, and run
# nothing else: CI has already tested them.
on:
  push:
    branches: [main]

permissions: {{}}

jobs:
  pending:
    name: Pending
    uses: {self.uses("release-check")}
    permissions:
      contents: read
    with:
      version-file: {version_file}

  check:
    name: Check
    needs: pending
    if: needs.pending.outputs.pending == 'true'
    uses: {self.uses(check)}
    permissions:
      contents: read

  release:
    name: Release
    needs: check
    uses: {self.uses("release")}
    permissions:
      contents: write
    with:
      version-file: {version_file}
"""

    def dependabot(self, stack, files, images, assignees=()):
        names = {Path(f).name for f in files}
        updates = [("github-actions", ["/"], "actions")]
        if stack == "python" or any(n.endswith(".lock") or n.startswith("requirements") for n in names):
            updates.append(("pip", ["/"], "python"))
        locked = bool(hashed_requirement_files(self.target, files))
        if "package.json" in names:
            updates.append(("npm", ["/"], "npm"))
        if images:
            updates.append(("docker", sorted({directory_of(f) for f in images}), "docker"))
        # Images named in compose files (a database, a search engine) are not in any
        # Dockerfile, so the docker ecosystem never sees them.
        if compose := compose_files(files):
            updates.append(("docker-compose", sorted({directory_of(f) for f in compose}), "docker-compose"))
        if ".gitmodules" in names:
            updates.append(("gitsubmodule", ["/"], "submodules"))
        assign = ""
        if assignees:
            assign = "\n    assignees: [" + ", ".join(f'"{a}"' for a in assignees) + "]"
        blocks = []
        for ecosystem, directories, group in updates:
            dirs = ", ".join(f'"{d}"' for d in directories)
            # With hash-locked files, only the locks should move, not the ranges in pyproject.toml.
            strategy = "\n    versioning-strategy: lockfile-only" if ecosystem == "pip" and locked else ""
            blocks.append(f"""  - package-ecosystem: {ecosystem}
    directories: [{dirs}]{strategy}
    schedule:
      interval: weekly
    cooldown:
      default-days: 7{assign}
    groups:
      {group}:
        patterns: ["*"]""")
        return f"""version: 2

# Weekly, grouped, and 7 days behind upstream releases so a compromised release
# has time to be caught before it is proposed here.
updates:
{(chr(10) * 2).join(blocks)}
"""


    def release_to_pypi(self, version_file, project):
        return f"""name: Release

# Tags and publishes the version in {version_file} once, after the checks pass:
# a GitHub Release with the sdist and wheel attached, then the same files on
# PyPI. Pushes that don't change the version find the tag already there, and
# run nothing else: CI has already tested them.
on:
  push:
    branches: [main]

permissions: {{}}

jobs:
  pending:
    name: Pending
    uses: {self.uses("release-check")}
    permissions:
      contents: read
    with:
      version-file: {version_file}

  check:
    name: Check
    needs: pending
    if: needs.pending.outputs.pending == 'true'
    uses: {self.uses("python-ci")}
    permissions:
      contents: read

  build:
    name: Build
    needs: check
    uses: {self.uses("python-build")}
    permissions:
      contents: read

  release:
    name: Release
    needs: build
    uses: {self.uses("release")}
    permissions:
      contents: write
    with:
      version-file: {version_file}
      assets: dist
      checksums: true

  # A job of this file rather than a reusable workflow: PyPI's trusted
  # publisher names this file, release.yml, and the pypi environment, so no
  # token is stored, and it accepts no workflow called from another repository
  publish:
    name: Publish to PyPI
    needs: release
    if: needs.release.outputs.released == 'true'
    runs-on: ubuntu-latest
    timeout-minutes: 10
    environment:
      name: pypi
      url: https://pypi.org/project/{project}/${{{{ needs.release.outputs.version }}}}/
    permissions:
      id-token: write
    steps:
      - uses: {self.uses_action("pypi-publish")}
"""

    def release_with_installers(self, check, version_file, scripts):
        installers = []
        for index, script in enumerate(scripts):
            suffix = "" if len(scripts) == 1 else f"-{index + 1}"
            installers.append(f"""  installer{suffix}:
    name: Installer ({script})
    needs: pending
    if: needs.pending.outputs.pending == 'true'
    uses: {self.uses("inno-setup")}
    permissions:
      contents: read
    with:
      script: {script}
      version: ${{{{ needs.pending.outputs.version }}}}
      artifact: installer{suffix}""")
        needs = ", ".join(["check", "attest"] + [f"installer{'' if len(scripts) == 1 else f'-{i + 1}'}"
                                                 for i in range(len(scripts))])
        installer_jobs = (chr(10) * 2).join(installers)
        attest_needs = ", ".join(f"installer{'' if len(scripts) == 1 else f'-{i + 1}'}"
                                 for i in range(len(scripts)))
        return f"""name: Release

# Tags and publishes the version in {version_file} once, after the checks pass, with
# the installers built at that version attached. Pushes that don't change the version
# find the tag already there, and build and check nothing: CI has already tested them.
on:
  push:
    branches: [main]

permissions: {{}}

jobs:
  pending:
    name: Pending
    uses: {self.uses("release-check")}
    permissions:
      contents: read
    with:
      version-file: {version_file}

  check:
    name: Check
    needs: pending
    if: needs.pending.outputs.pending == 'true'
    uses: {self.uses(check)}
    permissions:
      contents: read

{installer_jobs}

  # Signed provenance for the installers, checked with gh attestation verify.
  attest:
    name: Attest
    needs: [{attest_needs}]
    uses: {self.uses("attest")}
    permissions:
      id-token: write
      attestations: write
    with:
      artifacts: installer*

  release:
    name: Release
    needs: [{needs}]
    uses: {self.uses("release")}
    permissions:
      contents: write
    with:
      version-file: {version_file}
      assets: installer*
      checksums: true
"""


def repin(target, ref=None, dry_run=False):
    """Point every library reference in target's workflows at ref; with dry_run, write nothing.

    Returns the files that change, the files already pinned there, and the ref's label."""
    target = Path(target).resolve()
    sha, label = resolve_ref(ref)
    pattern = re.compile(
        rf"({re.escape(library_name())}/(?:\.github/workflows/[\w.-]+\.ya?ml|actions/[\w.-]+))@[0-9a-f]{{40}}(?: # \S+)?")
    changed, current = [], []
    for path in sorted((target / ".github" / "workflows").glob("*.y*ml")):
        text = path.read_text()
        if not pattern.search(text):
            continue
        updated = pattern.sub(lambda m: f"{m.group(1)}@{sha} # {label}", text)
        if updated == text:
            current.append(path.name)
            continue
        if not dry_run:
            path.write_text(updated)
        changed.append(path.name)
    return changed, current, label


def settings_commands(target):
    """The gh calls that turn on what target's workflows need, each with what it's for."""
    target = Path(target).resolve()
    slug = github_slug(git(target, "remote", "get-url", "origin"))
    if slug is None:
        raise InitError(f"{target}'s origin is not a GitHub repository")
    commands = [("Dependabot alerts and the dependency graph, for dependency review and Dependabot",
                 ["gh", "api", "-X", "PUT", f"repos/{slug}/vulnerability-alerts"])]
    if (target / ".github" / "workflows" / "prepare-release.yml").exists():
        commands.append(("Actions may open pull requests, for prepare-release",
                         ["gh", "api", "-X", "PUT", f"repos/{slug}/actions/permissions/workflow",
                          "-f", "default_workflow_permissions=read",
                          "-F", "can_approve_pull_request_reviews=true"]))
    if (target / "SECURITY.md").exists():
        commands.append(("private vulnerability reporting, for SECURITY.md's report form",
                         ["gh", "api", "-X", "PUT", f"repos/{slug}/private-vulnerability-reporting"]))
    return slug, commands


RULESET_NAME = "Protect main"
RULESET_PURPOSE = "pull requests required for the default branch, which can't be force-pushed or deleted"


def branch_ruleset():
    """The default branch's ruleset: every change by pull request, though one needs no
    approving review, since a sole maintainer can't approve their own; no force pushes, no
    deletion, and no one may bypass it. Release tags are untouched, so release.yml still tags."""
    return {
        "name": RULESET_NAME,
        "target": "branch",
        "enforcement": "active",
        "conditions": {"ref_name": {"include": ["~DEFAULT_BRANCH"], "exclude": []}},
        "rules": [
            {"type": "pull_request", "parameters": {
                "required_approving_review_count": 0,
                "dismiss_stale_reviews_on_push": False,
                "required_reviewers": [],
                "require_code_owner_review": False,
                "require_last_push_approval": False,
                "required_review_thread_resolution": False,
                "allowed_merge_methods": ["merge", "squash", "rebase"],
            }},
            {"type": "non_fast_forward"},
            {"type": "deletion"},
        ],
        "bypass_actors": [],
    }


def run_gh(command, payload=None):
    result = subprocess.run(command, input=payload, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise InitError(f"{shlex.join(command)} failed: {(result.stderr or result.stdout).strip()}")
    return result.stdout


def ruleset_command(slug):
    """The call that writes the ruleset: an update of the one with its name, else a create."""
    existing = [ruleset["id"] for ruleset in json.loads(run_gh(["gh", "api", f"repos/{slug}/rulesets"]))
                if ruleset["name"] == RULESET_NAME]
    if existing:
        return ["gh", "api", "-X", "PUT", f"repos/{slug}/rulesets/{existing[0]}", "--input", "-"]
    return ["gh", "api", "-X", "POST", f"repos/{slug}/rulesets", "--input", "-"]


def apply_settings(target, dry_run=False):
    """Run settings_commands and write the branch ruleset, or with dry_run print what would
    run. Returns the repository's slug."""
    slug, commands = settings_commands(target)
    payload = json.dumps(branch_ruleset(), indent=2)
    if dry_run:
        for purpose, command in commands:
            print(f"Would run ({purpose}):\n  {shlex.join(command)}")
        print(f"Would create or update the {RULESET_NAME!r} ruleset ({RULESET_PURPOSE}):\n"
              f"  gh api -X POST repos/{slug}/rulesets --input -   (PUT to its id when it exists)\n"
              + "\n".join("  " + line for line in payload.splitlines()))
        return slug
    for purpose, command in commands:
        run_gh(command)
        print(f"Set: {purpose}")
    run_gh(ruleset_command(slug), payload)
    print(f"Set: {RULESET_PURPOSE}")
    return slug


def pypi_project(target):
    """The [project] name in target's pyproject.toml, which is its name on PyPI."""
    path = target / "pyproject.toml"
    if not path.is_file():
        raise InitError(f"--pypi needs {path}")
    with path.open("rb") as handle:
        name = tomllib.load(handle).get("project", {}).get("name")
    if not name:
        raise InitError(f"--pypi needs a [project] name in {path}")
    return name


def plan(target, stack=None, version_file=None, release=True, ref=None, assignees=None, pypi=False):
    """assignees None means the owner of target's GitHub origin, when it has one."""
    target = Path(target).resolve()
    if not target.is_dir():
        raise InitError(f"{target} is not a directory")
    if assignees is None:
        owner = origin_owner(target)
        assignees = [owner] if owner else []
    files = tracked_files(target)
    stack = stack or detect_stack(files)
    if release and version_file is None:
        version_file = detect_version_file(target, stack)
    if version_file is not None:
        version_tool.read(target / version_file)
    sha, label = resolve_ref(ref)
    renderer = Renderer(library_name(), sha, label, target)
    images = dockerfiles(files)
    outputs = {
        ".github/workflows/ci.yml": renderer.ci(stack, files),
        ".github/workflows/security.yml": renderer.security(stack, hashed_requirement_files(target, files), images),
        ".github/dependabot.yml": renderer.dependabot(stack, files, images, assignees),
    }
    if release and version_file:
        outputs[".github/workflows/prepare-release.yml"] = renderer.prepare_release(version_file)
        project = pypi_project(target) if pypi else None
        outputs[".github/workflows/release.yml"] = renderer.release(stack, version_file, files, project)
    warnings = lock_warnings(target, hashed_requirement_files(target, files))
    return target, stack, version_file, outputs, warnings


def report_community_files(written, kept, dry_run=False):
    for name in written:
        print(f"  {'would write' if dry_run else 'wrote'} {name}")
    if kept:
        print(f"  kept the repo's own: {', '.join(kept)}")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("target")
    parser.add_argument("--stack", choices=["python", "cpp", "shell", "data"])
    parser.add_argument("--version-file", help="File holding the version (default: detected)")
    parser.add_argument("--no-release", action="store_true", help="Skip the release workflows")
    parser.add_argument("--pypi", action="store_true",
                        help="Build the package and publish it to PyPI as part of each release")
    parser.add_argument("--ref", help="Library tag or commit to pin (default: latest tag)")
    assignee = parser.add_mutually_exclusive_group()
    assignee.add_argument("--assignee", action="append", metavar="USER",
                          help="Assign Dependabot's pull requests to USER; repeatable "
                               "(default: the owner of TARGET's GitHub origin)")
    assignee.add_argument("--no-assignee", action="store_true", help="Leave Dependabot's pull requests unassigned")
    parser.add_argument("--force", action="store_true", help="Overwrite existing files")
    parser.add_argument("--dry-run", action="store_true", help="Print what would be written")
    parser.add_argument("--repin", action="store_true", help="Only move existing library pins to --ref")
    parser.add_argument("--settings", action="store_true",
                        help="Only turn on the GitHub settings the workflows need, with gh")
    parser.add_argument("--community", action="store_true",
                        help="Only write the community health files TARGET lacks")
    args = parser.parse_args(argv)

    if args.community:
        target = Path(args.target).resolve()
        if not target.is_dir():
            print(f"error: {target} is not a directory", file=sys.stderr)
            return 1
        slug = origin_slug(target)
        if not slug:
            print(f"error: {target} has no GitHub origin for the community files to link to", file=sys.stderr)
            return 1
        written, kept = write_community_files(target, slug, args.dry_run)
        report_community_files(written, kept, args.dry_run)
        return 0

    if args.settings:
        try:
            slug = apply_settings(args.target, args.dry_run)
        except InitError as e:
            print(f"error: {e}", file=sys.stderr)
            return 1
        if not args.dry_run:
            print(f"{slug} is set up for these workflows")
        return 0

    if args.repin:
        try:
            changed, current, label = repin(args.target, args.ref, args.dry_run)
        except InitError as e:
            print(f"error: {e}", file=sys.stderr)
            return 1
        if changed:
            print(f"{'Would pin' if args.dry_run else 'Pinned'} to {label}: {', '.join(changed)}")
        if current:
            print(f"Already pinned to {label}: {', '.join(current)}")
        if not changed and not current:
            print("No library pins found to move")
        return 0

    try:
        target, stack, version_file, outputs, warnings = plan(
            args.target, args.stack, args.version_file, not args.no_release, args.ref,
            [] if args.no_assignee else args.assignee, args.pypi)
    except (InitError, version_tool.VersionError, OSError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

    existing = [name for name in outputs if (target / name).exists()]
    if existing and not args.force and not args.dry_run:
        print("error: these already exist (use --force to overwrite):", file=sys.stderr)
        for name in existing:
            print(f"  {name}", file=sys.stderr)
        return 1

    print(f"{target.name}: stack {stack}, " +
          (f"releases from {version_file}" if version_file else "no release workflows (no version file found)"))
    for name, text in outputs.items():
        if args.dry_run:
            print(f"\n--- {name}\n{text}")
            continue
        path = target / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        print(f"  wrote {name}")

    slug = origin_slug(target)
    if slug:
        written, kept = write_community_files(target, slug, args.dry_run)
        report_community_files(written, kept, args.dry_run)
    else:
        print("  no GitHub origin, so no community files; add one and run with --community")

    for warning in warnings:
        print(f"  warning: {warning}")

    if not args.dry_run:
        print("\nRepo settings these workflows need (Settings → Advanced Security, Rules, and Actions → General):")
        print("  - Dependency graph and Dependabot alerts, for dependency review and Dependabot")
        print("  - A ruleset requiring pull requests for the default branch (Settings → Rules)")
        if version_file:
            print("  - \"Allow GitHub Actions to create and approve pull requests\", for prepare-release")
        if (target / "SECURITY.md").exists():
            print("  - Private vulnerability reporting, for SECURITY.md's report form")
        print(f"Turn them on with: {Path(__file__).name} {shlex.quote(str(target))} --settings")

    others = sorted(p.name for p in (target / ".github" / "workflows").glob("*.y*ml")
                    if f".github/workflows/{p.name}" not in outputs) \
        if (target / ".github" / "workflows").is_dir() else []
    if others:
        print(f"  also present, check for overlap: {', '.join(others)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
