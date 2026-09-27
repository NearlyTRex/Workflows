"""Parse every tracked *.toml file and report the ones that aren't valid TOML."""

import subprocess
import sys
import tomllib


def main():
    paths = subprocess.run(["git", "ls-files", "-z", "*.toml"], capture_output=True, check=True,
                           text=True).stdout.split("\0")
    bad = 0
    for path in filter(None, paths):
        try:
            with open(path, "rb") as f:
                tomllib.load(f)
        except (tomllib.TOMLDecodeError, UnicodeDecodeError) as e:
            print(f"::error file={path}::{e}")
            bad += 1
    print(f"{len([p for p in paths if p])} TOML files checked, {bad} invalid")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
