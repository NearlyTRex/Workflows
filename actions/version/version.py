"""Read or bump the X.Y.Z version held in a project file.

Usage:
    version.py read FILE
    version.py bump FILE (patch | minor | major | X.Y.Z)

FILE is a pyproject.toml ([project] version), package.json, CMakeLists.txt
(project(... VERSION X.Y.Z)), or any other file holding just the version.
bump rewrites the file in place and refuses a version that is not higher.
"""

import re
import sys
from pathlib import Path

SEMVER = r"(\d+)\.(\d+)\.(\d+)"


class VersionError(Exception):
    pass


def locate(path, text):
    """Return (start, end) of the version string within text."""
    name = path.name
    if name == "pyproject.toml":
        section = re.search(r"(?ms)^\[project\]\s*$(.*?)(?=^\[|\Z)", text)
        if not section:
            raise VersionError(f"{path}: no [project] table")
        match = re.search(rf'(?m)^version\s*=\s*"({SEMVER})"', section.group(1))
        offset = section.start(1)
    elif name == "package.json":
        match = re.search(rf'"version"\s*:\s*"({SEMVER})"', text)
        offset = 0
    elif name == "CMakeLists.txt":
        match = re.search(rf"(?is)\bproject\s*\([^)]*?\bVERSION\s+({SEMVER})\b", text)
        offset = 0
    else:
        match = re.fullmatch(rf"\s*({SEMVER})\s*", text)
        offset = 0
    if not match:
        raise VersionError(f"{path}: no X.Y.Z version found")
    return offset + match.start(1), offset + match.end(1)


def parse(version):
    match = re.fullmatch(rf"v?{SEMVER}", version.strip())
    if not match:
        raise VersionError(f"'{version}' is not X.Y.Z")
    return tuple(int(n) for n in match.groups())


def read(path):
    text = Path(path).read_text()
    start, end = locate(Path(path), text)
    return text[start:end]


def next_version(current, spec):
    major, minor, patch = parse(current)
    if spec == "patch":
        new = (major, minor, patch + 1)
    elif spec == "minor":
        new = (major, minor + 1, 0)
    elif spec == "major":
        new = (major + 1, 0, 0)
    else:
        new = parse(spec)
    if new <= (major, minor, patch):
        raise VersionError(f"{'.'.join(map(str, new))} is not higher than {current}")
    return ".".join(map(str, new))


def bump(path, spec):
    path = Path(path)
    text = path.read_text()
    start, end = locate(path, text)
    new = next_version(text[start:end], spec)
    path.write_text(text[:start] + new + text[end:])
    return new


def main(argv):
    try:
        if len(argv) == 2 and argv[0] == "read":
            print(read(argv[1]))
        elif len(argv) == 3 and argv[0] == "bump":
            print(bump(argv[1], argv[2]))
        else:
            print(__doc__.strip(), file=sys.stderr)
            return 2
    except (OSError, VersionError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
