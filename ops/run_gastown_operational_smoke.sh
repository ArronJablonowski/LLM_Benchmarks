#!/usr/bin/env bash
set -euo pipefail

campaign_dir="${BENCH_CAMPAIGN_DIR:?BENCH_CAMPAIGN_DIR is required}"
town_dir="$campaign_dir/hq"
source_dir="$campaign_dir/fixture-source"
remote_dir="$campaign_dir/fixture.git"
timeout_seconds="${BENCH_TASK_TIMEOUT:-1800}"
runtime_home="$HOME"
isolated_home="$campaign_dir/home"
export PATH="$runtime_home/.local/bin:$PATH"

(( timeout_seconds >= 1 && timeout_seconds <= 1800 )) || {
  echo "BENCH_TASK_TIMEOUT must be between 1 and 1800" >&2
  exit 1
}
[[ ! -e "$campaign_dir/campaign.done" ]] || {
  echo "Gas Town smoke evidence already exists; refusing duplicate" >&2
  exit 1
}
for command in gt bd dolt git tmux python3 timeout; do
  command -v "$command" >/dev/null || { echo "Missing prerequisite: $command" >&2; exit 1; }
done

mkdir -p "$campaign_dir" "$isolated_home"
# Gas Town uses Dolt/Beads internally.  Give the disposable fixture its own
# identity and configuration home so the smoke test never mutates the user's
# global Git or Dolt configuration on the benchmark host.
export HOME="$isolated_home"
export XDG_CONFIG_HOME="$isolated_home/.config"
export XDG_DATA_HOME="$isolated_home/.local/share"
export XDG_CACHE_HOME="$isolated_home/.cache"
mkdir -p "$XDG_CONFIG_HOME" "$XDG_DATA_HOME" "$XDG_CACHE_HOME"
git config --global user.name "Benchmark Fixture"
git config --global user.email "benchmark@example.invalid"
dolt config --global --add user.name "Benchmark Fixture"
dolt config --global --add user.email "benchmark@example.invalid"
gt version >"$campaign_dir/gastown-version.txt" 2>&1
bd version >"$campaign_dir/beads-version.txt" 2>&1
dolt version >"$campaign_dir/dolt-version.txt" 2>&1

cleanup() {
  if [[ -d "$town_dir" ]]; then
    (cd "$town_dir" && gt down) >>"$campaign_dir/cleanup.log" 2>&1 || true
  fi
}
trap cleanup EXIT

started=$(date +%s)
mkdir -p "$source_dir"
git -C "$source_dir" init -b main
git -C "$source_dir" config user.name "Benchmark Fixture"
git -C "$source_dir" config user.email "benchmark@example.invalid"
printf '# Gas Town benchmark fixture\n' >"$source_dir/README.md"
git -C "$source_dir" add README.md
git -C "$source_dir" commit -m "Initial fixture"
git clone --bare "$source_dir" "$remote_dir"

timeout "$timeout_seconds" gt install "$town_dir" --name benchmark-town --git \
  >"$campaign_dir/install.log" 2>&1
(
  cd "$town_dir"
  timeout "$timeout_seconds" gt rig add fixture "file://$remote_dir" --prefix fx
) >"$campaign_dir/rig-add.log" 2>&1

set +e
(
  cd "$town_dir"
  timeout "$timeout_seconds" gt doctor --verbose
) >"$campaign_dir/doctor.log" 2>&1
doctor_status=$?
set -e

ended=$(date +%s)
python3 - "$campaign_dir/result.json" "$started" "$ended" "$doctor_status" <<'PY'
import json
import sys
from pathlib import Path

path, started, ended, doctor_status = sys.argv[1:]
payload = {
    "benchmark_type": "orchestration_operational_smoke",
    "harness": "Gas Town",
    "model_runner": None,
    "accuracy_comparable": False,
    "install_seconds": int(ended) - int(started),
    "fixture_rig_created": True,
    "doctor_exit_code": int(doctor_status),
    "agent_preset_verified": "pi",
}
Path(path).write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
PY
touch "$campaign_dir/campaign.done"
