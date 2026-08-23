import contextlib
import csv
import hashlib
import importlib.util
import io
import json
import os
import pathlib
import socket
import sys
import tempfile
import unittest
from unittest import mock


ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import ollama_standardized_local_benchmarks as direct
import openclaw_18_test_benchmarks as openclaw
import platform_support as support
import accuracy_grading as grading
import thinking_pair_support as paired


def _fixture_linux_resource_snapshot(sampled=1.0):
    return {
        "mem_total_bytes":128*1024**3,"mem_available_bytes":120*1024**3,
        "swap_total_bytes":8*1024**3,"swap_free_bytes":8*1024**3,
        "swap_used_bytes":0,"oom_kill":0,"pswpout":0,
        "sampled_monotonic_seconds":float(sampled),
    }


def _fixture_task_guard(model, *_args, **_kwargs):
    watchdog=mock.Mock()
    baseline=_fixture_linux_resource_snapshot(2.0)
    parallelism=int(model.get("context_kv_parallelism") or 1)
    admission=direct.context_candidate_admission(
        {**model,"context_kv_parallelism":parallelism},
        int(model["requested_num_ctx"]),baseline,
        prior_attempts=model.get("context_calibration_attempts") or (),
    )
    return {
        "watchdog":watchdog,"baseline":baseline,"admission":admission,
        "parallelism":parallelism,
        "parallelism_source":model.get("context_kv_parallelism_source") or "",
        "system_page_size_bytes":direct.SYSTEM_PAGE_SIZE_BYTES,
        "fixture_campaign_baseline":_fixture_linux_resource_snapshot(1.0),
    }


def _fixture_task_guard_finish(guard, *_args, **_kwargs):
    campaign=guard["fixture_campaign_baseline"]
    task=guard["baseline"]
    recovery={**task,"sampled_monotonic_seconds":3.0}
    return {
        "system_page_size_bytes":direct.SYSTEM_PAGE_SIZE_BYTES,
        "context_kv_parallelism":guard["parallelism"],
        "context_kv_parallelism_source":guard["parallelism_source"],
        "memory_watchdog_ready_verified":True,"gpu_watchdog_ready_verified":True,
        "memory_watchdog_join_verified":True,"gpu_watchdog_join_verified":True,
        "memory_watchdog_error":"","gpu_watchdog_error":"",
        "watchdog_triggered":False,"resource_pressure_reason":"",
        "watchdog_trigger_seconds":"","watchdog_join_verified":True,
        "watchdog_target_stop_returned":"","memory_recovery_verified":True,
        "recovery_snapshot":recovery,"campaign_resource_baseline":campaign,
        "task_resource_baseline":task,"admission":guard["admission"],
        "mem_available_min_bytes":task["mem_available_bytes"],
        "swap_used_max_bytes":task["swap_used_bytes"],
        "oom_kill_before":task["oom_kill"],"oom_kill_after":recovery["oom_kill"],
        "pswpout_before":campaign["pswpout"],"pswpout_max":recovery["pswpout"],
        "pswpout_after":recovery["pswpout"],"infrastructure_error":"",
    }

DASHBOARD_PATH = ROOT / "dashboard" / "generate_local_llm_dashboard.py"
DASHBOARD_SPEC = importlib.util.spec_from_file_location("benchmark_dashboard", DASHBOARD_PATH)
dashboard = importlib.util.module_from_spec(DASHBOARD_SPEC)
DASHBOARD_SPEC.loader.exec_module(dashboard)


class FakeClock:
    def __init__(self, value=0.0):
        self.value = float(value)

    def __call__(self):
        return self.value


class FakeSocket:
    def __init__(self):
        self.timeouts = []

    def settimeout(self, value):
        self.timeouts.append(value)


class FakeStreamingResponse:
    def __init__(self, actions, status=200, reason="OK"):
        self.actions = list(actions)
        self.status = status
        self.reason = reason
        self.closed = False

    def readline(self, *args, **kwargs):
        if not self.actions:
            return b""
        action = self.actions.pop(0)
        if callable(action):
            action = action()
        if isinstance(action, BaseException):
            raise action
        return action

    def read(self, *args, **kwargs):
        chunks = []
        while self.actions:
            chunk = self.readline()
            if chunk:
                chunks.append(chunk)
        return b"".join(chunks)

    def close(self):
        self.closed = True

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        self.close()

    def __iter__(self):
        return self

    def __next__(self):
        line = self.readline()
        if not line:
            raise StopIteration
        return line


class FakeStreamingConnection:
    def __init__(self, response):
        self.response = response
        self.sock = FakeSocket()
        self.requests = []
        self.closed = False

    def request(self, method, path, body=None, headers=None):
        self.requests.append((method, path, body, headers or {}))

    def getresponse(self):
        return self.response

    def close(self):
        self.closed = True


def streaming_factory(response):
    created = []

    def factory(parts, timeout):
        connection = FakeStreamingConnection(response)
        connection.parts = parts
        connection.initial_timeout = timeout
        created.append(connection)
        return connection

    factory.created = created
    return factory


class PlatformSupportTests(unittest.TestCase):
    def test_nvidia_parser_handles_values_and_unavailable_fields(self):
        parsed = support.parse_nvidia_smi_line(
            "2026/08/14 15:12:18.209, 0, 87, 51, 42.75, 2405"
        )
        self.assertEqual(0, parsed["gpu_index"])
        self.assertEqual(87.0, parsed["gpu_usage_pct"])
        self.assertEqual(51.0, parsed["gpu_temp_c"])
        self.assertEqual(42.75, parsed["gpu_power_w"])
        unavailable = support.parse_nvidia_smi_line(
            "2026/08/14 15:12:19.209, 0, N/A, -, [N/A], N/A"
        )
        self.assertIsNone(unavailable["gpu_usage_pct"])
        self.assertIsNone(unavailable["gpu_temp_c"])
        self.assertIsNone(unavailable["gpu_power_w"])
        self.assertIsNone(support.parse_nvidia_smi_line("malformed"))

    def test_cpu_usage_uses_proc_stat_deltas(self):
        self.assertEqual(75.0, support.cpu_usage_percent((100, 40), (200, 65)))
        self.assertIsNone(support.cpu_usage_percent(None, (200, 65)))
        self.assertIsNone(support.cpu_usage_percent((100, 40), (100, 40)))

    def test_sampler_factory_selects_each_platform_and_falls_back(self):
        paths = {"mactop": "/opt/homebrew/bin/mactop", "nvidia-smi": "/usr/bin/nvidia-smi"}
        which = paths.get
        self.assertIsInstance(
            support.create_sampler("auto", system_name="Darwin", which=which),
            support.MactopSampler,
        )
        self.assertIsInstance(
            support.create_sampler("auto", system_name="Linux", which=which),
            support.NvidiaSmiSampler,
        )
        self.assertIsInstance(
            support.create_sampler("auto", system_name="FreeBSD", which=lambda _: None),
            support.NullSampler,
        )
        explicit = support.create_sampler(
            "nvidia-smi", system_name="Linux", which=lambda _: None
        )
        self.assertIsInstance(explicit, support.NullSampler)
        self.assertIn("not installed", explicit.error)

    def test_linux_dgx_system_specs_fixture(self):
        lscpu = json.dumps(
            {
                "lscpu": [
                    {"field": "Model name:", "data": "NVIDIA Grace CPU"},
                    {"field": "CPU(s):", "data": "20"},
                    {"field": "Core(s) per socket:", "data": "20"},
                    {"field": "Socket(s):", "data": "1"},
                ]
            }
        )

        def fake_read(path):
            value = str(path)
            if value.endswith("product_name"):
                return "NVIDIA_DGX_Spark"
            if value == "/proc/meminfo":
                return "MemTotal:       127600524 kB\n"
            return ""

        def fake_command(command, timeout=10):
            if command[:2] == ["lscpu", "-J"]:
                return lscpu
            if command and command[0] == "nvidia-smi":
                return "NVIDIA GB10, 580.173.02, 12.1"
            return ""

        disk = mock.Mock(free=2 * 1024**4, total=4 * 1024**4)
        with mock.patch.object(support.platform, "system", return_value="Linux"), \
             mock.patch.object(support.platform, "machine", return_value="aarch64"), \
             mock.patch.object(support.platform, "release", return_value="6.17.0-nvidia"), \
             mock.patch.object(support.platform, "node", return_value="arron-spark"), \
             mock.patch.object(support, "_read_text", side_effect=fake_read), \
             mock.patch.object(support, "_cmd_text", side_effect=fake_command), \
             mock.patch.object(support.shutil, "disk_usage", return_value=disk), \
             mock.patch.dict(os.environ, {"LLM_BENCHMARK_HOST_LABEL": ""}):
            specs = support.collect_system_specs()
            label = support.local_host_label()
        self.assertEqual("NVIDIA Grace CPU", specs["cpu"])
        self.assertEqual("NVIDIA GB10", specs["gpu"])
        self.assertEqual("Unified memory", specs["memory_label"])
        self.assertEqual("aarch64", specs["architecture"])
        self.assertEqual("NVIDIA DGX Spark", label)


class PairedThinkingPlannerTests(unittest.TestCase):
    MODELS = [
        {
            "name": "qwen-thinking:7b",
            "digest": "sha256:qwen",
            "family": "qwen",
            "capabilities": ["completion", "thinking"],
            "capabilities_known": True,
        },
        {
            "name": "hf.co/example/qwen-thinking:7b",
            "digest": "sha256:qwen",
            "family": "qwen",
            "capabilities": ["completion", "thinking"],
            "capabilities_known": True,
        },
        {
            "name": "gpt-oss:20b",
            "digest": "sha256:gpt-oss",
            "family": "gptoss",
            "capabilities": ["completion", "thinking"],
            "capabilities_known": True,
        },
        {
            "name": "plain:latest",
            "digest": "sha256:plain",
            "family": "plain",
            "capabilities": ["completion"],
            "capabilities_known": True,
        },
    ]
    TASKS = [
        {"id": "task_a", "prompt": "A"},
        {"id": "task_b", "prompt": "B"},
        {"id": "vision", "prompt": "read", "requires_image": True},
    ]

    def build_plan(self, **overrides):
        arguments = {
            "num_ctx": 8192,
            "timeout": 1800,
            "ollama_version": "0.test",
            "ollama_url": "http://fixture:11434",
            "host": "fixture",
            "host_label": "Fixture Host",
            "platform": "linux",
            "os_version": "Fixture Linux",
            "architecture": "aarch64",
            "telemetry_backend": "none",
            "telemetry_interval_ms": 1000,
            "no_stop": False,
            "keep_alive": "0s",
            "residency_policy": "cold-unload-every-task",
            "suite_version": "test",
            "benchmark_profile": direct.BENCHMARK_PROFILE,
            "grading_profile": grading.GRADING_PROFILE,
            "output_token_policy": direct.OUTPUT_TOKEN_POLICY,
            "runner_sha256": "runner-fixture",
            "grader_sha256": "grader-fixture",
            "experiment_id": "paired-fixture",
            "campaign_seed": 42,
        }
        arguments.update(overrides)
        return paired.build_paired_plan(self.MODELS, self.TASKS, **arguments)

    def test_planner_deduplicates_digest_aliases_and_excludes_nonthinking_models(self):
        plan = self.build_plan()

        self.assertEqual(
            ["gpt-oss:20b", "qwen-thinking:7b"],
            [model["name"] for model in plan["models"]],
        )
        qwen = next(model for model in plan["models"] if model["name"] == "qwen-thinking:7b")
        self.assertEqual(
            ["hf.co/example/qwen-thinking:7b", "qwen-thinking:7b"],
            qwen["aliases"],
        )
        self.assertEqual(["plain:latest"], plan["excluded_non_thinking"])

    def test_planner_uses_boolean_off_on_and_gpt_oss_low_high_never_off(self):
        plan = self.build_plan()
        by_name = {model["name"]: model for model in plan["models"]}

        qwen = by_name["qwen-thinking:7b"]["treatments"]
        self.assertEqual(["thinking-off", "thinking-on"], [item["treatment_key"] for item in qwen])
        self.assertEqual([False, True], [item["think_value"] for item in qwen])
        self.assertTrue(all(type(item["think_value"]) is bool for item in qwen))
        self.assertEqual(["false", "true"], [item["think_payload_json"] for item in qwen])
        self.assertTrue(all(item["off_available"] is True for item in qwen))

        gpt = by_name["gpt-oss:20b"]["treatments"]
        self.assertEqual(["thinking-low", "thinking-high"], [item["treatment_key"] for item in gpt])
        self.assertEqual(["low", "high"], [item["think_value"] for item in gpt])
        self.assertEqual(["low", "high"], [item["think_payload_json"].strip('"') for item in gpt])
        self.assertTrue(all(item["off_available"] is False for item in gpt))
        self.assertNotIn(False, [item["think_value"] for item in gpt])
        self.assertNotIn("thinking-off", [item["treatment_key"] for item in gpt])

    def test_planner_ids_are_unique_and_work_order_alternates_ab_ba(self):
        plan = self.build_plan()
        items = paired.ordered_work_items(plan, self.TASKS)

        treatment_ids = [
            treatment["treatment_id"]
            for model in plan["models"]
            for treatment in model["treatments"]
        ]
        self.assertEqual(len(treatment_ids), len(set(treatment_ids)))
        self.assertEqual(2, len({model["pair_id"] for model in plan["models"]}))
        self.assertEqual(len(items), len({item["row_id"] for item in items}))
        self.assertTrue(all(item["attempt"] == 1 for item in items))
        self.assertTrue(all(item["plan_sha256"] == plan["plan_sha256"] for item in items))
        self.assertTrue(all(item["pair_schema_version"] == paired.PAIR_SCHEMA_VERSION for item in items))

        for model in plan["models"]:
            grouped = []
            for task_id in plan["task_ids"]:
                grouped.append([
                    item["treatment"]["treatment_key"]
                    for item in items
                    if item["model"]["pair_id"] == model["pair_id"]
                    and item["task"]["id"] == task_id
                ])
            expected_ab = [item["treatment_key"] for item in model["treatments"]]
            expected_ba = list(reversed(expected_ab))
            first = expected_ba if (plan["campaign_seed"] + model["model_index"]) % 2 else expected_ab
            self.assertEqual(first, grouped[0])
            self.assertEqual(list(reversed(first)), grouped[1])
            self.assertEqual(first, grouped[2])

    def test_planned_counts_distinguish_rows_inferences_and_capability_skips(self):
        plan = self.build_plan()
        self.assertEqual(
            {"rows": 12, "inference_calls": 8, "capability_skips": 4},
            paired.planned_counts(plan, self.TASKS),
        )

    def test_paired_plan_requires_an_explicit_positive_context(self):
        for invalid in (0, None):
            with self.subTest(num_ctx=invalid), self.assertRaisesRegex(ValueError, "num_ctx"):
                self.build_plan(num_ctx=invalid)

    def test_run_task_sends_exact_paired_payloads(self):
        streamed = {
            "status": "ok", "text": "OK", "thinking": "", "raw": {},
            "error": "", "wall": 1.0, "timed_out": False, "done": True,
            "done_reason": "stop", "termination_reason": "stop",
            "stream_chunk_count": 1,
        }
        task = {"id": "fixture", "prompt": "Fixture prompt"}
        expected_options = {
            "temperature": 0, "seed": 42, "num_predict": -1, "num_ctx": 8192,
        }
        cases = [
            (
                self.MODELS[0],
                paired.treatments_for_model(self.MODELS[0]),
                [False, True],
            ),
            (
                self.MODELS[2],
                paired.treatments_for_model(self.MODELS[2]),
                ["low", "high"],
            ),
        ]
        for model, treatments, expected_think_values in cases:
            with self.subTest(model=model["name"]), \
                 mock.patch.object(direct, "stream_generate", return_value=streamed) as generate:
                for treatment in treatments:
                    direct.run_task(
                        model, task, 30, base_url="http://fixture:11434",
                        num_ctx=8192, treatment=treatment,
                    )
                payloads = [call.args[1] for call in generate.call_args_list]
            self.assertEqual(expected_think_values, [payload["think"] for payload in payloads])
            for payload, expected_think in zip(payloads, expected_think_values):
                self.assertEqual({
                    "model": model["name"],
                    "prompt": "Fixture prompt",
                    "stream": True,
                    "options": expected_options,
                    "keep_alive": "0s",
                    "think": expected_think,
                }, payload)


class ExecutionGuardTests(unittest.TestCase):
    def setUp(self):
        identity = mock.patch.object(
            direct, "verify_paired_runtime_identity", return_value=True
        )
        identity.start()
        self.addCleanup(identity.stop)
        endpoint = mock.patch.object(
            direct, "require_local_paired_endpoint", return_value=True
        )
        endpoint.start()
        self.addCleanup(endpoint.stop)
        watchdog = mock.Mock()
        start_guard = mock.patch.object(
            direct, "start_paired_task_resource_guard",
            side_effect=_fixture_task_guard,
        )
        finish_guard = mock.patch.object(
            direct, "finish_paired_task_resource_guard",
            side_effect=_fixture_task_guard_finish,
        )
        start_guard.start(); finish_guard.start()
        self.addCleanup(start_guard.stop); self.addCleanup(finish_guard.stop)

    METADATA = {
        "suite_version": "test",
        "host": "fixture",
        "host_label": "Fixture Host",
        "platform": "linux",
        "os_version": "Fixture Linux",
        "architecture": "aarch64",
        "telemetry_backend": "none",
        "ollama_version": "test",
        "started_at": "now",
    }

    @staticmethod
    def _resume_model():
        return {
            "name": "qwen-thinking:7b", "digest": "sha256:qwen",
            "family": "qwen", "params": "7B", "quant": "Q4",
            "capabilities": ["completion", "thinking"],
            "capabilities_known": True, "context_length": 8192,
            "size":1024**3,
            "model_info":{
                "fixture.context_length":8192,"fixture.block_count":8,
                "fixture.embedding_length":1024,"fixture.attention.head_count":8,
                "fixture.attention.head_count_kv":2,
            },
        }

    def _write_resume_plan(self, directory, *, limit_tasks=1):
        directory = pathlib.Path(directory)
        tasks = direct.TASKS[:limit_tasks]
        model = self._resume_model()
        runner_sha256 = hashlib.sha256(pathlib.Path(direct.__file__).read_bytes()).hexdigest()
        grader_sha256 = hashlib.sha256(
            pathlib.Path(direct.grade_task.__code__.co_filename).read_bytes()
        ).hexdigest()
        planner_sha256 = hashlib.sha256(pathlib.Path(paired.__file__).read_bytes()).hexdigest()
        plan = paired.build_paired_plan(
            [model], tasks, num_ctx=8192, timeout=1800,
            ollama_version=self.METADATA["ollama_version"],
            ollama_url="http://fixture:11434",
            host=self.METADATA["host"], host_label=self.METADATA["host_label"],
            platform=self.METADATA["platform"], os_version=self.METADATA["os_version"],
            architecture=self.METADATA["architecture"], telemetry_backend="none",
            telemetry_interval_ms=1000, no_stop=True, keep_alive=None,
            residency_policy="warm-runtime-default", suite_version=self.METADATA["suite_version"],
            benchmark_profile=direct.BENCHMARK_PROFILE, grading_profile=grading.GRADING_PROFILE,
            output_token_policy=direct.OUTPUT_TOKEN_POLICY,
            runner_sha256=runner_sha256, grader_sha256=grader_sha256,
            planner_sha256=planner_sha256,
            experiment_id="resume-experiment-fixture", campaign_seed=42,
        )
        prefix = "ollama_standardized_local_benchmark_original"
        plan["run_id"] = "original-run-id"
        plan["report_prefix"] = prefix
        plan_path = directory / f"{prefix}.plan.json"
        plan_path.write_text(json.dumps(plan, indent=2) + "\n", encoding="utf-8")
        return model, tasks, plan, plan_path

    @staticmethod
    def _validation_arguments(plan):
        return {
            field: plan[field]
            for field in (
                "num_ctx", "timeout", "ollama_version", "ollama_url", "host", "host_label",
                "platform", "os_version", "architecture", "telemetry_backend",
                "telemetry_interval_ms", "no_stop", "keep_alive", "residency_policy",
                "suite_version", "benchmark_profile", "grading_profile", "output_token_policy",
                "runner_sha256", "grader_sha256", "planner_sha256",
            )
        }

    @staticmethod
    def _canonical_record(plan, work):
        response = "BENCH_OK"
        canonical_grading = grading.grade_task(work["task"], "ok", response)
        row = direct.expected_paired_row_provenance(work, plan)
        resource_guard = _fixture_task_guard_finish(
            _fixture_task_guard(work["model"])
        )
        row.update({
            "protocol_valid": "true", "protocol_error": "",
            "status": "ok",
            "verdict": canonical_grading["verdict"],
            "wall_seconds": "1.0",
            "timed_out": "false",
            "done": "true",
            "done_reason": "stop",
            "termination_reason": "stop",
            "response_chars": str(len(response)),
            "response_bytes": str(len(response.encode("utf-8"))),
            "eval_count": "1",
            "thinking_chars": "0",
            "thinking_bytes": "0",
            "grader_type": canonical_grading["grader_type"],
            "grader_version": canonical_grading["grader_version"],
            "grader_tests_passed": str(canonical_grading["tests_passed"]),
            "grader_tests_total": str(canonical_grading["tests_total"]),
            "grader_error": canonical_grading["error"],
            "error": "",
            "resource_guard_json": json.dumps(
                resource_guard, separators=(",", ":"), sort_keys=True
            ),
        })
        metadata_fields = (
            "run_id", "experiment_id", "plan_sha256", "pair_schema_version", "campaign_seed",
            "suite_version", "host", "host_label", "platform", "os_version", "architecture",
            "telemetry_backend", "telemetry_interval_ms", "ollama_version", "ollama_url",
            "residency_policy", "keep_alive_request", "stop_before_task",
            "benchmark_profile", "grading_profile", "runner_sha256", "grader_sha256",
            "planner_sha256", "output_token_policy", "output_token_limit",
            "response_timeout_seconds", "context_policy", "requested_num_ctx",
        )
        return {
            "metadata": {field: row[field] for field in metadata_fields},
            "row": row,
            "grading": canonical_grading,
            "raw": {
                "response": response, "thinking": "", "done": True,
                "done_reason": "stop", "eval_count": 1,
            },
            "response": response, "thinking": "", "telemetry_samples": [],
            "resource_guard": resource_guard,
        }

    @staticmethod
    def _successful_paired_result(_model, _task, _timeout, _base_url, *, treatment, **_kwargs):
        is_on = treatment["treatment_role"] in {"on", "maximum"}
        thinking = "reasoning trace" if is_on else ""
        return {
            "status": "ok", "text": "BENCH_OK", "thinking": thinking,
            "raw": {"prompt_eval_count": 4, "eval_count": 4,
                    "eval_duration": 1_000_000_000, "done_reason": "stop"},
            "error": "", "wall": 1.0, "timed_out": False, "done": True,
            "done_reason": "stop", "termination_reason": "stop",
            "stream_chunk_count": 2, "time_to_first_output_seconds": 0.5,
            "time_to_first_answer_seconds": 0.5, "response_chars": 8,
            "response_bytes": 8, "thinking_chars": len(thinking),
            "thinking_bytes": len(thinking.encode("utf-8")),
            "thinking_capable": True,
            "thinking_requested": treatment["thinking_requested"],
            "thinking_resolved": treatment["thinking_resolved"],
            "thinking_effective": treatment["thinking_effective"],
            "thinking_used": is_on,
        }

    def test_direct_defaults_to_plan_only(self):
        model = {
            "name": "fixture:latest",
            "digest": "sha256:test",
            "params": "1B",
            "quant": "Q4",
            "capabilities": ["completion"],
        }
        sampler = mock.Mock()
        sampler.backend = "none"
        sampler.description = "fixture"
        with mock.patch.object(direct, "load_models", return_value=[model]), \
             mock.patch.object(direct, "create_sampler", return_value=sampler), \
             mock.patch.object(direct, "run_metadata", return_value=dict(self.METADATA)), \
             mock.patch.object(direct, "run_task") as run_task, \
             mock.patch.object(direct, "stop_model") as stop_model, \
             mock.patch.object(pathlib.Path, "mkdir") as mkdir, \
             contextlib.redirect_stdout(io.StringIO()) as output:
            result = direct.main(["--models", "fixture:latest", "--limit-tasks", "1"])
        self.assertEqual(0, result)
        self.assertIn("PLAN ONLY", output.getvalue())
        run_task.assert_not_called()
        stop_model.assert_not_called()
        sampler.start.assert_not_called()
        sampler.stop.assert_not_called()
        mkdir.assert_not_called()

    def test_direct_list_tasks_has_no_ollama_or_filesystem_side_effects(self):
        with mock.patch.object(direct, "load_models") as load_models, \
             mock.patch.object(pathlib.Path, "mkdir") as mkdir, \
             contextlib.redirect_stdout(io.StringIO()) as output:
            result = direct.main(["--list-tasks"])
        self.assertEqual(0, result)
        self.assertIn("exact_reply", output.getvalue())
        load_models.assert_not_called()
        mkdir.assert_not_called()

    def test_paired_cli_requires_context_before_network_or_filesystem_access(self):
        with mock.patch.object(direct, "create_sampler") as create_sampler, \
             mock.patch.object(direct, "load_models") as load_models, \
             mock.patch.object(pathlib.Path, "mkdir") as mkdir, \
             contextlib.redirect_stderr(io.StringIO()), \
             self.assertRaises(SystemExit):
            direct.main(["--thinking", "paired", "--dry-run"])
        create_sampler.assert_not_called()
        load_models.assert_not_called()
        mkdir.assert_not_called()

    def test_paired_cli_rejects_conflicting_compatibility_think_alias(self):
        with mock.patch.object(direct, "create_sampler") as create_sampler, \
             mock.patch.object(direct, "load_models") as load_models, \
             contextlib.redirect_stderr(io.StringIO()), \
             self.assertRaises(SystemExit):
            direct.main([
                "--thinking", "paired", "--think", "--num-ctx", "8192", "--run",
            ])
        create_sampler.assert_not_called()
        load_models.assert_not_called()

    def test_paired_dry_run_has_zero_mutations_and_reports_exact_plan_counts(self):
        models = PairedThinkingPlannerTests.MODELS
        sampler = mock.Mock()
        sampler.backend = "none"
        sampler.description = "fixture"
        with tempfile.TemporaryDirectory() as tmp:
            output_dir = pathlib.Path(tmp) / "reports-must-not-exist"
            with mock.patch.object(direct, "load_models", return_value=models), \
                 mock.patch.object(direct, "create_sampler", return_value=sampler), \
                 mock.patch.object(direct, "run_metadata", return_value=dict(self.METADATA)), \
                 mock.patch.object(direct, "run_task") as run_task, \
                 mock.patch.object(direct, "stream_generate") as stream_generate, \
                 mock.patch.object(direct, "stop_model") as stop_model, \
                 contextlib.redirect_stdout(io.StringIO()) as output:
                result = direct.main([
                    "--thinking", "paired", "--num-ctx", "8192",
                    "--output-dir", str(output_dir),
                    "--dry-run",
                ])
            self.assertFalse(output_dir.exists())

        self.assertEqual(0, result)
        text_output = output.getvalue()
        self.assertIn("Paired experiment:", text_output)
        self.assertIn("Models: 2", text_output)
        self.assertIn("Planned report rows: 72 (68 inference calls; 4 capability skips)", text_output)
        self.assertIn("Excluded non-thinking tags: plain:latest", text_output)
        self.assertIn("hf.co/example/qwen-thinking:7b", text_output)
        self.assertIn("PLAN ONLY", text_output)
        run_task.assert_not_called()
        stream_generate.assert_not_called()
        stop_model.assert_not_called()
        sampler.start.assert_not_called()
        sampler.stop.assert_not_called()

    def test_legacy_single_mode_plan_keeps_aliases_and_nonthinking_models(self):
        models = PairedThinkingPlannerTests.MODELS
        sampler = mock.Mock()
        sampler.backend = "none"
        sampler.description = "fixture"
        with mock.patch.object(direct, "load_models", return_value=models), \
             mock.patch.object(direct, "create_sampler", return_value=sampler), \
             mock.patch.object(direct, "run_metadata", return_value=dict(self.METADATA)), \
             mock.patch.object(direct, "run_task") as run_task, \
             mock.patch.object(direct, "stop_model") as stop_model, \
             contextlib.redirect_stdout(io.StringIO()) as output:
            result = direct.main([
                "--thinking", "off", "--limit-tasks", "1", "--dry-run",
            ])

        self.assertEqual(0, result)
        text_output = output.getvalue()
        self.assertIn("Models: 4", text_output)
        self.assertIn("Context request: runtime/model default", text_output)
        self.assertNotIn("Paired experiment:", text_output)
        self.assertNotIn("Planned report rows:", text_output)
        run_task.assert_not_called()
        stop_model.assert_not_called()
        sampler.start.assert_not_called()
        sampler.stop.assert_not_called()

    def test_direct_tasks_do_not_define_per_task_output_limits(self):
        limited = [task["id"] for task in direct.TASKS if "num_predict" in task]
        self.assertEqual([], limited)

    def test_direct_payload_uses_unlimited_generation_and_max_thinking_when_supported(self):
        model = {
            "name": "fixture:latest",
            "capabilities": ["completion", "thinking"],
        }
        task = {"prompt": "Reply exactly: OK"}
        streamed = {
            "status": "ok", "text": "OK", "thinking": "reasoning",
            "raw": {"eval_count": 2, "eval_duration": 1}, "error": "",
            "wall": 1.0, "timed_out": False, "done": True,
            "done_reason": "stop", "termination_reason": "stop",
            "stream_chunk_count": 3,
        }
        with mock.patch.object(direct, "stream_generate", return_value=streamed) as generate:
            result = direct.run_task(model, task, 10)
        self.assertEqual("OK", result["text"])
        _, payload, timeout = generate.call_args.args
        self.assertEqual(10, timeout)
        self.assertIs(True, payload["stream"])
        self.assertEqual(-1, payload["options"]["num_predict"])
        self.assertEqual("max", payload["think"])

    def test_direct_auto_thinking_omits_think_for_unsupported_model(self):
        model = {"name": "fixture:latest", "capabilities": ["completion"]}
        task = {"prompt": "Reply exactly: OK"}
        streamed = {
            "status": "ok", "text": "OK", "thinking": "", "raw": {},
            "error": "", "wall": 1.0, "timed_out": False, "done": True,
            "done_reason": "stop", "termination_reason": "stop",
            "stream_chunk_count": 2,
        }
        with mock.patch.object(direct, "stream_generate", return_value=streamed) as generate:
            direct.run_task(model, task, 10)
        payload = generate.call_args.args[1]
        self.assertNotIn("think", payload)

    def test_direct_gpt_oss_uses_high_instead_of_unsupported_max(self):
        model = {
            "name": "gpt-oss:20b", "family": "gptoss",
            "capabilities": ["completion", "thinking"],
        }
        streamed = {
            "status": "ok", "text": "OK", "thinking": "reasoning", "raw": {},
            "error": "", "wall": 1.0, "timed_out": False, "done": True,
            "done_reason": "stop", "termination_reason": "stop",
            "stream_chunk_count": 2,
        }
        with mock.patch.object(direct, "stream_generate", return_value=streamed) as generate:
            result = direct.run_task(model, {"prompt": "Reply exactly: OK"}, 10)
        self.assertEqual("high", generate.call_args.args[1]["think"])
        self.assertEqual("high", result["thinking_effective"])

    def test_direct_warm_run_omits_forced_unload(self):
        model = {"name": "fixture:latest", "capabilities": ["completion"]}
        streamed = {
            "status": "ok", "text": "OK", "thinking": "", "raw": {},
            "error": "", "wall": 1.0, "timed_out": False, "done": True,
            "done_reason": "stop", "termination_reason": "stop",
            "stream_chunk_count": 2,
        }
        with mock.patch.object(direct, "stream_generate", return_value=streamed) as generate:
            direct.run_task(
                model, {"prompt": "Reply exactly: OK"}, 10, keep_alive=None
            )
        self.assertNotIn("keep_alive", generate.call_args.args[1])

    def test_direct_explicit_context_is_sent_without_changing_default_behavior(self):
        model = {"name": "fixture:latest", "capabilities": ["completion"]}
        streamed = {
            "status": "ok", "text": "OK", "thinking": "", "raw": {},
            "error": "", "wall": 1.0, "timed_out": False, "done": True,
            "done_reason": "stop", "termination_reason": "stop",
            "stream_chunk_count": 2,
        }
        with mock.patch.object(direct, "stream_generate", return_value=streamed) as generate:
            direct.run_task(model, {"prompt": "Reply exactly: OK"}, 10, num_ctx=262144)
        self.assertEqual(262144, generate.call_args.args[1]["options"]["num_ctx"])

        with mock.patch.object(direct, "stream_generate", return_value=streamed) as generate:
            direct.run_task(model, {"prompt": "Reply exactly: OK"}, 10)
        self.assertNotIn("num_ctx", generate.call_args.args[1]["options"])

    def test_direct_run_aborts_when_capabilities_are_unknown(self):
        model = {
            "name": "fixture:latest", "digest": "sha256:test",
            "params": "1B", "quant": "Q4", "capabilities": [],
            "capabilities_known": False, "capability_error": "fixture show failure",
        }
        sampler = mock.Mock()
        sampler.backend = "none"
        sampler.description = "fixture"
        with mock.patch.object(direct, "load_models", return_value=[model]), \
             mock.patch.object(direct, "create_sampler", return_value=sampler), \
             mock.patch.object(direct, "run_metadata", return_value=dict(self.METADATA)), \
             mock.patch.object(direct, "run_task") as run_task, \
             mock.patch.object(pathlib.Path, "mkdir") as mkdir, \
             contextlib.redirect_stdout(io.StringIO()), \
             self.assertRaisesRegex(RuntimeError, "capabilities could not be verified"):
            direct.main(["--models", "fixture:latest", "--limit-tasks", "1", "--run"])
        run_task.assert_not_called()
        sampler.start.assert_not_called()
        mkdir.assert_not_called()

    def test_direct_timeout_cli_is_bounded_to_thirty_minutes(self):
        for invalid in ("0", "1801"):
            with self.subTest(timeout=invalid), \
                 contextlib.redirect_stderr(io.StringIO()), \
                 self.assertRaises(SystemExit):
                direct.main(["--timeout", invalid, "--list-tasks"])
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(0, direct.main(["--timeout", "1", "--list-tasks"]))
            self.assertEqual(0, direct.main(["--timeout", "1800", "--list-tasks"]))

    def test_direct_report_records_context_source_and_grader_provenance(self):
        model = {
            "name": "fixture:latest", "digest": "sha256:test", "family": "fixture",
            "params": "1B", "quant": "Q4", "capabilities": ["completion"],
            "capabilities_known": True, "context_length": 262144,
        }
        sampler = mock.Mock()
        sampler.backend = "none"
        sampler.description = "fixture"
        sampler.error = ""
        sampler.snapshot_len.return_value = 0
        sampler.get_since.return_value = []
        result = {
            "status": "ok", "text": "BENCH_OK", "thinking": "", "raw": {
                "prompt_eval_count": 4, "eval_count": 4,
                "eval_duration": 1_000_000_000, "done_reason": "stop",
            },
            "error": "", "wall": 1.0, "timed_out": False, "done": True,
            "done_reason": "stop", "termination_reason": "stop",
            "stream_chunk_count": 2, "time_to_first_output_seconds": 0.5,
            "time_to_first_answer_seconds": 0.5, "response_chars": 8,
            "response_bytes": 8, "thinking_chars": 0, "thinking_bytes": 0,
            "thinking_capable": False, "thinking_requested": "off",
            "thinking_resolved": "unsupported", "thinking_effective": "unsupported",
            "thinking_used": False,
        }
        with tempfile.TemporaryDirectory() as tmp, \
             mock.patch.object(direct, "load_models", return_value=[model]), \
             mock.patch.object(direct, "create_sampler", return_value=sampler), \
             mock.patch.object(direct, "run_metadata", return_value=dict(self.METADATA)), \
             mock.patch.object(direct, "run_task", return_value=result) as run_task, \
             mock.patch.object(direct, "stop_model", return_value=True), \
             contextlib.redirect_stdout(io.StringIO()):
            exit_code = direct.main([
                "--models", "fixture:latest", "--limit-tasks", "1",
                "--thinking", "off", "--num-ctx", "262144",
                "--output-dir", tmp, "--run",
            ])
            csv_path = next(pathlib.Path(tmp).glob("*.csv"))
            jsonl_path = next(pathlib.Path(tmp).glob("*.jsonl"))
            with csv_path.open(newline="", encoding="utf-8") as handle:
                row = next(csv.DictReader(handle))
            record = json.loads(jsonl_path.read_text(encoding="utf-8"))
        self.assertEqual(0, exit_code)
        self.assertEqual("262144", row["requested_num_ctx"])
        self.assertEqual("262144", row["model_context_length"])
        self.assertEqual("behavioral-v1", row["grading_profile"])
        self.assertEqual("exact_text", row["grader_type"])
        self.assertEqual("pass", row["verdict"])
        self.assertTrue(row["runner_sha256"])
        self.assertTrue(row["grader_sha256"])
        self.assertEqual("", row["experiment_id"])
        self.assertEqual("", row["treatment_id"])
        self.assertEqual("", row["row_id"])
        self.assertIn("grading", record)
        self.assertEqual(262144, run_task.call_args.kwargs["num_ctx"])
        self.assertIsNone(run_task.call_args.kwargs["treatment"])

    def test_paired_report_records_unique_ids_exact_payloads_and_protocol_flags(self):
        model = {
            "name": "qwen-thinking:7b", "digest": "sha256:qwen",
            "family": "qwen", "params": "7B", "quant": "Q4",
            "capabilities": ["completion", "thinking"],
            "capabilities_known": True, "context_length": 8192,
        }
        sampler = mock.Mock()
        sampler.backend = "none"
        sampler.description = "fixture"
        sampler.error = ""
        sampler.snapshot_len.return_value = 0
        sampler.get_since.return_value = []

        def fake_result(_model, _task, _timeout, _base_url, *, treatment, **_kwargs):
            is_on = treatment["treatment_role"] == "on"
            thinking = "reasoning trace" if is_on else ""
            return {
                "status": "ok", "text": "BENCH_OK", "thinking": thinking,
                "raw": {"prompt_eval_count": 4, "eval_count": 4,
                        "eval_duration": 1_000_000_000, "done_reason": "stop"},
                "error": "", "wall": 1.0, "timed_out": False, "done": True,
                "done_reason": "stop", "termination_reason": "stop",
                "stream_chunk_count": 2, "time_to_first_output_seconds": 0.5,
                "time_to_first_answer_seconds": 0.5, "response_chars": 8,
                "response_bytes": 8, "thinking_chars": len(thinking),
                "thinking_bytes": len(thinking.encode("utf-8")),
                "thinking_capable": True,
                "thinking_requested": treatment["thinking_requested"],
                "thinking_resolved": treatment["thinking_resolved"],
                "thinking_effective": treatment["thinking_effective"],
                "thinking_used": is_on,
            }

        with tempfile.TemporaryDirectory() as tmp, \
             mock.patch.object(direct, "load_models", return_value=[model]), \
             mock.patch.object(direct, "create_sampler", return_value=sampler), \
             mock.patch.object(direct, "run_metadata", return_value=dict(self.METADATA)), \
             mock.patch.object(direct, "run_task", side_effect=fake_result) as run_task, \
             mock.patch.object(direct, "stop_model") as stop_model, \
             contextlib.redirect_stdout(io.StringIO()):
            exit_code = direct.main([
                "--thinking", "paired", "--num-ctx", "8192",
                "--limit-tasks", "1", "--no-stop",
                "--output-dir", tmp, "--run",
            ])
            csv_path = next(pathlib.Path(tmp).glob("*.csv"))
            plan_path = next(pathlib.Path(tmp).glob("*.plan.json"))
            with csv_path.open(newline="", encoding="utf-8") as handle:
                rows = list(csv.DictReader(handle))
            plan = json.loads(plan_path.read_text(encoding="utf-8"))

        self.assertEqual(0, exit_code)
        self.assertEqual(2, run_task.call_count)
        stop_model.assert_not_called()
        self.assertEqual(2, len(rows))
        self.assertEqual(2, len({row["row_id"] for row in rows}))
        self.assertEqual(2, len({row["treatment_id"] for row in rows}))
        self.assertEqual(1, len({row["pair_id"] for row in rows}))
        self.assertEqual(1, len({row["experiment_id"] for row in rows}))
        self.assertEqual(plan["plan_sha256"], rows[0]["plan_sha256"])
        rows_by_treatment = {row["treatment_key"]: row for row in rows}
        self.assertEqual({"thinking-off", "thinking-on"}, set(rows_by_treatment))
        self.assertEqual("false", rows_by_treatment["thinking-off"]["think_payload_json"])
        self.assertEqual("true", rows_by_treatment["thinking-on"]["think_payload_json"])
        self.assertEqual({"1", "2"}, {row["treatment_order"] for row in rows})
        self.assertTrue(all(row["pair_schema_version"] == str(paired.PAIR_SCHEMA_VERSION) for row in rows))
        self.assertTrue(all(row["attempt"] == "1" for row in rows))
        self.assertTrue(all(row["off_available"] == "true" for row in rows))
        self.assertTrue(all(row["think_field_present"] == "true" for row in rows))
        self.assertTrue(all(row["protocol_valid"] == "true" for row in rows))
        self.assertTrue(all(row["protocol_error"] == "" for row in rows))
        self.assertTrue(all(row["requested_num_ctx"] == "8192" for row in rows))
        sent_treatments = [call.kwargs["treatment"] for call in run_task.call_args_list]
        sent_by_role = {item["treatment_role"]: item["think_value"] for item in sent_treatments}
        self.assertEqual({"off": False, "on": True}, sent_by_role)
        self.assertTrue(all(call.kwargs["num_ctx"] == 8192 for call in run_task.call_args_list))

    def test_paired_disabled_arm_reasoning_trace_invalidates_protocol(self):
        model = {
            "name": "qwen-thinking:7b", "digest": "sha256:qwen",
            "family": "qwen", "params": "7B", "quant": "Q4",
            "capabilities": ["completion", "thinking"],
            "capabilities_known": True, "context_length": 8192,
        }
        sampler = mock.Mock()
        sampler.backend = "none"
        sampler.description = "fixture"
        sampler.error = ""
        sampler.snapshot_len.return_value = 0
        sampler.get_since.return_value = []
        invalid_result = {
            "status": "ok", "text": "BENCH_OK", "thinking": "unexpected trace",
            "raw": {}, "error": "", "wall": 1.0, "timed_out": False,
            "done": True, "done_reason": "stop", "termination_reason": "stop",
            "stream_chunk_count": 2, "thinking_capable": True,
            "thinking_requested": "off", "thinking_resolved": "disabled",
            "thinking_effective": "disabled", "thinking_used": True,
        }
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(direct, "load_models", return_value=[model]), \
                 mock.patch.object(direct, "create_sampler", return_value=sampler), \
                 mock.patch.object(direct, "run_metadata", return_value=dict(self.METADATA)), \
                 mock.patch.object(direct, "run_task", return_value=invalid_result) as run_task, \
                 mock.patch.object(direct, "stop_model"), \
                 contextlib.redirect_stdout(io.StringIO()):
                exit_code = direct.main([
                    "--thinking", "paired", "--num-ctx", "8192",
                    "--limit-tasks", "1", "--no-stop",
                    "--output-dir", tmp, "--run",
                ])
            csv_path = next(pathlib.Path(tmp).glob("*.csv"))
            with csv_path.open(newline="", encoding="utf-8") as handle:
                rows = list(csv.DictReader(handle))

        self.assertEqual(0, exit_code)
        self.assertEqual(2, run_task.call_count)
        off_row = next(row for row in rows if row["treatment_role"] == "off")
        self.assertEqual("false", off_row["protocol_valid"])
        self.assertIn("reasoning trace", off_row["protocol_error"])
        self.assertEqual("off-control-ineffective", off_row["model_qualification_status"])
        self.assertGreater(int(off_row["omitted_remaining_work_count"]), 0)
        sampler.stop.assert_called_once()

    def test_resume_manifest_rejects_inference_provenance_mismatches(self):
        with tempfile.TemporaryDirectory() as tmp:
            model, tasks, plan, _plan_path = self._write_resume_plan(tmp)

        validation = {
            "num_ctx": plan["num_ctx"],
            "timeout": plan["timeout"],
            "ollama_version": plan["ollama_version"],
            "ollama_url": plan["ollama_url"],
            "host": plan["host"],
            "host_label": plan["host_label"],
            "platform": plan["platform"],
            "os_version": plan["os_version"],
            "architecture": plan["architecture"],
            "telemetry_backend": plan["telemetry_backend"],
            "telemetry_interval_ms": plan["telemetry_interval_ms"],
            "no_stop": plan["no_stop"],
            "keep_alive": plan["keep_alive"],
            "residency_policy": plan["residency_policy"],
            "suite_version": plan["suite_version"],
            "benchmark_profile": plan["benchmark_profile"],
            "grading_profile": plan["grading_profile"],
            "output_token_policy": plan["output_token_policy"],
            "runner_sha256": plan["runner_sha256"],
            "grader_sha256": plan["grader_sha256"],
            "planner_sha256": plan["planner_sha256"],
        }
        paired.validate_resume_plan(plan, [model], tasks, **validation)

        mutations = {
            "pair_schema_version": 999,
            "num_ctx": 4096,
            "timeout": 17,
            "ollama_version": "changed",
            "runner_sha256": "changed",
            "grader_sha256": "changed",
            "planner_sha256": "changed",
            "temperature": 0.5,
            "generation_seed": 999,
            "num_predict": 42,
            "task_ids": ["different-task"],
            "plan_sha256": "tampered",
            "ollama_url": "http://different:11434",
            "host": "different-host",
            "platform": "different-platform",
            "architecture": "x86_64",
            "telemetry_backend": "nvidia-smi",
            "telemetry_interval_ms": 250,
            "no_stop": False,
            "keep_alive": "0s",
            "residency_policy": "cold-unload-every-task",
        }
        for field, value in mutations.items():
            changed = json.loads(json.dumps(plan))
            changed[field] = value
            with self.subTest(field=field), \
                 self.assertRaisesRegex(ValueError, "resume plan provenance mismatch"):
                paired.validate_resume_plan(changed, [model], tasks, **validation)

        changed = json.loads(json.dumps(plan))
        changed["runtime_resource_safety_policy"]["system_page_size_bytes"] *= 2
        core = {
            key: value for key, value in changed.items()
            if key not in {"plan_sha256", "run_id", "report_prefix"}
        }
        changed["plan_sha256"] = hashlib.sha256(
            json.dumps(
                core, sort_keys=True, separators=(",", ":"), ensure_ascii=False
            ).encode("utf-8")
        ).hexdigest()
        with self.assertRaisesRegex(ValueError, "runtime_resource_safety_policy"):
            paired.validate_resume_plan(changed, [model], tasks, **validation)

        changed_model = {**model, "digest": "sha256:not-installed"}
        with self.assertRaisesRegex(ValueError, "planned model tag digest changed"):
            paired.validate_resume_plan(plan, [changed_model], tasks, **validation)

    def test_resume_manifest_mismatch_stops_before_telemetry_or_report_writes(self):
        sampler = mock.Mock()
        sampler.backend = "none"
        sampler.description = "fixture"
        with tempfile.TemporaryDirectory() as tmp:
            model, _tasks, plan, plan_path = self._write_resume_plan(tmp)
            plan["runner_sha256"] = "stale-runner"
            plan_path.write_text(json.dumps(plan, indent=2) + "\n", encoding="utf-8")
            with mock.patch.object(direct, "load_models", return_value=[model]), \
                 mock.patch.object(direct, "create_sampler", return_value=sampler), \
                 mock.patch.object(direct, "run_metadata", return_value=dict(self.METADATA)), \
                 mock.patch.object(direct, "run_task") as run_task, \
                 mock.patch.object(direct, "stop_model") as stop_model, \
                 contextlib.redirect_stdout(io.StringIO()), \
                 self.assertRaisesRegex(ValueError, "resume plan provenance mismatch"):
                direct.main([
                    "--thinking", "paired", "--num-ctx", "8192",
                    "--limit-tasks", "1", "--resume-plan", str(plan_path),
                    "--ollama-url", "http://fixture:11434",
                    "--no-stop", "--run",
                ])
            self.assertEqual([plan_path.name], [path.name for path in pathlib.Path(tmp).iterdir()])

        run_task.assert_not_called()
        stop_model.assert_not_called()
        sampler.start.assert_not_called()
        sampler.stop.assert_not_called()

    def test_resume_rejects_repointed_tag_even_when_old_digest_alias_remains(self):
        with tempfile.TemporaryDirectory() as tmp:
            model, tasks, plan, _plan_path = self._write_resume_plan(tmp)
        repointed = {**model, "digest": "sha256:new-checkpoint"}
        retained_alias = {
            **model, "name": "qwen-thinking:old", "digest": plan["models"][0]["digest"],
        }
        with self.assertRaisesRegex(ValueError, "planned model tag digest changed"):
            paired.validate_resume_plan(
                plan, [repointed, retained_alias], tasks,
                **self._validation_arguments(plan),
            )

    def test_resume_rejects_changed_endpoint_telemetry_or_residency_controls(self):
        with tempfile.TemporaryDirectory() as tmp:
            model, tasks, plan, _plan_path = self._write_resume_plan(tmp)
        baseline = self._validation_arguments(plan)
        changes = {
            "ollama_url": "http://other-ollama:11434",
            "host": "other-host",
            "telemetry_backend": "nvidia-smi",
            "telemetry_interval_ms": 250,
            "no_stop": False,
            "keep_alive": "0s",
            "residency_policy": "cold-unload-every-task",
        }
        for field,value in changes.items():
            with self.subTest(field=field), self.assertRaisesRegex(
                ValueError, "resume plan provenance mismatch"
            ):
                paired.validate_resume_plan(
                    plan, [model], tasks, **{**baseline, field:value}
                )

    def test_canonical_resume_row_rejects_work_item_and_provenance_tampering(self):
        with tempfile.TemporaryDirectory() as tmp:
            _model, tasks, plan, _plan_path = self._write_resume_plan(tmp)
        work = paired.ordered_work_items(plan, tasks)[0]
        canonical = self._canonical_record(plan, work)
        self.assertEqual(canonical["row"], direct.validate_resume_record(canonical, work, plan))
        mutations = (
            ("row", "task_id", "different-task"),
            ("row", "model_digest", "sha256:different"),
            ("row", "treatment_key", "thinking-on"),
            ("row", "runner_sha256", "different-runner"),
            ("metadata", "telemetry_interval_ms", 250),
            ("grading", "verdict", "content_mismatch"),
        )
        for section,field,value in mutations:
            changed=json.loads(json.dumps(canonical))
            changed[section][field]=value
            with self.subTest(section=section,field=field), self.assertRaisesRegex(
                RuntimeError, "Resume JSONL row provenance mismatch"
            ):
                direct.validate_resume_record(changed, work, plan)

        changed=json.loads(json.dumps(canonical))
        changed["resource_guard"]["system_page_size_bytes"] *= 2
        changed["row"]["resource_guard_json"] = json.dumps(
            changed["resource_guard"], separators=(",", ":"), sort_keys=True
        )
        with self.assertRaisesRegex(RuntimeError, "resource_guard.system_page_size_bytes"):
            direct.validate_resume_record(changed, work, plan)

        changed=json.loads(json.dumps(canonical))
        changed["resource_guard"]={
            "system_page_size_bytes":plan["runtime_resource_safety_policy"]["system_page_size_bytes"]
        }
        changed["row"]["resource_guard_json"]=json.dumps(
            changed["resource_guard"],separators=(",",":"),sort_keys=True
        )
        with self.assertRaisesRegex(RuntimeError, "resource_guard: missing fields"):
            direct.validate_resume_record(changed,work,plan)

        changed=json.loads(json.dumps(canonical))
        changed["resource_guard"]["watchdog_join_verified"]=False
        changed["resource_guard"]["memory_watchdog_join_verified"]=False
        changed["row"]["resource_guard_json"]=json.dumps(
            changed["resource_guard"],separators=(",",":"),sort_keys=True
        )
        with self.assertRaisesRegex(RuntimeError, "normal row lacks verified workers"):
            direct.validate_resume_record(changed,work,plan)

        changed=json.loads(json.dumps(canonical))
        changed["resource_guard"]["admission"]["model_blob_bytes"] += 1
        changed["row"]["resource_guard_json"]=json.dumps(
            changed["resource_guard"],separators=(",",":"),sort_keys=True
        )
        with self.assertRaisesRegex(RuntimeError, "recomputed frozen admission"):
            direct.validate_resume_record(changed,work,plan)

    def test_resume_skips_completed_rows_and_retains_paths_and_experiment(self):
        sampler = mock.Mock()
        sampler.backend = "none"
        sampler.description = "fixture"
        sampler.error = ""
        sampler.snapshot_len.return_value = 0
        sampler.get_since.return_value = []
        with tempfile.TemporaryDirectory() as tmp:
            model, tasks, plan, plan_path = self._write_resume_plan(tmp)
            work_items = paired.ordered_work_items(plan, tasks)
            prefix = plan_path.name[:-len(".plan.json")]
            jsonl_path = pathlib.Path(tmp) / f"{prefix}.jsonl"
            existing_line = json.dumps(self._canonical_record(plan, work_items[0]))
            jsonl_path.write_text(existing_line + "\n", encoding="utf-8")
            original_manifest = plan_path.read_text(encoding="utf-8")
            ignored_output = pathlib.Path(tmp) / "ignored-output"

            with mock.patch.object(direct, "load_models", return_value=[model]), \
                 mock.patch.object(direct, "create_sampler", return_value=sampler), \
                 mock.patch.object(direct, "run_metadata", return_value=dict(self.METADATA)), \
                 mock.patch.object(
                     direct, "run_task", side_effect=self._successful_paired_result
                 ) as run_task, \
                 mock.patch.object(direct, "stop_model") as stop_model, \
                 contextlib.redirect_stdout(io.StringIO()) as output:
                exit_code = direct.main([
                    "--thinking", "paired", "--num-ctx", "8192",
                    "--limit-tasks", "1", "--resume-plan", str(plan_path),
                    "--ollama-url", "http://fixture:11434",
                    "--output-dir", str(ignored_output), "--no-stop", "--run",
                ])

            lines = jsonl_path.read_text(encoding="utf-8").splitlines()
            records = [json.loads(line) for line in lines]
            csv_path = pathlib.Path(tmp) / f"{prefix}.csv"
            with csv_path.open(newline="", encoding="utf-8") as handle:
                csv_rows = list(csv.DictReader(handle))
            names = {path.name for path in pathlib.Path(tmp).iterdir()}

            self.assertEqual(original_manifest, plan_path.read_text(encoding="utf-8"))
            self.assertFalse(ignored_output.exists())

        self.assertEqual(0, exit_code)
        self.assertEqual(existing_line, lines[0])
        self.assertEqual(2, len(records))
        self.assertEqual(
            {item["row_id"] for item in work_items},
            {record["row"]["row_id"] for record in records},
        )
        self.assertTrue(all(
            record["row"]["experiment_id"] == plan["experiment_id"]
            for record in records
        ))
        self.assertEqual({item["row_id"] for item in work_items}, {row["row_id"] for row in csv_rows})
        self.assertEqual({
            f"{prefix}.plan.json", f"{prefix}.jsonl", f"{prefix}.csv", f"{prefix}.md",
        }, names)
        self.assertIn("Resume: 1 completed rows retained; 1 rows remain.", output.getvalue())
        self.assertEqual(1, run_task.call_count)
        self.assertIs(True, run_task.call_args.kwargs["treatment"]["think_value"])
        self.assertEqual(8192, run_task.call_args.kwargs["num_ctx"])
        stop_model.assert_not_called()
        sampler.start.assert_called_once()
        sampler.stop.assert_called_once()

    def test_resume_rejects_duplicate_and_unknown_completed_row_ids(self):
        cases = (
            ("duplicate", "Duplicate row_id in resume JSONL"),
            ("unknown", "row IDs absent from the frozen plan"),
        )
        for case, error_pattern in cases:
            with self.subTest(case=case), tempfile.TemporaryDirectory() as tmp:
                model, tasks, plan, plan_path = self._write_resume_plan(tmp)
                work = paired.ordered_work_items(plan, tasks)[0]
                record = self._canonical_record(plan, work)
                if case == "duplicate":
                    records = [record, record]
                else:
                    record["row"]["row_id"] = "unknown-row-id"
                    records = [record]
                prefix = plan_path.name[:-len(".plan.json")]
                jsonl_path = pathlib.Path(tmp) / f"{prefix}.jsonl"
                jsonl_path.write_text(
                    "".join(json.dumps(item) + "\n" for item in records),
                    encoding="utf-8",
                )
                sampler = mock.Mock()
                sampler.backend = "none"
                sampler.description = "fixture"
                with mock.patch.object(direct, "load_models", return_value=[model]), \
                     mock.patch.object(direct, "create_sampler", return_value=sampler), \
                     mock.patch.object(direct, "run_metadata", return_value=dict(self.METADATA)), \
                     mock.patch.object(direct, "run_task") as run_task, \
                     mock.patch.object(direct, "stop_model") as stop_model, \
                     contextlib.redirect_stdout(io.StringIO()), \
                     self.assertRaisesRegex(RuntimeError, error_pattern):
                    direct.main([
                        "--thinking", "paired", "--num-ctx", "8192",
                        "--limit-tasks", "1", "--resume-plan", str(plan_path),
                        "--ollama-url", "http://fixture:11434",
                        "--no-stop", "--run",
                    ])
                self.assertEqual(
                    {plan_path.name, jsonl_path.name},
                    {path.name for path in pathlib.Path(tmp).iterdir()},
                )
                run_task.assert_not_called()
                stop_model.assert_not_called()
                sampler.start.assert_not_called()
                sampler.stop.assert_not_called()

    def test_fully_completed_resume_does_not_start_telemetry_or_inference(self):
        sampler = mock.Mock()
        sampler.backend = "none"
        sampler.description = "fixture"
        with tempfile.TemporaryDirectory() as tmp:
            model, tasks, plan, plan_path = self._write_resume_plan(tmp)
            work_items = paired.ordered_work_items(plan, tasks)
            prefix = plan_path.name[:-len(".plan.json")]
            jsonl_path = pathlib.Path(tmp) / f"{prefix}.jsonl"
            original_jsonl = "".join(
                json.dumps(self._canonical_record(plan, work)) + "\n"
                for work in work_items
            )
            jsonl_path.write_text(original_jsonl, encoding="utf-8")
            original_manifest = plan_path.read_text(encoding="utf-8")

            with mock.patch.object(direct, "load_models", return_value=[model]), \
                 mock.patch.object(direct, "create_sampler", return_value=sampler), \
                 mock.patch.object(direct, "run_metadata", return_value=dict(self.METADATA)), \
                 mock.patch.object(direct, "run_task") as run_task, \
                 mock.patch.object(direct, "stream_generate") as stream_generate, \
                 mock.patch.object(direct, "stop_model") as stop_model, \
                 contextlib.redirect_stdout(io.StringIO()) as output:
                exit_code = direct.main([
                    "--thinking", "paired", "--num-ctx", "8192",
                    "--limit-tasks", "1", "--resume-plan", str(plan_path),
                    "--ollama-url", "http://fixture:11434",
                    "--no-stop", "--run",
                ])

            csv_path = pathlib.Path(tmp) / f"{prefix}.csv"
            with csv_path.open(newline="", encoding="utf-8") as handle:
                csv_rows = list(csv.DictReader(handle))
            names = {path.name for path in pathlib.Path(tmp).iterdir()}
            self.assertEqual(original_jsonl, jsonl_path.read_text(encoding="utf-8"))
            self.assertEqual(original_manifest, plan_path.read_text(encoding="utf-8"))

        self.assertEqual(0, exit_code)
        self.assertIn("Resume: 2 completed rows retained; 0 rows remain.", output.getvalue())
        self.assertEqual(
            {item["row_id"] for item in work_items},
            {row["row_id"] for row in csv_rows},
        )
        self.assertTrue(all(row["experiment_id"] == plan["experiment_id"] for row in csv_rows))
        self.assertEqual({
            f"{prefix}.plan.json", f"{prefix}.jsonl", f"{prefix}.csv", f"{prefix}.md",
        }, names)
        run_task.assert_not_called()
        stream_generate.assert_not_called()
        stop_model.assert_not_called()
        sampler.start.assert_not_called()
        sampler.stop.assert_not_called()

    def test_openclaw_defaults_to_plan_only(self):
        model = {"name": "fixture:latest", "digest": "sha256:test", "capabilities": ["completion"]}
        sampler = mock.Mock()
        sampler.backend = "none"
        sampler.description = "fixture"
        with mock.patch.object(openclaw, "get_ollama_models", return_value=[model]), \
             mock.patch.object(openclaw, "create_sampler", return_value=sampler), \
             mock.patch.object(openclaw, "run_metadata", return_value=dict(self.METADATA)), \
             mock.patch.object(openclaw, "openclaw_model_state") as state, \
             mock.patch.object(openclaw, "restart_openclaw_gateway") as restart_gateway, \
             mock.patch.object(openclaw, "checked_run") as checked_run, \
             mock.patch.object(openclaw, "stop_model") as stop_model, \
             mock.patch.object(pathlib.Path, "mkdir") as mkdir, \
             contextlib.redirect_stdout(io.StringIO()) as output:
            result = openclaw.main(["--models", "fixture:latest", "--tasks", "exact_reply"])
        self.assertEqual(0, result)
        self.assertIn("PLAN ONLY", output.getvalue())
        state.assert_not_called()
        restart_gateway.assert_not_called()
        checked_run.assert_not_called()
        stop_model.assert_not_called()
        sampler.start.assert_not_called()
        sampler.stop.assert_not_called()
        mkdir.assert_not_called()

    def test_openclaw_auto_thinking_is_capability_aware(self):
        capable = {
            "name": "qwen3.6:35b", "capabilities": ["completion", "thinking"]
        }
        gpt_oss = {
            "name": "gpt-oss:20b", "capabilities": ["completion", "thinking"]
        }
        unsupported = {"name": "plain:latest", "capabilities": ["completion"]}
        self.assertEqual(
            (True, None, "provider-default/off"),
            openclaw.thinking_request_for_model(capable),
        )
        self.assertEqual(
            (True, None, "provider-default/off"),
            openclaw.thinking_request_for_model(gpt_oss),
        )
        self.assertEqual((False, None, "unsupported"), openclaw.thinking_request_for_model(unsupported))
        self.assertEqual(
            (True, None, "provider-default/off"),
            openclaw.thinking_request_for_model(gpt_oss, "off"),
        )

    def test_openclaw_thinking_preflight_fails_before_mutation_when_flag_is_missing(self):
        help_result = mock.Mock(returncode=0, stdout="usage: openclaw agent", stderr="")
        with mock.patch.object(openclaw, "run", return_value=help_result), \
             self.assertRaisesRegex(RuntimeError, "--thinking"):
            openclaw.require_openclaw_thinking_support(["max"])

    def test_openclaw_timeout_bounds(self):
        self.assertEqual(1, openclaw.agent_timeout_seconds("1"))
        self.assertEqual(1800, openclaw.agent_timeout_seconds("1800"))
        for invalid in ("0", "1801"):
            with self.subTest(timeout=invalid), self.assertRaises(openclaw.argparse.ArgumentTypeError):
                openclaw.agent_timeout_seconds(invalid)


class DirectStreamingTests(unittest.TestCase):
    URL = "http://127.0.0.1:11434/api/generate"
    PAYLOAD = {
        "model": "fixture:latest",
        "prompt": "Fixture prompt",
        "stream": True,
        "options": {"temperature": 0, "seed": 42, "num_predict": -1},
    }

    @staticmethod
    def ndjson(**fields):
        return (json.dumps(fields) + "\n").encode("utf-8")

    def test_stream_generate_aggregates_response_thinking_and_final_metrics(self):
        response = FakeStreamingResponse([
            self.ndjson(model="fixture:latest", thinking="work ", response="", done=False),
            self.ndjson(model="fixture:latest", thinking="it out", response="", done=False),
            self.ndjson(model="fixture:latest", response="FINAL: ", done=False),
            self.ndjson(
                model="fixture:latest", response="42", done=True,
                done_reason="stop", total_duration=5_000_000_000,
                load_duration=1_000_000_000, prompt_eval_count=4,
                prompt_eval_duration=500_000_000, eval_count=8,
                eval_duration=2_000_000_000,
            ),
        ])
        factory = streaming_factory(response)

        result = direct.stream_generate(
            self.URL, dict(self.PAYLOAD), 30,
            connection_factory=factory, clock=FakeClock(10),
        )

        self.assertEqual("ok", result["status"])
        self.assertEqual("FINAL: 42", result["text"])
        self.assertEqual("work it out", result["thinking"])
        self.assertIs(True, result["done"])
        self.assertIs(False, result["timed_out"])
        self.assertEqual("stop", result["done_reason"])
        self.assertEqual("stop", result["termination_reason"])
        self.assertEqual(4, result["stream_chunk_count"])
        self.assertEqual(9, result["response_chars"])
        self.assertEqual(9, result["response_bytes"])
        self.assertEqual(11, result["thinking_chars"])
        self.assertEqual(11, result["thinking_bytes"])
        self.assertEqual(8, result["raw"]["eval_count"])
        self.assertEqual(2_000_000_000, result["raw"]["eval_duration"])
        connection = factory.created[0]
        self.assertEqual("POST", connection.requests[0][0])
        self.assertEqual("/api/generate", connection.requests[0][1])
        sent = json.loads(connection.requests[0][2].decode("utf-8"))
        self.assertIs(True, sent["stream"])
        self.assertEqual(-1, sent["options"]["num_predict"])
        self.assertTrue(connection.closed)

    def test_stream_timeout_preserves_partial_output(self):
        clock = FakeClock(0)

        def deadline_timeout():
            clock.value = 31
            return socket.timeout("fixture deadline")

        response = FakeStreamingResponse([
            self.ndjson(response="partial answer", thinking="partial thought", done=False),
            deadline_timeout,
        ])

        result = direct.stream_generate(
            self.URL, dict(self.PAYLOAD), 30,
            connection_factory=streaming_factory(response), clock=clock,
        )

        self.assertEqual("timeout", result["status"])
        self.assertIs(True, result["timed_out"])
        self.assertIs(False, result["done"])
        self.assertEqual("client_timeout", result["termination_reason"])
        self.assertEqual("partial answer", result["text"])
        self.assertEqual("partial thought", result["thinking"])
        self.assertEqual(1, result["stream_chunk_count"])
        self.assertGreaterEqual(result["wall"], 30)

    def test_stream_malformed_json_preserves_prior_output(self):
        response = FakeStreamingResponse([
            self.ndjson(response="kept", thinking="", done=False),
            b"{not valid json}\n",
        ])

        result = direct.stream_generate(
            self.URL, dict(self.PAYLOAD), 30,
            connection_factory=streaming_factory(response), clock=FakeClock(),
        )

        self.assertEqual("error", result["status"])
        self.assertEqual("kept", result["text"])
        self.assertFalse(result["done"])
        self.assertTrue(result["error"])

    def test_stream_midstream_api_error_preserves_prior_output(self):
        response = FakeStreamingResponse([
            self.ndjson(response="kept", thinking="reasoning", done=False),
            self.ndjson(error="fixture runner failure"),
        ])

        result = direct.stream_generate(
            self.URL, dict(self.PAYLOAD), 30,
            connection_factory=streaming_factory(response), clock=FakeClock(),
        )

        self.assertEqual("error", result["status"])
        self.assertEqual("kept", result["text"])
        self.assertEqual("reasoning", result["thinking"])
        self.assertFalse(result["done"])
        self.assertIn("fixture runner failure", result["error"])

    def test_stream_eof_without_done_is_an_error(self):
        response = FakeStreamingResponse([
            self.ndjson(response="partial", thinking="", done=False),
        ])
        result = direct.stream_generate(
            self.URL, dict(self.PAYLOAD), 30,
            connection_factory=streaming_factory(response), clock=FakeClock(),
        )
        self.assertEqual("error", result["status"])
        self.assertEqual("stream_ended_without_done", result["termination_reason"])
        self.assertEqual("partial", result["text"])

    def test_stream_http_error_is_recorded(self):
        response = FakeStreamingResponse([b'{"error":"bad request"}'], status=400)
        result = direct.stream_generate(
            self.URL, dict(self.PAYLOAD), 30,
            connection_factory=streaming_factory(response), clock=FakeClock(),
        )
        self.assertEqual("error", result["status"])
        self.assertEqual("http_400", result["termination_reason"])
        self.assertIn("HTTP 400", result["error"])

    def test_stream_records_first_thinking_and_answer_times(self):
        clock = FakeClock(0)

        def thinking_chunk():
            clock.value = 2
            return self.ndjson(thinking="thought", response="", done=False)

        def answer_chunk():
            clock.value = 5
            return self.ndjson(thinking="", response="answer", done=True, done_reason="stop")

        response = FakeStreamingResponse([thinking_chunk, answer_chunk])
        result = direct.stream_generate(
            self.URL, dict(self.PAYLOAD), 30,
            connection_factory=streaming_factory(response), clock=clock,
        )
        self.assertEqual(2, result["time_to_first_output_seconds"])
        self.assertEqual(5, result["time_to_first_answer_seconds"])


class AccuracyGradingTests(unittest.TestCase):
    ROBUST_PRIVATE = """\
def is_private_ipv4(ip):
    parts = ip.split('.')
    if len(parts) != 4:
        return False
    try:
        octets = [int(value) for value in parts]
    except ValueError:
        return False
    if any(value < 0 or value > 255 for value in octets):
        return False
    a, b = octets[:2]
    return a == 10 or (a == 172 and 16 <= b <= 31) or (a == 192 and b == 168)
"""

    NAIVE_PRIVATE = """\
def is_private_ipv4(ip):
    a, b, c, d = map(int, ip.split('.'))
    return a == 10 or (a == 172 and 16 <= b <= 31) or (a == 192 and b == 168)
"""

    GOOD_MBPP = """\
import re
def count_unique_ips(lines):
    found = set()
    for line in lines:
        for match in re.finditer(r'(?<![\\d.])(?:\\d{1,3}\\.){3}\\d{1,3}(?![\\d.])', line):
            value = match.group()
            parts = [int(part) for part in value.split('.')]
            if all(0 <= part <= 255 for part in parts):
                found.add(value)
    return len(found)
"""

    def test_private_ipv4_behavioral_grader_accepts_robust_fenced_code(self):
        result = grading.grade_python_function(
            f"```python\n{self.ROBUST_PRIVATE}```",
            {**grading.PRIVATE_IPV4_GRADER, "line_limit": 25},
        )
        self.assertEqual("pass", result["verdict"])
        self.assertEqual(result["tests_total"], result["tests_passed"])

    def test_private_ipv4_behavioral_grader_catches_observed_false_positive(self):
        result = grading.grade_python_function(
            self.NAIVE_PRIVATE,
            {**grading.PRIVATE_IPV4_GRADER, "line_limit": 25},
        )
        self.assertEqual("content_mismatch", result["verdict"])
        self.assertLess(result["tests_passed"], result["tests_total"])
        self.assertTrue(any("raised" in failure or "expected False" in failure for failure in result["failures"]))

    def test_private_ipv4_rejects_ipaddress_is_private_overbreadth(self):
        candidate = """\
import ipaddress
def is_private_ipv4(ip):
    try:
        return ipaddress.ip_address(ip).is_private
    except ValueError:
        return False
"""
        result = grading.grade_python_function(candidate, grading.PRIVATE_IPV4_GRADER)
        self.assertEqual("content_mismatch", result["verdict"])

    def test_mbpp_behavioral_grader_accepts_real_logic_and_rejects_marker_code(self):
        good = grading.grade_python_function(self.GOOD_MBPP, grading.COUNT_UNIQUE_IPS_GRADER)
        self.assertEqual("pass", good["verdict"])
        marker_only = "def count_unique_ips(lines):\n    return len(set(lines))\n"
        bad = grading.grade_python_function(marker_only, grading.COUNT_UNIQUE_IPS_GRADER)
        self.assertEqual("content_mismatch", bad["verdict"])

        module_regex = """\
import re
IP_RE = re.compile(r'(?<![\\d.])(?:\\d{1,3}\\.){3}\\d{1,3}(?![\\d.])')
def count_unique_ips(lines):
    found = set()
    for line in lines:
        for match in IP_RE.finditer(line):
            value = match.group()
            parts = [int(part) for part in value.split('.')]
            if all(0 <= part <= 255 for part in parts):
                found.add(value)
    return len(found)
"""
        module_level = grading.grade_python_function(module_regex, grading.COUNT_UNIQUE_IPS_GRADER)
        self.assertEqual("pass", module_level["verdict"])

    def test_python_grader_rejects_unsafe_import_and_times_out_loop(self):
        unsafe = "import os\ndef is_private_ipv4(ip):\n    return False\n"
        rejected = grading.grade_python_function(unsafe, grading.PRIVATE_IPV4_GRADER)
        self.assertEqual("content_mismatch", rejected["verdict"])
        self.assertIn("import is not allowed", rejected["error"])

        import_escape = "from re import __builtins__ as b\ndef is_private_ipv4(ip):\n    return False\n"
        rejected_escape = grading.grade_python_function(import_escape, grading.PRIVATE_IPV4_GRADER)
        self.assertEqual("content_mismatch", rejected_escape["verdict"])
        self.assertIn("imported symbol is not allowed", rejected_escape["error"])

        loop = "def is_private_ipv4(ip):\n    while True:\n        pass\n"
        timed_out = grading.grade_python_function(
            loop, grading.PRIVATE_IPV4_GRADER, timeout_seconds=0.2
        )
        self.assertEqual("content_mismatch", timed_out["verdict"])
        self.assertIn("timeout", timed_out["error"])

        resource_limited = grading.grade_python_function(
            loop, grading.PRIVATE_IPV4_GRADER, timeout_seconds=3
        )
        self.assertEqual("content_mismatch", resource_limited["verdict"])
        self.assertNotEqual("grader_error", resource_limited["verdict"])
        self.assertIn("resource/safety limit", resource_limited["error"])

    def test_strict_json_rejects_markdown_extra_keys_and_bool_as_int(self):
        task = {
            "json_expected": {"verdict": "pass", "count": 3},
            "strict_json": True,
            "exact_json_keys": True,
            "compact_json": True,
        }
        self.assertEqual("pass", grading.grade_task(task, "ok", '{"verdict":"pass","count":3}')["verdict"])
        for response in (
            '```json\n{"verdict":"pass","count":3}\n```',
            '{"verdict":"pass","count":3,"extra":true}',
            '{"verdict":"pass","count":true}',
            '{"verdict":"fail","verdict":"pass","count":3}',
            '{ "verdict": "pass", "count": 3 }',
        ):
            with self.subTest(response=response):
                self.assertEqual("content_mismatch", grading.grade_task(task, "ok", response)["verdict"])

    def test_terminal_final_answer_rejects_substrings_and_conflicts(self):
        task = {"final_answer": "90"}
        self.assertEqual("pass", grading.grade_task(task, "ok", "Calculation. FINAL: 90")["verdict"])
        for response in (
            "FINAL: 900",
            "FINAL: 90 is wrong. FINAL: 30",
            "FINAL: 30; perhaps 90",
        ):
            with self.subTest(response=response):
                self.assertEqual("content_mismatch", grading.grade_task(task, "ok", response)["verdict"])

    def test_ocr_requires_exact_terminal_text(self):
        task = next(task for task in direct.TASKS if task["id"] == "ocrbench_mini")
        self.assertEqual("pass", grading.grade_task(task, "ok", "FINAL: LOCAL OCR 42")["verdict"])
        self.assertEqual("content_mismatch", grading.grade_task(task, "ok", "FINAL: LOCAL 0CR 42")["verdict"])

    def test_cyber_json_requires_both_correct_classification_and_action(self):
        task = next(task for task in direct.TASKS if task["id"] == "cyber_soc_mini")
        correct = '{"classification":"horizontal_ssh_scan","action":"block_source_ip"}'
        self.assertEqual("pass", grading.grade_task(task, "ok", correct)["verdict"])
        self.assertEqual(
            "content_mismatch",
            grading.grade_task(task, "ok", '{"classification":"horizontal_ssh_scan","action":"ignore"}')["verdict"],
        )


class DashboardCompatibilityTests(unittest.TestCase):
    FIELDS = [
        "model", "task_id", "status", "verdict", "wall_seconds",
        "benchmark_family", "category", "host", "host_label",
        "model_digest", "suite_version", "ollama_version", "telemetry_backend",
        "grading_profile", "runner_sha256", "grader_sha256", "context_policy",
        "requested_num_ctx", "model_context_length", "grader_error",
        "experiment_id", "plan_sha256", "pair_schema_version", "campaign_seed",
        "pair_id", "row_id", "treatment_id", "treatment_key", "treatment_role",
        "treatment_order", "pair_kind", "off_available", "think_field_present",
        "think_payload_json", "thinking_requested", "thinking_resolved",
        "thinking_effective", "thinking_chars", "eval_count",
        "grader_tests_passed", "grader_tests_total", "protocol_valid",
        "protocol_error", "planner_sha256",
    ]

    def write_csv(self, path, rows):
        with path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=self.FIELDS)
            writer.writeheader()
            writer.writerows(rows)

    def paired_row(
        self, *, model, experiment_id, plan_sha256, pair_id, treatment_key,
        treatment_role, treatment_order, pair_kind, off_available, task_id,
        verdict="pass", wall_seconds=1, eval_count=10, thinking_chars=0,
        grader_passed=1, grader_total=1,
    ):
        effective = {
            "off": "disabled", "on": "enabled",
            "minimum": "low", "maximum": "high",
        }[treatment_role]
        think_value = {
            "off": "false", "on": "true",
            "minimum": '"low"', "maximum": '"high"',
        }[treatment_role]
        return {
            "model": model,
            "task_id": task_id,
            "status": "ok",
            "verdict": verdict,
            "wall_seconds": str(wall_seconds),
            "benchmark_family": "Fixture",
            "category": "fixture",
            "host": "spark",
            "host_label": "NVIDIA DGX Spark",
            "model_digest": f"sha256:{model}",
            "suite_version": "0.4.0",
            "ollama_version": "0.32.13",
            "telemetry_backend": "nvidia-smi",
            "grading_profile": "behavioral-v1",
            "runner_sha256": "runner-fixture",
            "grader_sha256": "grader-fixture",
            "planner_sha256": "planner-fixture",
            "context_policy": "explicit",
            "requested_num_ctx": "65536",
            "model_context_length": "262144",
            "experiment_id": experiment_id,
            "plan_sha256": plan_sha256,
            "pair_schema_version": "1",
            "campaign_seed": "42",
            "pair_id": pair_id,
            "row_id": f"{experiment_id}-{treatment_key}-{task_id}",
            "treatment_id": f"{pair_id}-{treatment_key}",
            "treatment_key": treatment_key,
            "treatment_role": treatment_role,
            "treatment_order": str(treatment_order),
            "pair_kind": pair_kind,
            "off_available": str(off_available).lower(),
            "think_field_present": "true",
            "think_payload_json": think_value,
            "thinking_requested": effective,
            "thinking_resolved": effective,
            "thinking_effective": effective,
            "thinking_chars": str(thinking_chars),
            "eval_count": str(eval_count),
            "grader_tests_passed": str(grader_passed),
            "grader_tests_total": str(grader_total),
            "protocol_valid": "true",
            "protocol_error": "",
        }

    @staticmethod
    def paired_plan(*, model, experiment_id, pair_id, task_ids, treatment_keys):
        plan_core = {
            "experiment_id": experiment_id,
            "task_ids": list(task_ids),
            "models": [{
                "name": model,
                "pair_id": pair_id,
                "treatments": [
                    {"treatment_key": treatment_key}
                    for treatment_key in treatment_keys
                ],
            }],
        }
        plan_sha256 = hashlib.sha256(
            json.dumps(
                plan_core, sort_keys=True, separators=(",", ":"), ensure_ascii=False
            ).encode("utf-8")
        ).hexdigest()
        return {
            **plan_core,
            "plan_sha256": plan_sha256,
            "run_id": f"run-{experiment_id}",
            "report_prefix": f"/fixtures/{experiment_id}",
        }

    @staticmethod
    def write_paired_plan(csv_path, plan):
        csv_path.with_suffix(".plan.json").write_text(
            json.dumps(plan, indent=2) + "\n", encoding="utf-8"
        )

    def test_full_capability_execution_fields_are_summarized(self):
        rows = [
            {
                "benchmark_profile": "full-capability-v1",
                "output_token_policy": "ollama-num-predict-unlimited",
                "output_token_limit": "-1", "response_timeout_seconds": "1800",
                "thinking_requested": "auto", "thinking_effective": "max",
                "thinking_capable": "true", "thinking_used": "true",
                "timed_out": "false", "done": "true", "done_reason": "stop",
                "termination_reason": "stop", "prompt_eval_count": "10",
                "eval_count": "20", "total_token_count": "30",
                "response_chars": "100", "thinking_chars": "200",
                "stream_chunk_count": "25", "time_to_first_output_seconds": "1",
                "time_to_first_answer_seconds": "3",
            },
            {
                "benchmark_profile": "full-capability-v1",
                "output_token_policy": "ollama-num-predict-unlimited",
                "output_token_limit": "-1", "response_timeout_seconds": "1800",
                "thinking_requested": "auto", "thinking_effective": "max",
                "thinking_capable": "true", "thinking_used": "true",
                "timed_out": "true", "done": "false",
                "termination_reason": "client_timeout", "response_chars": "50",
                "thinking_chars": "75", "stream_chunk_count": "12",
                "time_to_first_output_seconds": "2",
            },
        ]
        summary = dashboard.summarize_execution_schema(rows)
        self.assertEqual(["-1"], summary["output_token_limits"])
        self.assertEqual(["1800"], summary["response_timeouts"])
        self.assertEqual(1, summary["timeouts"])
        self.assertEqual(30, summary["total_token_count"])
        self.assertEqual(150, summary["response_chars"])
        self.assertEqual(275, summary["thinking_chars"])
        self.assertEqual(1.5, summary["avg_time_to_first_output"])

    def test_complete_off_on_campaign_attaches_valid_paired_metrics(self):
        model = "qwen-thinking:7b"
        experiment_id = "experiment-off-on"
        pair_id = "pair-off-on"
        tasks = ["task-a", "task-b"]
        plan = self.paired_plan(
            model=model, experiment_id=experiment_id, pair_id=pair_id,
            task_ids=tasks, treatment_keys=["thinking-off", "thinking-on"],
        )
        plan_sha256 = plan["plan_sha256"]
        rows = [
            self.paired_row(
                model=model, experiment_id=experiment_id, plan_sha256=plan_sha256,
                pair_id=pair_id, treatment_key="thinking-off", treatment_role="off",
                treatment_order=1, pair_kind="off-vs-on", off_available=True,
                task_id="task-a", verdict="pass", wall_seconds=2, eval_count=100,
                thinking_chars=0, grader_passed=20, grader_total=25,
            ),
            self.paired_row(
                model=model, experiment_id=experiment_id, plan_sha256=plan_sha256,
                pair_id=pair_id, treatment_key="thinking-off", treatment_role="off",
                treatment_order=1, pair_kind="off-vs-on", off_available=True,
                task_id="task-b", verdict="content_mismatch", wall_seconds=3,
                eval_count=200, thinking_chars=0, grader_passed=5, grader_total=10,
            ),
            self.paired_row(
                model=model, experiment_id=experiment_id, plan_sha256=plan_sha256,
                pair_id=pair_id, treatment_key="thinking-on", treatment_role="on",
                treatment_order=2, pair_kind="off-vs-on", off_available=True,
                task_id="task-a", verdict="pass", wall_seconds=4, eval_count=300,
                thinking_chars=100, grader_passed=25, grader_total=25,
            ),
            self.paired_row(
                model=model, experiment_id=experiment_id, plan_sha256=plan_sha256,
                pair_id=pair_id, treatment_key="thinking-on", treatment_role="on",
                treatment_order=2, pair_kind="off-vs-on", off_available=True,
                task_id="task-b", verdict="pass", wall_seconds=6, eval_count=600,
                thinking_chars=200, grader_passed=10, grader_total=10,
            ),
        ]
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "paired.csv"
            self.write_csv(path, rows)
            self.write_paired_plan(path, plan)
            summary, latest = dashboard.load_standardized_summary(path)

        self.assertEqual(path, latest)
        self.assertEqual(4, summary[model]["rows"])
        self.assertEqual(4, summary[model]["tasks"])
        self.assertEqual(3, summary[model]["passed"])
        comparison = summary[model]["treatment_comparison"]
        self.assertEqual(experiment_id, comparison["campaign_id"])
        self.assertEqual(pair_id, comparison["pair_id"])
        self.assertEqual("off-vs-on", comparison["pair_kind"])
        self.assertIs(True, comparison["off_available"])
        self.assertEqual(["off", "on"], comparison["expected_treatments"])
        self.assertEqual("off", comparison["first_label"])
        self.assertEqual("on", comparison["second_label"])
        self.assertEqual({"off", "on"}, set(comparison["treatments"]))
        self.assertIs(True, comparison["complete"])
        self.assertIs(True, comparison["valid"])
        self.assertEqual("valid", comparison["status"])
        self.assertEqual([], comparison["invalid_reasons"])
        self.assertEqual(1, comparison["treatments"]["off"]["passed"])
        self.assertEqual(2, comparison["treatments"]["on"]["passed"])
        self.assertEqual(25, comparison["treatments"]["off"]["grader_cases_passed"])
        self.assertEqual(35, comparison["treatments"]["on"]["grader_cases_passed"])
        self.assertEqual(1, comparison["strict_delta"])
        self.assertEqual(10, comparison["grader_delta"])
        self.assertEqual(2.0, comparison["wall_multiplier"])
        self.assertEqual(3.0, comparison["token_multiplier"])

    def test_gpt_oss_campaign_is_labeled_low_high_and_never_as_off(self):
        model = "gpt-oss:20b"
        experiment_id = "experiment-gpt"
        pair_id = "pair-gpt"
        tasks = ["task-a", "task-b"]
        plan = self.paired_plan(
            model=model, experiment_id=experiment_id, pair_id=pair_id,
            task_ids=tasks, treatment_keys=["thinking-low", "thinking-high"],
        )
        plan_sha256 = plan["plan_sha256"]
        rows = []
        for task_id, verdict, wall, tokens, thinking in (
            ("task-a", "pass", 2, 100, 50),
            ("task-b", "content_mismatch", 3, 200, 70),
        ):
            rows.append(self.paired_row(
                model=model, experiment_id=experiment_id, plan_sha256=plan_sha256,
                pair_id=pair_id, treatment_key="thinking-low",
                treatment_role="minimum", treatment_order=1,
                pair_kind="minimum-vs-maximum", off_available=False,
                task_id=task_id, verdict=verdict, wall_seconds=wall,
                eval_count=tokens, thinking_chars=thinking,
                grader_passed=1 if verdict == "pass" else 0, grader_total=1,
            ))
        for task_id, wall, tokens, thinking in (
            ("task-a", 4, 300, 200), ("task-b", 6, 600, 300),
        ):
            rows.append(self.paired_row(
                model=model, experiment_id=experiment_id, plan_sha256=plan_sha256,
                pair_id=pair_id, treatment_key="thinking-high",
                treatment_role="maximum", treatment_order=2,
                pair_kind="minimum-vs-maximum", off_available=False,
                task_id=task_id, verdict="pass", wall_seconds=wall,
                eval_count=tokens, thinking_chars=thinking,
                grader_passed=1, grader_total=1,
            ))
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "gpt.csv"
            self.write_csv(path, rows)
            self.write_paired_plan(path, plan)
            summary, _ = dashboard.load_standardized_summary(path)

        self.assertEqual(4, summary[model]["rows"])
        self.assertEqual(4, summary[model]["tasks"])
        self.assertEqual(3, summary[model]["passed"])
        comparison = summary[model]["treatment_comparison"]
        self.assertEqual("minimum-vs-maximum", comparison["pair_kind"])
        self.assertIs(False, comparison["off_available"])
        self.assertEqual(["low", "high"], comparison["expected_treatments"])
        self.assertEqual("low", comparison["first_label"])
        self.assertEqual("high", comparison["second_label"])
        self.assertEqual({"low", "high"}, set(comparison["treatments"]))
        self.assertNotIn("off", comparison["treatments"])
        self.assertIs(True, comparison["complete"])
        self.assertIs(True, comparison["valid"])
        self.assertEqual("valid", comparison["status"])

    def test_paired_campaign_with_missing_arm_is_marked_incomplete(self):
        model = "qwen-thinking:7b"
        experiment_id = "experiment-incomplete"
        pair_id = "pair-incomplete"
        tasks = ["task-a", "task-b"]
        plan = self.paired_plan(
            model=model, experiment_id=experiment_id, pair_id=pair_id,
            task_ids=tasks, treatment_keys=["thinking-off", "thinking-on"],
        )
        plan_sha256 = plan["plan_sha256"]
        rows = [
            self.paired_row(
                model=model, experiment_id=experiment_id, plan_sha256=plan_sha256,
                pair_id=pair_id, treatment_key="thinking-off", treatment_role="off",
                treatment_order=1, pair_kind="off-vs-on", off_available=True,
                task_id=task_id, thinking_chars=0,
            )
            for task_id in tasks
        ]
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "incomplete.csv"
            self.write_csv(path, rows)
            self.write_paired_plan(path, plan)
            summary, _ = dashboard.load_standardized_summary(path)

        comparison = summary[model]["treatment_comparison"]
        self.assertEqual({"off"}, set(comparison["treatments"]))
        self.assertIs(False, comparison["complete"])
        self.assertIs(False, comparison["valid"])
        self.assertEqual("incomplete", comparison["status"])
        self.assertIn("treatments incomplete or task sets differ", comparison["invalid_reasons"])
        self.assertIsNone(comparison["strict_delta"])
        self.assertIsNone(comparison["grader_delta"])
        self.assertIsNone(comparison["wall_multiplier"])
        self.assertIsNone(comparison["token_multiplier"])

    def test_newer_experiment_is_selected_without_merging_older_arm(self):
        model = "qwen-thinking:7b"
        tasks = ["task-a", "task-b"]
        old_plan = self.paired_plan(
            model=model, experiment_id="experiment-old", pair_id="pair-old",
            task_ids=tasks, treatment_keys=["thinking-off", "thinking-on"],
        )
        new_plan = self.paired_plan(
            model=model, experiment_id="experiment-new", pair_id="pair-new",
            task_ids=tasks, treatment_keys=["thinking-off", "thinking-on"],
        )
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            old = root / "old.csv"
            new = root / "new.csv"
            old_rows = [
                self.paired_row(
                    model=model, experiment_id="experiment-old",
                    plan_sha256=old_plan["plan_sha256"], pair_id="pair-old",
                    treatment_key="thinking-off", treatment_role="off",
                    treatment_order=1, pair_kind="off-vs-on", off_available=True,
                    task_id=task_id, thinking_chars=0,
                )
                for task_id in tasks
            ]
            new_rows = [
                self.paired_row(
                    model=model, experiment_id="experiment-new",
                    plan_sha256=new_plan["plan_sha256"], pair_id="pair-new",
                    treatment_key="thinking-on", treatment_role="on",
                    treatment_order=2, pair_kind="off-vs-on", off_available=True,
                    task_id=task_id, thinking_chars=100,
                )
                for task_id in tasks
            ]
            self.write_csv(old, old_rows)
            self.write_csv(new, new_rows)
            self.write_paired_plan(old, old_plan)
            self.write_paired_plan(new, new_plan)
            os.utime(old, (1, 1))
            os.utime(new, (2, 2))
            summary, latest = dashboard.load_standardized_summary([old, new])

        self.assertEqual(new, latest)
        comparison = summary[model]["treatment_comparison"]
        self.assertEqual("experiment-new", comparison["campaign_id"])
        self.assertEqual("pair-new", comparison["pair_id"])
        self.assertEqual({"on"}, set(comparison["treatments"]))
        self.assertNotIn("off", comparison["treatments"])
        self.assertEqual("incomplete", comparison["status"])

    def test_legacy_csv_without_provenance_remains_readable(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "legacy.csv"
            self.write_csv(path, [{
                "model": "fixture:latest", "task_id": "exact_reply",
                "status": "ok", "verdict": "pass", "wall_seconds": "1.25",
                "benchmark_family": "Smoke", "category": "smoke_instruction",
            }])
            summary, latest = dashboard.load_standardized_summary(path)
        self.assertEqual(path, latest)
        self.assertEqual(1, summary["fixture:latest"]["passed"])
        self.assertEqual([], summary["fixture:latest"]["hosts"])
        self.assertEqual([], summary["fixture:latest"]["model_digests"])
        self.assertNotIn("treatment_comparison", summary["fixture:latest"])

    def test_newest_provenance_cohort_prevents_cross_digest_merging(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            old = root / "old.csv"
            new = root / "new.csv"
            common_old = {
                "model": "fixture:latest", "status": "ok", "verdict": "pass",
                "wall_seconds": "2", "benchmark_family": "Smoke", "category": "smoke",
                "host": "spark", "host_label": "NVIDIA DGX Spark",
                "model_digest": "sha256:old", "suite_version": "0.2.0",
                "ollama_version": "0.32.13", "telemetry_backend": "nvidia-smi",
            }
            self.write_csv(old, [
                {**common_old, "task_id": "task-a"},
                {**common_old, "task_id": "task-b"},
            ])
            common_new = {**common_old, "model_digest": "sha256:new"}
            self.write_csv(new, [{**common_new, "task_id": "task-a"}])
            os.utime(old, (1, 1))
            os.utime(new, (2, 2))
            summary, _ = dashboard.load_standardized_summary([old, new])
        model = summary["fixture:latest"]
        self.assertEqual(1, model["rows"])
        self.assertEqual(["sha256:new"], model["model_digests"])
        self.assertEqual(["NVIDIA DGX Spark"], model["hosts"])
        self.assertEqual([str(new)], model["csvs"])

    def test_partial_runs_merge_within_the_same_provenance_cohort(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            first = root / "first.csv"
            second = root / "second.csv"
            common = {
                "model": "fixture:latest", "status": "ok", "verdict": "pass",
                "wall_seconds": "2", "benchmark_family": "Smoke", "category": "smoke",
                "host": "spark", "host_label": "NVIDIA DGX Spark",
                "model_digest": "sha256:same", "suite_version": "0.2.0",
                "ollama_version": "0.32.13", "telemetry_backend": "nvidia-smi",
            }
            self.write_csv(first, [{**common, "task_id": "task-a"}])
            self.write_csv(second, [{**common, "task_id": "task-b"}])
            os.utime(first, (1, 1))
            os.utime(second, (2, 2))
            summary, _ = dashboard.load_standardized_summary([first, second])
        model = summary["fixture:latest"]
        self.assertEqual(2, model["rows"])
        self.assertEqual([str(first), str(second)], model["csvs"])

    def test_different_grader_or_context_never_merges(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            old = root / "old.csv"
            new = root / "new.csv"
            common = {
                "model": "fixture:latest", "status": "ok", "verdict": "pass",
                "wall_seconds": "2", "benchmark_family": "Smoke", "category": "smoke",
                "host": "spark", "host_label": "NVIDIA DGX Spark",
                "model_digest": "sha256:same", "suite_version": "0.4.0",
                "ollama_version": "0.32.13", "telemetry_backend": "nvidia-smi",
                "grading_profile": "behavioral-v1", "runner_sha256": "runner-new",
                "grader_sha256": "grader-old", "context_policy": "explicit",
                "requested_num_ctx": "131072", "model_context_length": "262144",
            }
            self.write_csv(old, [{**common, "task_id": "task-a"}])
            changed = {**common, "grader_sha256": "grader-new", "requested_num_ctx": "262144"}
            self.write_csv(new, [{**changed, "task_id": "task-b"}])
            os.utime(old, (1, 1))
            os.utime(new, (2, 2))
            summary, _ = dashboard.load_standardized_summary([old, new])
        model = summary["fixture:latest"]
        self.assertEqual(1, model["rows"])
        self.assertEqual([str(new)], model["csvs"])

    def test_grader_error_is_invalid_not_model_incorrect(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "invalid.csv"
            common = {
                "model": "fixture:latest", "status": "ok", "wall_seconds": "1",
                "benchmark_family": "Smoke", "category": "smoke", "host": "spark",
                "model_digest": "sha256:same", "suite_version": "0.4.0",
                "grading_profile": "behavioral-v1", "grader_sha256": "grader",
            }
            self.write_csv(path, [
                {**common, "task_id": "valid", "verdict": "pass"},
                {**common, "task_id": "invalid", "verdict": "grader_error", "grader_error": "worker protocol failed"},
            ])
            summary, _ = dashboard.load_standardized_summary(path)
        model = summary["fixture:latest"]
        self.assertEqual(1, model["passed"])
        self.assertEqual(1, model["tasks"])
        self.assertEqual(1, model["grader_errors"])

    def test_linux_dashboard_without_openclaw_has_no_stale_recommendation(self):
        specs = {
            "cpu": "NVIDIA Grace CPU", "cpu_small": "20 cores · aarch64",
            "gpu": "NVIDIA GB10", "gpu_small": "driver fixture",
            "memory": "121.7 GB", "memory_label": "Unified memory",
            "memory_small": "CPU/GPU coherent unified memory",
            "disk_free": "2.0 TB", "disk_small": "free on local / · 4.0 TB total",
            "os": "Linux 6.17.0-nvidia", "architecture": "aarch64", "product": "NVIDIA DGX Spark",
        }
        hermes = {
            "model": "qwen3.6:35b", "provider": "ollama", "runtime": "Local model",
            "runtime_class": "local", "context_text": "131,072 tokens", "fallbacks_text": "none",
        }
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            output = root / "dashboard.html"
            with mock.patch.object(dashboard, "OUTPUT_ROOT", root), \
                 mock.patch.object(dashboard, "OUT", output), \
                 mock.patch.object(dashboard, "DETAIL_DIR", root / "details"), \
                 mock.patch.object(dashboard, "HOST_LABEL", "NVIDIA DGX Spark"), \
                 mock.patch.object(dashboard, "DASHBOARD_TITLE", "NVIDIA DGX Spark LLM Dashboard"), \
                 mock.patch.object(dashboard, "load_ollama_models", return_value=[]), \
                 mock.patch.object(dashboard, "load_system_specs", return_value=specs), \
                 mock.patch.object(dashboard, "load_hermes_model_status", return_value=hermes), \
                 mock.patch.object(dashboard, "load_openclaw_status", return_value={"error": "not installed"}), \
                 mock.patch.object(dashboard, "all_csvs", return_value=[]), \
                 contextlib.redirect_stdout(io.StringIO()):
                dashboard.main()
            rendered = output.read_text(encoding="utf-8")
        self.assertIn("NVIDIA DGX Spark LLM Dashboard", rendered)
        self.assertNotIn("ollama/gemma4:26b-mlx", rendered)
        self.assertNotIn("openclaw-hardware-ranking", rendered)


if __name__ == "__main__":
    unittest.main()
