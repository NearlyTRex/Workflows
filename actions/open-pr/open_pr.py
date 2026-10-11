"""Open a pull request from HEAD_BRANCH into BASE, or report the one already open from it.

Reads HEAD_BRANCH, BASE, TITLE, BODY and ASSIGNEES (logins separated by spaces, commas or
newlines; may be empty) from the environment, and GH_TOKEN for gh. Sets the step output url
when GITHUB_OUTPUT is set. An open pull request gets the new title and body; the push that
brought its branch up to date is the rest of the update.
"""

import os
import re
import subprocess
import sys


def open_pull_request(branch):
    result = subprocess.run(["gh", "pr", "view", branch, "--json", "url,state",
                             "--jq", 'select(.state == "OPEN") | .url'],
                            capture_output=True, text=True, check=False)
    return result.stdout.strip() if result.returncode == 0 else ""


def ensure_pull_request(head, base, title, body, assignees=()):
    """The URL of the open pull request from head, opening one first if there isn't one."""
    if url := open_pull_request(head):
        subprocess.run(["gh", "pr", "edit", url, "--title", title, "--body", body], check=True)
        print(f"Updated the open pull request: {url}")
        return url
    command = ["gh", "pr", "create", "--base", base, "--head", head, "--title", title, "--body", body]
    for assignee in assignees:
        command += ["--assignee", assignee]
    url = subprocess.run(command, capture_output=True, text=True, check=True).stdout.strip()
    print(f"Opened {url}")
    return url


def main(env=None):
    env = os.environ if env is None else env
    assignees = [name for name in re.split(r"[\s,]+", env.get("ASSIGNEES", "")) if name]
    url = ensure_pull_request(env["HEAD_BRANCH"], env["BASE"], env["TITLE"], env["BODY"], assignees)
    if output := env.get("GITHUB_OUTPUT"):
        with open(output, "a", encoding="utf-8") as out:
            out.write(f"url={url}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
