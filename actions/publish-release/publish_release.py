"""Tag a commit and publish its GitHub Release, all or nothing.

The tag is what marks a version released, so a tag left behind by a failed
publish would make every re-run skip the version. Everything that can fail on
its own (the notes, the checksums) is prepared before the tag exists; if
publishing then fails, the partial release and the tag are removed again, so a
re-run starts clean. With DRY_RUN=true it prepares everything and prints the gh
commands instead of running them.

Reads REPO, SHA, TAG, VERSION, TITLE, DRAFT, ASSETS_DIR, CHECKSUMS, NOTES_FILE,
DRY_RUN and RUNNER_TEMP from the environment, and writes released=true to
$GITHUB_OUTPUT on success.
"""

import hashlib
import os
import shlex
import subprocess
import sys
from pathlib import Path


def render_notes(template, version, tag, repo):
    return template.replace("{version}", version).replace("{tag}", tag).replace("{repository}", repo)


def find_assets(directory):
    directory = Path(directory)
    return sorted(p for p in directory.rglob("*") if p.is_file()) if directory.is_dir() else []


def checksums(directory, assets):
    lines = []
    for path in assets:
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        lines.append(f"{digest}  {path.relative_to(directory).as_posix()}\n")
    return "".join(lines)


def prepare(env, workdir):
    """Everything before the tag: returns the tag and release commands."""
    repo, tag, version = env["REPO"], env["TAG"], env["VERSION"]
    release = ["release", "create", tag, "--repo", repo, "--verify-tag",
               "--title", f"{env['TITLE']} {version}", "--generate-notes"]
    if env.get("DRAFT") == "true":
        release.append("--draft")
    if env.get("NOTES_FILE"):
        # gh puts these notes above the generated ones.
        notes = Path(workdir) / "release-notes.md"
        notes.write_text(render_notes(Path(env["NOTES_FILE"]).read_text(encoding="utf-8"),
                                      version, tag, repo), encoding="utf-8")
        release += ["--notes-file", str(notes)]
    assets_dir = env.get("ASSETS_DIR", "")
    assets = find_assets(assets_dir) if assets_dir else []
    if env.get("CHECKSUMS") == "true" and assets:
        sums = Path(workdir) / "SHA256SUMS.txt"
        sums.write_text(checksums(Path(assets_dir), assets), encoding="utf-8")
        assets.append(sums)
    release += [str(a) for a in assets]
    tag_ref = ["api", f"repos/{repo}/git/refs", "-f", f"ref=refs/tags/{tag}",
               "-f", f"sha={env['SHA']}", "--silent"]
    return tag_ref, release


def gh(args):
    return subprocess.run(["gh", *args]).returncode


def publish(env, run=gh):
    repo, tag = env["REPO"], env["TAG"]
    tag_ref, release = prepare(env, env["RUNNER_TEMP"])
    if env.get("DRY_RUN") == "true":
        print("Dry run: would run")
        for command in (tag_ref, release):
            print("  gh " + shlex.join(command))
        return 0
    if run(tag_ref) != 0:
        print(f"::error::Could not create the tag {tag}")
        return 1
    if run(release) != 0:
        print(f"::error::Publishing {tag} failed. Removed the tag and any partial release, "
              "so re-running this workflow retries it.")
        run(["release", "delete", tag, "--repo", repo, "--yes"])
        run(["api", "-X", "DELETE", f"repos/{repo}/git/refs/tags/{tag}", "--silent"])
        return 1
    with open(env["GITHUB_OUTPUT"], "a", encoding="utf-8") as out:
        out.write("released=true\n")
    return 0


def main(env=os.environ):
    return publish(env)


if __name__ == "__main__":
    sys.exit(main())
