#!/usr/bin/env python3
"""Benchmark a locally hosted OpenAI-compatible inference server."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import signal
import subprocess
import threading
import time
import urllib.request
from pathlib import Path

from accuracy_grading import GRADING_PROFILE, grade_task
from benchmark_tests import DEFAULT_SUITE, SUITE_CHOICES, suite_task_catalog
from ollama_standardized_local_benchmarks import (
    CONTEXT_SWAP_GROWTH_LIMIT_BYTES,
    read_linux_resource_snapshot,
)
from platform_support import create_sampler


MIN_AVAILABLE_BYTES = 8 * 1024**3
FIELDS = [
    "run_id", "harness", "harness_version", "model_runner", "benchmark_profile",
    "grading_profile", "model", "model_digest", "task_id", "task_name", "category",
    "status", "verdict", "grader_type", "grader_tests_passed", "grader_tests_total",
    "grader_error", "wall_seconds", "exit_code", "timed_out", "response_chars",
    "response_sha256", "max_gpu_temp_c", "max_host_temp_c",
    "max_host_memory_used_bytes", "max_host_memory_pct", "max_gpu_usage_pct",
    "sample_count", "response_preview", "error",
]


def parse_args(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--suite", choices=SUITE_CHOICES, default=DEFAULT_SUITE)
    parser.add_argument("--endpoint", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--model-digest", required=True)
    parser.add_argument("--model-runner", required=True)
    parser.add_argument("--runner-version", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--server-pid", type=int, required=True)
    parser.add_argument("--stop-command", nargs="+")
    parser.add_argument("--timeout", type=int, default=1800)
    parser.add_argument("--startup-timeout", type=int, default=900)
    parser.add_argument("--tasks", nargs="*")
    parser.add_argument("--run", action="store_true")
    return parser.parse_args(argv)


def maximum(samples, field):
    values = [sample.get(field) for sample in samples if sample.get(field) is not None]
    return max(values) if values else ""


def is_descendant(pid, ancestor):
    seen = set()
    while pid > 1 and pid not in seen:
        if pid == ancestor:
            return True
        seen.add(pid)
        try:
            fields = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8").split()
            pid = int(fields[3])
        except (OSError, ValueError, IndexError):
            return False
    return False


def gpu_compute_pids():
    proc = subprocess.run(
        ["nvidia-smi", "--query-compute-apps=pid", "--format=csv,noheader,nounits"],
        text=True, capture_output=True, timeout=15, check=False,
    )
    if proc.returncode:
        raise RuntimeError("Unable to verify GPU compute exclusivity: " + proc.stderr.strip())
    return [int(line.strip()) for line in proc.stdout.splitlines() if line.strip().isdigit()]


class ServerResourceGuard:
    def __init__(self, baseline, server_pid, stop_command):
        self.baseline = baseline
        self.server_pid = server_pid
        self.stop_command = stop_command
        self.reason = ""
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def start(self):
        self._thread.start()
        return self

    def stop(self):
        self._stop.set()
        self._thread.join(timeout=10)
        if self._thread.is_alive() and not self.reason:
            self.reason = "Resource watchdog did not join"

    def _trip(self, reason):
        self.reason = reason
        if self.stop_command:
            subprocess.run(self.stop_command, capture_output=True, timeout=30, check=False)
        else:
            try:
                os.killpg(os.getpgid(self.server_pid), signal.SIGTERM)
            except ProcessLookupError:
                pass
        self._stop.set()

    def _run(self):
        while not self._stop.wait(0.5):
            try:
                snapshot = read_linux_resource_snapshot()
                if int(snapshot["oom_kill"]) != int(self.baseline["oom_kill"]):
                    self._trip("Kernel OOM counter changed")
                    return
                growth = int(snapshot["swap_used_bytes"]) - int(self.baseline["swap_used_bytes"])
                if growth > CONTEXT_SWAP_GROWTH_LIMIT_BYTES:
                    self._trip("Campaign-relative swap growth exceeded 1 GiB")
                    return
                if int(snapshot["mem_available_bytes"]) < MIN_AVAILABLE_BYTES:
                    self._trip("MemAvailable fell below 8 GiB")
                    return
                foreign = [pid for pid in gpu_compute_pids() if not is_descendant(pid, self.server_pid)]
                if foreign:
                    self._trip("Unrelated GPU compute process(es): " + ",".join(map(str, foreign)))
                    return
            except Exception as exc:
                self._trip("Resource watchdog infrastructure failure: " + repr(exc))
                return


def wait_for_server(endpoint, timeout=120, server_pid=None):
    models_url = endpoint.rstrip("/").removesuffix("/chat/completions") + "/models"
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if server_pid is not None and not Path(f"/proc/{server_pid}").exists():
            raise RuntimeError("OpenAI-compatible server exited during startup")
        try:
            with urllib.request.urlopen(models_url, timeout=5) as response:
                if 200 <= response.status < 300:
                    return
        except Exception:
            time.sleep(1)
    raise RuntimeError("OpenAI-compatible server did not become ready")


def request_command(endpoint, model, prompt, timeout):
    payload = json.dumps({
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "stream": False,
        "temperature": 0,
    })
    return [
        "curl", "--fail-with-body", "--silent", "--show-error", "--max-time", str(timeout),
        "-H", "Content-Type: application/json", "--data-binary", payload, endpoint,
    ]


def response_text(raw):
    payload = json.loads(raw)
    choices = payload.get("choices") or []
    if not choices:
        raise RuntimeError("Response did not contain choices")
    message = choices[0].get("message") or {}
    content = message.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(str(part.get("text") or "") for part in content if isinstance(part, dict))
    raise RuntimeError("Response did not contain text content")


def write_csv(path, records):
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=FIELDS)
        writer.writeheader()
        for record in records:
            writer.writerow({field: record["row"].get(field, "") for field in FIELDS})


def main(argv=None):
    args = parse_args(argv)
    if not args.run:
        raise SystemExit("Plan only. Pass --run to execute benchmark observations.")
    if args.timeout < 1 or args.timeout > 1800:
        raise SystemExit("--timeout must be between 1 and 1800 seconds")
    if not Path(f"/proc/{args.server_pid}").exists():
        raise RuntimeError("Server PID does not exist")
    if args.startup_timeout < 1 or args.startup_timeout > 1800:
        raise SystemExit("--startup-timeout must be between 1 and 1800 seconds")
    baseline = read_linux_resource_snapshot()
    startup_guard = ServerResourceGuard(baseline, args.server_pid, args.stop_command).start()
    try:
        wait_for_server(
            args.endpoint, timeout=args.startup_timeout, server_pid=args.server_pid
        )
    finally:
        startup_guard.stop()
    if startup_guard.reason:
        raise RuntimeError("Resource safety guard during model load: " + startup_guard.reason)
    tasks = [task for task in suite_task_catalog(args.suite) if not task.get("requires_image")]
    if args.tasks:
        selected = set(args.tasks)
        tasks = [task for task in tasks if task["id"] in selected]
        missing = selected - {task["id"] for task in tasks}
        if missing:
            raise RuntimeError("Unknown or image-only task(s): " + ", ".join(sorted(missing)))
    args.output_dir.mkdir(parents=True, exist_ok=True)
    stem = args.model_runner.lower().replace("-", "_").replace(".", "_")
    jsonl_path = args.output_dir / f"{stem}_core_text.jsonl"
    csv_path = args.output_dir / f"{stem}_core_text.csv"
    records = [json.loads(line) for line in jsonl_path.read_text(encoding="utf-8").splitlines()] if jsonl_path.exists() else []
    completed = {record["row"]["task_id"] for record in records}
    if len(completed) != len(records):
        raise RuntimeError("Existing evidence contains duplicate task keys")
    sampler = create_sampler("auto", interval_ms=1000)
    sampler.start()
    run_id = records[0]["row"]["run_id"] if records else time.strftime("%Y%m%d_%H%M%S")
    try:
        for task in tasks:
            if task["id"] in completed:
                continue
            print(f"[{len(completed)+1}/{len(tasks)}] {args.model_runner} {args.model} :: {task['id']}", flush=True)
            command = request_command(args.endpoint, args.model, task["prompt"], args.timeout)
            sample_start = sampler.snapshot_len()
            guard = ServerResourceGuard(baseline, args.server_pid, args.stop_command).start()
            started = time.monotonic()
            proc = subprocess.Popen(command, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
            timed_out = False
            try:
                while True:
                    try:
                        raw, stderr = proc.communicate(timeout=1)
                        break
                    except subprocess.TimeoutExpired:
                        if guard.reason:
                            os.killpg(proc.pid, signal.SIGTERM)
                            raw, stderr = proc.communicate()
                            break
                        if time.monotonic() - started >= args.timeout:
                            timed_out = True
                            os.killpg(proc.pid, signal.SIGTERM)
                            raw, stderr = proc.communicate()
                            break
            finally:
                guard.stop()
            wall = round(time.monotonic() - started, 3)
            samples = sampler.get_since(sample_start)
            error = guard.reason
            text = ""
            if not error and not timed_out and proc.returncode == 0:
                try:
                    text = response_text(raw)
                except Exception as exc:
                    error = repr(exc)
            if timed_out:
                error = f"Request timeout after {args.timeout}s"
            status = "timeout" if timed_out else ("ok" if not error and text else "error")
            if status == "error" and not error:
                error = (stderr or raw or f"curl exit {proc.returncode}").strip()
            grading = grade_task(task, status, text)
            row = {
                "run_id": run_id, "harness": "direct-openai-compatible",
                "harness_version": args.runner_version, "model_runner": args.model_runner,
                "benchmark_profile": "core-text-17-v1", "grading_profile": GRADING_PROFILE,
                "model": args.model, "model_digest": args.model_digest, "task_id": task["id"],
                "task_name": task["name"], "category": task["category"], "status": status,
                "verdict": grading["verdict"], "grader_type": grading.get("grader_type", ""),
                "grader_tests_passed": grading.get("tests_passed", 0),
                "grader_tests_total": grading.get("tests_total", 0),
                "grader_error": grading.get("error", "")[:2000], "wall_seconds": wall,
                "exit_code": 124 if timed_out else proc.returncode, "timed_out": str(timed_out).lower(),
                "response_chars": len(text), "response_sha256": hashlib.sha256(text.encode()).hexdigest(),
                "max_gpu_temp_c": maximum(samples, "gpu_temp_c"),
                "max_host_temp_c": maximum(samples, "host_temp_c"),
                "max_host_memory_used_bytes": maximum(samples, "host_memory_used_bytes"),
                "max_host_memory_pct": maximum(samples, "host_memory_pct"),
                "max_gpu_usage_pct": maximum(samples, "gpu_usage_pct"), "sample_count": len(samples),
                "response_preview": " ".join(text.split())[:300], "error": error[:2000],
            }
            record = {"row": row, "assistant_text": text, "raw_response": raw, "stderr": stderr,
                      "grading": grading, "telemetry_samples": samples, "command": command}
            with jsonl_path.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(record, ensure_ascii=False) + "\n")
            records.append(record)
            completed.add(task["id"])
            write_csv(csv_path, records)
            print(f"  -> {status} {grading['verdict']} wall={wall}s", flush=True)
            if guard.reason:
                raise RuntimeError("Resource safety guard: " + guard.reason)
    finally:
        sampler.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
