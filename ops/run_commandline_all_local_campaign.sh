#!/usr/bin/env bash
set -euo pipefail

repo_dir="${BENCH_REPO:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
campaign_dir="${BENCH_CAMPAIGN_DIR:?BENCH_CAMPAIGN_DIR is required}"
models_file="${BENCH_MODELS_FILE:-$campaign_dir/models.tsv}"
workspace="$campaign_dir/workspace"
timeout="${BENCH_TASK_TIMEOUT:-3600}"
openhands_python="${BENCH_OPENHANDS_PYTHON:-$HOME/.local/venvs/openhands-1.11.0/bin/python}"
harnesses="${BENCH_HARNESSES:-ollama-direct hermes openclaw openhands pi goose}"
telemetry_file="$campaign_dir/temperature-telemetry.csv"
state_file="$campaign_dir/campaign-state.env"

export PATH="$HOME/.local/bin:$HOME/.openclaw/bin:$HOME/.openclaw/tools/node/bin:$PATH"
for openclaw_node_bin in "$HOME"/.openclaw/tools/node-v*/bin; do
  [[ -d "$openclaw_node_bin" ]] && export PATH="$openclaw_node_bin:$PATH"
done

mkdir -p "$campaign_dir" "$workspace"
if [[ ! -s "$models_file" ]]; then
  echo "Frozen models file is missing or empty: $models_file" >&2
  exit 1
fi
if (( timeout < 1 || timeout > 14400 )); then
  echo "BENCH_TASK_TIMEOUT must be between 1 and 14400" >&2
  exit 1
fi

for command in ollama hermes openclaw pi goose; do
  command -v "$command" >/dev/null || { echo "Missing harness command: $command" >&2; exit 1; }
done
[[ -x "$openhands_python" ]] || { echo "Missing OpenHands runtime: $openhands_python" >&2; exit 1; }

write_state() {
  local status="$1" harness="${2:-}" started="${3:-}"
  local temporary="$state_file.tmp"
  {
    printf 'status=%q\n' "$status"
    printf 'current_harness=%q\n' "$harness"
    printf 'current_harness_started_at=%q\n' "$started"
    printf 'updated_at=%q\n' "$(date --iso-8601=seconds)"
  } >"$temporary"
  mv "$temporary" "$state_file"
}

sample_temperature() {
  [[ -s "$telemetry_file" ]] || printf 'timestamp,gpu_temp_c\n' >"$telemetry_file"
  while true; do
    temperature="$(nvidia-smi --query-gpu=temperature.gpu --format=csv,noheader,nounits 2>/dev/null | head -1 | tr -d '[:space:]')"
    [[ "$temperature" =~ ^[0-9]+([.][0-9]+)?$ ]] && printf '%s,%s\n' "$(date --iso-8601=seconds)" "$temperature" >>"$telemetry_file"
    sleep 15
  done
}

telemetry_pid=""
campaign_completed=0
cleanup() {
  local exit_code="$?"
  [[ -n "$telemetry_pid" ]] && kill "$telemetry_pid" 2>/dev/null || true
  wait "$telemetry_pid" 2>/dev/null || true
  if (( campaign_completed == 0 )); then
    write_state failed "${harness:-}" "${started_at:-}"
  fi
  return "$exit_code"
}
trap cleanup EXIT INT TERM

sample_temperature &
telemetry_pid="$!"
write_state running

for harness in $harnesses; do
  started_at="$(date --iso-8601=seconds)"
  write_state running "$harness" "$started_at"
  python3 "$repo_dir/scripts/commandline_agent_benchmarks.py" \
    --harness "$harness" \
    --models-file "$models_file" \
    --output-dir "$campaign_dir/$harness" \
    --workspace "$workspace" \
    --timeout "$timeout" \
    --openhands-python "$openhands_python" \
    --run 2>&1 | tee -a "$campaign_dir/$harness.log"
done

write_state complete
touch "$campaign_dir/campaign.done"
campaign_completed=1
