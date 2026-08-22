#!/usr/bin/env bash
set -u
export BENCH_CAMPAIGN_ID=standard_three_path_20260822
script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BENCH_REPO_DIR="$(cd "$script_dir/.." && pwd)"
export BENCH_REPO_DIR
export BENCH_HERMES_CLOUD_MODELS="gpt-5.6-luna gpt-5.6-terra gpt-5.6-sol"
export BENCH_OPENCLAW_CLOUD_MODELS="gpt-5.6-luna gpt-5.6-terra gpt-5.6-sol"
export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"
exec /bin/bash "$BENCH_REPO_DIR/ops/run_standard_three_path_campaign.sh"
