"""Fail on a -latest runner label in the repo's workflows.

GitHub moves ubuntu-latest, windows-latest and macos-latest to a new image without a commit, and
whatever a job builds moves with it: a newer glibc, compiler or SDK in every binary. A versioned
label, such as ubuntu-24.04, changes only when someone changes it.

Usage:
    check_runner_labels.py [DIRECTORY]

Checks every *.yml and *.yaml in DIRECTORY (default .github/workflows), comments aside, so a
matrix list or an expression comparing against a label counts as well as runs-on itself.
"""

import re
import sys
from pathlib import Path

LATEST = re.compile(r"\b(?:ubuntu|windows|macos)-latest(?:-[\w-]+)?\b")
# A # that opens a comment: at the start of the line, or after whitespace.
COMMENT = re.compile(r"(?:^|\s)#.*$")


def latest_labels(text):
    """(line, column, label) for each -latest label outside a comment."""
    found = []
    for number, line in enumerate(text.splitlines(), start=1):
        code = COMMENT.sub("", line)
        found += [(number, match.start() + 1, match.group()) for match in LATEST.finditer(code)]
    return found


def main(argv=None):
    """Annotate each -latest label in the workflows; 1 when there is any."""
    argv = sys.argv[1:] if argv is None else argv
    directory = Path(argv[0] if argv else ".github/workflows")
    status = 0
    for path in sorted(directory.glob("*.y*ml")):
        for line, column, label in latest_labels(path.read_text(encoding="utf-8")):
            print(f"::error file={path},line={line},col={column}::{label} moves to a new image without a "
                  "commit; name a version, such as ubuntu-24.04, windows-2025 or macos-15")
            status = 1
    if status == 0:
        print(f"No -latest runner labels in {directory}")
    return status


if __name__ == "__main__":
    sys.exit(main())
