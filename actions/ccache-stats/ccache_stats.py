"""Print ccache's statistics since they were last zeroed, and warn when it ran cleanups.

A cleanup evicts the oldest entries to stay under CCACHE_MAXSIZE, so a build that triggers
them saves a cache holding only its tail, and the next run misses on everything before it.
"""

import os
import subprocess
import sys


def cleanups(print_stats):
    for line in print_stats.splitlines():
        name, _, value = line.partition("\t")
        if name == "cleanups_performed":
            return int(value)
    return 0


def main():
    subprocess.run(["ccache", "--show-stats", "--verbose"], check=True)
    stats = subprocess.run(["ccache", "--print-stats"], capture_output=True, text=True, check=True).stdout
    if count := cleanups(stats):
        size = os.environ.get("CCACHE_MAXSIZE") or "ccache's default"
        print(f"::warning title=ccache is too small::ccache ran out of room and cleaned up {count} times "
              f"during this build, so the next run recompiles what it evicted. Raise ccache-max-size "
              f"(now {size}).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
