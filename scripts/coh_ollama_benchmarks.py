#!/usr/bin/env python3
"""Run the 17 text tasks through COH's sealed local Ollama invocation path."""
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
from benchmark_tests import core_task_catalog
from ollama_standardized_local_benchmarks import stop_model
from platform_support import create_sampler


FIELDS = [
    "run_id", "harness", "harness_version", "model_runner", "benchmark_profile",
    "grading_profile", "model", "model_digest", "task_id", "task_name", "category",
    "status", "verdict", "grader_type", "grader_tests_passed", "grader_tests_total",
    "grader_error", "wall_seconds", "exit_code", "timed_out", "response_chars",
    "response_sha256", "input_tokens", "output_tokens", "total_tokens",
    "capability_digest", "binding_digest", "provenance_digest", "max_gpu_temp_c",
    "max_host_temp_c", "max_host_memory_used_bytes", "max_host_memory_pct",
    "max_gpu_usage_pct", "sample_count", "response_preview", "error",
]


def parse_args(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--binary", type=Path, required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--model-digest", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--timeout", type=int, default=1800)
    parser.add_argument("--max-output-tokens", type=int, default=8192)
    parser.add_argument("--tasks", nargs="*")
    parser.add_argument("--run", action="store_true")
    return parser.parse_args(argv)


def ollama_models():
    with urllib.request.urlopen("http://127.0.0.1:11434/api/tags", timeout=15) as response:
        return json.load(response).get("models", [])


def command(binary, model, prompt, timeout, maximum_output):
    return [
        str(binary), "--invoke", "--model", model, "--prompt", prompt,
        "--timeout", f"{timeout}s", "--max-output-tokens", str(maximum_output),
    ]


def parse_provenance(stderr, model, digest, version):
    lines = [line for line in stderr.splitlines() if line.strip()]
    if not lines:
        raise RuntimeError("COH invocation emitted no provenance")
    value = json.loads(lines[-1])
    required = ("capability_digest", "binding_digest", "provenance_digest", "usage")
    if value.get("harness_version") != version or value.get("model") != model:
        raise RuntimeError("COH provenance identity mismatch")
    if value.get("model_revision") != "sha256:" + digest:
        raise RuntimeError("COH provenance model digest mismatch")
    if any(not value.get(field) for field in required):
        raise RuntimeError("COH provenance is incomplete")
    usage = value["usage"]
    if not isinstance(usage, dict) or not all(
        isinstance(usage.get(field), int) for field in ("input_tokens", "output_tokens", "total_tokens")
    ):
        raise RuntimeError("COH provenance usage is malformed")
    return value


def maximum(samples, field):
    values = [sample.get(field) for sample in samples if sample.get(field) is not None]
    return max(values) if values else ""


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


def write_csv(path, records):
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=FIELDS)
        writer.writeheader()
        for record in records:
            writer.writerow({field: record["row"].get(field, "") for field in FIELDS})


def selected_tasks(wanted):
    tasks = [task for task in core_task_catalog() if not task.get("requires_image")]
    if not wanted:
        return tasks
    selected = set(wanted)
    tasks = [task for task in tasks if task["id"] in selected]
    missing = selected - {task["id"] for task in tasks}
    if missing:
        raise RuntimeError("Unknown or image-only task(s): " + ", ".join(sorted(missing)))
    return tasks


def main(argv=None):
    args = parse_args(argv)
    if args.timeout < 1 or args.timeout > 1800:
        raise SystemExit("--timeout must be between 1 and 1800 seconds")
    if args.max_output_tokens < 1:
        raise SystemExit("--max-output-tokens must be positive")
    if not args.binary.is_file() or not os.access(args.binary, os.X_OK):
        raise RuntimeError("COH benchmark binary is missing or not executable")
    version_proc = subprocess.run(
        [str(args.binary), "--version"], text=True, capture_output=True, timeout=30, check=False
    )
    if version_proc.returncode or not version_proc.stdout.strip():
        raise RuntimeError("Unable to determine COH benchmark harness version")
    version = version_proc.stdout.strip()
    installed = {str(item.get("name") or item.get("model")): item for item in ollama_models()}
    if args.model not in installed or installed[args.model].get("digest") != args.model_digest:
        raise RuntimeError("Frozen model provenance mismatch")
    tasks = selected_tasks(args.tasks)
    print(f"COH harness {version}; model={args.model}; text_tasks={len(tasks)}; timeout={args.timeout}s")
    print(f"model_digest={args.model_digest}; max_output_tokens={args.max_output_tokens}")
    if not args.run:
        print("PLAN ONLY: add --run to execute inference and write evidence.")
        return 0
    args.output_dir.mkdir(parents=True, exist_ok=True)
    jsonl_path = args.output_dir / "coh_ollama_core_text.jsonl"
    csv_path = args.output_dir / "coh_ollama_core_text.csv"
    records = [
        json.loads(line) for line in jsonl_path.read_text(encoding="utf-8").splitlines()
    ] if jsonl_path.exists() else []
    completed = {record["row"]["task_id"] for record in records}
    if len(completed) != len(records):
        raise RuntimeError("Existing evidence contains duplicate task IDs")
    run_id = records[0]["row"]["run_id"] if records else time.strftime("%Y%m%d_%H%M%S")
    sampler = create_sampler("auto", interval_ms=1000)
    sampler.start()
    try:
        for task in tasks:
            if task["id"] in completed:
                continue
            print(f"[{len(completed)+1}/{len(tasks)}] COH {args.model} :: {task['id']}", flush=True)
            stop_model(args.model)
            cmd = command(args.binary, args.model, task["prompt"], args.timeout, args.max_output_tokens)
            sample_start = sampler.snapshot_len()
            started = time.monotonic()
            proc = subprocess.Popen(
                cmd, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                start_new_session=True,
            )
            timed_out = False
            try:
                stdout, stderr = proc.communicate(timeout=args.timeout + 15)
            except subprocess.TimeoutExpired:
                timed_out = True
                terminate(proc)
                stdout, stderr = proc.communicate()
            wall = round(time.monotonic() - started, 3)
            stop_model(args.model)
            samples = sampler.get_since(sample_start)
            evidence = None
            error = ""
            if not timed_out and proc.returncode == 0 and stdout:
                try:
                    evidence = parse_provenance(stderr, args.model, args.model_digest, version)
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
                "run_id": run_id, "harness": "coh-ollama", "harness_version": version,
                "model_runner": "ollama", "benchmark_profile": "core-text-17-v1",
                "grading_profile": GRADING_PROFILE, "model": args.model,
                "model_digest": args.model_digest, "task_id": task["id"],
                "task_name": task["name"], "category": task["category"], "status": status,
                "verdict": grading["verdict"], "grader_type": grading.get("grader_type", ""),
                "grader_tests_passed": grading.get("tests_passed", 0),
                "grader_tests_total": grading.get("tests_total", 0),
                "grader_error": grading.get("error", "")[:2000], "wall_seconds": wall,
                "exit_code": 124 if timed_out else proc.returncode,
                "timed_out": str(timed_out).lower(), "response_chars": len(stdout),
                "response_sha256": hashlib.sha256(stdout.encode()).hexdigest(),
                "input_tokens": usage.get("input_tokens", ""),
                "output_tokens": usage.get("output_tokens", ""),
                "total_tokens": usage.get("total_tokens", ""),
                "capability_digest": evidence.get("capability_digest", "") if evidence else "",
                "binding_digest": evidence.get("binding_digest", "") if evidence else "",
                "provenance_digest": evidence.get("provenance_digest", "") if evidence else "",
                "max_gpu_temp_c": maximum(samples, "gpu_temp_c"),
                "max_host_temp_c": maximum(samples, "host_temp_c"),
                "max_host_memory_used_bytes": maximum(samples, "host_memory_used_bytes"),
                "max_host_memory_pct": maximum(samples, "host_memory_pct"),
                "max_gpu_usage_pct": maximum(samples, "gpu_usage_pct"), "sample_count": len(samples),
                "response_preview": " ".join(stdout.split())[:300], "error": error[:2000],
            }
            record = {"row": row, "assistant_text": stdout, "stderr": stderr,
                      "coh_provenance": evidence, "grading": grading,
                      "telemetry_samples": samples, "command": cmd}
            with jsonl_path.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(record, ensure_ascii=False) + "\n")
            records.append(record)
            completed.add(task["id"])
            write_csv(csv_path, records)
            print(f"  -> {status} {grading['verdict']} wall={wall}s", flush=True)
    finally:
        sampler.stop()
        stop_model(args.model)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
