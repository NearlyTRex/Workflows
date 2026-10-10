"""Report whether a tag exists on GitHub, as the step output exists=true or false.

Reads TAG and REPO from the environment, and GH_TOKEN for gh. Anything gh can't confirm,
such as a missing tag or a failed request, counts as not existing.
"""

import os
import subprocess
import sys


def tag_exists(repo, tag):
    result = subprocess.run(["gh", "api", f"repos/{repo}/git/ref/tags/{tag}", "--silent"],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
    return result.returncode == 0


def main(env=None):
    env = os.environ if env is None else env
    tag = env["TAG"]
    exists = tag_exists(env["REPO"], tag)
    print(f"{tag} already exists" if exists else f"{tag} does not exist yet")
    with open(env["GITHUB_OUTPUT"], "a", encoding="utf-8") as out:
        out.write(f"exists={'true' if exists else 'false'}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
