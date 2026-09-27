"""Write the caller workflows and Dependabot config that wire a repo up to this library.

Usage:
    init_repo.py TARGET [--stack python|cpp|shell|data] [--version-file FILE | --no-release]
                        [--ref REF] [--force] [--dry-run]
    init_repo.py TARGET --repin [--ref REF]

Writes into TARGET/.github:
    workflows/ci.yml               lint, plus python-ci for Python repos, cpp-build for CMake
                                   repos, compose-test per compose file and inno-setup per
                                   Inno Setup script
    workflows/security.yml         zizmor, gitleaks, pip-audit, CodeQL, dependency review,
                                   and a container scan per Dockerfile; also weekly
    workflows/prepare-release.yml  stage one of a release (when a version file is found)
    workflows/release.yml          stage two of a release, attaching the installers
    dependabot.yml                 actions, plus pip, docker, docker-compose, npm and
                                   submodules when present

Every library reference is pinned to the commit of REF (default: this repo's latest
tag) with the tag as a comment, which is what lets Dependabot bump the pin when the
library is released. Existing files are left alone unless --force is given.

--repin moves every library pin in TARGET's workflows to REF and changes nothing else,
so customized callers can be upgraded without regenerating them.
"""

import argparse
import importlib.util
import re
import subprocess
import sys
from pathlib import Path

LIBRARY_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_LIBRARY = "NearlyTRex/Workflows"

_spec = importlib.util.spec_from_file_location("version", LIBRARY_ROOT / "actions" / "version" / "version.py")
version_tool = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(version_tool)


class InitError(Exception):
    pass


def git(repo, *args):
    result = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True)
    if result.returncode != 0:
        raise InitError(f"git {' '.join(args)} failed in {repo}: {result.stderr.strip()}")
    return result.stdout.strip()


def library_name():
    try:
        url = git(LIBRARY_ROOT, "remote", "get-url", "origin")
    except InitError:
        return DEFAULT_LIBRARY
    match = re.search(r"github\.com[:/](.+?/.+?)(?:\.git)?$", url)
    return match.group(1) if match else DEFAULT_LIBRARY


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
    """Hash-locked files Dependabot can't regenerate.

    It only maintains pip-compile output compiled from a .in file and named after it
    (requirements.in -> requirements.txt). A lock compiled from pyproject.toml, or named
    *.lock, gets its pyproject.toml or .in ranges bumped while the lock itself goes stale.
    """
    warnings = []
    for name in requirement_files:
        header = (target / name).read_text(errors="ignore")[:2000]
        if name.endswith(".lock"):
            warnings.append(f"{name}: Dependabot never updates *.lock files; pip-compile it to a "
                            "requirements*.txt named after its .in file instead")
        elif re.search(r"#\s+pip-compile .*\bpyproject\.toml\s*$", header, re.M):
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

    def release(self, stack, version_file, files=()):
        check = "python-ci" if stack == "python" else "lint"
        scripts = installer_scripts(files)
        if scripts:
            return self.release_with_installers(check, version_file, scripts)
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

    def dependabot(self, stack, files, images):
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
      default-days: 7
    groups:
      {group}:
        patterns: ["*"]""")
        return f"""version: 2

# Weekly, grouped, and 7 days behind upstream releases so a compromised release
# has time to be caught before it is proposed here.
updates:
{(chr(10) * 2).join(blocks)}
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
        needs = ", ".join(["check"] + [f"installer{'' if len(scripts) == 1 else f'-{i + 1}'}"
                                       for i in range(len(scripts))])
        installer_jobs = (chr(10) * 2).join(installers)
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
    """Point every library reference in target's workflows at ref.

    Returns the files that change, the files already pinned there, and the ref's label.
    With dry_run nothing is written.
    """
    target = Path(target).resolve()
    sha, label = resolve_ref(ref)
    pattern = re.compile(rf"({re.escape(library_name())}/\.github/workflows/[\w.-]+\.ya?ml)@[0-9a-f]{{40}}(?: # \S+)?")
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


def plan(target, stack=None, version_file=None, release=True, ref=None):
    target = Path(target).resolve()
    if not target.is_dir():
        raise InitError(f"{target} is not a directory")
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
        ".github/dependabot.yml": renderer.dependabot(stack, files, images),
    }
    if release and version_file:
        outputs[".github/workflows/prepare-release.yml"] = renderer.prepare_release(version_file)
        outputs[".github/workflows/release.yml"] = renderer.release(stack, version_file, files)
    warnings = lock_warnings(target, hashed_requirement_files(target, files))
    return target, stack, version_file, outputs, warnings


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("target")
    parser.add_argument("--stack", choices=["python", "cpp", "shell", "data"])
    parser.add_argument("--version-file", help="File holding the version (default: detected)")
    parser.add_argument("--no-release", action="store_true", help="Skip the release workflows")
    parser.add_argument("--ref", help="Library tag or commit to pin (default: latest tag)")
    parser.add_argument("--force", action="store_true", help="Overwrite existing files")
    parser.add_argument("--dry-run", action="store_true", help="Print what would be written")
    parser.add_argument("--repin", action="store_true", help="Only move existing library pins to --ref")
    args = parser.parse_args(argv)

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
            args.target, args.stack, args.version_file, not args.no_release, args.ref)
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

    for warning in warnings:
        print(f"  warning: {warning}")

    if not args.dry_run:
        print("\nRepo settings these workflows need (Settings → Advanced Security, and Actions → General):")
        print("  - Dependency graph and Dependabot alerts, for dependency review and Dependabot")
        if version_file:
            print("  - \"Allow GitHub Actions to create and approve pull requests\", for prepare-release")

    others = sorted(p.name for p in (target / ".github" / "workflows").glob("*.y*ml")
                    if f".github/workflows/{p.name}" not in outputs) \
        if (target / ".github" / "workflows").is_dir() else []
    if others:
        print(f"  also present, check for overlap: {', '.join(others)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
