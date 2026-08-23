#!/usr/bin/env bash
set -u

# Resumable host-local campaign: Direct Ollama -> Hermes -> OpenClaw.
# The default 18-task core profile is intentional; --full-suite is not used.

repo_dir="${BENCH_REPO_DIR:-$HOME/gitRepo/local-llm-benchmark-suite}"
python_bin="${BENCH_PYTHON:-python3}"

# Non-interactive launchd/systemd jobs often omit user-local tool directories.
# Make a managed OpenClaw install discoverable without requiring callers to
# duplicate a host-specific PATH in every service definition.
prepend_path_dir() {
  local candidate="$1"
  [[ -d "$candidate" ]] || return 0
  case ":$PATH:" in
    *":$candidate:"*) ;;
    *) PATH="$candidate:$PATH" ;;
  esac
}
prepend_path_dir "${BENCH_OPENCLAW_BIN_DIR:-$HOME/.openclaw/bin}"
prepend_path_dir "$HOME/.openclaw/tools/node/bin"
for openclaw_node_bin in "$HOME"/.openclaw/tools/node-v*/bin; do
  prepend_path_dir "$openclaw_node_bin"
done
export PATH

selected_task=""
list_tasks=0
while (($#)); do
  case "$1" in
    --list-tasks|--list-tests)
      list_tasks=1; shift ;;
    --test|--task)
      [[ $# -ge 2 ]] || { echo "$1 requires one task ID" >&2; exit 2; }
      selected_task="$2"; shift 2 ;;
    -h|--help)
      echo "Usage: $0 [--list-tasks] [--test TASK_ID]"
      echo "--list-tasks is read-only. Without --test, campaign execution runs all 18 core tasks."
      exit 0 ;;
    *)
      echo "Unknown option: $1" >&2; exit 2 ;;
  esac
done
task_selection="all-core"
if [[ -n "$selected_task" ]]; then
  "$python_bin" "$repo_dir/scripts/ollama_standardized_local_benchmarks.py" --test "$selected_task" --list-tasks >/dev/null
  task_selection="$selected_task"
fi
if ((list_tasks)); then
  if [[ -n "$selected_task" ]]; then
    exec "$python_bin" "$repo_dir/scripts/ollama_standardized_local_benchmarks.py" --test "$selected_task" --list-tasks
  fi
  exec "$python_bin" "$repo_dir/scripts/ollama_standardized_local_benchmarks.py" --list-tasks
fi

# Validate the complete deployed runner set before creating campaign evidence.
# A partial deployment must fail once here instead of producing one false
# failure marker for every model in the Hermes and OpenClaw phases.
required_scripts=(
  ollama_standardized_local_benchmarks.py
  hermes_agent_17_test_benchmarks.py
  openclaw_18_test_benchmarks.py
  vision_benchmark_support.py
)
for required_script in "${required_scripts[@]}"; do
  if [[ ! -r "$repo_dir/scripts/$required_script" ]]; then
    echo "Missing required benchmark script: $repo_dir/scripts/$required_script" >&2
    exit 2
  fi
done
if ! PYTHONPATH="$repo_dir/scripts${PYTHONPATH:+:$PYTHONPATH}" \
  "$python_bin" -c 'import vision_benchmark_support' >/dev/null 2>&1; then
  echo "Unable to import required benchmark support module: vision_benchmark_support" >&2
  exit 2
fi

default_campaign_id="standard_three_path_20260822"
if [[ -n "$selected_task" ]]; then
  safe_task="${selected_task//[^A-Za-z0-9_.-]/-}"
  default_campaign_id+="_task_$safe_task"
fi
campaign_id="${BENCH_CAMPAIGN_ID:-$default_campaign_id}"
campaign_dir="${BENCH_CAMPAIGN_DIR:-$HOME/.hermes/reports/campaigns/$campaign_id}"
mkdir -p "$campaign_dir" "$campaign_dir/logs" "$campaign_dir/markers"

selection_file="$campaign_dir/task-selection.txt"
if [[ ! -f "$selection_file" ]] && compgen -G "$campaign_dir/markers/*.done" >/dev/null; then
  printf '%s\n' "all-core" >"$selection_file"
fi
if [[ -f "$selection_file" ]] && [[ "$(<"$selection_file")" != "$task_selection" ]]; then
  echo "Campaign directory is frozen for task selection $(<"$selection_file"); requested $task_selection" >&2
  exit 2
fi
[[ -f "$selection_file" ]] || printf '%s\n' "$task_selection" >"$selection_file"

exec >>"$campaign_dir/campaign.log" 2>&1
echo "[$(date '+%Y-%m-%dT%H:%M:%S%z')] campaign start/resume host=$(hostname) repo=$repo_dir"

if [[ ! -f "$campaign_dir/models.tsv" ]]; then
  "$python_bin" - "$campaign_dir/models.tsv" <<'PY'
import json, sys, urllib.request
out = sys.argv[1]
with urllib.request.urlopen("http://127.0.0.1:11434/api/tags", timeout=30) as response:
    tags = json.load(response).get("models") or []
groups = {}
for index, item in enumerate(tags):
    name = item.get("name") or item.get("model")
    if not name or name.startswith("x/flux"):
        continue
    digest = str(item.get("digest") or f"missing-{index}")
    groups.setdefault(digest, []).append(name)
rows = []
for digest, names in groups.items():
    canonical = sorted(set(names), key=lambda name: (name.lower().startswith("hf.co/"), len(name), name.lower()))[0]
    rows.append((canonical, digest, ",".join(sorted(set(names)))))
rows.sort(key=lambda row: row[0].lower())
with open(out, "x", encoding="utf-8") as handle:
    for row in rows:
        handle.write("\t".join(row) + "\n")
print(f"frozen {len(rows)} unique checkpoints")
PY
fi

uid="$(id -u)"
os_name="$(uname -s)"
state_file="$campaign_dir/service-state.env"
if [[ ! -f "$state_file" ]]; then
  if [[ "$os_name" == "Darwin" ]]; then
    hermes_active=0; openclaw_active=0
    launchctl print "gui/$uid/ai.hermes.gateway" >/dev/null 2>&1 && hermes_active=1
    launchctl print "gui/$uid/ai.openclaw.gateway" >/dev/null 2>&1 && openclaw_active=1
    printf 'HERMES_WAS_ACTIVE=%s\nOPENCLAW_WAS_ACTIVE=%s\nCOMFYUI_WAS_ACTIVE=0\n' "$hermes_active" "$openclaw_active" >"$state_file"
  else
    hermes_active=0; openclaw_active=0; comfy_active=0
    systemctl --user is-active --quiet hermes-gateway.service && hermes_active=1
    systemctl --user is-active --quiet openclaw-gateway.service && openclaw_active=1
    systemctl --user is-active --quiet comfyui.service && comfy_active=1
    printf 'HERMES_WAS_ACTIVE=%s\nOPENCLAW_WAS_ACTIVE=%s\nCOMFYUI_WAS_ACTIVE=%s\n' "$hermes_active" "$openclaw_active" "$comfy_active" >"$state_file"
  fi
fi
# shellcheck disable=SC1090
source "$state_file"

pause_gateways() {
  if [[ "$os_name" == "Darwin" ]]; then
    launchctl bootout "gui/$uid/ai.hermes.gateway" >/dev/null 2>&1 || true
    launchctl bootout "gui/$uid/ai.openclaw.gateway" >/dev/null 2>&1 || true
  else
    systemctl --user stop hermes-gateway.service openclaw-gateway.service >/dev/null 2>&1 || true
    systemctl --user stop comfyui.service >/dev/null 2>&1 || true
  fi
}

unload_all_ollama_models() {
  local residents
  for _attempt in 1 2 3 4 5; do
    residents="$("$python_bin" - <<'PY'
import json, urllib.request
try:
    with urllib.request.urlopen("http://127.0.0.1:11434/api/ps", timeout=15) as response:
        data = json.load(response)
    print("\n".join(str(item.get("name") or item.get("model") or "") for item in data.get("models") or [] if item.get("name") or item.get("model")))
except Exception:
    raise SystemExit(2)
PY
)" || return 1
    [[ -z "$residents" ]] && return 0
    while IFS= read -r resident; do
      [[ -n "$resident" ]] && ollama stop "$resident" >/dev/null 2>&1 || true
    done <<<"$residents"
    sleep 2
  done
  echo "Unable to verify an empty Ollama residency set" >&2
  return 1
}

start_openclaw() {
  if [[ "$os_name" == "Darwin" ]]; then
    launchctl bootstrap "gui/$uid" "$HOME/Library/LaunchAgents/ai.openclaw.gateway.plist" >/dev/null 2>&1 || true
    launchctl kickstart -k "gui/$uid/ai.openclaw.gateway"
  else
    systemctl --user start openclaw-gateway.service
  fi
}

restore_gateways() {
  if [[ "$os_name" == "Darwin" ]]; then
    if [[ "$HERMES_WAS_ACTIVE" == 1 ]]; then
      launchctl bootstrap "gui/$uid" "$HOME/Library/LaunchAgents/ai.hermes.gateway.plist" >/dev/null 2>&1 || true
      launchctl kickstart -k "gui/$uid/ai.hermes.gateway" || true
    fi
    if [[ "$OPENCLAW_WAS_ACTIVE" == 1 ]]; then
      launchctl bootstrap "gui/$uid" "$HOME/Library/LaunchAgents/ai.openclaw.gateway.plist" >/dev/null 2>&1 || true
      launchctl kickstart -k "gui/$uid/ai.openclaw.gateway" || true
    fi
  else
    [[ "$HERMES_WAS_ACTIVE" == 1 ]] && systemctl --user start hermes-gateway.service || true
    [[ "$OPENCLAW_WAS_ACTIVE" == 1 ]] && systemctl --user start openclaw-gateway.service || true
    [[ "$COMFYUI_WAS_ACTIVE" == 1 ]] && systemctl --user start comfyui.service || true
  fi
}

cleanup() {
  restore_gateways
}
trap cleanup EXIT
pause_gateways
unload_all_ollama_models || exit 1

run_model() {
  local phase="$1" model="$2" digest="$3"; shift 3
  local command=("$@")
  if [[ -n "$selected_task" ]]; then
    command+=(--test "$selected_task")
  fi
  local key="${phase}-${digest:0:16}"
  local done_marker="$campaign_dir/markers/$key.done"
  local fail_marker="$campaign_dir/markers/$key.failed"
  local terminal_marker="$campaign_dir/markers/$key.terminal"
  [[ -f "$done_marker" || -f "$terminal_marker" ]] && return 0
  local prior_failure=0
  [[ -f "$fail_marker" ]] && prior_failure=1
  echo "[$(date '+%Y-%m-%dT%H:%M:%S%z')] START phase=$phase model=$model digest=$digest"
  if "${command[@]}" >"$campaign_dir/logs/$key.log" 2>&1; then
    rm -f "$fail_marker"
    printf '%s\t%s\t%s\n' "$(date '+%Y-%m-%dT%H:%M:%S%z')" "$model" "$digest" >"$done_marker"
    echo "[$(date '+%Y-%m-%dT%H:%M:%S%z')] DONE phase=$phase model=$model"
  else
    status=$?
    if ((prior_failure)); then
      printf '%s\t%s\t%s\t%s\t%s\n' "$(date '+%Y-%m-%dT%H:%M:%S%z')" "$model" "$digest" "$status" "bounded-recovery-exhausted" >"$terminal_marker"
      echo "[$(date '+%Y-%m-%dT%H:%M:%S%z')] TERMINAL phase=$phase model=$model status=$status reason=bounded-recovery-exhausted; continuing"
    else
      printf '%s\t%s\t%s\t%s\n' "$(date '+%Y-%m-%dT%H:%M:%S%z')" "$model" "$digest" "$status" >"$fail_marker"
      echo "[$(date '+%Y-%m-%dT%H:%M:%S%z')] FAILED phase=$phase model=$model status=$status; continuing"
    fi
  fi
  ollama stop "$model" >/dev/null 2>&1 || true
}

ollama_native_context_for_model() {
  "$python_bin" - "$1" <<'PY'
import json, sys, urllib.request
request = urllib.request.Request(
    "http://127.0.0.1:11434/api/show",
    data=json.dumps({"model": sys.argv[1]}).encode(),
    headers={"Content-Type": "application/json"},
)
with urllib.request.urlopen(request, timeout=30) as response:
    info = json.load(response).get("model_info") or {}
values = [int(value) for key, value in info.items() if str(key).endswith(".context_length")]
print(max(values) if values else 0)
PY
}

terminally_account_hermes_context_incompatible() {
  local model="$1" digest="$2" context="$3"
  local key="hermes-${digest:0:16}"
  local marker="$campaign_dir/markers/$key.terminal"
  [[ -f "$campaign_dir/markers/$key.done" || -f "$marker" ]] && return 0
  printf '%s\t%s\t%s\t%s\t%s\n' \
    "$(date '+%Y-%m-%dT%H:%M:%S%z')" "$model" "$digest" "0" \
    "hermes-minimum-context-64000:model-advertises-$context" >"$marker"
  echo "[$(date '+%Y-%m-%dT%H:%M:%S%z')] TERMINAL phase=hermes model=$model reason=Hermes-requires-64000-context advertised=$context; continuing"
}

direct_context_args_for_model() {
  local target_model="$1" entry override_model override_ctx
  DIRECT_NUM_CTX=""
  for entry in ${BENCH_DIRECT_NUM_CTX_OVERRIDES:-}; do
    override_model="${entry%%=*}"
    override_ctx="${entry#*=}"
    if [[ "$override_model" == "$target_model" && "$override_ctx" =~ ^[1-9][0-9]*$ ]]; then
      DIRECT_NUM_CTX="$override_ctx"
      echo "[$(date '+%Y-%m-%dT%H:%M:%S%z')] ADJUSTMENT phase=direct model=$target_model num_ctx=$override_ctx reason=model-specific-memory-fit"
      return 0
    fi
  done
}

while IFS=$'\t' read -r model digest _aliases; do
  direct_context_args_for_model "$model"
  if [[ -n "$DIRECT_NUM_CTX" ]]; then
    run_model direct "$model" "$digest" \
      "$python_bin" "$repo_dir/scripts/ollama_standardized_local_benchmarks.py" \
        --models "$model" --thinking auto --timeout 1800 --run \
        --num-ctx "$DIRECT_NUM_CTX" \
        --output-dir "$campaign_dir/direct/$digest"
  else
    run_model direct "$model" "$digest" \
      "$python_bin" "$repo_dir/scripts/ollama_standardized_local_benchmarks.py" \
        --models "$model" --thinking auto --timeout 1800 --run \
        --output-dir "$campaign_dir/direct/$digest"
  fi
done <"$campaign_dir/models.tsv"

while IFS=$'\t' read -r model digest _aliases; do
  hermes_native_context="$(ollama_native_context_for_model "$model")" || exit 1
  if [[ "$hermes_native_context" =~ ^[0-9]+$ ]] && ((hermes_native_context > 0 && hermes_native_context < 64000)); then
    terminally_account_hermes_context_incompatible "$model" "$digest" "$hermes_native_context"
    continue
  fi
  run_model hermes "$model" "$digest" \
    "$python_bin" "$repo_dir/scripts/hermes_agent_17_test_benchmarks.py" \
      --models "$model" --timeout 1800 --run \
      --output-dir "$campaign_dir/hermes/$digest"
done <"$campaign_dir/models.tsv"

if [[ -n "${BENCH_HERMES_CLOUD_MODELS:-}" ]]; then
  for model in $BENCH_HERMES_CLOUD_MODELS; do
    digest="$(printf '%s' "hermes-cloud:$model" | shasum -a 256 | awk '{print $1}')"
    run_model hermes-cloud "$model" "$digest" \
      "$python_bin" "$repo_dir/scripts/hermes_agent_17_test_benchmarks.py" \
        --external-models "$model" --provider openai-codex --timeout 1800 --run \
        --output-dir "$campaign_dir/hermes-cloud/$digest"
  done
fi

start_openclaw
sleep 5
if [[ "$os_name" == "Darwin" ]]; then
  gateway_restart_override=""
else
  gateway_restart_override="systemctl --user restart openclaw-gateway.service"
fi
while IFS=$'\t' read -r model digest _aliases; do
  run_model openclaw "$model" "$digest" \
    "$python_bin" "$repo_dir/scripts/openclaw_18_test_benchmarks.py" \
      --models "$model" --thinking auto --timeout 1800 --run \
      --output-dir "$campaign_dir/openclaw/$digest" --gateway-restart-command "$gateway_restart_override"
done <"$campaign_dir/models.tsv"

if [[ -n "${BENCH_OPENCLAW_CLOUD_MODELS:-}" ]]; then
  for model in $BENCH_OPENCLAW_CLOUD_MODELS; do
    digest="$(printf '%s' "openclaw-cloud:$model" | shasum -a 256 | awk '{print $1}')"
    run_model openclaw-cloud "$model" "$digest" \
      "$python_bin" "$repo_dir/scripts/openclaw_18_test_benchmarks.py" \
        --external-models "openai/$model" --thinking auto --timeout 1800 --run \
        --output-dir "$campaign_dir/openclaw-cloud/$digest" --gateway-restart-command "$gateway_restart_override"
  done
fi

touch "$campaign_dir/campaign.complete"
echo "[$(date '+%Y-%m-%dT%H:%M:%S%z')] campaign complete"
