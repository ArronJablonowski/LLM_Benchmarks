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
from darwinrouter_coding_benchmarks import record_feedback, task_metadata, quality_verdict


FIELDS = ["run_id", "benchmark_profile", "harness", "model", "task_id", "task_name",
          "status", "verdict", "checks_passed", "checks_total", "wall_seconds",
          "exit_code", "response_chars", "response_sha256", "max_gpu_temp_c",
          "max_host_temp_c", "max_host_memory_used_bytes", "max_host_memory_pct",
          "max_gpu_usage_pct", "sample_count", "error"]


FIELDS += ["darwin_task_id", "previous_task_ids", "resolved_model", "resolved_provider", "context_window_tokens", "feedback_recorded"]

def parse_args():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--host", type=Path, required=True)
    p.add_argument("--config", type=Path, required=True)
    p.add_argument("--database", type=Path, required=True)
    p.add_argument("--darwin", default="darwin")
    p.add_argument("--telemetry", choices=["auto", "none"], default="none")
    p.add_argument("--model", default="local-gpt-oss")
    p.add_argument("--output-dir", type=Path, required=True)
    p.add_argument("--workspace", type=Path, required=True)
    p.add_argument("--timeout", type=int, default=900)
    p.add_argument("--unload-model", action="append", default=[], help="Allow cleanup of this Ollama model only if the isolated task lineage actually used it")
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


def unload_model(model):
    stopped = subprocess.run(["ollama", "stop", model], text=True, capture_output=True, timeout=30)
    if stopped.returncode != 0:
        return stopped.stderr.strip()[:300] or "ollama stop failed"
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        resident = subprocess.run(["ollama", "ps"], text=True, capture_output=True, timeout=10)
        if resident.returncode == 0 and model not in resident.stdout:
            return ""
        time.sleep(2)
    return "model remained resident after stop"


def main():
    args = parse_args()
    args.host = args.host.expanduser().resolve()
    args.config = args.config.expanduser().resolve()
    args.output_dir = args.output_dir.expanduser().resolve()
    args.workspace = args.workspace.expanduser().resolve()
    args.database = args.database.expanduser().resolve()
    # The default remains the original suite. Explicit IDs may opt into fresh
    # expanded labs without silently expanding an existing campaign.
    tasks = suite_task_catalog("commandline", full=bool(args.tasks))
    if args.tasks:
        wanted = set(args.tasks); tasks = [t for t in tasks if t["id"] in wanted]
        missing = wanted - {t["id"] for t in tasks}
        if missing: raise SystemExit("Unknown task(s): " + ", ".join(sorted(missing)))
    profile = "commandline-agent-v2-expanded" if any(t['id'].startswith('cli_exp_') for t in tasks) else "commandline-agent-v2-standard-20"
    print(f"Suite: commandline ({profile}); harness: DarwinRouter; tasks: {len(tasks)}")
    if not args.run: return 0
    if not args.host.is_file() or not args.config.is_file():
        raise SystemExit("--host and --config must exist")
    args.output_dir.mkdir(parents=True, exist_ok=True); args.workspace.mkdir(parents=True, exist_ok=True)
    jsonl = args.output_dir / "darwinrouter_commandline.jsonl"
    csv_path = args.output_dir / "darwinrouter_commandline.csv"
    records = [json.loads(x) for x in jsonl.read_text().splitlines()] if jsonl.exists() else []
    completed = {r["row"]["task_id"] for r in records}
    if len(completed) != len(records): raise RuntimeError("duplicate command-line evidence")
    sampler = create_sampler(args.telemetry, interval_ms=1000); sampler.start()
    run_id = records[0]["row"]["run_id"] if records else time.strftime("%Y%m%d_%H%M%S")
    try:
        for task in tasks:
            if task["id"] in completed: continue
            print(f"[{len(completed)+1}/{len(tasks)}] DarwinRouter {args.model} :: {task['id']}", flush=True)
            workspace = prepare_workspace(args.workspace / run_id, "darwinrouter", args.model, task)
            prompt = task["prompt"] + "\n\nUse the supplied benchmark tools and save the final answer.json."
            start_sample = sampler.snapshot_len(); started = time.monotonic()
            command = [str(args.host), "--config", str(args.config), "--workspace", str(workspace),
                       "--database", str(args.database), "--model", args.model, "--prompt", prompt, "--timeout", f"{args.timeout}s"]
            try:
                proc = subprocess.run(command, text=True, capture_output=True, timeout=args.timeout + 30,
                                      env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})
            except subprocess.TimeoutExpired as exc:
                proc = subprocess.CompletedProcess(command, 124, "", "host deadline exceeded")
            # A failed attempt is evidence. Never retry in its mutated workspace.
            wall = round(time.monotonic()-started, 3); samples = sampler.get_since(start_sample)
            grading, grader_error = grade_workspace(task, workspace)
            stdout = proc.stdout.strip(); error = (proc.stderr.strip() + ("; " + grader_error if grader_error else ""))[:2000]
            try:
                result = json.loads(stdout.splitlines()[-1])
            except (ValueError, IndexError):
                result = {}
            task_id = result.get("TaskID") or result.get("task_id") or ""
            previous_ids = result.get("PreviousTaskIDs") or result.get("previous_task_ids") or []
            if not isinstance(previous_ids, list) or any(not isinstance(t, str) for t in previous_ids):
                raise RuntimeError("invalid durable recovery lineage; inspect before cleanup")
            host_error = result.get("error", "") or ("missing durable task identity" if not task_id else "")
            verdict = quality_verdict(proc.returncode, host_error, grader_error, grading)
            try:
                metadata = task_metadata(args.database, task_id) if task_id else {}
            except Exception as exc:
                metadata = {}
                error = (error + "; metadata: " + str(exc))[:2000]
            row = {"run_id":run_id,"benchmark_profile":profile,"harness":"darwinrouter",
                   "model":args.model,"task_id":task["id"],"task_name":task["name"],
                   "status":"ok" if verdict in ("pass", "fail") else "error","verdict":verdict,
                   "checks_passed":grading.get("passed",0),"checks_total":grading.get("total",0),"wall_seconds":wall,
                   "exit_code":proc.returncode,"response_chars":len(stdout),"response_sha256":hashlib.sha256(stdout.encode()).hexdigest(),
                   "max_gpu_temp_c":maximum(samples,"gpu_temp_c"),"max_host_temp_c":maximum(samples,"host_temp_c"),
                   "max_host_memory_used_bytes":maximum(samples,"host_memory_used_bytes"),"max_host_memory_pct":maximum(samples,"host_memory_pct"),
                   "max_gpu_usage_pct":maximum(samples,"gpu_usage_pct"),"sample_count":len(samples),"error":error}
            row.update(darwin_task_id=task_id, previous_task_ids=previous_ids, resolved_model=metadata.get("model_id", ""), resolved_provider=metadata.get("provider_id", ""),
                       context_window_tokens=metadata.get("context_tokens", ""), feedback_recorded=False)
            record={"row":row,"stdout":stdout,"stderr":proc.stderr,"grading":grading,"telemetry_samples":samples,
                    "workspace":str(workspace), "learning_database":str(args.database)}
            with jsonl.open("a",encoding="utf-8") as f: f.write(json.dumps(record)+"\n")
            # Save inference evidence before feedback; a crash can be repaired
            # from the separate append-only feedback ledger without rerunning.
            if task_id and verdict in ("pass", "fail"):
                receipt={"darwin_task_id":task_id,"accepted":verdict=="pass","recorded":False}
                try:
                    record_feedback(args.darwin,args.database,task_id,verdict=="pass")
                    receipt["recorded"]=True
                except Exception as exc:
                    receipt["error"]=str(exc)[:1000]
                with (args.output_dir / "feedback.jsonl").open("a") as f:
                    f.write(json.dumps(receipt)+"\n")
                row["feedback_recorded"]=receipt["recorded"]
            records.append(record); completed.add(task["id"]); write_csv(csv_path,records)
            print(f"  -> {row['status']} {row['verdict']} wall={wall}s",flush=True)
            # A candidate allowlist is not proof of ownership. Only stop models
            # recorded in this attempt's durable lineage; unrelated residents
            # must be left for the campaign's ownership guard to investigate.
            used_models = {metadata.get("model_id")}
            for previous_id in previous_ids:
                used_models.add(task_metadata(args.database, previous_id).get("model_id"))
            for owned_model in sorted(set(args.unload_model or []) & used_models):
                unload_error = unload_model(owned_model)
                if unload_error:
                    raise RuntimeError(f"owned model unload failed: {unload_error}")
    finally:
        sampler.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
