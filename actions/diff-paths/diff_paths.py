"""Stage the changes under some paths and write them to a patch another job can apply.

Reads PATHS (separated by whitespace) and PATCH, the file to write, from the environment; an
empty PATCH means changes.patch in RUNNER_TEMP. Sets the step output changed=true or false, and
writes a --stat of the changes to the job summary.
"""

import os
import subprocess
import sys


def patch_path(env):
    """PATCH, or changes.patch in RUNNER_TEMP when it's empty."""
    return env.get("PATCH") or os.path.join(env["RUNNER_TEMP"], "changes.patch")


def git(*args, check=True, **kwargs):
    """Run git with args, passing kwargs to subprocess.run."""
    return subprocess.run(["git", *args], check=check, **kwargs)


def main(env=None):
    """Stage PATHS, write the patch when anything changed, and set the changed output."""
    env = os.environ if env is None else env
    paths = env["PATHS"].split()
    if not paths:
        print("::error::paths is empty")
        return 1
    git("add", "--all", "--", *paths)
    changed = git("diff", "--cached", "--quiet", check=False).returncode != 0
    if changed:
        with open(patch_path(env), "wb") as patch:
            git("diff", "--cached", "--binary", stdout=patch)
        stat = git("diff", "--cached", "--stat", capture_output=True, text=True).stdout
        summary = f"```text\n{stat}```\n"
    else:
        summary = "Nothing changed.\n"
    print(summary, end="")
    if step_summary := env.get("GITHUB_STEP_SUMMARY"):
        with open(step_summary, "a", encoding="utf-8") as out:
            out.write(summary)
    with open(env["GITHUB_OUTPUT"], "a", encoding="utf-8") as out:
        out.write(f"changed={'true' if changed else 'false'}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
