#!/usr/bin/env bash
# Installs the fixture app's config and service, the way a script for a fresh
# server would. python-ci's shell coverage measures it, and the tests run it in
# the shell sandbox.
set -euo pipefail

port=${1:-}
[ -n "$port" ] || { echo "usage: install.sh PORT" >&2; exit 2; }

mkdir -p /etc/fixture-app
printf 'port=%s\n' "$port" >/etc/fixture-app/config
systemctl enable fixture-app
echo "installed on port $port"
