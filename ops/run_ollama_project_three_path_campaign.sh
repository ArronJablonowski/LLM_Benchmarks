#!/usr/bin/env bash
set -euo pipefail

repo_dir="${BENCH_REPO:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
campaign_dir="${BENCH_CAMPAIGN_DIR:?BENCH_CAMPAIGN_DIR is required}"
models_file="${BENCH_MODELS_FILE:-$campaign_dir/models.tsv}"
python_bin="${BENCH_PYTHON:-python3}"
timeout="${BENCH_TASK_TIMEOUT:-7200}"
state_file="$campaign_dir/pre-campaign-services.env"
suite_list="${BENCH_PROJECT_SUITES:-coding creative}"
harness_list="${BENCH_PROJECT_HARNESSES:-ollama-direct hermes openclaw}"

export PATH="$HOME/.local/bin:$HOME/.openclaw/bin:$PATH"
mkdir -p "$campaign_dir"
[[ -s "$models_file" ]] || { echo "Frozen models file is missing: $models_file" >&2; exit 1; }
(( timeout >= 1 && timeout <= 14400 )) || { echo "BENCH_TASK_TIMEOUT must be 1..14400" >&2; exit 1; }
exec >>"$campaign_dir/campaign.log" 2>&1
echo "[$(date '+%Y-%m-%dT%H:%M:%S%z')] project campaign start/resume host=$(hostname) repo=$repo_dir"

for suite in $suite_list; do
  [[ "$suite" == coding || "$suite" == creative ]] || {
    echo "Unsupported project suite: $suite" >&2; exit 1;
  }
done
for harness in $harness_list; do
  [[ "$harness" == ollama-direct || "$harness" == hermes || "$harness" == openclaw ]] || {
    echo "Unsupported project harness: $harness" >&2; exit 1;
  }
done

if [[ ! -e "$state_file" ]]; then
  {
    for service in hermes-gateway openclaw-gateway comfyui; do
      key="$(printf '%s_WAS_ACTIVE' "$service" | tr '[:lower:]-' '[:upper:]_')"
      if systemctl --user is-active --quiet "$service.service"; then
        printf '%s=1\n' "$key"
      else
        printf '%s=0\n' "$key"
      fi
    done
  } >"$state_file"
fi
# shellcheck disable=SC1090
source "$state_file"

stop_model_residency() {
  while read -r resident; do
    [[ -n "$resident" ]] && ollama stop "$resident" >/dev/null 2>&1 || true
  done < <(curl -fsS http://127.0.0.1:11434/api/ps | python3 -c 'import json,sys; [print(x.get("name") or x.get("model") or "") for x in json.load(sys.stdin).get("models",[])]')
}

restore_services() {
  stop_model_residency || true
  systemctl --user stop hermes-gateway.service openclaw-gateway.service comfyui.service >/dev/null 2>&1 || true
  [[ "${HERMES_GATEWAY_WAS_ACTIVE:-0}" == 1 ]] && systemctl --user start hermes-gateway.service || true
  [[ "${OPENCLAW_GATEWAY_WAS_ACTIVE:-0}" == 1 ]] && systemctl --user start openclaw-gateway.service || true
  [[ "${COMFYUI_WAS_ACTIVE:-0}" == 1 ]] && systemctl --user start comfyui.service || true
}
trap restore_services EXIT

systemctl --user stop hermes-gateway.service openclaw-gateway.service comfyui.service
stop_model_residency
curl -fsS http://127.0.0.1:11434/api/version >"$campaign_dir/ollama-version.json"

for suite in $suite_list; do
  for harness in $harness_list; do
    marker="$campaign_dir/markers/$suite-$harness.done"
    [[ -e "$marker" ]] && { echo "already complete: $suite / $harness"; continue; }
    mkdir -p "$(dirname "$marker")" "$campaign_dir/$suite/$harness" "$campaign_dir/workspace"
    stop_model_residency
    systemctl --user stop hermes-gateway.service openclaw-gateway.service comfyui.service
    if [[ "$harness" == openclaw ]]; then
      systemctl --user start openclaw-gateway.service
      for _attempt in $(seq 1 60); do
        systemctl --user is-active --quiet openclaw-gateway.service && break
        sleep 1
      done
      systemctl --user is-active --quiet openclaw-gateway.service || {
        echo "OpenClaw gateway failed to start" >&2; exit 1;
      }
      openclaw health >"$campaign_dir/$suite/$harness/openclaw-health.txt" 2>&1
    fi

    "$python_bin" "$repo_dir/scripts/${suite}_agent_benchmarks.py" \
      --suite "$suite" --harness "$harness" --model-runner ollama \
      --models-file "$models_file" \
      --output-dir "$campaign_dir/$suite/$harness" \
      --workspace "$campaign_dir/workspace/$suite" \
      --timeout "$timeout" --run
    stop_model_residency
    touch "$marker"
  done

  if [[ "$suite" == coding ]]; then
    "$python_bin" "$repo_dir/dashboard/generate_coding_report.py" \
      --input-root "$campaign_dir/coding" \
      --output "$campaign_dir/coding_agent_report.html"
  else
    "$python_bin" "$repo_dir/dashboard/generate_creative_review.py" \
      --input-root "$campaign_dir/creative" \
      --output "$campaign_dir/creative_human_review.html"
  fi
done

stop_model_residency
curl -fsS http://127.0.0.1:11434/api/ps | python3 -c 'import json,sys; assert not json.load(sys.stdin).get("models")'
touch "$campaign_dir/campaign.done"
