#!/usr/bin/env bash
set -euo pipefail

if (( $# < 3 )); then
  echo "usage: $0 PRIOR_UNIT REQUIRED_MARKER COMMAND [ARG ...]" >&2
  exit 2
fi

prior_unit=$1
required_marker=$2
shift 2

while systemctl --user is-active --quiet "$prior_unit"; do
  sleep 10
done

if [[ ! -f "$required_marker" ]]; then
  echo "Prior campaign ended without required marker: $required_marker" >&2
  exit 1
fi

exec "$@"
