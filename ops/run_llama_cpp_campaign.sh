#!/usr/bin/env bash
set -euo pipefail

repo_dir="${BENCH_REPO:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
campaign_dir="${BENCH_CAMPAIGN_DIR:?BENCH_CAMPAIGN_DIR is required}"
model_file="${BENCH_GGUF_FILE:?BENCH_GGUF_FILE is required}"
model_id="${BENCH_MODEL_ID:?BENCH_MODEL_ID is required}"
model_digest="${BENCH_MODEL_DIGEST:?BENCH_MODEL_DIGEST is required}"
context_size="${BENCH_CONTEXT_SIZE:-32768}"
port="${BENCH_PORT:-18080}"
timeout="${BENCH_TASK_TIMEOUT:-1800}"
llama_server="${BENCH_LLAMA_SERVER:-$HOME/.local/bin/llama-server}"
state_file="$campaign_dir/pre-campaign-services.env"
server_pid=""

mkdir -p "$campaign_dir"
[[ -f "$model_file" ]] || { echo "GGUF model is missing: $model_file" >&2; exit 1; }
[[ -x "$llama_server" ]] || { echo "llama-server is missing: $llama_server" >&2; exit 1; }
(( timeout >= 1 && timeout <= 1800 )) || { echo "Invalid task timeout" >&2; exit 1; }
(( context_size == 32768 )) || { echo "This campaign requires the frozen 32768 context" >&2; exit 1; }

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

cleanup() {
  if [[ -n "$server_pid" ]] && kill -0 "$server_pid" 2>/dev/null; then
    kill -- "-$server_pid" 2>/dev/null || true
    wait "$server_pid" 2>/dev/null || true
  fi
  [[ "${HERMES_GATEWAY_WAS_ACTIVE:-0}" == 1 ]] && systemctl --user start hermes-gateway.service || true
  [[ "${OPENCLAW_GATEWAY_WAS_ACTIVE:-0}" == 1 ]] && systemctl --user start openclaw-gateway.service || true
  [[ "${COMFYUI_WAS_ACTIVE:-0}" == 1 ]] && systemctl --user start comfyui.service || true
}
trap cleanup EXIT

systemctl --user stop hermes-gateway.service openclaw-gateway.service comfyui.service
while read -r resident; do
  [[ -n "$resident" ]] && ollama stop "$resident" >/dev/null 2>&1 || true
done < <(curl -fsS http://127.0.0.1:11434/api/ps | python3 -c 'import json,sys; [print(x.get("name") or x.get("model") or "") for x in json.load(sys.stdin).get("models",[])]')

if [[ -n "$(nvidia-smi --query-compute-apps=pid --format=csv,noheader,nounits | tr -d '[:space:]')" ]]; then
  echo "Unrelated GPU compute is active after service shutdown" >&2
  exit 1
fi

setsid "$llama_server" \
  --model "$model_file" --alias "$model_id" --ctx-size "$context_size" \
  --host 127.0.0.1 --port "$port" --jinja --reasoning off --temp 0 \
  --n-gpu-layers 999 >"$campaign_dir/llama-server.log" 2>&1 &
server_pid=$!
printf '%s\n' "$server_pid" >"$campaign_dir/server.pid"
"$llama_server" --version >"$campaign_dir/runner-version.txt" 2>&1 || true

python3 "$repo_dir/scripts/openai_compatible_benchmarks.py" \
  --endpoint "http://127.0.0.1:$port/v1/chat/completions" \
  --model "$model_id" --model-digest "$model_digest" \
  --model-runner "llama.cpp" \
  --runner-version "$(head -1 "$campaign_dir/runner-version.txt")" \
  --output-dir "$campaign_dir/results" --server-pid "$server_pid" \
  --startup-timeout 900 --timeout "$timeout" --run

touch "$campaign_dir/campaign.done"
