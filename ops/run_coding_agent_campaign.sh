#!/usr/bin/env bash
set -euo pipefail

export BENCH_SUITE=coding
export BENCH_TASK_TIMEOUT="${BENCH_TASK_TIMEOUT:-7200}"
exec "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/run_cli_agent_campaign.sh" "$@"
