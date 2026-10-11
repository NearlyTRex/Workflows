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


def _load(action, module):
    spec = importlib.util.spec_from_file_location(module, Path(__file__).resolve().parent.parent / action / f"{module}.py")
    loaded = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(loaded)
    return loaded


tag_exists = _load("tag-exists", "tag_exists").tag_exists
ensure_pull_request = _load("open-pr", "open_pr").ensure_pull_request

BOT_NAME = "github-actions[bot]"
BOT_EMAIL = "41898282+github-actions[bot]@users.noreply.github.com"


def git(*arguments):
    subprocess.run(["git", *arguments], check=True)


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

    body = f"Sets the version to {version} in `{version_file}`. Merging this tags {tag} and publishes the release."
    ensure_pull_request(branch, env["BASE"], f"Release {tag}", body)
    return 0


if __name__ == "__main__":
    sys.exit(main())
