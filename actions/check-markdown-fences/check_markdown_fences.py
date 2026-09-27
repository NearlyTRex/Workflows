"""Report Markdown code fences that don't close where they appear to.

markdownlint's rules can't see this. Under CommonMark a fence line with text after
it does not close a block, so the block runs on to the next bare fence or the end
of the file, and the prose in between is treated as code that the rules skip.
Tracked *.md files are checked, minus the ones .markdownlintignore excludes.
"""

import fnmatch
import re
import subprocess
import sys

FENCE = re.compile(r"(`{3,}|~{3,})(.*)$")
QUOTE = re.compile(r"^(\s*>)+")


def ignored_patterns():
    try:
        with open(".markdownlintignore", encoding="utf-8") as f:
            lines = f.read().splitlines()
    except FileNotFoundError:
        return []
    return [line.strip() for line in lines if line.strip() and not line.strip().startswith("#")]


def is_ignored(path, patterns):
    for pattern in patterns:
        pattern = pattern.lstrip("/")
        if pattern.endswith("/"):
            if path.startswith(pattern):
                return True
        elif fnmatch.fnmatch(path, pattern) or fnmatch.fnmatch(path.rsplit("/", 1)[-1], pattern):
            return True
    return False


def find_problems(text):
    """Return (line, message) for every fence that doesn't open and close cleanly."""
    problems = []
    opened = None
    for number, raw in enumerate(text.splitlines(), 1):
        match = FENCE.match(QUOTE.sub("", raw).lstrip())
        if not match:
            continue
        fence, rest = match.groups()
        if opened is None:
            # A backtick fence's info string can't hold a backtick, so ```x``` is inline code
            if fence[0] == "`" and "`" in rest:
                continue
            opened = (fence[0], len(fence), number)
        elif fence[0] == opened[0] and len(fence) >= opened[1]:
            if rest.strip():
                problems.append((number, "text after a closing fence, so the code block opened on "
                                         f"line {opened[2]} does not end here: {raw.strip()}"))
            opened = None
    if opened is not None:
        problems.append((opened[2], "code block is never closed"))
    return problems


def main():
    paths = subprocess.run(["git", "ls-files", "-z", "*.md"], capture_output=True, check=True,
                           text=True).stdout.split("\0")
    patterns = ignored_patterns()
    checked = [path for path in filter(None, paths) if not is_ignored(path, patterns)]
    bad = 0
    for path in checked:
        with open(path, encoding="utf-8") as f:
            text = f.read()
        for line, message in find_problems(text):
            print(f"::error file={path},line={line}::{message}")
            bad += 1
    print(f"{len(checked)} Markdown files checked, {bad} fence problems")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
