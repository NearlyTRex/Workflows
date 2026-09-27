"""Work out which commits a secret scan needs to cover.

Pull requests and pushes cover only their new commits: everything before them
was scanned when it landed. Anything else, and a push whose earlier commit is
unknown (a new branch, a force push), covers the whole history rather than risk
skipping a commit.

Reads the event from EVENT, PR_BASE, PR_HEAD, BEFORE and AFTER, and writes the
git log range, empty for the whole history, as log-opts to $GITHUB_OUTPUT.
"""

import os
import subprocess
import sys


def commit_exists(sha):
    return subprocess.run(["git", "cat-file", "-e", f"{sha}^{{commit}}"],
                          capture_output=True).returncode == 0


def scan_range(event, pr_base, pr_head, before, after, exists=commit_exists):
    if event == "pull_request" and pr_base and pr_head:
        return f"{pr_base}..{pr_head}"
    # A new branch reports its earlier commit as all zeros.
    if event == "push" and before.strip("0") and after and exists(before):
        return f"{before}..{after}"
    return ""


def count(rev):
    return subprocess.run(["git", "rev-list", "--count", rev], capture_output=True,
                          text=True, check=True).stdout.strip()


def main(env=os.environ):
    log_opts = scan_range(env.get("EVENT", ""), env.get("PR_BASE", ""), env.get("PR_HEAD", ""),
                          env.get("BEFORE", ""), env.get("AFTER", ""))
    if log_opts:
        print(f"Scanning the new commits: {log_opts} ({count(log_opts)} commits)")
    else:
        print(f"Scanning the whole history ({count('HEAD')} commits)")
    with open(env["GITHUB_OUTPUT"], "a", encoding="utf-8") as out:
        out.write(f"log-opts={log_opts}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
