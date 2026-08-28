#!/usr/bin/env python3
"""Run the official standard-local suite through COH's Codex Runtime path."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import signal
import subprocess
import time
from pathlib import Path

from accuracy_grading import GRADING_PROFILE, grade_task
from platform_support import create_sampler
from standard_local_tasks import STANDARD_LOCAL_PROFILE, load_standard_local_tasks


DEFAULT_MODELS = (
    "gpt-5.6-sol",
    "gpt-5.6-terra",
    "gpt-5.6-luna",
    "gpt-daybreak-blue-latest",
)
FIELDS = [
    "run_id", "row_id", "attempt", "harness", "harness_version", "model_runner",
    "benchmark_profile", "grading_profile", "model", "model_revision", "task_id",
    "task_name", "task_family", "category", "status", "verdict", "grader_type",
    "grader_tests_passed", "grader_tests_total", "grader_error", "wall_seconds",
    "exit_code", "timed_out", "response_chars", "response_sha256", "input_tokens",
    "output_tokens", "total_tokens", "cached_input_tokens", "reasoning_tokens",
    "capability_digest", "binding_digest", "provenance_digest", "max_host_temp_c",
    "max_host_memory_used_bytes", "max_host_memory_pct", "sample_count",
    "response_preview", "error",
]


def parse_args(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--binary", type=Path, required=True)
    parser.add_argument("--codex-binary", type=Path, required=True)
    parser.add_argument("--codex-home", type=Path, required=True)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--models", nargs="+", default=list(DEFAULT_MODELS))
    parser.add_argument("--task-profile", choices=("standard-local", "aime2026", "gpqa-diamond"), default="standard-local")
    parser.add_argument("--tasks", nargs="*")
    parser.add_argument("--timeout", type=int, default=1800)
    parser.add_argument("--max-output-tokens", type=int, default=32768)
    parser.add_argument("--max-retries", type=int, default=2)
    parser.add_argument("--retry-delay", type=float, default=10.0)
    parser.add_argument("--telemetry", choices=("auto", "mactop", "nvidia-smi", "none"), default="auto")
    parser.add_argument("--force", action="store_true", help="rerun selected successful model-task rows")
    parser.add_argument("--run", action="store_true")
    return parser.parse_args(argv)


def model_revision(model):
    payload = b"COH-CODEX-MODEL-ALIAS-V1\x00" + model.encode()
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def command(args, model, prompt):
    return [
        str(args.binary), "--invoke", "--model", model, "--prompt", prompt,
        "--workspace", str(args.workspace), "--codex-binary", str(args.codex_binary),
        "--codex-home", str(args.codex_home), "--timeout", f"{args.timeout}s",
        "--max-output-tokens", str(args.max_output_tokens),
    ]


def parse_provenance(stderr, model, version):
    lines = [line for line in stderr.splitlines() if line.strip()]
    if not lines:
        raise RuntimeError("COH invocation emitted no provenance")
    value = json.loads(lines[-1])
    required = ("capability_digest", "binding_digest", "provenance_digest", "usage")
    if value.get("harness_version") != version or value.get("model") != model:
        raise RuntimeError("COH provenance identity mismatch")
    if value.get("model_revision") != model_revision(model):
        raise RuntimeError("COH model-alias binding mismatch")
    if any(not value.get(field) for field in required):
        raise RuntimeError("COH provenance is incomplete")
    usage = value["usage"]
    if not isinstance(usage, dict) or not all(isinstance(usage.get(field), int) for field in ("input_tokens", "output_tokens", "total_tokens")):
        raise RuntimeError("COH provenance usage is malformed")
    return value


def selected_tasks(profile, wanted):
    tasks = load_standard_local_tasks(profile)
    if not wanted:
        return tasks
    selected = set(wanted)
    tasks = [task for task in tasks if task["id"] in selected]
    missing = selected - {task["id"] for task in tasks}
    if missing:
        raise RuntimeError("Unknown task(s): " + ", ".join(sorted(missing)))
    return tasks


def terminate(proc):
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
    latest = {}
    for record in records:
        row = record["row"]
        latest[(row["model"], row["task_id"])] = record
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=FIELDS)
        writer.writeheader()
        for key in sorted(latest):
            writer.writerow({field: latest[key]["row"].get(field, "") for field in FIELDS})


def validate(args):
    if args.timeout < 1 or args.timeout > 1800:
        raise SystemExit("--timeout must be between 1 and 1800 seconds")
    if args.max_output_tokens < 1 or args.max_output_tokens > 128000:
        raise SystemExit("--max-output-tokens must be between 1 and 128000")
    if args.max_retries < 0 or args.retry_delay < 0:
        raise SystemExit("retry settings cannot be negative")
    if len(args.models) != len(set(args.models)) or any(model not in DEFAULT_MODELS for model in args.models):
        raise SystemExit("models must be unique supported Codex model identifiers")
    for path, label in ((args.binary, "COH binary"), (args.codex_binary, "Codex binary")):
        if not path.is_file() or not os.access(path, os.X_OK):
            raise RuntimeError(f"{label} is missing or not executable")
    if not args.codex_home.is_dir() or not args.workspace.is_dir() or not (args.workspace / ".git").exists():
        raise RuntimeError("Codex home and managed Git workspace must exist")


def main(argv=None):
    args = parse_args(argv)
    validate(args)
    version_proc = subprocess.run([str(args.binary), "--version"], text=True, capture_output=True, timeout=30, check=False)
    if version_proc.returncode or not version_proc.stdout.strip():
        raise RuntimeError("Unable to determine COH benchmark harness version")
    version = version_proc.stdout.strip()
    tasks = selected_tasks(args.task_profile, args.tasks)
    print(f"COH Codex harness {version}; profile={STANDARD_LOCAL_PROFILE}; selected={args.task_profile}; tasks={len(tasks)}")
    print(f"models={','.join(args.models)}; observations={len(tasks) * len(args.models)}; retries={args.max_retries}")
    if not args.run:
        print("PLAN ONLY: add --run to execute inference and write evidence.")
        return 0

    args.output_dir.mkdir(parents=True, exist_ok=True)
    jsonl_path = args.output_dir / "coh_codex_standard.jsonl"
    csv_path = args.output_dir / "coh_codex_standard.csv"
    # JSON strings may legally contain Unicode line-separator characters.
    # Split only on the JSONL record delimiter, never with str.splitlines().
    records = [json.loads(line) for line in jsonl_path.read_text(encoding="utf-8").split("\n") if line] if jsonl_path.exists() else []
    run_id = records[0]["row"]["run_id"] if records else time.strftime("%Y%m%d_%H%M%S")
    successful = {(record["row"]["model"], record["row"]["task_id"]) for record in records if record["row"]["status"] == "ok"}
    attempt_counts = {}
    for record in records:
        key = (record["row"]["model"], record["row"]["task_id"])
        attempt_counts[key] = max(attempt_counts.get(key, 0), int(record["row"]["attempt"]))
    sampler = create_sampler(args.telemetry, interval_ms=1000)
    sampler.start()
    total = len(tasks) * len(args.models)
    ordinal = len(successful)
    try:
        for model in args.models:
            for task in tasks:
                key = (model, task["id"])
                if key in successful and not args.force:
                    continue
                ordinal += 1
                for retry in range(args.max_retries + 1):
                    attempt_counts[key] = attempt_counts.get(key, 0) + 1
                    attempt = attempt_counts[key]
                    print(f"[{ordinal}/{total}] {model} :: {task['id']} attempt={attempt}", flush=True)
                    cmd = command(args, model, task["prompt"])
                    sample_start = sampler.snapshot_len()
                    started = time.monotonic()
                    proc = subprocess.Popen(cmd, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
                    timed_out = False
                    try:
                        stdout, stderr = proc.communicate(timeout=args.timeout + 15)
                    except subprocess.TimeoutExpired:
                        timed_out = True
                        terminate(proc)
                        stdout, stderr = proc.communicate()
                    wall = round(time.monotonic() - started, 3)
                    samples = sampler.get_since(sample_start)
                    evidence, error = None, ""
                    if not timed_out and proc.returncode == 0 and stdout:
                        try:
                            evidence = parse_provenance(stderr, model, version)
                        except Exception as exc:
                            error = repr(exc)
                    elif timed_out:
                        error = f"COH request timeout after {args.timeout}s"
                    else:
                        error = (stderr or f"COH exit {proc.returncode}").strip()
                    status = "timeout" if timed_out else ("ok" if evidence is not None else "error")
                    grading = grade_task(task, status, stdout)
                    usage = evidence.get("usage", {}) if evidence else {}
                    row = {
                        "run_id": run_id, "row_id": f"{model}:{task['id']}:{attempt}", "attempt": attempt,
                        "harness": "coh-codex-runtime", "harness_version": version, "model_runner": "codex-exec",
                        "benchmark_profile": STANDARD_LOCAL_PROFILE, "grading_profile": GRADING_PROFILE, "model": model,
                        "model_revision": evidence.get("model_revision", "") if evidence else model_revision(model),
                        "task_id": task["id"], "task_name": task["name"], "task_family": task["family"],
                        "category": task["category"], "status": status, "verdict": grading["verdict"],
                        "grader_type": grading.get("grader_type", ""), "grader_tests_passed": grading.get("tests_passed", 0),
                        "grader_tests_total": grading.get("tests_total", 0), "grader_error": grading.get("error", "")[:2000],
                        "wall_seconds": wall, "exit_code": 124 if timed_out else proc.returncode,
                        "timed_out": str(timed_out).lower(), "response_chars": len(stdout),
                        "response_sha256": hashlib.sha256(stdout.encode()).hexdigest(),
                        "input_tokens": usage.get("input_tokens", ""), "output_tokens": usage.get("output_tokens", ""),
                        "total_tokens": usage.get("total_tokens", ""), "cached_input_tokens": usage.get("cached_input_tokens", ""),
                        "reasoning_tokens": usage.get("reasoning_tokens", ""),
                        "capability_digest": evidence.get("capability_digest", "") if evidence else "",
                        "binding_digest": evidence.get("binding_digest", "") if evidence else "",
                        "provenance_digest": evidence.get("provenance_digest", "") if evidence else "",
                        "max_host_temp_c": maximum(samples, "host_temp_c"),
                        "max_host_memory_used_bytes": maximum(samples, "host_memory_used_bytes"),
                        "max_host_memory_pct": maximum(samples, "host_memory_pct"), "sample_count": len(samples),
                        "response_preview": " ".join(stdout.split())[:300], "error": error[:2000],
                    }
                    record = {"row": row, "assistant_text": stdout, "stderr": stderr, "coh_provenance": evidence,
                              "grading": grading, "telemetry_samples": samples, "command": cmd}
                    with jsonl_path.open("a", encoding="utf-8") as stream:
                        stream.write(json.dumps(record, ensure_ascii=False) + "\n")
                        stream.flush()
                    records.append(record)
                    write_csv(csv_path, records)
                    print(f"  -> {status} {grading['verdict']} wall={wall}s", flush=True)
                    if status == "ok":
                        successful.add(key)
                        break
                    if retry < args.max_retries and args.retry_delay:
                        time.sleep(min(args.retry_delay * (2 ** retry), 60.0))
    finally:
        sampler.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
