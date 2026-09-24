"""Write the caller workflows and Dependabot config that wire a repo up to this library.

Usage:
    init_repo.py TARGET [--stack python|cpp|shell|data] [--version-file FILE | --no-release]
                        [--ref REF] [--force] [--dry-run]

Writes into TARGET/.github:
    workflows/ci.yml               lint, plus python-ci for Python repos
    workflows/security.yml         zizmor, gitleaks, pip-audit, CodeQL, dependency review,
                                   and a container scan per Dockerfile; also weekly
    workflows/prepare-release.yml  stage one of a release (when a version file is found)
    workflows/release.yml          stage two of a release
    dependabot.yml                 actions, plus pip, docker, npm and submodules when present

Every library reference is pinned to the commit of REF (default: this repo's latest
tag) with the tag as a comment, which is what lets Dependabot bump the pin when the
library is released. Existing files are left alone unless --force is given.
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


def resolve_ref(ref):
    """Return (sha, label) for the library commit that callers pin."""
    if ref is None:
        try:
            ref = git(LIBRARY_ROOT, "describe", "--tags", "--abbrev=0")
        except InitError:
            raise InitError("the library has no release tag yet; release it first or pass --ref")
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

    def ci(self, stack):
        jobs = []
        if stack == "python":
            jobs.append(f"""  python:
    uses: {self.uses("python-ci")}
    permissions:
      contents: read
    # with:
    #   commands: |
    #     extra checks that run before the tests""")
        jobs.append(f"""  lint:
    uses: {self.uses("lint")}
    permissions:
      contents: read""")
        return f"""name: ci

on:
  push:
    branches: [main]
  pull_request:

permissions: {{}}

concurrency:
  group: ci-${{{{ github.ref }}}}
  cancel-in-progress: true

jobs:
{(chr(10) * 2).join(jobs)}
"""

    def security(self, stack, requirement_files, images):
        languages = {"python": '["python", "actions"]', "cpp": '["c-cpp", "actions"]'}.get(stack, '["actions"]')
        audit = f"""
    with:
      pip-audit-files: {" ".join(requirement_files)}""" if requirement_files else ""
        jobs = [f"""  security:
    uses: {self.uses("security")}
    permissions:
      contents: read{audit}""",
                f"""  codeql:
    uses: {self.uses("codeql")}
    permissions:
      actions: read
      contents: read
      security-events: write
    with:
      languages: '{languages}'""",
                f"""  dependency-review:
    if: github.event_name == 'pull_request'
    uses: {self.uses("dependency-review")}
    permissions:
      contents: read"""]
        for index, dockerfile in enumerate(images):
            suffix = "" if len(images) == 1 else f"-{index + 1}"
            context = str(Path(dockerfile).parent)
            jobs.append(f"""  container-scan{suffix}:
    uses: {self.uses("container-scan")}
    permissions:
      contents: read
    with:
      dockerfile: {dockerfile}
      context: {context}""")
        return f"""name: security

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
  cancel-in-progress: true

jobs:
{(chr(10) * 2).join(jobs)}
"""

    def prepare_release(self, version_file):
        return f"""name: prepare release

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
    uses: {self.uses("prepare-release")}
    permissions:
      contents: write
      pull-requests: write
    with:
      bump: ${{{{ inputs.bump }}}}
      version-file: {version_file}
"""

    def release(self, stack, version_file):
        check = "python-ci" if stack == "python" else "lint"
        return f"""name: release

# Tags and publishes the version in {version_file} once, after the checks pass.
# Pushes that don't change the version find the tag already there and do nothing.
on:
  push:
    branches: [main]

permissions: {{}}

jobs:
  check:
    uses: {self.uses(check)}
    permissions:
      contents: read

  release:
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
        ".github/workflows/ci.yml": renderer.ci(stack),
        ".github/workflows/security.yml": renderer.security(stack, hashed_requirement_files(target, files), images),
        ".github/dependabot.yml": renderer.dependabot(stack, files, images),
    }
    if release and version_file:
        outputs[".github/workflows/prepare-release.yml"] = renderer.prepare_release(version_file)
        outputs[".github/workflows/release.yml"] = renderer.release(stack, version_file)
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
    args = parser.parse_args(argv)

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
