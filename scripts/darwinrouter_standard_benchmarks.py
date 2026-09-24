#!/usr/bin/env python3
"""Run the deterministic Standard suite through DarwinRouter's native API.

The adapter uses DarwinRouter's ``auto`` route, records the durable task ID, and
feeds the deterministic benchmark verdict back through the authenticated
feedback API. Evidence is append-only and resumes by task ID.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import sys
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

from accuracy_grading import GRADING_PROFILE, grade_task
from benchmark_tests import suite_task_catalog
from platform_support import create_sampler


FIELDS = [
    "run_id", "harness", "harness_version", "benchmark_profile", "grading_profile",
    "model", "task_id", "darwin_task_id", "task_name", "category", "status",
    "verdict", "grader_type", "grader_tests_passed", "grader_tests_total",
    "grader_error", "wall_seconds", "response_chars", "response_sha256",
    "route_estimated_cost", "feedback_recorded", "max_gpu_temp_c",
    "max_host_temp_c", "max_host_memory_used_bytes", "max_host_memory_pct",
    "max_gpu_usage_pct", "sample_count", "response_preview", "error",
]


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:7788")
    parser.add_argument("--token-env", default="DARWIN_API_TOKEN")
    parser.add_argument("--model", default="auto")
    parser.add_argument("--local-required", action="store_true", help="Require DarwinRouter to select a local model")
    parser.add_argument("--ollama-model", action="append", default=[], help="Unload this local Ollama model before each task so host admission observes free memory (repeatable)")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--timeout", type=int, default=300)
    parser.add_argument("--tasks", "--test", dest="tasks", nargs="*")
    parser.add_argument("--list-tasks", action="store_true")
    parser.add_argument("--run", action="store_true")
    return parser.parse_args(argv)


def maximum(samples, field):
    values = [sample.get(field) for sample in samples if sample.get(field) is not None]
    return max(values) if values else ""


def request_json(url, token, *, method="GET", payload=None, headers=None, timeout=30):
    body = None if payload is None else json.dumps(payload, separators=(",", ":")).encode()
    request_headers = {"Authorization": f"Bearer {token}"}
    if body is not None:
        request_headers["Content-Type"] = "application/json"
    request_headers.update(headers or {})
    request = urllib.request.Request(url, data=body, headers=request_headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, json.load(response)
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:2000]
        raise RuntimeError(f"DarwinRouter HTTP {exc.code}: {detail}") from exc


def write_csv(path, records):
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=FIELDS)
        writer.writeheader()
        for record in records:
            writer.writerow({field: record["row"].get(field, "") for field in FIELDS})


def unload_ollama(model):
    if not model:
        return
    request = urllib.request.Request(
        "http://127.0.0.1:11434/api/generate",
        data=json.dumps({"model": model, "keep_alive": 0}).encode(),
        headers={"Content-Type": "application/json"}, method="POST",
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        if response.status != 200:
            raise RuntimeError(f"Ollama unload returned HTTP {response.status}")
        response.read(1 << 20)


def main(argv=None):
    args = parse_args(argv)
    tasks = [task for task in suite_task_catalog("standard") if not task.get("requires_image")]
    if args.tasks:
        wanted = set(args.tasks)
        tasks = [task for task in tasks if task["id"] in wanted]
        missing = wanted - {task["id"] for task in tasks}
        if missing:
            raise SystemExit("Unknown or image-only task(s): " + ", ".join(sorted(missing)))
    if args.list_tasks:
        for task in tasks:
            print(f"{task['id']}\t{task['category']}\t{task['name']}")
        return 0
    print(f"Suite: standard (core-text-17-v1); harness: DarwinRouter; tasks: {len(tasks)}")
    if not args.run:
        print("Plan only. Pass --run to execute benchmark observations.")
        return 0
    if not 1 <= args.timeout <= 300:
        raise SystemExit("--timeout must be between 1 and DarwinRouter's 300-second HTTP limit")
    token = os.environ.get(args.token_env, "")
    if len(token) < 32:
        raise SystemExit(f"{args.token_env} must contain the running daemon bearer token")
    base_url = args.base_url.rstrip("/")
    _, health = request_json(base_url + "/v1/health", token, timeout=30)
    if not health.get("ready"):
        raise RuntimeError("DarwinRouter health report is not ready")
    args.output_dir.mkdir(parents=True, exist_ok=True)
    jsonl_path = args.output_dir / "darwinrouter_standard.jsonl"
    csv_path = args.output_dir / "darwinrouter_standard.csv"
    records = [json.loads(line) for line in jsonl_path.read_text(encoding="utf-8").split("\n") if line] if jsonl_path.exists() else []
    completed = {record["row"]["task_id"] for record in records}
    if len(completed) != len(records):
        raise RuntimeError("Existing DarwinRouter evidence contains duplicate task IDs")
    sampler = create_sampler("auto", interval_ms=1000)
    sampler.start()
    run_id = records[0]["row"]["run_id"] if records else time.strftime("%Y%m%d_%H%M%S")
    try:
        for task in tasks:
            if task["id"] in completed:
                continue
            print(f"[{len(completed)+1}/{len(tasks)}] DarwinRouter {args.model} :: {task['id']}", flush=True)
            sample_start = sampler.snapshot_len()
            started = time.monotonic()
            error = ""
            response = {}
            try:
                for ollama_model in args.ollama_model:
                    unload_ollama(ollama_model)
                if args.ollama_model:
                    # Ollama acknowledges unload before macOS has necessarily
                    # reclaimed unified memory. Let host admission observe the
                    # post-unload state instead of turning pressure into rows.
                    time.sleep(5)
                for attempt in range(3):
                    try:
                        _, response = request_json(
                            base_url + "/v1/tasks", token, method="POST",
                            headers={"Idempotency-Key": f"benchmark-{run_id}-{task['id']}-{uuid.uuid4().hex}"},
                            payload={"model_id": args.model, "prompt": task["prompt"],
                                     "domain": task["category"], "profile": "benchmark",
                                     "local_required": args.local_required}, timeout=args.timeout,
                        )
                        break
                    except RuntimeError as exc:
                        if "execution_failed" not in str(exc) or attempt == 2:
                            raise
                        print(f"  -> transient execution failure; retry {attempt + 2}/3", flush=True)
                        for ollama_model in args.ollama_model:
                            unload_ollama(ollama_model)
                        # A failed local call can leave its model resident. Clear
                        # it before retry admission so the host guard does not
                        # double-count the same model as old plus incoming load.
                        time.sleep(5)
                if not response.get("task_id") or not isinstance(response.get("text"), str):
                    raise RuntimeError("DarwinRouter task response omitted task_id or text")
            except Exception as exc:
                if "admission_denied" in str(exc):
                    raise RuntimeError("DarwinRouter admission denied after model unload; campaign paused without grading resource pressure") from exc
                error = repr(exc)
            wall = round(time.monotonic() - started, 3)
            samples = sampler.get_since(sample_start)
            text = response.get("text", "") if not error else ""
            status = "ok" if text and not error else "error"
            grading = grade_task(task, status, text)
            feedback_recorded = False
            task_id = response.get("task_id", "")
            route_cost = response.get("route_estimated_cost")
            if task_id and status == "ok" and route_cost is not None and grading.get("verdict") in ("pass", "content_mismatch", "fail"):
                outcome = "accepted" if grading["verdict"] == "pass" else "rejected"
                try:
                    request_json(
                        base_url + "/v1/feedback", token, method="POST",
                        payload={"task_id": task_id, "outcome": outcome, "attempt_cost": route_cost},
                        timeout=30,
                    )
                    feedback_recorded = True
                except Exception as exc:
                    error = (error + "; " if error else "") + f"feedback: {exc!r}"
            row = {
                "run_id": run_id, "harness": "darwinrouter", "harness_version": health.get("version", 1),
                "benchmark_profile": "core-text-17-v1", "grading_profile": GRADING_PROFILE,
                "model": args.model, "task_id": task["id"], "darwin_task_id": task_id,
                "task_name": task["name"], "category": task["category"], "status": status,
                "verdict": grading["verdict"], "grader_type": grading.get("grader_type", ""),
                "grader_tests_passed": grading.get("tests_passed", 0),
                "grader_tests_total": grading.get("tests_total", 0),
                "grader_error": grading.get("error", "")[:2000], "wall_seconds": wall,
                "response_chars": len(text), "response_sha256": hashlib.sha256(text.encode()).hexdigest(),
                "route_estimated_cost": "" if route_cost is None else route_cost,
                "feedback_recorded": str(feedback_recorded).lower(),
                "max_gpu_temp_c": maximum(samples, "gpu_temp_c"),
                "max_host_temp_c": maximum(samples, "host_temp_c"),
                "max_host_memory_used_bytes": maximum(samples, "host_memory_used_bytes"),
                "max_host_memory_pct": maximum(samples, "host_memory_pct"),
                "max_gpu_usage_pct": maximum(samples, "gpu_usage_pct"), "sample_count": len(samples),
                "response_preview": " ".join(text.split())[:300], "error": error[:2000],
            }
            record = {"row": row, "assistant_text": text, "grading": grading,
                      "telemetry_samples": samples, "darwin_response": response}
            with jsonl_path.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(record, ensure_ascii=False) + "\n")
            records.append(record)
            completed.add(task["id"])
            write_csv(csv_path, records)
            print(f"  -> {status} {grading['verdict']} wall={wall}s", flush=True)
    finally:
        sampler.stop()
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except RuntimeError as exc:
        if "campaign paused without grading resource pressure" in str(exc):
            print(str(exc), file=sys.stderr)
            raise SystemExit(75)
        raise
