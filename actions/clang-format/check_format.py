"""Check every tracked C and C++ source with clang-format and annotate what it would change.

Usage: check_format.py CLANG_FORMAT
"""

import re
import subprocess
import sys

PATTERNS = ["*.c", "*.cc", "*.cpp", "*.cxx", "*.c++", "*.cppm", "*.ixx",
            "*.h", "*.hh", "*.hpp", "*.hxx", "*.h++", "*.inl", "*.ipp", "*.mm"]
# Keeps each command line well under the OS limit in a repo with many sources.
BATCH = 200
VIOLATION = re.compile(r"(?m)^(?P<file>.+?):(?P<line>\d+):(?P<col>\d+): (?:error|warning): "
                       r"(?P<message>.+) \[-Wclang-format-violations\]$")


def main(argv=None):
    clang_format = (argv if argv is not None else sys.argv[1:])[0]
    paths = [p for p in subprocess.run(["git", "ls-files", "-z", *PATTERNS], capture_output=True, check=True,
                                       text=True).stdout.split("\0") if p]
    unformatted, failed = set(), False
    for start in range(0, len(paths), BATCH):
        result = subprocess.run([clang_format, "--dry-run", "--Werror", "--style=file", *paths[start:start + BATCH]],
                                capture_output=True, text=True)
        if result.returncode == 0:
            continue
        failed = True
        # Each violation is followed by the source line and a caret, which the
        # annotation replaces. Anything else, such as a .clang-format it can't
        # read, is shown as is.
        if not VIOLATION.search(result.stderr):
            print(result.stderr, end="")
        for line in result.stderr.splitlines():
            if match := VIOLATION.match(line):
                unformatted.add(match["file"])
                print(f"::error file={match['file']},line={match['line']},col={match['col']}::{match['message']}")
    print(f"{len(paths)} files checked, {len(unformatted)} not formatted")
    if unformatted:
        print("Fix with: clang-format -i " + " ".join(sorted(unformatted)))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
