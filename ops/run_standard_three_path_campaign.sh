#!/usr/bin/env bash
set -u

# Resumable host-local campaign: Direct Ollama -> Hermes -> OpenClaw.
# The default 18-task core profile is intentional; --full-suite is not used.

repo_dir="${BENCH_REPO_DIR:-$HOME/gitRepo/local-llm-benchmark-suite}"
campaign_id="${BENCH_CAMPAIGN_ID:-standard_three_path_20260822}"
campaign_dir="${BENCH_CAMPAIGN_DIR:-$HOME/.hermes/reports/campaigns/$campaign_id}"
python_bin="${BENCH_PYTHON:-python3}"
mkdir -p "$campaign_dir" "$campaign_dir/logs" "$campaign_dir/markers"

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
  local key="${phase}-${digest:0:16}"
  local done_marker="$campaign_dir/markers/$key.done"
  local fail_marker="$campaign_dir/markers/$key.failed"
  [[ -f "$done_marker" ]] && return 0
  echo "[$(date '+%Y-%m-%dT%H:%M:%S%z')] START phase=$phase model=$model digest=$digest"
  if "$@" >"$campaign_dir/logs/$key.log" 2>&1; then
    rm -f "$fail_marker"
    printf '%s\t%s\t%s\n' "$(date '+%Y-%m-%dT%H:%M:%S%z')" "$model" "$digest" >"$done_marker"
    echo "[$(date '+%Y-%m-%dT%H:%M:%S%z')] DONE phase=$phase model=$model"
  else
    status=$?
    printf '%s\t%s\t%s\t%s\n' "$(date '+%Y-%m-%dT%H:%M:%S%z')" "$model" "$digest" "$status" >"$fail_marker"
    echo "[$(date '+%Y-%m-%dT%H:%M:%S%z')] FAILED phase=$phase model=$model status=$status; continuing"
  fi
  ollama stop "$model" >/dev/null 2>&1 || true
}

while IFS=$'\t' read -r model digest _aliases; do
  run_model direct "$model" "$digest" \
    "$python_bin" "$repo_dir/scripts/ollama_standardized_local_benchmarks.py" \
      --models "$model" --thinking auto --timeout 1800 --run \
      --output-dir "$campaign_dir/direct/$digest"
done <"$campaign_dir/models.tsv"

while IFS=$'\t' read -r model digest _aliases; do
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
  gateway_args=()
else
  gateway_args=(--gateway-restart-command "systemctl --user restart openclaw-gateway.service")
fi
while IFS=$'\t' read -r model digest _aliases; do
  run_model openclaw "$model" "$digest" \
    "$python_bin" "$repo_dir/scripts/openclaw_18_test_benchmarks.py" \
      --models "$model" --thinking auto --timeout 1800 --run \
      --output-dir "$campaign_dir/openclaw/$digest" "${gateway_args[@]}"
done <"$campaign_dir/models.tsv"

if [[ -n "${BENCH_OPENCLAW_CLOUD_MODELS:-}" ]]; then
  for model in $BENCH_OPENCLAW_CLOUD_MODELS; do
    digest="$(printf '%s' "openclaw-cloud:$model" | shasum -a 256 | awk '{print $1}')"
    run_model openclaw-cloud "$model" "$digest" \
      "$python_bin" "$repo_dir/scripts/openclaw_18_test_benchmarks.py" \
        --external-models "openai/$model" --thinking auto --timeout 1800 --run \
        --output-dir "$campaign_dir/openclaw-cloud/$digest" "${gateway_args[@]}"
  done
fi

touch "$campaign_dir/campaign.complete"
echo "[$(date '+%Y-%m-%dT%H:%M:%S%z')] campaign complete"
