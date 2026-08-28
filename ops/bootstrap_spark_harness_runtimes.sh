#!/usr/bin/env bash
set -u -o pipefail

evidence_dir="${BENCH_BOOTSTRAP_DIR:?BENCH_BOOTSTRAP_DIR is required}"
mkdir -p "$evidence_dir" "$HOME/.local/venvs" "$HOME/.local/bin" "$HOME/.local/opt"
export PATH="$HOME/.local/bin:$PATH"

# Package and model downloads do not perform inference and intentionally leave
# pre-active user services untouched. GPU exclusivity is enforced later by each
# benchmark campaign immediately before its server/model is started.

run_step() {
  local name=$1
  shift
  if [[ -f "$evidence_dir/$name.done" ]]; then
    echo "already complete: $name"
    return 0
  fi
  rm -f "$evidence_dir/$name.failed"
  echo "starting: $name"
  if "$@" >"$evidence_dir/$name.log" 2>&1; then
    date -u +%Y-%m-%dT%H:%M:%SZ >"$evidence_dir/$name.done"
    echo "complete: $name"
  else
    status=$?
    printf 'exit=%s\ncompleted=%s\n' "$status" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" >"$evidence_dir/$name.failed"
    echo "failed: $name (exit $status); continuing" >&2
  fi
}

install_python_runtime() {
  local target=$1 package=$2
  python3 -m venv "$target"
  "$target/bin/python" -m pip install --upgrade pip
  "$target/bin/python" -m pip install --no-cache-dir "$package"
}

install_beads() {
  local version=1.2.2 root="$HOME/.local/opt/beads/v1.2.2"
  local archive="$evidence_dir/beads_1.2.2_linux_arm64.tar.gz"
  mkdir -p "$root"
  [[ -f "$archive" ]] || curl -fL --retry 5 -o "$archive" \
    https://github.com/gastownhall/beads/releases/download/v1.2.2/beads_1.2.2_linux_arm64.tar.gz
  printf '%s  %s\n' 501f38a1070d4b9b3b6261a86a3c92c4a52366869021560430a4bb0036afd83a "$archive" | sha256sum -c -
  tar -xzf "$archive" -C "$root"
  binary=$(find "$root" -type f -name bd -perm -111 | head -1)
  [[ -n "$binary" ]]
  ln -sfn "$binary" "$HOME/.local/bin/bd"
  bd version
}

install_dolt() {
  local root="$HOME/.local/opt/dolt/v2.3.1"
  local archive="$evidence_dir/dolt-linux-arm64.tar.gz"
  mkdir -p "$root"
  [[ -f "$archive" ]] || curl -fL --retry 5 -o "$archive" \
    https://github.com/dolthub/dolt/releases/download/v2.3.1/dolt-linux-arm64.tar.gz
  printf '%s  %s\n' 33ce669f922a3424f271a9905b815ea442133a0504eea1f43b07cb5b1fef589e "$archive" | sha256sum -c -
  tar -xzf "$archive" -C "$root"
  binary=$(find "$root" -type f -name dolt -perm -111 | head -1)
  [[ -n "$binary" ]]
  ln -sfn "$binary" "$HOME/.local/bin/dolt"
  dolt version
}

download_common_model() {
  local hf="$HOME/.local/venvs/vllm-0.28.0/bin/hf"
  [[ -x "$hf" ]]
  "$hf" download Qwen/Qwen3-8B \
    --revision b968826d9c46dd6066d109eabc6255188de91218 \
    --local-dir "$HOME/models/Qwen3-8B-hf"
  "$hf" download Qwen/Qwen3-8B-GGUF Qwen3-8B-Q4_K_M.gguf \
    --revision 7c41481f57cb95916b40956ab2f0b139b296d974 \
    --local-dir "$HOME/models/Qwen3-8B-GGUF"
  ollama pull qwen3:8b
  curl -fsS http://127.0.0.1:11434/api/tags >"$evidence_dir/ollama-tags-after-qwen3-8b.json"
  ollama stop qwen3:8b >/dev/null 2>&1 || true
}

run_step vllm install_python_runtime "$HOME/.local/venvs/vllm-0.28.0" "vllm==0.28.0"
run_step tensorrt-llm install_python_runtime "$HOME/.local/venvs/tensorrt-llm-1.2.1" "tensorrt_llm==1.2.1"
run_step openhands install_python_runtime "$HOME/.local/venvs/openhands-1.11.0" "openhands-ai==1.11.0"
run_step beads install_beads
run_step dolt install_dolt
run_step common-model download_common_model

{
  "$HOME/.local/venvs/vllm-0.28.0/bin/python" -c 'import vllm; print(vllm.__version__)' 2>&1 || true
  "$HOME/.local/venvs/tensorrt-llm-1.2.1/bin/python" -c 'import tensorrt_llm; print(tensorrt_llm.__version__)' 2>&1 || true
  "$HOME/.local/venvs/openhands-1.11.0/bin/python" -c 'import openhands; print(getattr(openhands, "__version__", "import-ok"))' 2>&1 || true
  bd version 2>&1 || true
  dolt version 2>&1 || true
  gt version 2>&1 || true
} >"$evidence_dir/versions.txt"
