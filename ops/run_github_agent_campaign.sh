#!/usr/bin/env bash
set -euo pipefail

export BENCH_SUITE=github
export BENCH_TASK_TIMEOUT="${BENCH_TASK_TIMEOUT:-3600}"
exec "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/run_cli_agent_campaign.sh" "$@"
