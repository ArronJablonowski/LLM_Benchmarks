#!/usr/bin/env python3
"""Run the text benchmark suite through a supported command-line agent harness.

The runner intentionally disables tools, sessions, extensions, and repository
context.  That keeps the treatment comparable to the other prompt/response
harness paths while preserving exact model provenance and resource evidence.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import signal
import subprocess
import time
import urllib.request
from pathlib import Path

from accuracy_grading import GRADING_PROFILE, grade_task
from benchmark_tests import DEFAULT_SUITE, SUITE_CHOICES, suite_task_catalog
from ollama_standardized_local_benchmarks import (
    CONTEXT_SWAP_GROWTH_LIMIT_BYTES,
    ContextResourceWatchdog,
    discover_ollama_daemon_identity,
    read_linux_resource_snapshot,
    verify_no_external_gpu_compute,
)
from platform_support import create_sampler


FIELDS = [
    "run_id", "harness", "harness_version", "model_runner",
    "benchmark_profile", "grading_profile", "model", "model_digest",
    "task_id", "task_name", "category", "status", "verdict",
    "grader_type", "grader_tests_passed", "grader_tests_total", "grader_error",
    "wall_seconds", "exit_code", "timed_out", "response_chars",
    "response_sha256", "max_gpu_temp_c", "max_host_temp_c",
    "max_host_memory_used_bytes", "max_host_memory_pct", "max_gpu_usage_pct",
    "sample_count", "response_preview", "error",
]


def parse_args(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--suite", choices=SUITE_CHOICES, default=DEFAULT_SUITE)
    parser.add_argument("--harness", choices=("pi", "goose", "openhands"), required=True)
    parser.add_argument("--models-file", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--timeout", type=int, default=1800)
    parser.add_argument(
        "--openhands-python",
        default=str(Path.home() / ".local/venvs/openhands-1.11.0/bin/python"),
    )
    parser.add_argument("--tasks", nargs="*")
    parser.add_argument("--run", action="store_true")
    return parser.parse_args(argv)


def command_output(command):
    proc = subprocess.run(command, text=True, capture_output=True, timeout=30, check=False)
    if proc.returncode:
        raise RuntimeError((proc.stderr or proc.stdout).strip())
    return proc.stdout.strip()


def ollama_json(path):
    with urllib.request.urlopen("http://127.0.0.1:11434" + path, timeout=15) as response:
        return json.load(response)


def load_models(path):
    rows = []
    for number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not raw.strip() or raw.startswith("#"):
            continue
        parts = raw.split("\t")
        if len(parts) < 2 or not parts[0] or not parts[1]:
            raise RuntimeError(f"Malformed frozen model row {number}")
        rows.append({"name": parts[0], "digest": parts[1]})
    if not rows or len({row["name"] for row in rows}) != len(rows):
        raise RuntimeError("Frozen model list is empty or contains duplicate tags")
    return rows


def stop_model(model):
    deadline = time.monotonic() + 120
    while time.monotonic() < deadline:
        subprocess.run(["ollama", "stop", model], capture_output=True, timeout=30, check=False)
        residents = ollama_json("/api/ps").get("models", [])
        if not residents:
            return True
        names = [str(item.get("name") or item.get("model") or "") for item in residents]
        if any(name != model for name in names):
            raise RuntimeError(f"Unexpected resident model(s): {names}")
        time.sleep(1)
    raise RuntimeError(f"Ollama remained resident after stopping {model}")


def terminate_group(proc):
    if proc.poll() is not None:
        return
    try:
        os.killpg(proc.pid, signal.SIGTERM)
        proc.wait(timeout=10)
    except Exception:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        proc.wait(timeout=10)


def maximum(samples, field):
    values = [sample.get(field) for sample in samples if sample.get(field) is not None]
    return max(values) if values else ""


def write_csv(path, records):
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=FIELDS)
        writer.writeheader()
        for record in records:
            writer.writerow({field: record["row"].get(field, "") for field in FIELDS})


def pi_configuration(config_dir, models):
    config_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "providers": {
            "ollama": {
                "baseUrl": "http://127.0.0.1:11434/v1",
                "api": "openai-completions",
                "apiKey": "ollama",
                "compat": {
                    "supportsDeveloperRole": False,
                    "supportsReasoningEffort": False,
                },
                "models": [{"id": row["name"]} for row in models],
            }
        }
    }
    (config_dir / "models.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def harness_command(harness, model, prompt, config_dir, openhands_python=None, workspace=None):
    if harness == "pi":
        return [
            "pi", "--provider", "ollama", "--model", model, "--api-key", "ollama",
            "--no-session", "--no-tools", "--no-extensions", "--no-skills",
            "--no-context-files", "--offline", "--mode", "text", "--print", prompt,
        ]
    if harness == "goose":
        return [
        "goose", "run", "--provider", "ollama", "--model", model,
        "--no-session", "--no-profile", "--max-turns", "1", "--quiet",
        "--output-format", "text", "--text", prompt,
        ]
    if not openhands_python or not workspace:
        raise RuntimeError("OpenHands requires its Python runtime and workspace")
    return [
        openhands_python,
        str(Path(__file__).with_name("openhands_prompt.py")),
        "--model", model,
        "--workspace", str(workspace),
        "--prompt", prompt,
    ]


def run_guarded(command, env, workspace, model, timeout, baseline):
    verify_no_external_gpu_compute()
    current = read_linux_resource_snapshot()
    if int(current["oom_kill"]) != int(baseline["oom_kill"]):
        raise RuntimeError("Kernel OOM counter changed during campaign")
    swap_growth = int(current["swap_used_bytes"]) - int(baseline["swap_used_bytes"])
    if swap_growth > CONTEXT_SWAP_GROWTH_LIMIT_BYTES:
        raise RuntimeError("Campaign-relative swap growth exceeded 1 GiB before task")
    daemon_identity = discover_ollama_daemon_identity()
    if daemon_identity is None:
        raise RuntimeError("Unable to freeze Ollama daemon identity")
    guard = ContextResourceWatchdog(
        model, current,
        swap_reference_used_bytes=int(baseline["swap_used_bytes"]),
        pswpout_reference=int(baseline["pswpout"]),
        daemon_identity=daemon_identity,
        stop_fn=lambda name, _url: stop_model(name),
    ).start()
    proc = subprocess.Popen(
        command, cwd=workspace, env=env, text=True, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, start_new_session=True,
    )
    started = time.monotonic()
    timed_out = False
    stdout = stderr = ""
    try:
        while True:
            try:
                stdout, stderr = proc.communicate(timeout=1)
                break
            except subprocess.TimeoutExpired:
                if guard.triggered or guard.infrastructure_error:
                    terminate_group(proc)
                    stdout, stderr = proc.communicate()
                    break
                if time.monotonic() - started >= timeout:
                    timed_out = True
                    terminate_group(proc)
                    stdout, stderr = proc.communicate()
                    break
    finally:
        guard.stop_and_join()
    pressure_error = guard.resource_pressure_reason or guard.infrastructure_error
    if not guard.join_verified:
        pressure_error = pressure_error or "Resource watchdog did not join"
    return proc.returncode, stdout or "", stderr or "", timed_out, pressure_error


def main(argv=None):
    args = parse_args(argv)
    if args.suite == "coding":
        from coding_agent_benchmarks import main as coding_main
        return coding_main(argv)
    if args.suite == "creative":
        from creative_agent_benchmarks import main as creative_main
        return creative_main(argv)
    if args.suite == "cybersecurity":
        from cybersecurity_agent_benchmarks import main as cybersecurity_main
        return cybersecurity_main(argv)
    if not args.run:
        raise SystemExit("Plan only. Pass --run to execute benchmark observations.")
    if args.timeout < 1 or args.timeout > 1800:
        raise SystemExit("--timeout must be between 1 and 1800 seconds")
    args.output_dir.mkdir(parents=True, exist_ok=True)
    args.workspace.mkdir(parents=True, exist_ok=True)
    models = load_models(args.models_file)
    installed = {item["name"]: item for item in ollama_json("/api/tags").get("models", [])}
    for model in models:
        if model["name"] not in installed or installed[model["name"]].get("digest") != model["digest"]:
            raise RuntimeError(f"Frozen model provenance mismatch: {model['name']}")
    tasks = [task for task in suite_task_catalog(args.suite) if not task.get("requires_image")]
    if args.tasks:
        selected = set(args.tasks)
        tasks = [task for task in tasks if task["id"] in selected]
        missing = selected - {task["id"] for task in tasks}
        if missing:
            raise RuntimeError("Unknown or image-only task(s): " + ", ".join(sorted(missing)))
    config_dir = args.output_dir / "config"
    if args.harness == "pi":
        pi_configuration(config_dir, models)
    if args.harness == "openhands":
        harness_version = command_output([
            args.openhands_python, "-c",
            "import importlib.metadata as m; print(m.version('openhands-ai'))",
        ])
    else:
        harness_version = command_output([args.harness, "--version"])
    jsonl_path = args.output_dir / f"{args.harness}_core_text.jsonl"
    csv_path = args.output_dir / f"{args.harness}_core_text.csv"
    records = [json.loads(line) for line in jsonl_path.read_text(encoding="utf-8").splitlines()] if jsonl_path.exists() else []
    completed = {(record["row"]["model"], record["row"]["task_id"]) for record in records}
    if len(completed) != len(records):
        raise RuntimeError("Existing evidence contains duplicate model/task keys")
    run_id = records[0]["row"]["run_id"] if records else time.strftime("%Y%m%d_%H%M%S")
    baseline = read_linux_resource_snapshot()
    sampler = create_sampler("auto", interval_ms=1000)
    sampler.start()
    try:
        total = len(models) * len(tasks)
        for model in models:
            for task in tasks:
                key = (model["name"], task["id"])
                if key in completed:
                    continue
                print(f"[{len(completed)+1}/{total}] {args.harness} {model['name']} :: {task['id']}", flush=True)
                stop_model(model["name"])
                env = dict(os.environ)
                env.update({
                    "PI_CODING_AGENT_DIR": str(config_dir),
                    "PI_TELEMETRY": "0",
                    "GOOSE_PROVIDER": "ollama",
                    "GOOSE_MODEL": model["name"],
                    "GOOSE_TELEMETRY_ENABLED": "false",
                    "XDG_CONFIG_HOME": str(config_dir / "xdg-config"),
                    "XDG_DATA_HOME": str(config_dir / "xdg-data"),
                })
                task_workspace = args.workspace / args.harness / model["name"].replace("/", "_").replace(":", "_") / task["id"]
                task_workspace.mkdir(parents=True, exist_ok=True)
                command = harness_command(
                    args.harness, model["name"], task["prompt"], config_dir,
                    args.openhands_python, task_workspace,
                )
                sample_start = sampler.snapshot_len()
                started = time.monotonic()
                exit_code, stdout, stderr, timed_out, pressure_error = run_guarded(
                    command, env, args.workspace, model["name"], args.timeout, baseline
                )
                wall = round(time.monotonic() - started, 3)
                stop_model(model["name"])
                samples = sampler.get_since(sample_start)
                status = "timeout" if timed_out else ("ok" if exit_code == 0 and stdout.strip() else "error")
                error = pressure_error or ((stderr or f"{args.harness} exit {exit_code}").strip() if status == "error" else "")
                grading = grade_task(task, status, stdout)
                row = {
                    "run_id": run_id, "harness": f"{args.harness}-agent", "harness_version": harness_version,
                    "model_runner": "ollama", "benchmark_profile": "core-text-17-v1",
                    "grading_profile": GRADING_PROFILE, "model": model["name"],
                    "model_digest": model["digest"], "task_id": task["id"], "task_name": task["name"],
                    "category": task["category"], "status": status, "verdict": grading["verdict"],
                    "grader_type": grading.get("grader_type", ""),
                    "grader_tests_passed": grading.get("tests_passed", 0),
                    "grader_tests_total": grading.get("tests_total", 0),
                    "grader_error": grading.get("error", "")[:2000], "wall_seconds": wall,
                    "exit_code": 124 if timed_out else exit_code, "timed_out": str(timed_out).lower(),
                    "response_chars": len(stdout), "response_sha256": hashlib.sha256(stdout.encode()).hexdigest(),
                    "max_gpu_temp_c": maximum(samples, "gpu_temp_c"),
                    "max_host_temp_c": maximum(samples, "host_temp_c"),
                    "max_host_memory_used_bytes": maximum(samples, "host_memory_used_bytes"),
                    "max_host_memory_pct": maximum(samples, "host_memory_pct"),
                    "max_gpu_usage_pct": maximum(samples, "gpu_usage_pct"), "sample_count": len(samples),
                    "response_preview": " ".join(stdout.split())[:300], "error": error[:2000],
                }
                record = {"row": row, "assistant_text": stdout, "stderr": stderr,
                          "grading": grading, "telemetry_samples": samples, "command": command}
                with jsonl_path.open("a", encoding="utf-8") as stream:
                    stream.write(json.dumps(record, ensure_ascii=False) + "\n")
                records.append(record)
                completed.add(key)
                write_csv(csv_path, records)
                print(f"  -> {status} {grading['verdict']} wall={wall}s", flush=True)
                if pressure_error:
                    raise RuntimeError("Resource safety guard: " + pressure_error)
    finally:
        sampler.stop()
        for model in models:
            stop_model(model["name"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
