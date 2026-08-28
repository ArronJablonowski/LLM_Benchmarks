#!/usr/bin/env bash
set -euo pipefail

repo_dir="${BENCH_REPO:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
campaign_dir="${BENCH_CAMPAIGN_DIR:?BENCH_CAMPAIGN_DIR is required}"
model_path="${BENCH_MODEL_PATH:?BENCH_MODEL_PATH is required}"
model_id="${BENCH_MODEL_ID:?BENCH_MODEL_ID is required}"
model_digest="${BENCH_MODEL_DIGEST:?BENCH_MODEL_DIGEST is required}"
context_size="${BENCH_CONTEXT_SIZE:-32768}"
port="${BENCH_PORT:-18081}"
timeout="${BENCH_TASK_TIMEOUT:-1800}"
gpu_memory_utilization="${BENCH_GPU_MEMORY_UTILIZATION:-0.70}"
reasoning_parser="${BENCH_REASONING_PARSER:-}"
vllm_bin="${BENCH_VLLM_BIN:-$HOME/.local/venvs/vllm-0.28.0/bin/vllm}"
vllm_env_bin="$(dirname "$vllm_bin")"
state_file="$campaign_dir/pre-campaign-services.env"
server_pid=""

mkdir -p "$campaign_dir"
[[ -d "$model_path" ]] || { echo "Model directory is missing: $model_path" >&2; exit 1; }
[[ -x "$vllm_bin" ]] || { echo "vLLM is missing: $vllm_bin" >&2; exit 1; }
export PATH="$vllm_env_bin:$PATH"
command -v ninja >/dev/null || {
  echo "vLLM FlashInfer JIT prerequisite is missing: ninja" >&2
  exit 1
}
(( timeout >= 1 && timeout <= 1800 )) || { echo "Invalid task timeout" >&2; exit 1; }
(( context_size == 32768 )) || { echo "This campaign requires the frozen 32768 context" >&2; exit 1; }
python3 - "$gpu_memory_utilization" <<'PY'
import sys
value = float(sys.argv[1])
if not 0.50 <= value <= 0.85:
    raise SystemExit("GPU memory utilization must be between 0.50 and 0.85")
PY

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

"$vllm_bin" --version >"$campaign_dir/runner-version.txt" 2>&1
server_args=(
  serve "$model_path"
  --served-model-name "$model_id" --host 127.0.0.1 --port "$port"
  --max-model-len "$context_size" --gpu-memory-utilization "$gpu_memory_utilization"
)
if [[ -n "$reasoning_parser" ]]; then
  server_args+=(--reasoning-parser "$reasoning_parser")
fi
setsid "$vllm_bin" "${server_args[@]}" \
  >"$campaign_dir/vllm-server.log" 2>&1 &
server_pid=$!
printf '%s\n' "$server_pid" >"$campaign_dir/server.pid"

python3 "$repo_dir/scripts/openai_compatible_benchmarks.py" \
  --endpoint "http://127.0.0.1:$port/v1/chat/completions" \
  --model "$model_id" --model-digest "$model_digest" \
  --model-runner "vLLM" --runner-version "$(head -1 "$campaign_dir/runner-version.txt")" \
  --output-dir "$campaign_dir/results" --server-pid "$server_pid" \
  --startup-timeout 900 --timeout "$timeout" --run

touch "$campaign_dir/campaign.done"
