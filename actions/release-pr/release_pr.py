"""Commit the version file on a release branch and open a pull request for it.

Reads TAG, VERSION, VERSION_FILE, BASE and REPO from the environment, and GH_TOKEN for gh.
Fails when the tag already exists. The release/TAG branch belongs to this action, so a re-run
after a failure force-pushes over it, and an open pull request from it is updated rather than
opened again.
"""

import importlib.util
import os
import subprocess
import sys
from pathlib import Path

_spec = importlib.util.spec_from_file_location(
    "tag_exists", Path(__file__).resolve().parent.parent / "tag-exists" / "tag_exists.py")
_tag_exists = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_tag_exists)
tag_exists = _tag_exists.tag_exists

BOT_NAME = "github-actions[bot]"
BOT_EMAIL = "41898282+github-actions[bot]@users.noreply.github.com"


def git(*arguments):
    subprocess.run(["git", *arguments], check=True)


def open_pull_request(branch):
    result = subprocess.run(["gh", "pr", "view", branch, "--json", "url,state",
                             "--jq", 'select(.state == "OPEN") | .url'],
                            capture_output=True, text=True, check=False)
    return result.stdout.strip() if result.returncode == 0 else ""


def main(env=None):
    env = os.environ if env is None else env
    tag, version, version_file = env["TAG"], env["VERSION"], env["VERSION_FILE"]
    if tag_exists(env["REPO"], tag):
        print(f"::error::Tag {tag} already exists")
        return 1

    branch = f"release/{tag}"
    git("config", "user.name", BOT_NAME)
    git("config", "user.email", BOT_EMAIL)
    git("switch", "-c", branch)
    git("commit", "-m", f"Release {tag}", "--", version_file)
    git("push", "--force", "origin", branch)

    if url := open_pull_request(branch):
        print(f"Updated the open pull request: {url}")
        return 0
    body = f"Sets the version to {version} in `{version_file}`. Merging this tags {tag} and publishes the release."
    subprocess.run(["gh", "pr", "create", "--base", env["BASE"], "--head", branch, "--title", f"Release {tag}",
                    "--body", body], check=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
