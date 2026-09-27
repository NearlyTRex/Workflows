"""Fail if git tracks a file that could hold a credential.

gitleaks scans file contents; this checks the complementary thing, that whole
categories of file are never committed at all. A binary file, such as a SQLite
database an app keeps a token in, is not something a content scanner reliably
flags.

Usage:
    check_tracked_files.py [PATTERN ...]

Each PATTERN adds to the defaults. A pattern containing "/" matches the whole
path, anything else matches the file name. "!PATTERN" makes an exception.
Templates (*.example, *.sample, *.template) are always allowed: they hold
placeholders.
"""

import fnmatch
import subprocess
import sys

DEFAULTS = [
    ".env", ".env.*", "*.env",
    "*.pem", "*.key", "*.p12", "*.pfx",
    "id_rsa", "id_dsa", "id_ecdsa", "id_ed25519",
]
TEMPLATES = ["*.example", "*.sample", "*.template"]


def matches(path, pattern):
    target = path if "/" in pattern else path.rsplit("/", 1)[-1]
    return fnmatch.fnmatchcase(target, pattern)


def forbidden(paths, patterns):
    rules = DEFAULTS + [p for p in patterns if not p.startswith("!")]
    exceptions = TEMPLATES + [p[1:] for p in patterns if p.startswith("!")]
    return [path for path in paths
            if any(matches(path, rule) for rule in rules)
            and not any(matches(path, allowed) for allowed in exceptions)]


def main(argv=None):
    patterns = sys.argv[1:] if argv is None else argv
    paths = subprocess.run(["git", "ls-files", "-z"], capture_output=True, check=True,
                           text=True).stdout.split("\0")
    found = forbidden(filter(None, paths), patterns)
    for path in found:
        print(f"::error file={path}::{path} may hold a credential and must not be tracked")
    if found:
        print("Remove each with `git rm --cached <file>`, or add `!<pattern>` if it is genuinely safe. "
              "Anything already pushed must be treated as leaked: rotate the credential.")
        return 1
    print("No credential-bearing files are tracked")
    return 0


if __name__ == "__main__":
    sys.exit(main())
