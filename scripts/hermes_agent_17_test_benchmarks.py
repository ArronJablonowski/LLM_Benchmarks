#!/usr/bin/env python3
"""Accuracy-first text benchmarks through the real Hermes Agent one-shot path."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from accuracy_grading import GRADING_PROFILE, grade_task
from ollama_standardized_local_benchmarks import (
    TASKS as ALL_TASKS,
    RESOURCE_GUARD_INFRASTRUCTURE_FAILURE,
    avg_field,
    finish_paired_task_resource_guard,
    max_field,
    read_linux_resource_snapshot,
    start_paired_task_resource_guard,
    stop_model,
    verify_empty_paired_residency,
    verify_paired_runtime_identity,
)
from platform_support import create_sampler, run_metadata

SUITE_VERSION = "0.1.0"
BENCHMARK_PROFILE = "hermes-agent-accuracy-first-v1"
DEFAULT_OLLAMA_URL = "http://127.0.0.1:11434"
DEFAULT_TIMEOUT = 1800
DEFAULT_OUT_DIR = Path.home() / ".hermes/reports/hermes_agent_benchmarks"
DEFAULT_HERMES_HOME = Path.home() / ".hermes"
TEXT_TASKS = [task for task in ALL_TASKS if task["id"] != "ocrbench_mini"]


def _json_file(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"Expected a JSON object in {path}")
    return value


def _plan_models(plan: dict, selected: list[str] | None) -> list[dict]:
    models = [dict(model) for model in plan.get("models") or []]
    if selected:
        wanted = set(selected)
        models = [model for model in models if model.get("name") in wanted]
        missing = wanted - {model.get("name") for model in models}
        if missing:
            raise RuntimeError("Models absent from context plan: " + ", ".join(sorted(missing)))
    for model in models:
        if not model.get("requested_num_ctx"):
            raise RuntimeError(f"{model.get('name')} lacks a calibrated requested_num_ctx")
    return models


def _treatments(model: dict) -> list[dict]:
    result = []
    for item in model.get("treatments") or []:
        role = str(item.get("treatment_role") or "")
        key = str(item.get("treatment_key") or role or "default")
        resolved = str(item.get("thinking_resolved") or "").lower()
        if role == "off" or "off" in key or resolved in {"disabled", "none"}:
            reasoning = "none"
        elif resolved in {"low", "high", "max"}:
            reasoning = resolved
        elif "low" in key:
            reasoning = "low"
        elif "high" in key:
            reasoning = "high"
        else:
            reasoning = "max"
        result.append({**item, "hermes_reasoning": reasoning})
    return result or [{"treatment_key": "model-default", "treatment_role": "default", "hermes_reasoning": "max"}]


def _run(cmd: list[str], timeout: int) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, text=True, capture_output=True, timeout=timeout)


def _set_context(hermes_python: Path, context: int) -> None:
    cmd = [str(hermes_python), "-m", "hermes_cli.main", "config", "set", "model.ollama_num_ctx", str(context)]
    proc = _run(cmd, 30)
    if proc.returncode:
        raise RuntimeError((proc.stderr or proc.stdout or "Hermes context update failed").strip())
    verify = _run([str(hermes_python), "-m", "hermes_cli.main", "config", "get", "model.ollama_num_ctx"], 30)
    if verify.returncode or verify.stdout.strip() != str(context):
        raise RuntimeError(f"Hermes context verification failed: expected {context}, got {verify.stdout.strip()!r}")


def _hermes_command(
    hermes_python: Path, model: str, prompt: str, reasoning: str, usage_file: Path
) -> list[str]:
    return [
        str(hermes_python), "-m", "hermes_cli.main",
        "--oneshot", prompt,
        "--usage-file", str(usage_file),
        "--model", model,
        "--provider", "custom",
        "--reasoning", reasoning,
        "--toolsets", "clarify",
        "--ignore-rules",
    ]


def _summary(rows: list[dict], path: Path, metadata: dict) -> None:
    lines = [
        "# Hermes Agent 17-Test Text Benchmark", "",
        f"- Run: `{metadata['run_id']}`",
        f"- Host: {metadata['host_label']}",
        f"- Hermes: `{metadata['hermes_version']}`",
        f"- Execution: Hermes one-shot agent → custom provider → local Ollama",
        f"- Tasks: 17 text tasks; OCR is excluded because Hermes one-shot has no image flag.",
        f"- Tool surface: `clarify` only; rules/memory injection disabled for repeatability.", "",
        "| Model | Treatment | Pass | Scored | Errors | Avg wall s | Output tokens |", "|---|---:|---:|---:|---:|---:|---:|",
    ]
    keys = sorted({(row["model"], row["treatment_key"]) for row in rows})
    for model, treatment in keys:
        group = [row for row in rows if row["model"] == model and row["treatment_key"] == treatment]
        scored = [row for row in group if row["verdict"] != "skip"]
        passes = sum(row["verdict"] == "pass" for row in scored)
        errors = sum(row["status"] not in {"ok", "skip"} for row in group)
        avg = sum(float(row["wall_seconds"]) for row in scored) / len(scored) if scored else 0
        tokens = sum(int(row["eval_count"] or 0) for row in group)
        lines.append(f"| `{model}` | `{treatment}` | {passes} | {len(scored)} | {errors} | {avg:.2f} | {tokens} |")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _row_key(row: dict) -> tuple[str, str, str]:
    return (str(row.get("model") or ""), str(row.get("treatment_key") or ""), str(row.get("task_id") or ""))


def _load_resume_records(
    jsonl_path: Path, plan_sha256: str, models: list[dict], tasks: list[dict]
) -> tuple[list[dict], list[dict], set[tuple[str, str, str]]]:
    model_map = {model["name"]: model for model in models}
    task_map = {task["id"]: task for task in tasks}
    treatment_map = {
        (model["name"], item["treatment_key"]): item
        for model in models for item in _treatments(model)
    }
    records: list[dict] = []; rows: list[dict] = []; completed = set()
    for line_number, line in enumerate(jsonl_path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        record = json.loads(line); row = record.get("row") or {}
        key = _row_key(row)
        if key in completed:
            raise RuntimeError(f"Resume JSONL duplicates {key} at line {line_number}")
        model = model_map.get(key[0]); task = task_map.get(key[2]); treatment = treatment_map.get((key[0], key[1]))
        if model is None or task is None or treatment is None:
            raise RuntimeError(f"Resume JSONL contains an unplanned row at line {line_number}: {key}")
        mismatches = []
        expected = {
            "context_plan_sha256": plan_sha256,
            "model_digest": model.get("digest", ""),
            "requested_num_ctx": str(model["requested_num_ctx"]),
            "hermes_reasoning_requested": treatment["hermes_reasoning"],
        }
        for field, value in expected.items():
            if str(row.get(field, "")) != str(value):
                mismatches.append(field)
        status = str(row.get("status") or "")
        if status not in {"ok", "error", "timeout"}:
            mismatches.append("status")
        grading = grade_task(task, status, str(record.get("assistant_text") or ""), skipped=False)
        if grading.get("verdict") != row.get("verdict"):
            mismatches.append("verdict")
        guard = record.get("resource_guard") or {}
        if (
            guard.get("watchdog_triggered") is not False
            or guard.get("memory_recovery_verified") is not True
            or guard.get("watchdog_join_verified") is not True
            or guard.get("infrastructure_error")
        ):
            mismatches.append("resource_guard")
        if mismatches:
            raise RuntimeError(
                f"Resume JSONL line {line_number} failed validation: " + ", ".join(mismatches)
            )
        completed.add(key); records.append(record); rows.append(dict(row))
    return records, rows, completed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--context-plan", type=Path, required=True)
    parser.add_argument("--models", nargs="*")
    parser.add_argument("--tasks", nargs="*")
    parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT)
    parser.add_argument("--ollama-url", default=DEFAULT_OLLAMA_URL)
    parser.add_argument("--hermes-home", type=Path, default=DEFAULT_HERMES_HOME)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUT_DIR)
    parser.add_argument("--resume-prefix", type=Path, help="Existing report prefix without .jsonl/.csv suffix")
    parser.add_argument("--telemetry", choices=("auto", "mactop", "nvidia-smi", "none"), default="auto")
    parser.add_argument("--telemetry-interval-ms", type=int, default=1000)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--run", action="store_true")
    mode.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    if not 1 <= args.timeout <= 1800:
        parser.error("--timeout must be between 1 and 1800 seconds")

    plan = _json_file(args.context_plan.expanduser())
    models = _plan_models(plan, args.models)
    tasks = TEXT_TASKS
    if args.tasks:
        wanted = set(args.tasks)
        tasks = [task for task in tasks if task["id"] in wanted]
        missing = wanted - {task["id"] for task in tasks}
        if missing:
            raise RuntimeError("Unknown/non-text task IDs: " + ", ".join(sorted(missing)))
    hermes_python = args.hermes_home.expanduser() / "hermes-agent/venv/bin/python"
    if not hermes_python.exists():
        raise RuntimeError(f"Hermes runtime not found: {hermes_python}")
    model_calls = sum(len(_treatments(model)) for model in models)
    print(f"Models: {len(models)}; treatments: {model_calls}; text tasks: {len(tasks)}; calls: {model_calls * len(tasks)}")
    for model in models:
        labels = ", ".join(f"{item['treatment_key']}=>{item['hermes_reasoning']}" for item in _treatments(model))
        print(f" - {model['name']}: num_ctx={model['requested_num_ctx']}; {labels}")
    print("Persistent Hermes config is backed up and restored; gateway must be stopped during the run.")
    if args.dry_run or not args.run:
        print("PLAN ONLY: no config mutation, telemetry, model stop, or inference occurred.")
        return 0

    gateway = _run(["systemctl", "--user", "is-active", "hermes-gateway.service"], 15)
    if gateway.stdout.strip() == "active":
        raise RuntimeError("Stop hermes-gateway.service before running to prevent concurrent Ollama traffic")
    base_url = args.ollama_url.rstrip("/")
    metadata = run_metadata(base_url)
    version = _run([str(hermes_python), "-m", "hermes_cli.main", "--version"], 30)
    if version.returncode:
        raise RuntimeError("Unable to read Hermes version")
    current_runner_sha256 = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    metadata.update({
        "run_id": time.strftime("%Y%m%d_%H%M%S"),
        "suite_version": SUITE_VERSION,
        "benchmark_profile": BENCHMARK_PROFILE,
        "grading_profile": GRADING_PROFILE,
        "runner_sha256": current_runner_sha256,
        "recovery_runner_sha256": "",
        "hermes_version": version.stdout.splitlines()[0].strip(),
        "context_plan": str(args.context_plan.expanduser()),
        "context_plan_sha256": hashlib.sha256(args.context_plan.expanduser().read_bytes()).hexdigest(),
    })
    out_dir = args.output_dir.expanduser(); out_dir.mkdir(parents=True, exist_ok=True)
    prefix = (
        args.resume_prefix.expanduser()
        if args.resume_prefix else out_dir / f"hermes_agent_text_benchmark_{metadata['run_id']}"
    )
    csv_path, jsonl_path, md_path = prefix.with_suffix(".csv"), prefix.with_suffix(".jsonl"), prefix.with_suffix(".md")
    records: list[dict] = []; rows: list[dict] = []; completed: set[tuple[str,str,str]] = set()
    if args.resume_prefix:
        if not jsonl_path.exists():
            raise RuntimeError(f"Resume JSONL does not exist: {jsonl_path}")
        records, rows, completed = _load_resume_records(
            jsonl_path, metadata["context_plan_sha256"], models, tasks
        )
        first_meta = records[0].get("metadata") if records else {}
        if records and not isinstance(first_meta, dict):
            raise RuntimeError("Resume JSONL lacks canonical metadata")
        if records:
            metadata["run_id"] = str(first_meta.get("run_id") or rows[0].get("run_id") or "")
            metadata["runner_sha256"] = str(rows[0].get("runner_sha256") or first_meta.get("runner_sha256") or "")
            metadata["recovery_runner_sha256"] = current_runner_sha256
        print(f"Resume: {len(completed)} completed calls retained; {model_calls * len(tasks) - len(completed)} remain.")
    config_path = args.hermes_home.expanduser() / "config.yaml"
    config_backup = prefix.with_suffix(".config-backup.yaml")
    shutil.copy2(config_path, config_backup); os.chmod(config_backup, 0o600)
    sampler = create_sampler(args.telemetry, args.telemetry_interval_ms)
    metadata["telemetry_backend"] = sampler.backend
    fields = [
        "run_id","suite_version","host","host_label","platform","os_version","architecture","telemetry_backend","ollama_version",
        "benchmark_profile","grading_profile","runner_sha256","recovery_runner_sha256","hermes_version","context_plan_sha256",
        "model","model_digest","requested_num_ctx","native_context_length","treatment_key","treatment_role","hermes_reasoning_requested",
        "benchmark_family","category","task_id","task_name","status","verdict","grader_type","grader_version","grader_tests_passed","grader_tests_total","grader_error","grading_wall_seconds",
        "wall_seconds","timed_out","termination_reason","prompt_eval_count","eval_count","reasoning_tokens","total_token_count","api_calls","response_chars","response_bytes",
        "max_cpu_usage_pct","avg_cpu_usage_pct","max_gpu_usage_pct","avg_gpu_usage_pct","max_gpu_temp_c","avg_gpu_temp_c","max_host_temp_c","avg_host_temp_c","max_gpu_power_w","avg_gpu_power_w","sample_count","response_preview","exit_code","error",
    ]
    campaign_baseline = read_linux_resource_snapshot()
    try:
        sampler.start()
        with csv_path.open("w" if args.resume_prefix else "x", newline="", encoding="utf-8") as cf, jsonl_path.open("a" if args.resume_prefix else "x", encoding="utf-8") as jf:
            writer = csv.DictWriter(cf, fieldnames=fields); writer.writeheader(); cf.flush()
            for existing in rows:
                writer.writerow({field:existing.get(field, "") for field in fields})
            cf.flush()
            call_index = 0
            for model_index, model in enumerate(models, 1):
                print(f"\n=== {model_index}/{len(models)} {model['name']} ===", flush=True)
                _set_context(hermes_python, int(model["requested_num_ctx"]))
                for treatment in _treatments(model):
                    for task in tasks:
                        call_index += 1
                        if (model["name"], treatment["treatment_key"], task["id"]) in completed:
                            continue
                        verify_paired_runtime_identity(plan, model, base_url)
                        stop_model(model["name"], base_url); verify_empty_paired_residency(model["name"], base_url)
                        guard = start_paired_task_resource_guard(
                            model, base_url, campaign_baseline=campaign_baseline,
                            expected_system_page_size_bytes=plan["runtime_resource_safety_policy"]["system_page_size_bytes"],
                        )
                        print(f"[{call_index}] {task['id']} / {treatment['treatment_key']}...", flush=True)
                        usage_fd, usage_name = tempfile.mkstemp(prefix="hermes-bench-usage-", suffix=".json")
                        os.close(usage_fd)
                        usage_file = Path(usage_name)
                        sample_start = sampler.snapshot_len(); started = time.monotonic()
                        stdout = stderr = error = ""; exit_code = 1; timed_out = False
                        try:
                            proc = _run(_hermes_command(hermes_python, model["name"], task["prompt"], treatment["hermes_reasoning"], usage_file), args.timeout + 30)
                            stdout, stderr, exit_code = proc.stdout or "", proc.stderr or "", proc.returncode
                        except subprocess.TimeoutExpired as exc:
                            timed_out = True; exit_code = 124
                            stdout = (exc.stdout or "") if isinstance(exc.stdout, str) else ""
                            stderr = (exc.stderr or "") if isinstance(exc.stderr, str) else ""
                            error = f"outer timeout after {args.timeout + 30}s"
                            stop_model(model["name"], base_url)
                        wall = round(time.monotonic() - started, 3)
                        stop_model(model["name"], base_url); verify_empty_paired_residency(model["name"], base_url)
                        evidence = finish_paired_task_resource_guard(guard, model["name"], base_url, campaign_baseline=campaign_baseline)
                        if evidence.get("infrastructure_error") or evidence.get("watchdog_triggered"):
                            raise RuntimeError(evidence.get("infrastructure_error") or evidence.get("resource_pressure_reason") or "resource guard triggered")
                        samples = sampler.get_since(sample_start)
                        usage = _json_file(usage_file) if usage_file.exists() else {}
                        usage_file.unlink(missing_ok=True)
                        if not error and exit_code:
                            error = (stderr or usage.get("failure") or f"Hermes exit {exit_code}").strip()[:1000]
                        status = "timeout" if timed_out else ("ok" if exit_code == 0 and stdout.strip() else "error")
                        grading_started = time.monotonic(); grading = grade_task(task, status, stdout, skipped=False)
                        grading_wall = round(time.monotonic() - grading_started, 3)
                        row = {
                            **{key: metadata.get(key, "") for key in ("run_id","suite_version","host","host_label","platform","os_version","architecture","telemetry_backend","ollama_version","benchmark_profile","grading_profile","runner_sha256","recovery_runner_sha256","hermes_version","context_plan_sha256")},
                            "model":model["name"],"model_digest":model.get("digest", ""),"requested_num_ctx":model["requested_num_ctx"],"native_context_length":model.get("native_context_length") or model.get("model_context_length", ""),
                            "treatment_key":treatment.get("treatment_key", ""),"treatment_role":treatment.get("treatment_role", ""),"hermes_reasoning_requested":treatment["hermes_reasoning"],
                            "benchmark_family":task["family"],"category":task["category"],"task_id":task["id"],"task_name":task["name"],"status":status,"verdict":grading["verdict"],
                            "grader_type":grading.get("grader_type", ""),"grader_version":grading.get("grader_version", ""),"grader_tests_passed":grading.get("tests_passed", 0),"grader_tests_total":grading.get("tests_total", 0),"grader_error":str(grading.get("error") or "").replace("\n", " ")[:1000],"grading_wall_seconds":grading_wall,
                            "wall_seconds":wall,"timed_out":str(timed_out).lower(),"termination_reason":"timeout" if timed_out else ("completed" if status == "ok" else "error"),
                            "prompt_eval_count":usage.get("input_tokens", ""),"eval_count":usage.get("output_tokens", ""),"reasoning_tokens":usage.get("reasoning_tokens", ""),"total_token_count":usage.get("total_tokens", ""),"api_calls":usage.get("api_calls", ""),
                            "response_chars":len(stdout),"response_bytes":len(stdout.encode()),
                            "max_cpu_usage_pct":max_field(samples,"cpu_usage_pct"),"avg_cpu_usage_pct":avg_field(samples,"cpu_usage_pct"),"max_gpu_usage_pct":max_field(samples,"gpu_usage_pct"),"avg_gpu_usage_pct":avg_field(samples,"gpu_usage_pct"),"max_gpu_temp_c":max_field(samples,"gpu_temp_c"),"avg_gpu_temp_c":avg_field(samples,"gpu_temp_c"),"max_host_temp_c":max_field(samples,"host_temp_c"),"avg_host_temp_c":avg_field(samples,"host_temp_c"),"max_gpu_power_w":max_field(samples,"gpu_power_w"),"avg_gpu_power_w":avg_field(samples,"gpu_power_w"),"sample_count":len(samples),
                            "response_preview":stdout.replace("\n", " ")[:300],"exit_code":exit_code,"error":error,
                        }
                        writer.writerow(row); cf.flush(); rows.append(row)
                        jf.write(json.dumps({"metadata":metadata,"row":row,"grading":grading,"usage":usage,"assistant_text":stdout,"stderr":stderr,"telemetry_samples":samples,"resource_guard":evidence}, ensure_ascii=False) + "\n"); jf.flush()
                        print(f"  -> {status} {row['verdict']} grade={row['grader_tests_passed']}/{row['grader_tests_total']} wall={wall}s tokens={row['eval_count']} err={(row['grader_error'] or error)[:90]}", flush=True)
                        if grading["verdict"] == "grader_error":
                            raise RuntimeError(f"Grader failed for {model['name']} / {task['id']}")
    finally:
        sampler.stop()
        shutil.copy2(config_backup, config_path)
        config_backup.unlink(missing_ok=True)
    _summary(rows, md_path, metadata)
    print("DONE"); print("CSV:", csv_path); print("JSONL:", jsonl_path); print("MD:", md_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
