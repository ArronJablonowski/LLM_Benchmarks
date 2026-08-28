#!/usr/bin/env bash
set -euo pipefail

repo=${BENCH_REPO:-"$(cd "$(dirname "$0")/.." && pwd)"}
campaign_dir=${BENCH_CAMPAIGN_DIR:?BENCH_CAMPAIGN_DIR is required}
models_file=${BENCH_MODELS_FILE:-"$campaign_dir/models.tsv"}
coh_binary=${BENCH_COH_BINARY:-"$HOME/Developer/COH-toolchains/bin/cohollamabench"}
python_bin=${BENCH_PYTHON:-python3}
timeout=${BENCH_TASK_TIMEOUT:-1800}
uid_value=$(id -u)

[[ -x "$coh_binary" ]] || { echo "COH binary is missing or not executable: $coh_binary" >&2; exit 2; }
[[ -s "$models_file" ]] || { echo "Frozen models file is missing or empty: $models_file" >&2; exit 2; }
[[ "$timeout" =~ ^[0-9]+$ ]] && (( timeout >= 1 && timeout <= 1800 )) || {
  echo "BENCH_TASK_TIMEOUT must be between 1 and 1800 seconds" >&2; exit 2;
}

mkdir -p "$campaign_dir/logs" "$campaign_dir/markers" "$campaign_dir/results"
service_state="$campaign_dir/service-state.tsv"
terminal_file="$campaign_dir/terminal-models.tsv"
[[ -e "$terminal_file" ]] || printf 'model\tdigest\treason\n' >"$terminal_file"

hermes_active=0
openclaw_active=0
launchctl print "gui/$uid_value/ai.hermes.gateway" >/dev/null 2>&1 && hermes_active=1
launchctl print "gui/$uid_value/ai.openclaw.gateway" >/dev/null 2>&1 && openclaw_active=1
printf 'service\tpre_active\nhermes\t%s\nopenclaw\t%s\n' "$hermes_active" "$openclaw_active" >"$service_state"

restore_services() {
  /opt/homebrew/bin/ollama ps 2>/dev/null | awk 'NR > 1 {print $1}' | while IFS= read -r model; do
    [[ -n "$model" ]] && /opt/homebrew/bin/ollama stop "$model" >/dev/null 2>&1 || true
  done
  if (( hermes_active )); then
    launchctl bootstrap "gui/$uid_value" "$HOME/Library/LaunchAgents/ai.hermes.gateway.plist" >/dev/null 2>&1 || true
    launchctl kickstart -k "gui/$uid_value/ai.hermes.gateway" >/dev/null 2>&1 || true
  fi
  if (( openclaw_active )); then
    launchctl bootstrap "gui/$uid_value" "$HOME/Library/LaunchAgents/ai.openclaw.gateway.plist" >/dev/null 2>&1 || true
    launchctl kickstart -k "gui/$uid_value/ai.openclaw.gateway" >/dev/null 2>&1 || true
  fi
}
trap restore_services EXIT INT TERM

launchctl bootout "gui/$uid_value/ai.hermes.gateway" >/dev/null 2>&1 || true
launchctl bootout "gui/$uid_value/ai.openclaw.gateway" >/dev/null 2>&1 || true

while IFS=$'\t' read -r model digest aliases; do
  [[ -n "$model" && "$model" != \#* ]] || continue
  safe_name=${model//\//_}; safe_name=${safe_name//:/_}
  marker="$campaign_dir/markers/$safe_name.done"
  [[ -e "$marker" ]] && continue
  output_dir="$campaign_dir/results/$safe_name"
  log="$campaign_dir/logs/$safe_name.log"
  printf '%s\t%s\t%s\n' "$(date -u +%FT%TZ)" "$model" "starting" >>"$campaign_dir/progress.tsv"
  if PATH="/opt/homebrew/bin:/usr/bin:/bin:$PATH" "$python_bin" "$repo/scripts/coh_ollama_benchmarks.py" \
      --binary "$coh_binary" --model "$model" --model-digest "$digest" \
      --output-dir "$output_dir" --timeout "$timeout" --run >"$log" 2>&1; then
    touch "$marker"
    printf '%s\t%s\t%s\n' "$(date -u +%FT%TZ)" "$model" "complete" >>"$campaign_dir/progress.tsv"
  else
    reason=$(tail -n 20 "$log" | tr '\n' ' ' | cut -c1-1200)
    printf '%s\t%s\t%s\n' "$model" "$digest" "$reason" >>"$terminal_file"
    touch "$campaign_dir/markers/$safe_name.terminal"
    printf '%s\t%s\t%s\n' "$(date -u +%FT%TZ)" "$model" "terminal" >>"$campaign_dir/progress.tsv"
  fi
done <"$models_file"

touch "$campaign_dir/campaign.done"
