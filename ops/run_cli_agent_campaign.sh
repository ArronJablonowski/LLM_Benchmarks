#!/usr/bin/env bash
set -euo pipefail

repo_dir="${BENCH_REPO:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
campaign_dir="${BENCH_CAMPAIGN_DIR:?BENCH_CAMPAIGN_DIR is required}"
models_file="${BENCH_MODELS_FILE:-$campaign_dir/models.tsv}"
workspace="$campaign_dir/workspace"
python_bin="${BENCH_PYTHON:-python3}"
timeout="${BENCH_TASK_TIMEOUT:-1800}"
suite="${BENCH_SUITE:-standard}"
state_file="$campaign_dir/pre-campaign-services.env"

export PATH="$HOME/.local/bin:$PATH"
mkdir -p "$campaign_dir" "$workspace"

if [[ ! -s "$models_file" ]]; then
  echo "Frozen models file is missing or empty: $models_file" >&2
  exit 1
fi
max_timeout=1800
[[ "$suite" == "coding" || "$suite" == "creative" ]] && max_timeout=14400
if (( timeout < 1 || timeout > max_timeout )); then
  echo "BENCH_TASK_TIMEOUT must be between 1 and $max_timeout for suite $suite" >&2
  exit 1
fi
if systemctl --user is-active --quiet download-qwen38-flash-next-quants-20260828.service; then
  echo "Model download is still active; refusing to overlap inference" >&2
  exit 1
fi

if [[ ! -e "$state_file" ]]; then
  {
    for service in hermes-gateway openclaw-gateway comfyui; do
      if systemctl --user is-active --quiet "$service.service"; then
        printf '%s_WAS_ACTIVE=1\n' "${service^^}" | tr '-' '_'
      else
        printf '%s_WAS_ACTIVE=0\n' "${service^^}" | tr '-' '_'
      fi
    done
  } >"$state_file"
fi
# shellcheck disable=SC1090
source "$state_file"

restore_services() {
  while read -r model _digest _rest; do
    [[ -n "${model:-}" && "$model" != \#* ]] && ollama stop "$model" >/dev/null 2>&1 || true
  done <"$models_file"
  [[ "${HERMES_GATEWAY_WAS_ACTIVE:-0}" == 1 ]] && systemctl --user start hermes-gateway.service || true
  [[ "${OPENCLAW_GATEWAY_WAS_ACTIVE:-0}" == 1 ]] && systemctl --user start openclaw-gateway.service || true
  [[ "${COMFYUI_WAS_ACTIVE:-0}" == 1 ]] && systemctl --user start comfyui.service || true
}
trap restore_services EXIT

systemctl --user stop hermes-gateway.service openclaw-gateway.service comfyui.service

for harness in ${BENCH_CLI_HARNESSES:-pi goose}; do
  "$python_bin" "$repo_dir/scripts/cli_agent_benchmarks.py" \
    --suite "$suite" \
    --harness "$harness" \
    --models-file "$models_file" \
    --output-dir "$campaign_dir/$harness" \
    --workspace "$workspace" \
    --timeout "$timeout" \
    --run
done

if [[ "$suite" == "coding" ]]; then
  "$python_bin" "$repo_dir/dashboard/generate_coding_report.py" \
    --input-root "$campaign_dir" \
    --output "$campaign_dir/coding_agent_report.html"
elif [[ "$suite" == "creative" ]]; then
  "$python_bin" "$repo_dir/dashboard/generate_creative_review.py" \
    --input-root "$campaign_dir" \
    --output "$campaign_dir/creative_human_review.html"
fi

touch "$campaign_dir/campaign.done"
