#!/usr/bin/env python3
"""Run human-reviewed creative briefs through tool-capable agent harnesses."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import time
from pathlib import Path

from benchmark_tests import suite_task_catalog
from cli_agent_benchmarks import (
    command_output, load_models, maximum, ollama_json, pi_configuration,
    run_guarded, stop_model,
)
from coding_agent_benchmarks import (
    fingerprint_tree, harness_command, prepare_workspace,
    hermes_configuration, provider_configuration, run_guarded_server,
)
from ollama_standardized_local_benchmarks import read_linux_resource_snapshot
from platform_support import create_sampler


PROFILE = "creative-human-v1"
FIELDS = [
    "run_id", "benchmark_suite", "benchmark_profile", "evaluation_method",
    "review_status", "harness", "harness_version", "model_runner",
    "model_runner_version", "model", "model_digest", "task_id", "task_name",
    "category", "creative_medium", "time_class", "status", "wall_seconds",
    "exit_code", "timed_out", "workspace", "preview_entry", "artifact_count",
    "changed_artifact_count", "artifact_extensions", "response_chars",
    "response_sha256", "max_gpu_temp_c", "max_host_temp_c",
    "max_host_memory_used_bytes", "max_host_memory_pct", "max_gpu_usage_pct",
    "sample_count", "error",
]


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--suite", choices=("creative",), default="creative")
    parser.add_argument(
        "--harness",
        choices=("ollama-direct", "hermes", "openclaw", "pi", "goose", "openhands"),
        required=True,
    )
    parser.add_argument("--model-runner", choices=("ollama", "llama.cpp", "vllm", "tensorrt-llm"), default="ollama")
    parser.add_argument("--base-url", default="http://127.0.0.1:11434/v1")
    parser.add_argument("--api-key", default="ollama")
    parser.add_argument("--runner-version", default="")
    parser.add_argument("--server-pid", type=int)
    parser.add_argument("--stop-command", nargs="+")
    parser.add_argument("--models-file", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--timeout", type=int, default=7200)
    parser.add_argument("--openhands-python", default=str(Path.home() / ".local/venvs/openhands-1.11.0/bin/python"))
    parser.add_argument("--tasks", "--test", dest="tasks", nargs="*")
    parser.add_argument("--list-tasks", action="store_true")
    parser.add_argument("--run", action="store_true")
    return parser.parse_args(argv)


def artifact_inventory(workspace: Path) -> list[str]:
    ignored = {".git", "node_modules", ".next", "__pycache__"}
    return sorted(
        str(path.relative_to(workspace))
        for path in workspace.rglob("*")
        if path.is_file() and not any(part in ignored for part in path.relative_to(workspace).parts)
    )


def write_csv(path: Path, records: list[dict]) -> None:
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=FIELDS); writer.writeheader()
        for record in records:
            writer.writerow({field: record["row"].get(field, "") for field in FIELDS})


def validate_existing_records(records: list[dict]) -> None:
    profiles = {
        record.get("row", {}).get("benchmark_profile", "unversioned")
        for record in records
        if record.get("row", {}).get("benchmark_profile") != PROFILE
    }
    if profiles:
        raise RuntimeError(
            "Existing evidence uses a different creative profile "
            f"({', '.join(sorted(profiles))}); use a new output directory for {PROFILE}"
        )


def main(argv=None) -> int:
    args = parse_args(argv)
    if args.harness in {"ollama-direct", "hermes", "openclaw"} and args.model_runner != "ollama":
        raise SystemExit(f"--harness {args.harness} currently requires --model-runner ollama")
    tasks = suite_task_catalog("creative")
    if args.tasks:
        wanted = set(args.tasks); tasks = [task for task in tasks if task["id"] in wanted]
        missing = wanted - {task["id"] for task in tasks}
        if missing:
            raise SystemExit("Unknown creative task(s): " + ", ".join(sorted(missing)))
    if args.list_tasks:
        for task in tasks:
            print(f"{task['id']}\t{task['creative_medium']}\t{task['name']}")
        return 0
    if not args.run:
        print(f"Suite: creative ({PROFILE})")
        print(f"Harness: {args.harness}; tasks: {len(tasks)}; timeout: {args.timeout}s")
        print("Human review only; automated quality scoring is disabled.")
        print("Plan only. Pass --run to create benchmark artifacts.")
        return 0
    if not 1 <= args.timeout <= 14_400:
        raise SystemExit("--timeout must be between 1 and 14400 seconds")
    models = load_models(args.models_file)
    if args.model_runner == "ollama":
        installed = {item["name"]: item for item in ollama_json("/api/tags").get("models", [])}
        for model in models:
            if model["name"] not in installed or installed[model["name"]].get("digest") != model["digest"]:
                raise RuntimeError(f"Frozen model provenance mismatch: {model['name']}")
    elif not args.server_pid or not Path(f"/proc/{args.server_pid}").exists():
        raise RuntimeError("A live --server-pid is required for non-Ollama model runners")
    args.output_dir.mkdir(parents=True, exist_ok=True); args.workspace.mkdir(parents=True, exist_ok=True)
    config_dir = args.output_dir / "config"
    if args.harness == "pi":
        if args.model_runner == "ollama": pi_configuration(config_dir, models)
        else: provider_configuration(config_dir, models, args.base_url, args.api_key)
    if args.harness == "hermes":
        hermes_configuration(config_dir, models[0]["name"], args.base_url)
    if args.harness == "openhands":
        harness_version = command_output([args.openhands_python, "-c", "import importlib.metadata as m; print(m.version('openhands-ai'))"])
    elif args.harness == "ollama-direct":
        harness_version = "ollama-workspace-agent-v1"
    else:
        harness_version = command_output([args.harness, "--version"])
    runner_version = args.runner_version or (command_output(["ollama", "--version"]) if args.model_runner == "ollama" else "unreported")
    jsonl_path = args.output_dir / f"{args.harness}_creative.jsonl"; csv_path = args.output_dir / f"{args.harness}_creative.csv"
    records = [json.loads(line) for line in jsonl_path.read_text(encoding="utf-8").splitlines()] if jsonl_path.exists() else []
    validate_existing_records(records)
    completed = {(record["row"]["model"], record["row"]["task_id"]) for record in records}
    if len(completed) != len(records):
        raise RuntimeError("Existing creative evidence contains duplicate model/task keys")
    baseline = read_linux_resource_snapshot(); sampler = create_sampler("auto", interval_ms=1000); sampler.start()
    run_id = records[0]["row"]["run_id"] if records else time.strftime("%Y%m%d_%H%M%S")
    try:
        total = len(models) * len(tasks)
        for model in models:
            for task in tasks:
                key = (model["name"], task["id"])
                if key in completed: continue
                print(f"[{len(completed)+1}/{total}] {args.harness} {model['name']} :: {task['id']}", flush=True)
                if args.model_runner == "ollama": stop_model(model["name"])
                workspace = prepare_workspace(args.workspace, args.harness, model["name"], task)
                before = fingerprint_tree(workspace)
                review = "\n".join(f"- {item}" for item in task["review_dimensions"])
                deliverables = "\n".join(f"- {item}" for item in task["deliverables"])
                prompt = (
                    task["prompt"] + "\n\nRequired deliverables:\n" + deliverables
                    + "\n\nHuman review dimensions:\n" + review
                    + "\n\nWork only inside: " + str(workspace)
                )
                provider = "ollama" if args.model_runner == "ollama" else "openai"
                command = harness_command(
                    args.harness, model["name"], prompt, workspace,
                    args.openhands_python,
                    "ollama" if args.model_runner == "ollama" else "benchmark",
                    args.base_url, args.api_key, args.timeout,
                )
                env = {**os.environ, "PI_CODING_AGENT_DIR": str(config_dir), "PI_TELEMETRY": "0", "GOOSE_PROVIDER": provider, "GOOSE_MODEL": model["name"], "GOOSE_TELEMETRY_ENABLED": "false", "GOOSE_PROVIDER__HOST": args.base_url, "GOOSE_PROVIDER__API_KEY": args.api_key, "OPENAI_HOST": args.base_url, "OPENAI_API_KEY": args.api_key, "XDG_CONFIG_HOME": str(config_dir / "xdg-config"), "XDG_DATA_HOME": str(config_dir / "xdg-data")}
                if args.harness == "hermes":
                    env["HERMES_HOME"] = str(config_dir / "hermes-home")
                sample_start = sampler.snapshot_len(); started = time.monotonic()
                if args.model_runner == "ollama":
                    result = run_guarded(command, env, workspace, model["name"], args.timeout, baseline)
                else:
                    result = run_guarded_server(command, env, workspace, args.timeout, baseline, args.server_pid, args.stop_command)
                exit_code, stdout, stderr, timed_out, pressure_error = result
                timed_out = timed_out or exit_code == 124
                wall = round(time.monotonic() - started, 3)
                if args.model_runner == "ollama": stop_model(model["name"])
                samples = sampler.get_since(sample_start); after = fingerprint_tree(workspace)
                changed = sorted(name for name in set(before) | set(after) if before.get(name) != after.get(name))
                artifacts = artifact_inventory(workspace)
                extensions = sorted({Path(name).suffix.lower() or "[none]" for name in artifacts})
                status = "timeout" if timed_out else ("error" if pressure_error or exit_code else "submitted")
                row = {"run_id": run_id, "benchmark_suite": "creative", "benchmark_profile": PROFILE, "evaluation_method": "human_only", "review_status": "awaiting_human_review", "harness": f"{args.harness}-creative-agent", "harness_version": harness_version, "model_runner": args.model_runner, "model_runner_version": runner_version, "model": model["name"], "model_digest": model["digest"], "task_id": task["id"], "task_name": task["name"], "category": task["category"], "creative_medium": task["creative_medium"], "time_class": task["time_class"], "status": status, "wall_seconds": wall, "exit_code": 124 if timed_out else exit_code, "timed_out": str(timed_out).lower(), "workspace": str(workspace.resolve()), "preview_entry": task["preview_entry"], "artifact_count": len(artifacts), "changed_artifact_count": len(changed), "artifact_extensions": " ".join(extensions), "response_chars": len(stdout), "response_sha256": hashlib.sha256(stdout.encode()).hexdigest(), "max_gpu_temp_c": maximum(samples, "gpu_temp_c"), "max_host_temp_c": maximum(samples, "host_temp_c"), "max_host_memory_used_bytes": maximum(samples, "host_memory_used_bytes"), "max_host_memory_pct": maximum(samples, "host_memory_pct"), "max_gpu_usage_pct": maximum(samples, "gpu_usage_pct"), "sample_count": len(samples), "error": (pressure_error or stderr)[-2000:]}
                record = {"row": row, "assistant_text": stdout, "stderr": stderr, "artifacts": artifacts, "changed_artifacts": changed, "review_dimensions": task["review_dimensions"], "deliverables": task["deliverables"], "telemetry_samples": samples, "command": command}
                with jsonl_path.open("a", encoding="utf-8") as stream: stream.write(json.dumps(record, ensure_ascii=False) + "\n")
                records.append(record); completed.add(key); write_csv(csv_path, records)
                if pressure_error: raise RuntimeError("Resource safety guard: " + pressure_error)
    finally:
        sampler.stop()
        if args.model_runner == "ollama":
            for model in models: stop_model(model["name"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
