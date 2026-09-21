#!/usr/bin/env python3
"""Run the isolated command-line suite through DarwinRouter's SDK tool loop."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path

from benchmark_tests import suite_task_catalog
from coding_agent_benchmarks import prepare_workspace, grade_workspace
from platform_support import create_sampler


FIELDS = ["run_id", "benchmark_profile", "harness", "model", "task_id", "task_name",
          "status", "verdict", "checks_passed", "checks_total", "wall_seconds",
          "exit_code", "response_chars", "response_sha256", "max_gpu_temp_c",
          "max_host_temp_c", "max_host_memory_used_bytes", "max_host_memory_pct",
          "max_gpu_usage_pct", "sample_count", "error"]


def parse_args():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--host", type=Path, required=True)
    p.add_argument("--config", type=Path, required=True)
    p.add_argument("--model", default="local-gpt-oss")
    p.add_argument("--output-dir", type=Path, required=True)
    p.add_argument("--workspace", type=Path, required=True)
    p.add_argument("--timeout", type=int, default=900)
    p.add_argument("--unload-model", help="Ollama model identity to unload after each isolated task")
    p.add_argument("--tasks", nargs="*")
    p.add_argument("--run", action="store_true")
    return p.parse_args()


def maximum(samples, field):
    values = [s.get(field) for s in samples if s.get(field) is not None]
    return max(values) if values else ""


def write_csv(path, records):
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS); w.writeheader()
        for record in records:
            w.writerow({k: record["row"].get(k, "") for k in FIELDS})


def main():
    args = parse_args()
    args.host = args.host.expanduser().resolve()
    args.config = args.config.expanduser().resolve()
    args.output_dir = args.output_dir.expanduser().resolve()
    args.workspace = args.workspace.expanduser().resolve()
    tasks = suite_task_catalog("commandline")
    if args.tasks:
        wanted = set(args.tasks); tasks = [t for t in tasks if t["id"] in wanted]
        missing = wanted - {t["id"] for t in tasks}
        if missing: raise SystemExit("Unknown task(s): " + ", ".join(sorted(missing)))
    print(f"Suite: commandline (commandline-agent-v2-standard-20); harness: DarwinRouter; tasks: {len(tasks)}")
    if not args.run: return 0
    if not args.host.is_file() or not args.config.is_file():
        raise SystemExit("--host and --config must exist")
    args.output_dir.mkdir(parents=True, exist_ok=True); args.workspace.mkdir(parents=True, exist_ok=True)
    jsonl = args.output_dir / "darwinrouter_commandline.jsonl"
    csv_path = args.output_dir / "darwinrouter_commandline.csv"
    records = [json.loads(x) for x in jsonl.read_text().splitlines()] if jsonl.exists() else []
    completed = {r["row"]["task_id"] for r in records}
    if len(completed) != len(records): raise RuntimeError("duplicate command-line evidence")
    sampler = create_sampler("auto", interval_ms=1000); sampler.start()
    run_id = records[0]["row"]["run_id"] if records else time.strftime("%Y%m%d_%H%M%S")
    try:
        for task in tasks:
            if task["id"] in completed: continue
            print(f"[{len(completed)+1}/{len(tasks)}] DarwinRouter {args.model} :: {task['id']}", flush=True)
            workspace = prepare_workspace(args.workspace, "darwinrouter", args.model, task)
            prompt = task["prompt"] + "\n\nUse the supplied benchmark tools and save the final answer.json."
            start_sample = sampler.snapshot_len(); started = time.monotonic()
            command = [str(args.host), "--config", str(args.config), "--workspace", str(workspace),
                       "--model", args.model, "--prompt", prompt, "--timeout", f"{args.timeout}s"]
            admission_retries = 8
            for attempt in range(admission_retries):
                proc = subprocess.run(command, text=True, capture_output=True, timeout=args.timeout + 30,
                                      env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})
                if proc.returncode == 0:
                    break
                if "local resource capacity unavailable" in proc.stderr or "task admission failed" in proc.stderr:
                    if attempt + 1 == admission_retries:
                        raise SystemExit(75)
                    delay = min(5 * (attempt + 1), 30)
                    print(f"  -> local capacity busy; retry {attempt + 2}/{admission_retries} in {delay}s", flush=True)
                    time.sleep(delay)
                    continue
                if attempt < 2:
                    print(f"  -> transient host failure; retry {attempt + 2}/3", flush=True)
                    time.sleep(5)
                    continue
                break
            wall = round(time.monotonic()-started, 3); samples = sampler.get_since(start_sample)
            grading, grader_error = grade_workspace(task, workspace)
            stdout = proc.stdout.strip(); error = (proc.stderr.strip() + ("; " + grader_error if grader_error else ""))[:2000]
            row = {"run_id":run_id,"benchmark_profile":"commandline-agent-v2-standard-20","harness":"darwinrouter",
                   "model":args.model,"task_id":task["id"],"task_name":task["name"],
                   "status":"ok" if proc.returncode == 0 else "error","verdict":grading.get("verdict","grader_error"),
                   "checks_passed":grading.get("passed",0),"checks_total":grading.get("total",0),"wall_seconds":wall,
                   "exit_code":proc.returncode,"response_chars":len(stdout),"response_sha256":hashlib.sha256(stdout.encode()).hexdigest(),
                   "max_gpu_temp_c":maximum(samples,"gpu_temp_c"),"max_host_temp_c":maximum(samples,"host_temp_c"),
                   "max_host_memory_used_bytes":maximum(samples,"host_memory_used_bytes"),"max_host_memory_pct":maximum(samples,"host_memory_pct"),
                   "max_gpu_usage_pct":maximum(samples,"gpu_usage_pct"),"sample_count":len(samples),"error":error}
            record={"row":row,"stdout":stdout,"grading":grading,"telemetry_samples":samples}
            with jsonl.open("a",encoding="utf-8") as f: f.write(json.dumps(record)+"\n")
            records.append(record); completed.add(task["id"]); write_csv(csv_path,records)
            print(f"  -> {row['status']} {row['verdict']} wall={wall}s",flush=True)
            if args.unload_model:
                unloaded = subprocess.run(["ollama", "stop", args.unload_model], text=True,
                                          capture_output=True, timeout=30)
                if unloaded.returncode != 0:
                    print(f"  -> model unload failed: {unloaded.stderr.strip()[:300]}", flush=True)
    finally:
        sampler.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
