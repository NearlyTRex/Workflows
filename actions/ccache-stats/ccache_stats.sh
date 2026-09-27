#!/usr/bin/env bash
# Prints ccache's statistics since they were last zeroed, and warns when it ran
# cleanups in that time. A cleanup evicts the oldest entries to stay under
# CCACHE_MAXSIZE, so a build that triggers them saves a cache holding only its
# tail, and the next run misses on everything before it.
set -euo pipefail

ccache --show-stats --verbose

cleanups="$(ccache --print-stats | awk -F '\t' '$1 == "cleanups_performed" { print $2 }')"
if [ "${cleanups:-0}" -gt 0 ]; then
    default="ccache's default"
    echo "::warning title=ccache is too small::ccache ran out of room and cleaned up $cleanups times during this build, so the next run recompiles what it evicted. Raise ccache-max-size (now ${CCACHE_MAXSIZE:-$default})."
fi
