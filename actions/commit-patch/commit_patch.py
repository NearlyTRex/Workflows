"""Apply a patch from diff-paths, commit it as github-actions[bot], and push it.

Reads PATCH, MESSAGE and BRANCH from the environment; an empty PATCH means changes.patch in
RUNNER_TEMP. {date} in MESSAGE becomes today's UTC date. Needs a checkout of BRANCH with push
credentials.
"""

import datetime
import os
import subprocess
import sys

BOT_NAME = "github-actions[bot]"
BOT_EMAIL = "41898282+github-actions[bot]@users.noreply.github.com"


def patch_path(env):
    return env.get("PATCH") or os.path.join(env["RUNNER_TEMP"], "changes.patch")


def git(*args, check=True, **kwargs):
    return subprocess.run(["git", *args], check=check, **kwargs)


def main(env=None, today=None):
    env = os.environ if env is None else env
    today = today or datetime.datetime.now(datetime.timezone.utc).date()
    branch = env["BRANCH"]
    message = env["MESSAGE"].replace("{date}", today.isoformat())
    git("apply", "--index", patch_path(env))
    git("-c", f"user.name={BOT_NAME}", "-c", f"user.email={BOT_EMAIL}", "commit", "--quiet", "-m", message)
    git("push", "origin", f"HEAD:{branch}")
    commit = git("rev-parse", "--short", "HEAD", capture_output=True, text=True).stdout.strip()
    summary = f"Pushed {commit} to {branch}: {message}\n"
    print(summary, end="")
    if step_summary := env.get("GITHUB_STEP_SUMMARY"):
        with open(step_summary, "a", encoding="utf-8") as out:
            out.write(summary)
    return 0


if __name__ == "__main__":
    sys.exit(main())
