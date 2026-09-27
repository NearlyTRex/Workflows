#!/bin/sh
# Runs gitleaks over the git history, or only over a range of it.
#
#   entrypoint.sh                  # the whole history
#   entrypoint.sh BASE..HEAD       # only the commits in that range
set -eu

if [ -n "${1:-}" ]; then
    exec gitleaks git --redact --no-banner --exit-code 1 --log-opts="$1" .
fi
exec gitleaks git --redact --no-banner --exit-code 1 .
