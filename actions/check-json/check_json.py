"""Parse every tracked *.json file and report the ones that aren't valid JSON."""

import json
import subprocess
import sys


def main():
    paths = subprocess.run(["git", "ls-files", "-z", "*.json"], capture_output=True, check=True,
                           text=True).stdout.split("\0")
    bad = 0
    for path in filter(None, paths):
        try:
            with open(path, encoding="utf-8") as f:
                json.load(f)
        except (ValueError, UnicodeDecodeError) as e:
            print(f"::error file={path}::{e}")
            bad += 1
    print(f"{len([p for p in paths if p])} JSON files checked, {bad} invalid")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
