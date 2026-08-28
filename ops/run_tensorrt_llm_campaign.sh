#!/usr/bin/env bash
set -euo pipefail

repo_dir="${BENCH_REPO:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
campaign_dir="${BENCH_CAMPAIGN_DIR:?BENCH_CAMPAIGN_DIR is required}"
model_path="${BENCH_MODEL_PATH:?BENCH_MODEL_PATH is required}"
model_id="${BENCH_MODEL_ID:?BENCH_MODEL_ID is required}"
model_digest="${BENCH_MODEL_DIGEST:?BENCH_MODEL_DIGEST is required}"
context_size="${BENCH_CONTEXT_SIZE:-32768}"
port="${BENCH_PORT:-18082}"
timeout="${BENCH_TASK_TIMEOUT:-1800}"
kv_cache_fraction="${BENCH_KV_CACHE_FRACTION:-0.70}"
image="${BENCH_TENSORRT_LLM_IMAGE:-nvcr.io/nvidia/tensorrt-llm/release:1.3.0rc13}"
container_name="${BENCH_TENSORRT_CONTAINER_NAME:-tensorrt-llm-benchmark}"
reasoning_parser="${BENCH_REASONING_PARSER:-qwen3}"
state_file="$campaign_dir/pre-campaign-services.env"

mkdir -p "$campaign_dir"
[[ -d "$model_path" ]] || { echo "Model directory is missing: $model_path" >&2; exit 1; }
command -v docker >/dev/null || { echo "Docker is required" >&2; exit 1; }
docker info >/dev/null 2>&1 || {
  echo "Docker is installed but inaccessible to this process; refresh its docker-group membership or launch through 'sg docker -c'." >&2
  exit 1
}
docker image inspect "$image" >/dev/null 2>&1 || {
  echo "TensorRT-LLM image is missing: $image" >&2
  exit 1
}
(( timeout >= 1 && timeout <= 1800 )) || { echo "Invalid task timeout" >&2; exit 1; }
(( context_size == 32768 )) || { echo "This campaign requires the frozen 32768 context" >&2; exit 1; }
python3 - "$kv_cache_fraction" <<'PY'
import sys
value = float(sys.argv[1])
if not 0.50 <= value <= 0.85:
    raise SystemExit("KV cache fraction must be between 0.50 and 0.85")
PY

if docker container inspect "$container_name" >/dev/null 2>&1; then
  echo "Refusing to replace existing container: $container_name" >&2
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

cleanup() {
  docker rm -f "$container_name" >/dev/null 2>&1 || true
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

{
  docker image inspect "$image" --format 'image={{index .RepoDigests 0}} id={{.Id}}'
  docker run --rm --gpus all "$image" python3 -c 'import tensorrt_llm; print("version=" + tensorrt_llm.__version__)' 2>&1 | tail -1
} >"$campaign_dir/runner-version.txt"

server_args=(
  trtllm-serve serve /model
  --served_model_name "$model_id" --host 127.0.0.1 --port "$port"
  --backend pytorch --max_seq_len "$context_size" --max_batch_size 1
  --free_gpu_memory_fraction "$kv_cache_fraction" --no-telemetry
)
if [[ -n "$reasoning_parser" ]]; then
  server_args+=(--reasoning_parser "$reasoning_parser")
fi

docker run --detach --rm --name "$container_name" \
  --gpus all --ipc host --network host \
  --ulimit memlock=-1 --ulimit stack=67108864 \
  --volume "$model_path:/model:ro" \
  "$image" "${server_args[@]}" >"$campaign_dir/container.id"

docker logs --follow "$container_name" >"$campaign_dir/tensorrt-llm-server.log" 2>&1 &
log_pid=$!
trap 'kill "$log_pid" 2>/dev/null || true; cleanup' EXIT

server_pid="$(docker inspect --format '{{.State.Pid}}' "$container_name")"
[[ "$server_pid" =~ ^[1-9][0-9]*$ ]] || { echo "Could not resolve container server PID" >&2; exit 1; }
printf '%s\n' "$server_pid" >"$campaign_dir/server.pid"

python3 "$repo_dir/scripts/openai_compatible_benchmarks.py" \
  --suite "${BENCH_SUITE:-standard}" \
  --endpoint "http://127.0.0.1:$port/v1/chat/completions" \
  --model "$model_id" --model-digest "$model_digest" \
  --model-runner "TensorRT-LLM" --runner-version "$(tr '\n' ' ' <"$campaign_dir/runner-version.txt")" \
  --output-dir "$campaign_dir/results" --server-pid "$server_pid" \
  --startup-timeout 900 --timeout "$timeout" --run

touch "$campaign_dir/campaign.done"
