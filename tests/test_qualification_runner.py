import copy
import contextlib
import csv
import importlib.util
import io
import json
import pathlib
import tempfile
import threading
import time
import unittest
from unittest import mock


ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPT_DIR = ROOT / "scripts"
SPEC = importlib.util.spec_from_file_location(
    "qualification_runner",
    SCRIPT_DIR / "ollama_standardized_local_benchmarks.py",
)
runner = importlib.util.module_from_spec(SPEC)
import sys
sys.path.insert(0, str(SCRIPT_DIR))
SPEC.loader.exec_module(runner)


class ModelDiscoveryTests(unittest.TestCase):
    def test_ollama_show_requests_verbose_architecture_metadata(self):
        with mock.patch.object(runner, "req_json", return_value={}) as request:
            runner.ollama_show("fixture:latest", "http://127.0.0.1:11434")
        request.assert_called_once_with(
            "http://127.0.0.1:11434/api/show",
            {"name": "fixture:latest", "verbose": True},
            timeout=30,
        )


def fixture_linux_resource_snapshot(sampled=1.0):
    return {
        "mem_total_bytes": 128 * 1024**3,
        "mem_available_bytes": 120 * 1024**3,
        "swap_total_bytes": 8 * 1024**3,
        "swap_free_bytes": 8 * 1024**3,
        "swap_used_bytes": 0,
        "oom_kill": 0,
        "pswpout": 0,
        "sampled_monotonic_seconds": float(sampled),
    }


def fixture_task_resource_guard(model, *_args, **_kwargs):
    watchdog = mock.Mock()
    watchdog.bind_connection = mock.Mock()
    watchdog.stop_and_join = mock.Mock()
    baseline=fixture_linux_resource_snapshot(2.0)
    parallelism=int(model.get("context_kv_parallelism") or 1)
    admission=runner.context_candidate_admission(
        {**model,"context_kv_parallelism":parallelism},
        int(model["requested_num_ctx"]),baseline,
        prior_attempts=model.get("context_calibration_attempts") or (),
    )
    return {
        "watchdog":watchdog,"baseline":baseline,"admission":admission,
        "parallelism":parallelism,
        "parallelism_source":model.get("context_kv_parallelism_source") or "",
        "system_page_size_bytes":runner.SYSTEM_PAGE_SIZE_BYTES,
        "fixture_campaign_baseline":fixture_linux_resource_snapshot(1.0),
    }


def fixture_task_resource_finish(guard, *_args, **_kwargs):
    campaign=guard.get("fixture_campaign_baseline") or fixture_linux_resource_snapshot(1.0)
    task=guard.get("baseline") or fixture_linux_resource_snapshot(2.0)
    recovery={**task,"sampled_monotonic_seconds":3.0}
    return {
        "system_page_size_bytes": runner.SYSTEM_PAGE_SIZE_BYTES,
        "context_kv_parallelism":guard.get("parallelism",1),
        "context_kv_parallelism_source":guard.get("parallelism_source","") or "",
        "memory_watchdog_ready_verified":True,
        "gpu_watchdog_ready_verified":True,
        "memory_watchdog_join_verified":True,
        "gpu_watchdog_join_verified":True,
        "memory_watchdog_error":"","gpu_watchdog_error":"",
        "watchdog_triggered": False, "resource_pressure_reason": "",
        "watchdog_trigger_seconds": "", "watchdog_join_verified": True,
        "watchdog_target_stop_returned": "", "memory_recovery_verified": True,
        "recovery_snapshot":recovery,
        "campaign_resource_baseline":campaign,"task_resource_baseline":task,
        "admission":guard.get("admission") or {},
        "mem_available_min_bytes":task["mem_available_bytes"],
        "swap_used_max_bytes":task["swap_used_bytes"],
        "oom_kill_before":task["oom_kill"],"oom_kill_after":recovery["oom_kill"],
        "pswpout_before":campaign["pswpout"],"pswpout_max":recovery["pswpout"],
        "pswpout_after":recovery["pswpout"],"infrastructure_error":"",
    }


class QualificationEvidenceTests(unittest.TestCase):
    def test_trace_evidence_counts_separated_and_inline_transports(self):
        evidence = runner.reasoning_trace_evidence(
            "separate reasoning",
            "<think>inline one</think>answer<think>two</think>",
        )

        self.assertTrue(evidence["reasoning_trace_observed"])
        self.assertEqual("both", evidence["reasoning_transport"])
        self.assertEqual(len("separate reasoning"), evidence["separated_thinking_chars"])
        self.assertEqual(len("inline one") + len("two"), evidence["inline_thinking_chars"])
        self.assertIn("separated:", evidence["reasoning_trace_evidence"])
        self.assertIn("inline:", evidence["reasoning_trace_evidence"])

    def test_empty_marker_is_not_evidence_but_unclosed_content_is(self):
        empty = runner.reasoning_trace_evidence("", "<think></think>answer")
        unclosed = runner.reasoning_trace_evidence("", "answer<think>unfinished")

        self.assertFalse(empty["reasoning_trace_observed"])
        self.assertEqual("none", empty["reasoning_transport"])
        self.assertEqual(0, empty["inline_thinking_chars"])
        self.assertTrue(unclosed["reasoning_trace_observed"])
        self.assertEqual(len("unfinished"), unclosed["inline_thinking_chars"])

    def test_off_trace_is_scientific_protocol_evidence_not_an_exception(self):
        evidence = runner.reasoning_trace_evidence("", "<think>visible</think>answer")
        protocol = runner.protocol_fields_for_treatment(
            {"treatment_role": "off"}, evidence
        )

        self.assertFalse(protocol["protocol_valid"])
        self.assertIn("reasoning trace", protocol["protocol_error"])

    def test_schema_v3_row_fields_carry_dynamic_disposition_and_static_policy(self):
        work = {
            "pair_schema_version": 3,
            "qualification_phase": "qualification",
            "qualification_task": True,
            "qualification_required": True,
            "qualification_probe": "fallback",
            "model_qualification_status": "on-control-unverified",
            "model_qualification_reason": "no trace after fallback",
            "omitted_remaining_work_count": 12,
            "evidence_code": "on-trace-not-observed",
        }
        model = {
            "control_policy": "boolean-toggle",
            "off_observability": "observable",
            "evidence_code": "fixture-control-policy",
        }
        fields = runner.qualification_row_fields(
            work, model, runner.reasoning_trace_evidence("", "answer")
        )

        self.assertEqual("qualification", fields["qualification_phase"])
        self.assertTrue(fields["qualification_task"])
        self.assertEqual("fallback", fields["qualification_probe"])
        self.assertEqual("on-control-unverified", fields["model_qualification_status"])
        self.assertEqual(12, fields["omitted_remaining_work_count"])
        self.assertEqual("boolean-toggle", fields["control_policy"])
        self.assertEqual("observable", fields["off_observability"])


class AdaptiveContextCalibrationTests(unittest.TestCase):
    MODEL = {
        "name": "fixture-thinking:latest",
        "context_length": 65536,
        "size": 1024**3,
        "model_info": {
            "fixture.context_length": 65536,
            "fixture.block_count": 8,
            "fixture.embedding_length": 1024,
            "fixture.attention.head_count": 8,
            "fixture.attention.head_count_kv": 2,
        },
    }
    RESOURCE = {
        "mem_total_bytes": 128 * 1024**3,
        "mem_available_bytes": 120 * 1024**3,
        "swap_total_bytes": 8 * 1024**3,
        "swap_free_bytes": 8 * 1024**3,
        "swap_used_bytes": 0,
        "oom_kill": 10,
        "pswpout": 20,
    }

    @staticmethod
    def result(num_ctx, *, success=False, capacity=False, error=""):
        return {
            "num_ctx": num_ctx,
            "success": success,
            "status": "success" if success else "capacity-failure" if capacity else "inconclusive",
            "capacity_failure": capacity,
            "loaded_context_length": num_ctx if success else "",
            "unload_verified": True,
            "attempted": True, "request_issued": True, "admitted": True,
            "watchdog_join_verified": True,
            "memory_watchdog_ready_verified": True,
            "gpu_watchdog_ready_verified": True,
            "memory_watchdog_join_verified": True,
            "gpu_watchdog_join_verified": True,
            "memory_watchdog_error": "", "gpu_watchdog_error": "",
            "memory_recovery_verified": True,
            "watchdog_triggered": False, "infrastructure_failure": False,
            "oom_kill_delta": 0, "resource_pressure_reason": "",
            "system_page_size_bytes": runner.SYSTEM_PAGE_SIZE_BYTES,
            "error": error,
        }

    def test_native_fit_stops_after_one_verified_attempt(self):
        calls = []

        def attempt(_model, candidate, _base_url):
            calls.append(candidate)
            return self.result(candidate, success=True)

        calibrated = runner.calibrate_adaptive_model_context(
            self.MODEL, attempt_fn=attempt
        )

        self.assertEqual([8192, 16384, 32768, 65536], calls)
        self.assertEqual(65536, calibrated["requested_num_ctx"])
        self.assertEqual("native-fit", calibrated["context_calibration_status"])
        self.assertFalse(calibrated["context_adjusted"])

    def test_capacity_failures_halve_then_binary_refine_at_8192_steps(self):
        calls = []

        def attempt(_model, candidate, _base_url):
            calls.append(candidate)
            succeeds = candidate <= 40960
            return self.result(
                candidate, success=succeeds, capacity=not succeeds,
                error="out of memory" if not succeeds else "",
            )

        calibrated = runner.calibrate_adaptive_model_context(
            self.MODEL, attempt_fn=attempt
        )

        self.assertEqual([8192, 16384, 32768, 65536, 49152, 40960], calls)
        self.assertEqual(40960, calibrated["requested_num_ctx"])
        self.assertEqual("adjusted-fit", calibrated["context_calibration_status"])
        self.assertTrue(calibrated["context_adjusted"])
        self.assertIn("65536", calibrated["context_adjustment_reason"])

    def test_non_capacity_error_does_not_trigger_context_lowering(self):
        calls = []

        def attempt(_model, candidate, _base_url):
            calls.append(candidate)
            return self.result(candidate, error="connection refused")

        calibrated = runner.calibrate_adaptive_model_context(
            self.MODEL, attempt_fn=attempt
        )

        self.assertEqual([8192], calls)
        self.assertIsNone(calibrated["requested_num_ctx"])
        self.assertEqual("no-fit", calibrated["context_calibration_status"])
        self.assertIn("connection refused", calibrated["context_adjustment_reason"])

    def test_inconclusive_refinement_retains_last_verified_lower_fit(self):
        calls = []

        def attempt(_model, candidate, _base_url):
            calls.append(candidate)
            if candidate <= 32768:
                return self.result(candidate, success=True)
            if candidate == 65536:
                return self.result(candidate, capacity=True, error="out of memory")
            return self.result(candidate, error="runner disconnected")

        calibrated = runner.calibrate_adaptive_model_context(
            self.MODEL, attempt_fn=attempt
        )

        self.assertEqual([8192, 16384, 32768, 65536, 49152], calls)
        self.assertEqual(32768, calibrated["requested_num_ctx"])
        self.assertEqual("adjusted-fit", calibrated["context_calibration_status"])
        self.assertIn("refinement stopped", calibrated["context_adjustment_reason"])

    def test_load_probe_uses_empty_prompt_and_requires_ps_context_then_unloads(self):
        daemon_identity_resolver = mock.Mock(
            side_effect=AssertionError("unit test inspected the host Ollama daemon")
        )
        parallelism_resolver = mock.Mock(
            side_effect=AssertionError("unit test inspected host Ollama parallelism")
        )
        responses = [
            {"models": []},
            {"load_duration": 1_000_000_000, "total_duration": 2_000_000_000},
            {"models": [{
                "name": self.MODEL["name"], "context_length": 65536,
                "size": 100, "size_vram": 90,
            }]},
            {"models": []},
        ]
        with mock.patch.object(runner, "stop_model", return_value=True) as stop, \
             mock.patch.object(runner, "req_json", side_effect=responses) as request:
            attempt = runner.context_load_calibration_attempt(
                self.MODEL, 65536, "http://fixture:11434",
                resource_reader=lambda: dict(self.RESOURCE),
                gpu_process_reader=lambda: [],
                nvidia_runtime_detector=lambda: False,
                daemon_identity_resolver=daemon_identity_resolver,
                parallelism_resolver=parallelism_resolver,
                ollama_version="fixture", identity_verifier=lambda *_args: True,
            )

        self.assertTrue(attempt["success"])
        self.assertTrue(attempt["unload_verified"])
        self.assertEqual(65536, attempt["loaded_context_length"])
        self.assertEqual(2, stop.call_count)
        load_call = request.call_args_list[1]
        payload = load_call.args[1]
        self.assertEqual("", payload["prompt"])
        self.assertFalse(payload["stream"])
        self.assertEqual(65536, payload["options"]["num_ctx"])
        self.assertNotIn("num_predict", payload["options"])
        daemon_identity_resolver.assert_not_called()
        parallelism_resolver.assert_not_called()

    def test_unrelated_resident_model_aborts_calibration_without_stopping_it(self):
        contaminated = {"models": [{"name": "hermes-live:latest"}]}
        with mock.patch.object(runner, "stop_model", return_value=True) as stop, \
             mock.patch.object(runner, "req_json", side_effect=[contaminated, contaminated]) as request:
            attempt = runner.context_load_calibration_attempt(
                self.MODEL, 65536, "http://fixture:11434",
                ollama_version="fixture",
            )

        self.assertFalse(attempt["success"])
        self.assertFalse(attempt["capacity_failure"])
        self.assertTrue(attempt["infrastructure_failure"])
        self.assertFalse(attempt["unload_verified"])
        self.assertIn("hermes-live:latest", attempt["error"])
        self.assertEqual(
            [self.MODEL["name"], self.MODEL["name"]],
            [call.args[0] for call in stop.call_args_list],
        )
        self.assertTrue(all(call.args[0].endswith("/api/ps") for call in request.call_args_list))
        with self.assertRaisesRegex(
            runner.ContextCalibrationContaminationError, "hermes-live"
        ):
            runner.calibrate_adaptive_model_context(
                self.MODEL, attempt_fn=lambda *_args: attempt
            )

    def test_cold_paired_residency_rejects_unrelated_model_without_polling(self):
        sleeper = mock.Mock()
        with mock.patch.object(
            runner, "req_json",
            return_value={"models": [{"name": "hermes-live:latest"}]},
        ):
            with self.assertRaisesRegex(RuntimeError, "residency contaminated"):
                runner.verify_empty_paired_residency(
                    self.MODEL["name"], "http://fixture:11434", sleeper=sleeper
                )
        sleeper.assert_not_called()

    def test_cold_paired_residency_retries_only_expected_model_stop(self):
        stop_request = mock.Mock(return_value=True)
        sleeper = mock.Mock()
        states = [
            {"models": [{"name": self.MODEL["name"]}]},
            {"models": [{"name": self.MODEL["name"]}]},
            {"models": []},
        ]
        with mock.patch.object(runner, "req_json", side_effect=states):
            self.assertTrue(runner.verify_empty_paired_residency(
                self.MODEL["name"], "http://fixture:11434",
                stop_request=stop_request, sleeper=sleeper,
            ))

        self.assertEqual(2, stop_request.call_count)
        self.assertTrue(all(call.args[0] == self.MODEL["name"] for call in stop_request.call_args_list))
        self.assertEqual(2, sleeper.call_count)

    def test_contamination_is_persisted_as_infrastructure_failure(self):
        model = {
            **self.MODEL, "digest": "sha256:fixture",
            "capabilities": ["completion", "thinking"],
        }

        def contaminated(current, _base_url, *, on_attempt, **_kwargs):
            attempt = {
                **self.result(current["context_length"], error="hermes-live resident"),
                "infrastructure_failure": True,
            }
            on_attempt(current, [attempt])
            raise runner.ContextCalibrationContaminationError("hermes-live resident")

        with tempfile.TemporaryDirectory() as tmp:
            artifact = pathlib.Path(tmp) / "calibration.json"
            with contextlib.redirect_stdout(io.StringIO()):
                with self.assertRaisesRegex(
                    runner.ContextCalibrationContaminationError, "hermes-live"
                ):
                    runner.calibrate_adaptive_contexts(
                        [model], "http://fixture:11434", artifact,
                        run_id="run", report_prefix="report", calibrate_fn=contaminated,
                        ollama_version="fixture",
                    )
            document = json.loads(artifact.read_text(encoding="utf-8"))

        self.assertEqual("infrastructure-failure", document["models"][0]["status"])
        self.assertIn("hermes-live", document["models"][0]["context_adjustment_reason"])

    def test_daemon_outage_during_calibration_is_global_infrastructure_failure(self):
        outage = ConnectionRefusedError("Ollama daemon down")
        with mock.patch.object(runner, "stop_model", return_value=False), \
             mock.patch.object(
                 runner, "req_json", side_effect=[outage, outage, outage]
             ) as request, \
             self.assertRaisesRegex(
                 runner.ContextCalibrationContaminationError, "health check failed"
             ):
            runner.calibrate_adaptive_model_context(
                self.MODEL, "http://fixture:11434",
                resource_reader=lambda: dict(self.RESOURCE),
                gpu_process_reader=lambda: [],
                nvidia_runtime_detector=lambda: False,
                ollama_version="fixture",
            )

        self.assertTrue(request.call_args_list[-1].args[0].endswith("/api/tags"))

    def test_healthy_daemon_keeps_noncapacity_calibration_failure_model_scoped(self):
        responses = [
            {"models": []},
            ConnectionRefusedError("model runner connection failed"),
            {"models": []},
            {"models": []},
        ]
        with mock.patch.object(runner, "stop_model", return_value=True), \
             mock.patch.object(runner, "req_json", side_effect=responses) as request:
            calibrated = runner.calibrate_adaptive_model_context(
                self.MODEL, "http://fixture:11434",
                resource_reader=lambda: dict(self.RESOURCE),
                gpu_process_reader=lambda: [],
                nvidia_runtime_detector=lambda: False,
                ollama_version="fixture", identity_verifier=lambda *_args: True,
            )

        self.assertEqual("no-fit", calibrated["context_calibration_status"])
        self.assertIsNone(calibrated["requested_num_ctx"])
        self.assertTrue(request.call_args_list[-1].args[0].endswith("/api/tags"))


class ResourceGuardV2Tests(unittest.TestCase):
    GIB = 1024**3

    @staticmethod
    def resource(*, available=110 * 1024**3, swap_used=0, oom=4, pswpout=7):
        total = 128 * 1024**3
        swap_total = 8 * 1024**3
        return {
            "mem_total_bytes": total, "mem_available_bytes": available,
            "swap_total_bytes": swap_total,
            "swap_free_bytes": swap_total - swap_used,
            "swap_used_bytes": swap_used, "oom_kill": oom,
            "pswpout": pswpout,
        }

    @staticmethod
    def small_model():
        return {
            "name": "fixture:latest", "context_length": 65536,
            "requested_num_ctx": 8192, "size": 2 * 1024**3,
            "model_info": {
                "fixture.context_length": 65536,
                "fixture.block_count": 8,
                "fixture.embedding_length": 1024,
                "fixture.attention.head_count": 8,
                "fixture.attention.head_count_kv": 2,
            },
        }

    def test_meminfo_vmstat_parsing_is_strict_and_uses_kib(self):
        parsed = runner.parse_linux_meminfo(
            "MemTotal: 131072 kB\nMemAvailable: 65536 kB\n"
            "SwapTotal: 4096 kB\nSwapFree: 3072 kB\n"
        )
        self.assertEqual(131072 * 1024, parsed["mem_total_bytes"])
        self.assertEqual(1024 * 1024, parsed["swap_used_bytes"])
        self.assertEqual(
            {"oom_kill": 3, "pswpout": 17},
            runner.parse_linux_vmstat_counters("oom_kill 3\npswpout 17\n"),
        )
        with self.assertRaisesRegex(RuntimeError, "duplicates"):
            runner.parse_linux_meminfo(
                "MemTotal: 10 kB\nMemTotal: 10 kB\nMemAvailable: 5 kB\n"
                "SwapTotal: 0 kB\nSwapFree: 0 kB\n"
            )

    def test_full_ram_policy_keeps_only_four_gib_buffer(self):
        total = 128 * self.GIB
        self.assertEqual(4*self.GIB, runner.context_required_headroom(total))
        self.assertEqual(4*self.GIB, runner.context_operating_headroom(total))
        self.assertEqual(0, runner.CONTEXT_SWAP_GROWTH_LIMIT_BYTES)

    def test_mistral_native_kv_estimate_is_88_gib_and_small_buffer_rejects(self):
        model = {
            "name": "mistral-medium-3.5:128b", "context_length": 262144,
            "size": 80_000_000_000,
            "model_info": {
                "mistral.context_length": 262144,
                "mistral.block_count": 88,
                "mistral.embedding_length": 4096,
                "mistral.attention.head_count": 32,
                "mistral.attention.head_count_kv": 8,
                "mistral.attention.key_length": 128,
                "mistral.attention.value_length": 128,
            },
        }
        estimate = runner.estimate_context_candidate_bytes(model, 262144)
        self.assertEqual(94_489_280_512, estimate["kv_cache_estimate_bytes"])
        admission=runner.context_candidate_admission(
            model,262144,self.resource(available=115*self.GIB)
        )
        self.assertFalse(admission["admitted"])
        self.assertLess(
            admission["projected_mem_available_bytes"],
            admission["headroom_required_bytes"],
        )

    def test_workspace_estimate_is_recorded_but_does_not_reserve_ram(self):
        estimate = {
            "admission_estimator_complete": True,
            "admission_estimator_error": "",
            "model_blob_bytes": 70 * self.GIB,
            "kv_cache_estimate_bytes": self.GIB,
            "workspace_allowance_bytes": 8 * self.GIB,
            "static_peak_estimate_bytes": 79 * self.GIB,
        }
        with mock.patch.object(
            runner, "estimate_context_candidate_bytes", return_value=estimate
        ):
            admission = runner.context_candidate_admission(
                self.small_model(), 8192,
                self.resource(available=101 * self.GIB),
            )
        self.assertTrue(admission["admitted"])
        self.assertGreater(
            admission["projected_mem_available_bytes"],
            0,
        )

    def test_qwen_policy_never_reduces_observed_kv_heads(self):
        model = {
            "name": "qwen3.5:fixture", "size": 2 * self.GIB,
            "model_info": {
                "qwen35.context_length": 65536,
                "qwen35.block_count": 40,
                "qwen35.embedding_length": 4096,
                "qwen35.attention.head_count": 32,
                "qwen35.attention.head_count_kv": 8,
                "qwen35.full_attention_interval": 4,
            },
        }
        estimate = runner.estimate_context_candidate_bytes(model, 8192)
        self.assertTrue(estimate["admission_estimator_complete"])
        self.assertEqual(8, estimate["kv_head_count"])
        self.assertEqual(0, estimate["local_attention_blocks"])

    def test_nemotron35_lightning_uses_metadata_sparse_attention_layers(self):
        kv_layout = [0] * 53
        for index in (5, 12, 19, 26, 33, 42, 52):
            kv_layout[index] = 2
        model = {
            "name": "nemotron-3.5-lightning:latest",
            "family": "nemotron_h_moe",
            "size": 25_430_749_387,
            "model_info": {
                "nemotron_h_moe.context_length": 1_048_576,
                "nemotron_h_moe.block_count": 53,
                "nemotron_h_moe.attention.head_count": 32,
                "nemotron_h_moe.attention.head_count_kv": kv_layout,
                "nemotron_h_moe.attention.key_length": 128,
                "nemotron_h_moe.attention.value_length": 128,
            },
        }
        estimate = runner.estimate_context_candidate_bytes(model, 1_048_576)
        self.assertTrue(estimate["admission_estimator_complete"])
        self.assertEqual(
            "nemotron-h-moe-metadata-attention-layers",
            estimate["context_estimator_policy"],
        )
        self.assertEqual(7, estimate["full_attention_blocks"])
        self.assertEqual(0, estimate["local_attention_blocks"])
        self.assertEqual(2, estimate["kv_head_count"])
        self.assertEqual(7 * self.GIB, estimate["kv_cache_estimate_bytes"])

    def test_tag_name_cannot_override_contradictory_global_metadata(self):
        model = {
            "name": "gemma4-custom:latest", "size": 2 * self.GIB,
            "model_info": {
                "llama.context_length": 262144,
                "llama.block_count": 60,
                "llama.embedding_length": 8192,
                "llama.attention.head_count": 32,
                "llama.attention.head_count_kv": 16,
                "llama.attention.key_length": 256,
                "llama.attention.value_length": 256,
            },
        }
        estimate = runner.estimate_context_candidate_bytes(model, 262144)
        self.assertEqual("metadata-all-layers-global", estimate["context_estimator_policy"])
        self.assertEqual(240 * self.GIB, estimate["kv_cache_estimate_bytes"])

    def test_missing_frozen_ollama_version_is_global_and_zero_request(self):
        with mock.patch.object(runner, "req_json") as request, \
             mock.patch.object(runner, "stop_model") as stop:
            attempt = runner.context_load_calibration_attempt(
                self.small_model(), 8192, "http://fixture:11434",
                ollama_version="",
            )
        self.assertTrue(attempt["infrastructure_failure"])
        self.assertIn("nonempty frozen Ollama version", attempt["error"])
        request.assert_not_called(); stop.assert_not_called()

    def test_external_gpu_process_aborts_without_stopping_it(self):
        model = self.small_model()
        process = [{"pid": 321, "process_name": "python ComfyUI", "used_gpu_memory_bytes": ""}]
        with mock.patch.object(runner, "stop_model", return_value=True) as stop, \
             mock.patch.object(
                 runner, "req_json", side_effect=[{"models": []}, {"models": []}]
             ) as request:
            attempt = runner.context_load_calibration_attempt(
                model, 8192, "http://fixture:11434",
                resource_reader=lambda: self.resource(),
                gpu_process_reader=lambda: process,
                ollama_version="fixture",
            )
        self.assertTrue(attempt["infrastructure_failure"])
        self.assertIn("ComfyUI", attempt["error"])
        self.assertTrue(all(call.args[0] == model["name"] for call in stop.call_args_list))
        self.assertFalse(any(call.args[0].endswith("/api/generate") for call in request.call_args_list))

    def test_unknown_hybrid_is_model_no_fit_without_generate(self):
        model = {
            "name": "future-hybrid:latest", "context_length": 65536,
            "size": 2 * self.GIB,
            "model_info": {
                "future.context_length": 65536,
                "future.block_count": 40,
                "future.embedding_length": 4096,
                "future.attention.head_count": 32,
                "future.full_attention_interval": 4,
            },
        }
        with mock.patch.object(runner, "stop_model", return_value=True), \
             mock.patch.object(
                 runner, "req_json", side_effect=[{"models": []}, {"models": []}]
             ) as request:
            calibrated = runner.calibrate_adaptive_model_context(
                model, "http://fixture:11434",
                resource_reader=lambda: self.resource(),
                gpu_process_reader=lambda: [],
                nvidia_runtime_detector=lambda: False,
                ollama_version="fixture",
            )
        self.assertEqual("no-fit", calibrated["context_calibration_status"])
        self.assertFalse(any(call.args[0].endswith("/api/generate") for call in request.call_args_list))

    def test_nvidia_parser_treats_na_memory_as_a_real_process(self):
        proc = mock.Mock(returncode=0, stdout="777, python3, N/A\n", stderr="")
        processes = runner.query_nvidia_compute_processes(
            runner=lambda *_args, **_kwargs: proc,
            which=lambda _name: "/usr/bin/nvidia-smi",
        )
        self.assertEqual(777, processes[0]["pid"])
        self.assertEqual("", processes[0]["used_gpu_memory_bytes"])

    def test_watchdog_late_bind_refuses_request_after_pressure_trigger(self):
        stop = mock.Mock(return_value=True)
        watchdog = runner.ContextResourceWatchdog(
            "fixture", self.resource(), resource_reader=lambda: self.resource(),
            gpu_reader=lambda: [], stop_fn=stop,
        )
        watchdog.started_at = 0
        watchdog._trigger("reserve crossed")
        connection = mock.Mock()
        with self.assertRaisesRegex(RuntimeError, "cancelled before request"):
            watchdog.bind_connection(connection)
        connection.close.assert_called_once()
        stop.assert_called_once()

    def test_pressure_then_oom_escalates_to_infrastructure(self):
        watchdog = runner.ContextResourceWatchdog(
            "fixture", self.resource(), resource_reader=lambda: self.resource(),
            gpu_reader=lambda: [], stop_fn=mock.Mock(return_value=True),
        )
        watchdog.started_at = 0
        watchdog._trigger("headroom")
        watchdog._trigger("kernel oom_kill counter increased", infrastructure=True)
        self.assertTrue(watchdog.triggered)
        self.assertIn("oom_kill", watchdog.infrastructure_error)

    def test_four_gib_buffer_triggers_before_swap_growth(self):
        high=self.resource()
        low=self.resource(
            available=runner.context_operating_headroom(high["mem_total_bytes"])-1
        )
        stop=mock.Mock(return_value=True)
        watchdog=runner.ContextResourceWatchdog(
            "fixture",high,resource_reader=lambda:low,
            gpu_reader=lambda:[],stop_fn=stop,
        )
        watchdog.started_at=0
        watchdog._sample_resources_once()
        self.assertTrue(watchdog.triggered)
        self.assertIn("below buffer",watchdog.resource_pressure_reason)
        self.assertEqual(high["swap_used_bytes"],low["swap_used_bytes"])
        stop.assert_called_once()

    def test_blocked_initial_gpu_poll_fails_closed_before_request(self):
        entered=threading.Event(); release=threading.Event(); request=mock.Mock()
        stopped=threading.Event()

        def blocked_gpu_reader():
            entered.set()
            release.wait(2)
            return []

        watchdog=runner.ContextResourceWatchdog(
            "fixture",self.resource(),resource_reader=lambda:self.resource(),
            gpu_reader=blocked_gpu_reader,
            stop_fn=lambda *_args:(stopped.set() or True),
            poll_interval=0.01,gpu_poll_interval=0.01,
            startup_timeout=0.05,
        )

        def launch():
            watchdog.start()
            if not watchdog.triggered and not watchdog.infrastructure_error:
                request()

        worker=threading.Thread(target=launch)
        worker.start()
        self.assertTrue(entered.wait(1))
        self.assertTrue(stopped.wait(1))
        request.assert_not_called()
        release.set(); worker.join(2)
        self.assertFalse(worker.is_alive())
        request.assert_not_called()
        self.assertIn("initial poll",watchdog.infrastructure_error)
        self.assertTrue(watchdog.join_verified)

    def test_blocked_gpu_poll_cannot_delay_swap_pressure_cancellation(self):
        entered=threading.Event(); release=threading.Event(); stopped=threading.Event()
        request=mock.Mock()
        high=self.resource()
        low=self.resource(swap_used=high["swap_used_bytes"]+1)

        def resource_reader():
            return low if entered.is_set() else high

        def blocked_gpu_reader():
            entered.set()
            release.wait(2)
            return []

        def stop_target(*_args):
            stopped.set()
            return True

        watchdog=runner.ContextResourceWatchdog(
            "fixture",high,resource_reader=resource_reader,
            gpu_reader=blocked_gpu_reader,stop_fn=stop_target,
            poll_interval=0.01,gpu_poll_interval=0.01,
        )

        def launch():
            watchdog.start()
            if not watchdog.triggered and not watchdog.infrastructure_error:
                request()

        worker=threading.Thread(target=launch)
        worker.start()
        self.assertTrue(entered.wait(1))
        self.assertTrue(stopped.wait(1),"memory watchdog did not cancel promptly")
        request.assert_not_called()
        self.assertTrue(watchdog.triggered)
        release.set(); worker.join(2)
        self.assertFalse(worker.is_alive())
        self.assertTrue(watchdog.join_verified)
        self.assertTrue(watchdog.memory_join_verified)
        self.assertTrue(watchdog.gpu_join_verified)
        request.assert_not_called()

    def test_transient_unowned_gpu_pid_is_confirmed_before_abort(self):
        process={"pid":321,"process_name":"[No data]","used_gpu_memory_bytes":""}
        classifier_calls=[]

        def classifier(_process,_daemon):
            classifier_calls.append(True)
            return len(classifier_calls) > 1

        stop_target=mock.Mock(return_value=True)
        watchdog=runner.ContextResourceWatchdog(
            "fixture",self.resource(),resource_reader=lambda:self.resource(),
            gpu_reader=lambda:[process],stop_fn=stop_target,
            poll_interval=0.01,gpu_poll_interval=0.01,
        )
        with mock.patch.object(
            runner,"_ollama_runner_compute_process",side_effect=classifier
        ):
            watchdog.start()
            self.assertTrue(watchdog.gpu_ready_event.is_set())
            self.assertFalse(watchdog.triggered)
            self.assertEqual("",watchdog.infrastructure_error)
            watchdog.stop_and_join()

        self.assertGreaterEqual(len(classifier_calls),2)
        self.assertTrue(watchdog.memory_join_verified)
        self.assertTrue(watchdog.gpu_join_verified)
        stop_target.assert_not_called()

    def test_transient_ollama_binary_name_gets_identity_grace(self):
        process={
            "pid":321,"process_name":"/usr/local/bin/ollama",
            "used_gpu_memory_bytes":"",
        }
        classifier_calls=[]

        def classifier(_process,_daemon):
            classifier_calls.append(True)
            return len(classifier_calls) > 1

        stop_target=mock.Mock(return_value=True)
        watchdog=runner.ContextResourceWatchdog(
            "fixture",self.resource(),resource_reader=lambda:self.resource(),
            gpu_reader=lambda:[process],stop_fn=stop_target,
            poll_interval=0.01,gpu_poll_interval=0.01,
        )
        with mock.patch.object(
            runner,"_ollama_runner_compute_process",side_effect=classifier
        ):
            watchdog.start()
            self.assertTrue(watchdog.gpu_ready_event.is_set())
            self.assertFalse(watchdog.triggered)
            watchdog.stop_and_join()

        self.assertGreaterEqual(len(classifier_calls),2)
        stop_target.assert_not_called()

    def test_persistent_unverified_gpu_pid_fails_closed_after_grace(self):
        process={"pid":321,"process_name":"[No data]","used_gpu_memory_bytes":""}
        clock_values=iter((0.0,0.0,1.1,2.2,2.2,2.2,2.2,2.2))
        stop_target=mock.Mock(return_value=True)
        watchdog=runner.ContextResourceWatchdog(
            "fixture",self.resource(),resource_reader=lambda:self.resource(),
            gpu_reader=lambda:[process],stop_fn=stop_target,
            poll_interval=0.01,gpu_poll_interval=0.01,
            startup_timeout=1,clock=lambda:next(clock_values,2.2),
        )
        with mock.patch.object(
            runner,"_ollama_runner_compute_process",return_value=False
        ):
            watchdog.start()

        self.assertFalse(watchdog.gpu_ready_event.is_set())
        self.assertIn("external NVIDIA compute process",watchdog.infrastructure_error)
        self.assertTrue(watchdog.join_verified)
        self.assertTrue(watchdog.memory_join_verified)
        self.assertTrue(watchdog.gpu_join_verified)
        stop_target.assert_called_once()

    def test_exited_exact_ollama_runner_pid_gets_bounded_identity_grace(self):
        process={
            "pid":321,
            "process_name":"/usr/local/lib/ollama/llama-server",
            "used_gpu_memory_bytes":"",
        }
        gpu_reads=iter(([process],[]))
        stop_target=mock.Mock(return_value=True)
        watchdog=runner.ContextResourceWatchdog(
            "fixture",self.resource(),resource_reader=lambda:self.resource(),
            gpu_reader=lambda:next(gpu_reads,[]),stop_fn=stop_target,
            poll_interval=0.01,gpu_poll_interval=0.01,
        )
        with mock.patch.object(
            runner,"_ollama_runner_compute_process",return_value=False
        ):
            watchdog.start()
            self.assertTrue(watchdog.gpu_ready_event.is_set())
            self.assertFalse(watchdog.triggered)
            self.assertEqual("",watchdog.infrastructure_error)
            watchdog.stop_and_join()

        self.assertTrue(watchdog.join_verified)
        stop_target.assert_not_called()

    def test_parallelism_falls_back_to_exact_systemd_service_identity(self):
        daemon = {"pid": 42}
        main_pid = mock.Mock(returncode=0, stdout="42\n", stderr="")
        environment = mock.Mock(returncode=0, stdout="PATH=/usr/bin\n", stderr="")
        with tempfile.TemporaryDirectory() as tmp:
            value, source = runner.resolve_ollama_parallelism(
                daemon, pathlib.Path(tmp),
                runner=mock.Mock(side_effect=[main_pid, environment]),
            )
        self.assertEqual(1, value)
        self.assertIn("systemctl", source)

    def test_resolved_parallelism_four_is_frozen_through_plan_and_runtime(self):
        model = {
            **self.small_model(), "digest": "sha256:parallel-four",
            "family": "fixture", "capabilities": ["completion", "thinking"],
            "capabilities_known": True, "context_length": 8192,
        }
        attempt = AdaptiveContextCalibrationTests.result(8192, success=True)
        attempt.update(
            kv_parallelism=4,
            kv_parallelism_source="systemctl-ollama-service-environment-explicit",
        )
        calibrated = runner.calibrate_adaptive_model_context(
            model, attempt_fn=lambda *_args: attempt
        )
        self.assertEqual(4, calibrated["context_kv_parallelism"])
        plan = runner.build_paired_plan(
            [calibrated], [runner.TASKS[1], runner.TASKS[6]], num_ctx=None,
            timeout=1800, ollama_version="fixture", ollama_url="http://127.0.0.1:11434",
            host="spark", host_label="Spark", platform="linux", os_version="fixture",
            architecture="aarch64", telemetry_backend="none", telemetry_interval_ms=1000,
            no_stop=False, keep_alive="0s", residency_policy="cold-unload-every-task",
            suite_version="fixture", benchmark_profile=runner.BENCHMARK_PROFILE,
            grading_profile=runner.GRADING_PROFILE,
            output_token_policy=runner.OUTPUT_TOKEN_POLICY,
            runner_sha256="runner", grader_sha256="grader", planner_sha256="planner",
        )
        planned = plan["models"][0]
        self.assertEqual(4, planned["context_kv_parallelism"])
        watchdog = mock.Mock(infrastructure_error="", triggered=False)
        watchdog.start.return_value = watchdog
        with mock.patch.object(runner.shutil, "which", return_value="/usr/bin/nvidia-smi"):
            guard = runner.start_paired_task_resource_guard(
                planned, "http://127.0.0.1:11434",
                campaign_baseline=self.resource(),
                resource_reader=lambda: self.resource(),
                gpu_process_reader=lambda: [],
                daemon_identity_resolver=lambda: {
                    "pid": 42, "starttime": 7, "cgroup": "service"
                },
                parallelism_resolver=lambda _daemon: (
                    4, "systemctl-ollama-service-environment-explicit"
                ),
                watchdog_factory=lambda *_args, **_kwargs: watchdog,
            )
        self.assertEqual(4, guard["parallelism"])

    def test_calibration_identity_drift_aborts_before_generate(self):
        model = {**self.small_model(), "digest": "sha256:frozen"}
        responses = [
            {"models": []},
            {"version": "changed"},
            {"models": [{"name": model["name"], "digest": model["digest"]}]},
            {"models": []},
        ]
        with mock.patch.object(runner, "stop_model", return_value=True), \
             mock.patch.object(runner, "req_json", side_effect=responses) as request:
            attempt = runner.context_load_calibration_attempt(
                model, 8192, "http://fixture:11434", ollama_version="frozen",
                resource_reader=lambda: self.resource(),
                gpu_process_reader=lambda: [],
                nvidia_runtime_detector=lambda: False,
            )
        self.assertTrue(attempt["infrastructure_failure"])
        self.assertIn("identity check failed", attempt["error"])
        self.assertFalse(any(call.args[0].endswith("/api/generate") for call in request.call_args_list))

    def test_finish_guard_captures_recovery_reader_error(self):
        watchdog = mock.Mock(
            join_verified=True, triggered=False, infrastructure_error="",
            resource_pressure_reason="", trigger_seconds="",
            target_stop_returned=True, min_mem_available_bytes=1,
            max_swap_used_bytes=0, oom_kill_before=1, pswpout_before=1,
            max_pswpout=1,
            last_snapshot=None,
        )
        baseline = self.resource()
        evidence = runner.finish_paired_task_resource_guard(
            {"watchdog": watchdog, "baseline": baseline, "admission": {}},
            "fixture", campaign_baseline=baseline,
            resource_reader=mock.Mock(side_effect=OSError("meminfo unavailable")),
            gpu_process_reader=lambda: [],
        )
        self.assertFalse(evidence["memory_recovery_verified"])
        self.assertIn("recovery verification failed", evidence["infrastructure_error"])
        self.assertEqual(
            runner.SYSTEM_PAGE_SIZE_BYTES, evidence["system_page_size_bytes"]
        )

    def test_task_guard_waits_for_campaign_memory_recovery_before_admission(self):
        campaign=self.resource(available=120*self.GIB)
        low=self.resource(available=runner.CONTEXT_HEADROOM_MIN_BYTES-1)
        recovered=self.resource(available=120*self.GIB)
        reads=iter((low,low,recovered))
        watchdog=mock.Mock(infrastructure_error="",triggered=False)
        watchdog.start.return_value=watchdog
        guard=runner.start_paired_task_resource_guard(
            self.small_model(),"http://127.0.0.1:11434",
            campaign_baseline=campaign,
            resource_reader=lambda:next(reads),gpu_process_reader=lambda:[],
            watchdog_factory=lambda *_args,**_kwargs:watchdog,
            nvidia_runtime_detector=lambda:False,
            recovery_timeout=1,recovery_interval=0,
            clock=mock.Mock(side_effect=(0.0,0.1)),sleeper=lambda _seconds:None,
        )
        self.assertEqual(recovered,guard["baseline"])
        self.assertTrue(guard["admission"]["admitted"])

    def test_recovery_accepts_memavailable_cache_drift_when_buffer_and_swap_are_safe(self):
        baseline=self.resource(available=120*self.GIB)
        recovered=self.resource(available=32*self.GIB)
        ok,snapshot,error=runner.verify_context_resource_recovery(
            baseline,baseline["swap_used_bytes"],
            resource_reader=lambda:recovered,timeout=0,
        )
        self.assertTrue(ok)
        self.assertEqual(recovered,snapshot)
        self.assertEqual("",error)

    def test_recovery_rejects_memavailable_below_active_buffer(self):
        baseline=self.resource(available=120*self.GIB)
        unsafe=self.resource(available=runner.CONTEXT_HEADROOM_MIN_BYTES-1)
        ok,snapshot,error=runner.verify_context_resource_recovery(
            baseline,baseline["swap_used_bytes"],
            resource_reader=lambda:unsafe,timeout=0,
        )
        self.assertFalse(ok)
        self.assertEqual(unsafe,snapshot)
        self.assertIn("did not recover",error)

    def test_finish_guard_recovers_to_campaign_not_ratcheted_task_baseline(self):
        watchdog=mock.Mock(
            join_verified=True,triggered=False,infrastructure_error="",
            resource_pressure_reason="",trigger_seconds="",
            target_stop_returned=True,min_mem_available_bytes=1,
            max_swap_used_bytes=0,oom_kill_before=1,pswpout_before=1,
            max_pswpout=1,last_snapshot=None,
        )
        task=self.resource(available=90*self.GIB)
        campaign=self.resource(available=120*self.GIB)
        with mock.patch.object(
            runner,"verify_context_resource_recovery",
            return_value=(True,campaign,""),
        ) as verify:
            evidence=runner.finish_paired_task_resource_guard(
                {"watchdog":watchdog,"baseline":task,"admission":{}},
                "fixture",campaign_baseline=campaign,
                resource_reader=lambda:campaign,gpu_process_reader=lambda:[],
            )
        self.assertIs(verify.call_args.args[0],campaign)
        self.assertTrue(evidence["memory_recovery_verified"])

    def test_task_guard_rejects_frozen_page_size_drift_before_reading_resources(self):
        read_resources = mock.Mock(return_value=self.resource())
        with self.assertRaisesRegex(RuntimeError, "system page size drift"):
            runner.start_paired_task_resource_guard(
                self.small_model(), "http://127.0.0.1:11434",
                expected_system_page_size_bytes=runner.SYSTEM_PAGE_SIZE_BYTES * 2,
                resource_reader=read_resources,
                gpu_process_reader=lambda: [],
            )
        read_resources.assert_not_called()

    def test_guard_validator_requires_numeric_pressure_and_infrastructure_consistency(self):
        model={
            **self.small_model(),"requested_num_ctx":8192,
            "context_calibration_attempts":[],
        }
        guard=fixture_task_resource_finish(fixture_task_resource_guard(model))
        plan={
            "platform":"linux",
            "runtime_resource_safety_policy":{
                "system_page_size_bytes":runner.SYSTEM_PAGE_SIZE_BYTES,
            },
        }
        work={"model":model}

        pressure=copy.deepcopy(guard)
        pressure.update({
            "watchdog_triggered":True,"resource_pressure_reason":"reserve crossed",
            "watchdog_trigger_seconds":0.1,"watchdog_target_stop_returned":True,
        })
        mismatches=[]
        runner._validate_resume_resource_guard(
            pressure,plan,{"termination_reason":runner.RESOURCE_PRESSURE_CANCELLED},
            work,mismatches,
        )
        self.assertTrue(any("numeric pressure evidence" in item for item in mismatches))

        pressure["swap_used_max_bytes"]=(
            pressure["campaign_resource_baseline"]["swap_used_bytes"]+1
        )
        mismatches=[]
        runner._validate_resume_resource_guard(
            pressure,plan,{"termination_reason":runner.RESOURCE_PRESSURE_CANCELLED},
            work,mismatches,
        )
        self.assertEqual([],mismatches)

        infrastructure=copy.deepcopy(guard)
        infrastructure.update({
            "gpu_watchdog_ready_verified":False,
            "gpu_watchdog_error":"GPU query failed",
            "infrastructure_error":"GPU query failed",
            "watchdog_target_stop_returned":True,
            "watchdog_trigger_seconds":0.2,
        })
        mismatches=[]
        runner._validate_resume_resource_guard(
            infrastructure,plan,
            {"termination_reason":runner.RESOURCE_GUARD_INFRASTRUCTURE_FAILURE},
            work,mismatches,
        )
        self.assertEqual([],mismatches)

        non_linux=copy.deepcopy(guard)
        non_linux.update({
            "campaign_resource_baseline":None,"task_resource_baseline":None,
            "recovery_snapshot":None,
            "admission":{
                "admitted":True,
                "admission_reason":"non-Linux task guard not applicable",
            },
            "mem_available_min_bytes":None,"swap_used_max_bytes":None,
            "oom_kill_before":0,"oom_kill_after":"",
            "pswpout_before":0,"pswpout_max":0,"pswpout_after":"",
        })
        mismatches=[]
        runner._validate_resume_resource_guard(
            non_linux,{**plan,"platform":"darwin"},{"termination_reason":"stop"},
            work,mismatches,
        )
        self.assertEqual([],mismatches)

    def test_campaign_relative_swap_growth_fails_before_task_watchdog(self):
        campaign = self.resource(swap_used=0, pswpout=10)
        current = self.resource(
            swap_used=runner.CONTEXT_SWAP_GROWTH_LIMIT_BYTES + 1, pswpout=10
        )
        with self.assertRaisesRegex(RuntimeError, "swap pressure"):
            runner.start_paired_task_resource_guard(
                self.small_model(), "http://fixture:11434",
                campaign_baseline=campaign,
                resource_reader=lambda: current,
                gpu_process_reader=lambda: [],
            )

    def test_one_resource_baseline_is_shared_across_all_model_calibrations(self):
        baseline = self.resource(swap_used=123, pswpout=456)
        seen = []

        def calibrate(current, _base_url, *, campaign_resource_baseline, **_kwargs):
            seen.append(campaign_resource_baseline)
            return {
                **current, "requested_num_ctx": 8192,
                "context_calibration_status": "native-fit",
                "context_adjusted": False, "context_adjustment_reason": "",
                "context_calibration_attempts": [],
                "context_calibration_profile": runner.CONTEXT_CALIBRATION_PROFILE,
            }

        models = [
            {
                **self.small_model(), "name": f"fixture-{index}:latest",
                "digest": f"sha256:{index}", "context_length": 8192,
                "capabilities": ["completion", "thinking"],
                "capabilities_known": True,
            }
            for index in (1, 2)
        ]
        with tempfile.TemporaryDirectory() as tmp, contextlib.redirect_stdout(io.StringIO()):
            _models, document, _digest = runner.calibrate_adaptive_contexts(
                models, "http://fixture:11434", pathlib.Path(tmp) / "calibration.json",
                run_id="run", report_prefix="report", calibrate_fn=calibrate,
                resource_reader=lambda: dict(baseline),
                ollama_version="fixture",
            )
        self.assertEqual([baseline, baseline], seen)
        self.assertEqual(baseline, document["campaign_resource_baseline"])


class RuntimeIdentityTests(unittest.TestCase):
    PLAN = {"ollama_version": "fixture-version"}
    MODEL = {"name": "qwen-thinking:fixture", "digest": "sha256:frozen"}

    def test_pre_task_identity_accepts_exact_runtime_and_tag_digest(self):
        responses = [
            {"version": "fixture-version"},
            {"models": [{
                "name": self.MODEL["name"], "digest": self.MODEL["digest"],
            }]},
        ]
        with mock.patch.object(runner, "req_json", side_effect=responses) as request:
            self.assertTrue(runner.verify_paired_runtime_identity(
                self.PLAN, self.MODEL, "http://fixture:11434"
            ))

        self.assertTrue(request.call_args_list[0].args[0].endswith("/api/version"))
        self.assertTrue(request.call_args_list[1].args[0].endswith("/api/tags"))

    def test_pre_task_identity_rejects_version_digest_and_deletion_drift(self):
        cases = (
            (
                [{"version": "next-version"}, {"models": []}],
                "Ollama version changed",
            ),
            (
                [{"version": "fixture-version"}, {"models": [{
                    "name": self.MODEL["name"], "digest": "sha256:repointed",
                }]}],
                "digest changed",
            ),
            (
                [{"version": "fixture-version"}, {"models": []}],
                "appeared 0 times",
            ),
        )
        for responses,error in cases:
            with self.subTest(error=error), \
                 mock.patch.object(runner, "req_json", side_effect=responses), \
                 self.assertRaisesRegex(RuntimeError, error):
                runner.verify_paired_runtime_identity(
                    self.PLAN, self.MODEL, "http://fixture:11434"
                )

    def test_post_task_live_residency_accepts_exact_or_already_unloaded_model(self):
        responses = (
            {"models": []},
            {"models": [{
                "name": self.MODEL["name"], "digest": self.MODEL["digest"],
            }]},
        )
        for response in responses:
            with self.subTest(response=response), mock.patch.object(
                runner, "req_json", return_value=response,
            ):
                self.assertTrue(runner.verify_paired_live_residency(
                    self.MODEL, "http://fixture:11434"
                ))

    def test_post_task_live_residency_rejects_wrong_model_digest_or_multiplicity(self):
        cases = (
            ({"models": [{"name": "wrong:latest", "digest": "sha256:wrong"}]}, "expected"),
            ({"models": [{"name": self.MODEL["name"], "digest": "sha256:wrong"}]}, "digest changed"),
            ({"models": [
                {"name": self.MODEL["name"], "digest": self.MODEL["digest"]},
                {"name": "other:latest", "digest": "sha256:other"},
            ]}, "multiple models"),
        )
        for response, error in cases:
            with self.subTest(error=error), mock.patch.object(
                runner, "req_json", return_value=response,
            ), self.assertRaisesRegex(RuntimeError, error):
                runner.verify_paired_live_residency(
                    self.MODEL, "http://fixture:11434"
                )


class QualificationResumeEvidenceTests(unittest.TestCase):
    @staticmethod
    def canonical_record():
        model = {
            "name": "fixture-thinking:latest",
            "digest": "sha256:fixture",
            "family": "fixture",
            "params": "7B",
            "quant": "Q4",
            "capabilities": ["completion", "thinking"],
            "context_length": 8192,
            "aliases": ["fixture-thinking:latest"],
            "control_policy": "boolean-toggle",
            "off_observability": "observable",
            "evidence_code": "fixture-control-policy",
        }
        task = {
            "id": "simple_reasoning",
            "family": "Smoke",
            "category": "smoke_reasoning",
            "name": "Short reasoning answer",
        }
        treatment = {
            "treatment_id": "treatment-off",
            "treatment_key": "thinking-off",
            "treatment_role": "off",
            "pair_kind": "off-vs-on",
            "off_available": True,
            "think_present": True,
            "think_payload_json": "false",
            "thinking_requested": "off",
            "thinking_resolved": "disabled",
            "thinking_effective": "disabled",
        }
        work = {
            "model": model,
            "task": task,
            "treatment": treatment,
            "row_id": "row-off-simple",
            "attempt": 1,
            "pair_id": "pair-fixture",
            "treatment_order": 1,
            "qualification_phase": "qualification",
            "qualification_task": True,
            "qualification_required": True,
            "qualification_probe": "primary",
        }
        plan = {
            "run_id": "run-fixture",
            "experiment_id": "experiment-fixture",
            "plan_sha256": "plan-fixture",
            "pair_schema_version": 3,
            "campaign_seed": 42,
            "suite_version": "0.6.0",
            "benchmark_profile": runner.BENCHMARK_PROFILE,
            "grading_profile": runner.GRADING_PROFILE,
            "runner_sha256": "runner-fixture",
            "grader_sha256": "grader-fixture",
            "planner_sha256": "planner-fixture",
            "host": "spark",
            "host_label": "NVIDIA DGX Spark",
            "platform": "Linux",
            "os_version": "Ubuntu",
            "architecture": "aarch64",
            "telemetry_backend": "nvidia-smi",
            "telemetry_interval_ms": 1000,
            "ollama_version": "0.0.0-fixture",
            "ollama_url": "http://fixture:11434",
            "residency_policy": "warm-runtime-default",
            "keep_alive": None,
            "no_stop": True,
            "output_token_policy": runner.OUTPUT_TOKEN_POLICY,
            "num_predict": -1,
            "temperature": 0,
            "generation_seed": 42,
            "timeout": 1800,
            "num_ctx": 8192,
        }
        work.update({
            "experiment_id": plan["experiment_id"],
            "plan_sha256": plan["plan_sha256"],
            "pair_schema_version": 3,
        })
        expected = runner.expected_paired_row_provenance(work, plan)
        response = "<think>off leaked inline</think>FINAL: 90"
        thinking = ""
        canonical_grading = runner.grade_task(task, "ok", response)
        evidence = runner.reasoning_trace_evidence(thinking, response)
        protocol = runner.protocol_fields_for_treatment(treatment, evidence)
        row = {
            **expected,
            **evidence,
            "qualification_phase": "qualification",
            "qualification_task": "true",
            "qualification_required": "true",
            "qualification_probe": "primary",
            "model_qualification_status": "off-control-ineffective",
            "model_qualification_reason": "off arm exposed a trace",
            "omitted_remaining_work_count": 10,
            "evidence_code": "fixture-control-policy",
            "protocol_valid": str(protocol["protocol_valid"]).lower(),
            "protocol_error": protocol["protocol_error"],
            "thinking_chars": 0,
            "thinking_bytes": 0,
            "status": "ok",
            "timed_out": "false",
            "done": "true",
            "done_reason": "stop",
            "termination_reason": "stop",
            "response_chars": len(response),
            "response_bytes": len(response.encode("utf-8")),
            "error": "",
            "verdict": canonical_grading["verdict"],
            "grader_type": canonical_grading["grader_type"],
            "grader_version": canonical_grading["grader_version"],
            "grader_tests_passed": canonical_grading["tests_passed"],
            "grader_tests_total": canonical_grading["tests_total"],
            "grader_error": canonical_grading["error"],
        }
        metadata_fields = (
            "run_id", "experiment_id", "plan_sha256", "pair_schema_version",
            "campaign_seed", "suite_version", "host", "host_label", "platform",
            "os_version", "architecture", "telemetry_backend",
            "telemetry_interval_ms", "ollama_version", "ollama_url",
            "residency_policy", "keep_alive_request", "stop_before_task",
            "benchmark_profile", "grading_profile", "runner_sha256",
            "grader_sha256", "planner_sha256", "output_token_policy",
            "output_token_limit", "response_timeout_seconds", "context_policy",
            "requested_num_ctx",
        )
        record = {
            "metadata": {field: expected[field] for field in metadata_fields},
            "row": row,
            "grading": canonical_grading,
            "raw": {
                "response": response, "thinking": thinking, "done": True,
                "done_reason": "stop",
            },
            "response": response,
            "thinking": thinking,
            "telemetry_samples": [],
        }
        return record, work, plan

    def test_resume_accepts_protocol_invalid_scientific_row_when_evidence_matches(self):
        record, work, plan = self.canonical_record()

        validated = runner.validate_resume_record(record, work, plan)

        self.assertEqual("false", validated["protocol_valid"])
        self.assertEqual("off-control-ineffective", validated["model_qualification_status"])

    def test_resume_rejects_trace_fields_that_disagree_with_canonical_text(self):
        record, work, plan = self.canonical_record()
        changed = copy.deepcopy(record)
        changed["row"]["inline_thinking_chars"] = 0

        with self.assertRaisesRegex(RuntimeError, "inline_thinking_chars"):
            runner.validate_resume_record(changed, work, plan)

    def test_resume_rejects_protocol_flag_that_disagrees_with_canonical_trace(self):
        record, work, plan = self.canonical_record()
        changed = copy.deepcopy(record)
        changed["row"]["protocol_valid"] = "true"
        changed["row"]["protocol_error"] = ""

        with self.assertRaisesRegex(RuntimeError, "protocol_valid"):
            runner.validate_resume_record(changed, work, plan)

    def test_resume_recomputes_coherently_tampered_grading(self):
        record, work, plan = self.canonical_record()
        changed = copy.deepcopy(record)
        forged = {
            "verdict": "content_mismatch", "grader_type": "final_answer",
            "grader_version": runner.GRADING_PROFILE,
            "tests_passed": 0, "tests_total": 1,
            "error": "forged failure", "failures": ["forged failure"],
        }
        changed["grading"] = forged
        changed["row"].update({
            "verdict": forged["verdict"],
            "grader_type": forged["grader_type"],
            "grader_version": forged["grader_version"],
            "grader_tests_passed": forged["tests_passed"],
            "grader_tests_total": forged["tests_total"],
            "grader_error": forged["error"],
        })

        with self.assertRaisesRegex(RuntimeError, "recomputed"):
            runner.validate_resume_record(changed, work, plan)

    def test_resume_rejects_coherent_timeout_tamper_against_raw_final_event(self):
        record, work, plan = self.canonical_record()
        changed = copy.deepcopy(record)
        forged = runner.grade_task(work["task"], "timeout", changed["response"])
        changed["grading"] = forged
        changed["row"].update({
            "status": "timeout", "timed_out": "true", "done": "false",
            "done_reason": "", "termination_reason": "client_timeout",
            "error": "hard response timeout after 1800s",
            "verdict": forged["verdict"],
            "grader_type": forged["grader_type"],
            "grader_version": forged["grader_version"],
            "grader_tests_passed": forged["tests_passed"],
            "grader_tests_total": forged["tests_total"],
            "grader_error": forged["error"],
        })

        with self.assertRaisesRegex(RuntimeError, "raw.done|raw.done_reason"):
            runner.validate_resume_record(changed, work, plan)


class QualificationExecutionIntegrationTests(unittest.TestCase):
    MODEL = {
        "name": "qwen-thinking:fixture", "digest": "sha256:qwen-fixture",
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
    METADATA = {
        "suite_version": "0.6.0", "host": "fixture",
        "host_label": "Fixture Host", "platform": "linux",
        "os_version": "Fixture Linux", "architecture": "aarch64",
        "telemetry_backend": "none", "ollama_version": "fixture",
        "started_at": "now",
    }

    def setUp(self):
        identity = mock.patch.object(
            runner, "verify_paired_runtime_identity", return_value=True
        )
        identity.start()
        self.addCleanup(identity.stop)
        endpoint = mock.patch.object(
            runner, "require_local_paired_endpoint", return_value=True
        )
        endpoint.start(); self.addCleanup(endpoint.stop)
        start_guard = mock.patch.object(
            runner, "start_paired_task_resource_guard",
            side_effect=fixture_task_resource_guard,
        )
        finish_guard = mock.patch.object(
            runner, "finish_paired_task_resource_guard",
            side_effect=fixture_task_resource_finish,
        )
        start_guard.start(); finish_guard.start()
        self.addCleanup(start_guard.stop); self.addCleanup(finish_guard.stop)

    @staticmethod
    def sampler():
        sampler = mock.Mock()
        sampler.backend = "none"
        sampler.description = "fixture"
        sampler.error = ""
        sampler.snapshot_len.return_value = 0
        sampler.get_since.return_value = []
        return sampler

    @staticmethod
    def result(task, treatment, *, fallback=False, late_leak=False):
        role = treatment["treatment_role"]
        if task["id"] == "exact_reply":
            text = "BENCH_OK"
        elif task["id"] == "math500_mini":
            text = "FINAL: 7"
        else:
            text = "FINAL: 90"
        thinking = ""
        if role in {"on", "maximum"}:
            if task["id"] == "simple_reasoning" and not fallback:
                thinking = "primary reasoning"
            elif task["id"] == "math500_mini" and fallback:
                thinking = "fallback reasoning"
        if late_leak and task["id"] == "math500_mini" and role == "off":
            text = "<think>late leak</think>FINAL: 7"
        return {
            "status": "ok", "text": text, "thinking": thinking,
            "raw": {"prompt_eval_count": 4, "eval_count": 4,
                    "eval_duration": 1_000_000_000, "done_reason": "stop"},
            "error": "", "wall": 1.0, "timed_out": False, "done": True,
            "done_reason": "stop", "termination_reason": "stop",
            "stream_chunk_count": 2, "time_to_first_output_seconds": 0.5,
            "time_to_first_answer_seconds": 0.5,
            "response_chars": len(text), "response_bytes": len(text.encode()),
            "thinking_chars": len(thinking),
            "thinking_bytes": len(thinking.encode()),
            "thinking_capable": True,
            "thinking_requested": treatment["thinking_requested"],
            "thinking_resolved": treatment["thinking_resolved"],
            "thinking_effective": treatment["thinking_effective"],
            "thinking_used": bool(thinking),
        }

    def run_campaign(self, directory, *, fallback=False, late_leak=False):
        sampler = self.sampler()

        def fake_result(_model, task, _timeout, _base_url, *, treatment, **_kwargs):
            return self.result(
                task, treatment, fallback=fallback, late_leak=late_leak
            )

        with mock.patch.object(runner, "load_models", return_value=[dict(self.MODEL)]), \
             mock.patch.object(runner, "create_sampler", return_value=sampler), \
             mock.patch.object(runner, "run_metadata", return_value=dict(self.METADATA)), \
             mock.patch.object(runner, "run_task", side_effect=fake_result) as run_task, \
             mock.patch.object(runner, "stop_model", return_value=True), \
             contextlib.redirect_stdout(io.StringIO()):
            result = runner.main([
                "--thinking", "paired", "--num-ctx", "8192",
                "--limit-tasks", "1", "--no-stop",
                "--output-dir", str(directory), "--run",
            ])
        csv_path = next(pathlib.Path(directory).glob("*.csv"))
        jsonl_path = next(pathlib.Path(directory).glob("*.jsonl"))
        plan_path = next(pathlib.Path(directory).glob("*.plan.json"))
        with csv_path.open(newline="", encoding="utf-8") as handle:
            rows = list(csv.DictReader(handle))
        records = [json.loads(line) for line in jsonl_path.read_text().splitlines()]
        return result, rows, records, plan_path, run_task, sampler

    def test_primary_rows_are_reused_and_every_call_uses_frozen_model_context(self):
        with tempfile.TemporaryDirectory() as tmp:
            result, rows, _records, _plan, run_task, _sampler = self.run_campaign(tmp)
            markdown = next(pathlib.Path(tmp).glob("*.md")).read_text(encoding="utf-8")

        self.assertEqual(0, result)
        self.assertEqual(6, len(rows))
        self.assertEqual(6, run_task.call_count)
        self.assertTrue(all(call.kwargs["num_ctx"] == 8192 for call in run_task.call_args_list))
        simple = [row for row in rows if row["task_id"] == "simple_reasoning"]
        math = [row for row in rows if row["task_id"] == "math500_mini"]
        self.assertEqual({"qualification"}, {row["qualification_phase"] for row in simple})
        self.assertEqual({"primary"}, {row["qualification_probe"] for row in simple})
        self.assertEqual({"benchmark"}, {row["qualification_phase"] for row in math})
        self.assertEqual(2, len(simple))
        self.assertEqual(1, sum(row["model_qualification_status"] == "observable-toggle-qualified" for row in rows))
        self.assertIn("explicit-uniform; qwen-thinking:fixture=8192", markdown)
        self.assertIn("observable-toggle-qualified", markdown)
        self.assertIn("causal off/on", markdown)

    def test_dynamic_fallback_is_reused_as_benchmark_rows(self):
        with tempfile.TemporaryDirectory() as tmp:
            _result, rows, _records, _plan, run_task, _sampler = self.run_campaign(
                tmp, fallback=True
            )

        self.assertEqual(6, run_task.call_count)
        fallback_rows = [row for row in rows if row["task_id"] == "math500_mini"]
        self.assertEqual(2, len(fallback_rows))
        self.assertEqual({"qualification"}, {row["qualification_phase"] for row in fallback_rows})
        self.assertEqual({"fallback"}, {row["qualification_probe"] for row in fallback_rows})
        self.assertEqual(1, sum(row["model_qualification_status"] == "observable-toggle-qualified" for row in rows))

    def test_late_off_leak_omits_remaining_work_and_resume_accepts_evidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            _result, rows, _records, plan_path, _run_task, _sampler = self.run_campaign(
                tmp, late_leak=True
            )
            self.assertFalse(any(row["task_id"] == "exact_reply" for row in rows))
            terminal = [
                row for row in rows
                if row["model_qualification_status"] == "off-control-ineffective"
            ]
            self.assertEqual(1, len(terminal))
            self.assertEqual("false", terminal[0]["protocol_valid"])
            self.assertGreater(int(terminal[0]["omitted_remaining_work_count"]), 0)

            resume_sampler = self.sampler()
            with mock.patch.object(runner, "load_models", return_value=[dict(self.MODEL)]), \
                 mock.patch.object(runner, "create_sampler", return_value=resume_sampler), \
                 mock.patch.object(runner, "run_metadata", return_value=dict(self.METADATA)), \
                 mock.patch.object(runner, "run_task") as resumed_inference, \
                 mock.patch.object(runner, "stop_model", return_value=True), \
                 contextlib.redirect_stdout(io.StringIO()):
                code = runner.main([
                    "--thinking", "paired", "--num-ctx", "8192",
                    "--limit-tasks", "1", "--no-stop",
                    "--resume-plan", str(plan_path), "--run",
                ])

        self.assertEqual(0, code)
        resumed_inference.assert_not_called()
        resume_sampler.start.assert_not_called()

    @staticmethod
    def transport_error(treatment):
        return {
            "status": "error", "text": "", "thinking": "", "raw": {},
            "error": "ConnectionRefusedError('Ollama unavailable')", "wall": 1.0,
            "timed_out": False, "done": False, "done_reason": "",
            "termination_reason": "client_error", "stream_chunk_count": 0,
            "time_to_first_output_seconds": "", "time_to_first_answer_seconds": "",
            "response_chars": 0, "response_bytes": 0,
            "thinking_chars": 0, "thinking_bytes": 0,
            "thinking_capable": True,
            "thinking_requested": treatment["thinking_requested"],
            "thinking_resolved": treatment["thinking_resolved"],
            "thinking_effective": treatment["thinking_effective"],
            "thinking_used": False,
        }

    @staticmethod
    def timeout_result(treatment):
        text = "partial answer"
        thinking = "partial reasoning"
        return {
            "status": "timeout", "text": text, "thinking": thinking,
            "raw": {
                "response": text, "thinking": thinking, "done": False,
            },
            "error": "hard response timeout after 1800s", "wall": 1800.0,
            "timed_out": True, "done": False, "done_reason": "",
            "termination_reason": "client_timeout", "stream_chunk_count": 2,
            "time_to_first_output_seconds": 1.0,
            "time_to_first_answer_seconds": 2.0,
            "response_chars": len(text), "response_bytes": len(text.encode("utf-8")),
            "thinking_chars": len(thinking),
            "thinking_bytes": len(thinking.encode("utf-8")),
            "thinking_capable": True,
            "thinking_requested": treatment["thinking_requested"],
            "thinking_resolved": treatment["thinking_resolved"],
            "thinking_effective": treatment["thinking_effective"],
            "thinking_used": True,
        }

    def test_post_qualification_transport_failure_aborts_if_ollama_is_unhealthy(self):
        sampler = self.sampler()

        def fake_result(_model, task, _timeout, _base_url, *, treatment, **_kwargs):
            if task["id"] == "exact_reply":
                return self.transport_error(treatment)
            return self.result(task, treatment)

        with tempfile.TemporaryDirectory() as tmp, \
             mock.patch.object(runner, "load_models", return_value=[dict(self.MODEL)]), \
             mock.patch.object(runner, "create_sampler", return_value=sampler), \
             mock.patch.object(runner, "run_metadata", return_value=dict(self.METADATA)), \
             mock.patch.object(runner, "run_task", side_effect=fake_result) as inference, \
             mock.patch.object(runner, "req_json", side_effect=ConnectionRefusedError("Ollama down")) as health, \
             mock.patch.object(runner, "stop_model", return_value=True), \
             contextlib.redirect_stdout(io.StringIO()), \
             self.assertRaisesRegex(RuntimeError, "health did not recover after generation failure"):
            runner.main([
                "--thinking", "paired", "--num-ctx", "8192",
                "--limit-tasks", "1", "--no-stop",
                "--output-dir", tmp, "--run",
            ])

        self.assertGreater(inference.call_count, 4)
        health.assert_called_once()
        sampler.stop.assert_called_once()

    def test_healthy_model_scoped_errors_continue_but_withhold_paired_delta(self):
        sampler = self.sampler()

        def fake_result(_model, task, _timeout, _base_url, *, treatment, **_kwargs):
            if task["id"] == "exact_reply":
                return self.transport_error(treatment)
            return self.result(task, treatment)

        with tempfile.TemporaryDirectory() as tmp, \
             mock.patch.object(runner, "load_models", return_value=[dict(self.MODEL)]), \
             mock.patch.object(runner, "create_sampler", return_value=sampler), \
             mock.patch.object(runner, "run_metadata", return_value=dict(self.METADATA)), \
             mock.patch.object(runner, "run_task", side_effect=fake_result) as inference, \
             mock.patch.object(runner, "req_json", return_value={"models": []}) as health, \
             mock.patch.object(runner, "stop_model", return_value=True), \
             contextlib.redirect_stdout(io.StringIO()):
            exit_code = runner.main([
                "--thinking", "paired", "--num-ctx", "8192",
                "--limit-tasks", "1", "--no-stop",
                "--output-dir", tmp, "--run",
            ])
            markdown = next(pathlib.Path(tmp).glob("*.md")).read_text(encoding="utf-8")

        self.assertEqual(0, exit_code)
        self.assertEqual(6, inference.call_count)
        self.assertEqual(2, health.call_count)
        self.assertIn("paired comparison withheld", markdown)
        delta_section = markdown.split("## Paired accuracy deltas", 1)[1].split(
            "## Per-category summary", 1
        )[0]
        self.assertNotIn(self.MODEL["name"], delta_section)

    def test_cold_paired_run_checks_empty_residency_before_inference(self):
        sampler = self.sampler()
        with tempfile.TemporaryDirectory() as tmp, \
             mock.patch.object(runner, "load_models", return_value=[dict(self.MODEL)]), \
             mock.patch.object(runner, "create_sampler", return_value=sampler), \
             mock.patch.object(runner, "run_metadata", return_value=dict(self.METADATA)), \
             mock.patch.object(runner, "run_task") as inference, \
             mock.patch.object(runner, "stop_model", return_value=True), \
             mock.patch.object(
                 runner, "verify_empty_paired_residency",
                 side_effect=RuntimeError("residency contaminated by hermes-live"),
             ) as residency, \
             contextlib.redirect_stdout(io.StringIO()), \
             self.assertRaisesRegex(RuntimeError, "hermes-live"):
            runner.main([
                "--thinking", "paired", "--num-ctx", "8192",
                "--limit-tasks", "1", "--output-dir", tmp, "--run",
            ])

        residency.assert_called_once_with(self.MODEL["name"], runner.DEFAULT_OLLAMA_URL)
        inference.assert_not_called()
        sampler.stop.assert_called_once()

    def test_post_task_residency_failure_flushes_partial_timeout_before_abort(self):
        sampler = self.sampler()
        sampler.get_since.return_value = [{"timestamp": "sample-fixture"}]

        def timed_out(_model, _task, _timeout, _base_url, *, treatment, **_kwargs):
            return self.timeout_result(treatment)

        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(runner, "load_models", return_value=[dict(self.MODEL)]), \
                 mock.patch.object(runner, "create_sampler", return_value=sampler), \
                 mock.patch.object(runner, "run_metadata", return_value=dict(self.METADATA)), \
                 mock.patch.object(runner, "run_task", side_effect=timed_out), \
                 mock.patch.object(runner, "stop_model", return_value=True), \
                 mock.patch.object(
                     runner, "verify_empty_paired_residency",
                     side_effect=[True, RuntimeError("target remained loaded")],
                 ) as residency, \
                 contextlib.redirect_stdout(io.StringIO()), \
                 self.assertRaisesRegex(RuntimeError, "Unable to verify cancellation"):
                runner.main([
                    "--thinking", "paired", "--num-ctx", "8192",
                    "--limit-tasks", "1", "--output-dir", tmp, "--run",
                ])
            jsonl_path = next(pathlib.Path(tmp).glob("*.jsonl"))
            records = [json.loads(line) for line in jsonl_path.read_text().splitlines()]
        self.assertEqual(1, len(records))
        record = records[0]
        self.assertEqual("partial answer", record["response"])
        self.assertEqual("partial reasoning", record["thinking"])
        self.assertEqual([{"timestamp": "sample-fixture"}], record["telemetry_samples"])
        self.assertEqual(
            "client_timeout_cancellation_unverified",
            record["row"]["termination_reason"],
        )
        self.assertIn("target remained loaded", record["row"]["error"])
        self.assertEqual(2, residency.call_count)
        sampler.stop.assert_called_once()

    def test_verified_empty_residency_overrides_false_stop_return_on_timeout(self):
        sampler = self.sampler()

        def timed_out(_model, _task, _timeout, _base_url, *, treatment, **_kwargs):
            return self.timeout_result(treatment)

        with tempfile.TemporaryDirectory() as tmp, \
             mock.patch.object(runner, "load_models", return_value=[dict(self.MODEL)]), \
             mock.patch.object(runner, "create_sampler", return_value=sampler), \
             mock.patch.object(runner, "run_metadata", return_value=dict(self.METADATA)), \
             mock.patch.object(runner, "run_task", side_effect=timed_out) as inference, \
             mock.patch.object(runner, "stop_model", return_value=False), \
             mock.patch.object(runner, "verify_empty_paired_residency", return_value=True), \
             mock.patch.object(runner, "req_json", return_value={"models": []}), \
             contextlib.redirect_stdout(io.StringIO()):
            exit_code = runner.main([
                "--thinking", "paired", "--num-ctx", "8192",
                "--limit-tasks", "1", "--output-dir", tmp, "--run",
            ])
            records = [
                json.loads(line)
                for line in next(pathlib.Path(tmp).glob("*.jsonl")).read_text().splitlines()
            ]

        self.assertEqual(0, exit_code)
        self.assertGreaterEqual(inference.call_count, 1)
        self.assertTrue(records)
        self.assertTrue(all(
            record["row"]["termination_reason"] == "client_timeout"
            for record in records
        ))

    def test_non_timeout_residency_failure_records_invalid_infrastructure_observation(self):
        sampler = self.sampler()

        def successful(_model, task, _timeout, _base_url, *, treatment, **_kwargs):
            return self.result(task, treatment)

        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(runner, "load_models", return_value=[dict(self.MODEL)]), \
                 mock.patch.object(runner, "create_sampler", return_value=sampler), \
                 mock.patch.object(runner, "run_metadata", return_value=dict(self.METADATA)), \
                 mock.patch.object(runner, "run_task", side_effect=successful), \
                 mock.patch.object(runner, "stop_model", return_value=True), \
                 mock.patch.object(
                     runner, "verify_empty_paired_residency",
                     side_effect=[True, RuntimeError("unexpected resident")],
                 ), \
                 contextlib.redirect_stdout(io.StringIO()), \
                 self.assertRaisesRegex(RuntimeError, "invalid infrastructure observation"):
                runner.main([
                    "--thinking", "paired", "--num-ctx", "8192",
                    "--limit-tasks", "1", "--output-dir", tmp, "--run",
                ])
            record = json.loads(next(pathlib.Path(tmp).glob("*.jsonl")).read_text())

        self.assertEqual("error", record["row"]["status"])
        self.assertEqual(
            runner.POST_TASK_RESIDENCY_UNVERIFIED,
            record["row"]["termination_reason"],
        )
        self.assertTrue(record["response"])
        self.assertEqual("fail", record["row"]["verdict"])
        self.assertEqual("transport", record["row"]["grader_type"])

    def test_mid_campaign_identity_drift_aborts_before_next_inference(self):
        sampler = self.sampler()

        def successful(_model, task, _timeout, _base_url, *, treatment, **_kwargs):
            return self.result(task, treatment)

        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(runner, "load_models", return_value=[dict(self.MODEL)]), \
                 mock.patch.object(runner, "create_sampler", return_value=sampler), \
                 mock.patch.object(runner, "run_metadata", return_value=dict(self.METADATA)), \
                 mock.patch.object(runner, "run_task", side_effect=successful) as inference, \
                 mock.patch.object(runner, "stop_model", return_value=True), \
                 mock.patch.object(
                     runner, "verify_paired_runtime_identity",
                     side_effect=[True, True, RuntimeError("tag digest drift")],
                 ) as identity, \
                 contextlib.redirect_stdout(io.StringIO()), \
                 self.assertRaisesRegex(RuntimeError, "digest drift"):
                runner.main([
                    "--thinking", "paired", "--num-ctx", "8192",
                    "--limit-tasks", "1", "--no-stop",
                    "--output-dir", tmp, "--run",
                ])
            records = [
                json.loads(line)
                for line in next(pathlib.Path(tmp).glob("*.jsonl")).read_text().splitlines()
            ]

        self.assertEqual(3, identity.call_count)
        self.assertEqual(2, inference.call_count)
        self.assertEqual(2, len(records))
        self.assertTrue(all(
            record["row"]["model_digest"] == self.MODEL["digest"]
            for record in records
        ))
        sampler.stop.assert_called_once()


    def test_runtime_pressure_flushes_canonical_row_then_aborts_campaign(self):
        sampler = self.sampler()
        fixture_model={**self.MODEL,"requested_num_ctx":8192}
        pressure = fixture_task_resource_finish(
            fixture_task_resource_guard(fixture_model)
        )
        pressure.update({
            "watchdog_triggered":True,
            "resource_pressure_reason":"campaign-relative swap growth detected",
            "watchdog_trigger_seconds":0.5,
            "watchdog_target_stop_returned":True,
            "swap_used_max_bytes":(
                pressure["campaign_resource_baseline"]["swap_used_bytes"]+1
            ),
        })

        def successful(_model, task, _timeout, _base_url, *, treatment, **_kwargs):
            return self.result(task, treatment)

        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(runner, "load_models", return_value=[dict(self.MODEL)]), \
                 mock.patch.object(runner, "create_sampler", return_value=sampler), \
                 mock.patch.object(runner, "run_metadata", return_value=dict(self.METADATA)), \
                 mock.patch.object(runner, "run_task", side_effect=successful) as inference, \
                 mock.patch.object(runner, "finish_paired_task_resource_guard", return_value=pressure), \
                 mock.patch.object(runner, "req_json", return_value={"models": []}), \
                 contextlib.redirect_stdout(io.StringIO()), \
                 self.assertRaisesRegex(RuntimeError, "safe recalibration"):
                runner.main([
                    "--thinking", "paired", "--num-ctx", "8192",
                    "--limit-tasks", "1", "--no-stop",
                    "--output-dir", tmp, "--run",
                ])
            records = [
                json.loads(line)
                for line in next(pathlib.Path(tmp).glob("*.jsonl")).read_text().splitlines()
            ]
            plan_path=next(pathlib.Path(tmp).glob("*.plan.json"))
            plan=json.loads(plan_path.read_text())
            tasks=[
                next(task for task in runner.TASKS if task["id"] == task_id)
                for task_id in plan["task_ids"]
            ]
            work=next(
                item for item in runner.ordered_work_items(plan,tasks)
                if item["row_id"] == records[0]["row"]["row_id"]
            )
            self.assertEqual(
                records[0]["row"],
                runner.validate_resume_record(records[0],work,plan),
            )
            resume_sampler=self.sampler()
            with mock.patch.object(runner,"load_models",return_value=[dict(self.MODEL)]), \
                 mock.patch.object(runner,"create_sampler",return_value=resume_sampler), \
                 mock.patch.object(runner,"run_metadata",return_value=dict(self.METADATA)), \
                 mock.patch.object(runner,"run_task") as resumed_inference, \
                 contextlib.redirect_stdout(io.StringIO()), \
                 self.assertRaisesRegex(RuntimeError,"Cannot resume after unverified task infrastructure"):
                runner.main([
                    "--thinking","paired","--num-ctx","8192","--no-stop",
                    "--resume-plan",str(plan_path),"--run",
                ])
            resumed_inference.assert_not_called()
        self.assertEqual(1, inference.call_count)
        self.assertEqual(1, len(records))
        self.assertEqual("error",records[0]["row"]["status"])
        self.assertEqual("true",records[0]["row"]["done"])
        self.assertEqual("stop",records[0]["row"]["done_reason"])
        self.assertEqual("stop",records[0]["raw"]["done_reason"])
        self.assertEqual(
            runner.RESOURCE_PRESSURE_CANCELLED,
            records[0]["row"]["termination_reason"],
        )
        self.assertEqual(pressure, records[0]["resource_guard"])

    def test_runtime_guard_infrastructure_row_is_canonical_then_nonresumable(self):
        sampler=self.sampler()
        fixture_model={**self.MODEL,"requested_num_ctx":8192}
        infrastructure=fixture_task_resource_finish(
            fixture_task_resource_guard(fixture_model)
        )
        infrastructure.update({
            "gpu_watchdog_error":"GPU watchdog query failed",
            "infrastructure_error":"GPU watchdog query failed",
            "watchdog_trigger_seconds":0.25,
            "watchdog_target_stop_returned":True,
        })

        def successful(_model,task,_timeout,_base_url,*,treatment,**_kwargs):
            return self.result(task,treatment)

        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(runner,"load_models",return_value=[dict(self.MODEL)]), \
                 mock.patch.object(runner,"create_sampler",return_value=sampler), \
                 mock.patch.object(runner,"run_metadata",return_value=dict(self.METADATA)), \
                 mock.patch.object(runner,"run_task",side_effect=successful) as inference, \
                 mock.patch.object(
                     runner,"finish_paired_task_resource_guard",return_value=infrastructure
                 ), contextlib.redirect_stdout(io.StringIO()), \
                 self.assertRaisesRegex(RuntimeError,"Post-task resource safety verification failed"):
                runner.main([
                    "--thinking","paired","--num-ctx","8192","--limit-tasks","1",
                    "--no-stop","--output-dir",tmp,"--run",
                ])
            record=json.loads(next(pathlib.Path(tmp).glob("*.jsonl")).read_text())
            plan_path=next(pathlib.Path(tmp).glob("*.plan.json"))
            plan=json.loads(plan_path.read_text())
            tasks=[
                next(task for task in runner.TASKS if task["id"] == task_id)
                for task_id in plan["task_ids"]
            ]
            work=next(
                item for item in runner.ordered_work_items(plan,tasks)
                if item["row_id"] == record["row"]["row_id"]
            )
            self.assertEqual(
                record["row"],runner.validate_resume_record(record,work,plan)
            )
            resume_sampler=self.sampler()
            with mock.patch.object(runner,"load_models",return_value=[dict(self.MODEL)]), \
                 mock.patch.object(runner,"create_sampler",return_value=resume_sampler), \
                 mock.patch.object(runner,"run_metadata",return_value=dict(self.METADATA)), \
                 mock.patch.object(runner,"run_task") as resumed_inference, \
                 contextlib.redirect_stdout(io.StringIO()), \
                 self.assertRaisesRegex(RuntimeError,"Cannot resume after unverified task infrastructure"):
                runner.main([
                    "--thinking","paired","--num-ctx","8192","--no-stop",
                    "--resume-plan",str(plan_path),"--run",
                ])
            resumed_inference.assert_not_called()

        self.assertEqual(1,inference.call_count)
        self.assertEqual("error",record["row"]["status"])
        self.assertEqual("true",record["row"]["done"])
        self.assertEqual("stop",record["row"]["done_reason"])
        self.assertEqual("stop",record["raw"]["done_reason"])
        self.assertEqual(
            runner.RESOURCE_GUARD_INFRASTRUCTURE_FAILURE,
            record["row"]["termination_reason"],
        )

    def test_fixed_context_remote_paired_endpoint_fails_before_inference(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = pathlib.Path(tmp) / "must-not-exist"
            with mock.patch.object(
                runner, "require_local_paired_endpoint",
                side_effect=RuntimeError("requires a loopback Ollama URL"),
            ), mock.patch.object(runner, "load_models") as load, \
                 contextlib.redirect_stdout(io.StringIO()), \
                 self.assertRaisesRegex(RuntimeError, "loopback"):
                runner.main([
                    "--thinking", "paired", "--num-ctx", "8192",
                    "--ollama-url", "http://10.77.7.227:11434",
                    "--output-dir", str(output), "--run",
                ])
            self.assertFalse(output.exists())
        load.assert_not_called()


class AdaptiveContextExecutionIntegrationTests(unittest.TestCase):
    MODEL = QualificationExecutionIntegrationTests.MODEL
    METADATA = QualificationExecutionIntegrationTests.METADATA
    sampler = staticmethod(QualificationExecutionIntegrationTests.sampler)
    result = staticmethod(QualificationExecutionIntegrationTests.result)

    def setUp(self):
        identity = mock.patch.object(
            runner, "verify_paired_runtime_identity", return_value=True
        )
        identity.start()
        self.addCleanup(identity.stop)
        endpoint = mock.patch.object(
            runner, "require_local_linux_adaptive_endpoint", return_value=True
        )
        paired_endpoint = mock.patch.object(
            runner, "require_local_paired_endpoint", return_value=True
        )
        start_guard = mock.patch.object(
            runner, "start_paired_task_resource_guard",
            side_effect=fixture_task_resource_guard,
        )
        finish_guard = mock.patch.object(
            runner, "finish_paired_task_resource_guard",
            side_effect=fixture_task_resource_finish,
        )
        endpoint.start(); paired_endpoint.start(); start_guard.start(); finish_guard.start()
        self.addCleanup(endpoint.stop); self.addCleanup(start_guard.stop)
        self.addCleanup(paired_endpoint.stop)
        self.addCleanup(finish_guard.stop)

    SECOND_MODEL = {
        **QualificationExecutionIntegrationTests.MODEL,
        "name": "qwen-thinking-second:fixture",
        "digest": "sha256:qwen-fixture-second",
        "context_length": 32768,
    }

    @staticmethod
    def successful_attempt(candidate):
        return {
            "num_ctx": candidate, "success": True, "status": "success",
            "capacity_failure": False, "loaded_context_length": candidate,
            "size": 100, "size_vram": 90, "load_duration_seconds": 1,
            "total_duration_seconds": 1, "wall_seconds": 1,
            "error": "", "unload_verified": True,
            "attempted": True, "request_issued": True, "admitted": True,
            "watchdog_join_verified": True,
            "memory_watchdog_ready_verified": True,
            "gpu_watchdog_ready_verified": True,
            "memory_watchdog_join_verified": True,
            "gpu_watchdog_join_verified": True,
            "memory_watchdog_error": "", "gpu_watchdog_error": "",
            "memory_recovery_verified": True,
            "watchdog_triggered": False, "infrastructure_failure": False,
            "oom_kill_delta": 0, "resource_pressure_reason": "",
            "system_page_size_bytes": runner.SYSTEM_PAGE_SIZE_BYTES,
        }

    @staticmethod
    def capacity_attempt(candidate):
        return {
            "num_ctx": candidate, "success": False,
            "status": "capacity-failure", "capacity_failure": True,
            "loaded_context_length": "", "size": "", "size_vram": "",
            "load_duration_seconds": "", "total_duration_seconds": "",
            "wall_seconds": 1, "error": "out of memory",
            "unload_verified": True,
            "attempted": True, "request_issued": True, "admitted": True,
            "watchdog_join_verified": True,
            "memory_watchdog_ready_verified": True,
            "gpu_watchdog_ready_verified": True,
            "memory_watchdog_join_verified": True,
            "gpu_watchdog_join_verified": True,
            "memory_watchdog_error": "", "gpu_watchdog_error": "",
            "memory_recovery_verified": True,
            "watchdog_triggered": False, "infrastructure_failure": False,
            "oom_kill_delta": 0, "resource_pressure_reason": "",
            "system_page_size_bytes": runner.SYSTEM_PAGE_SIZE_BYTES,
        }

    def test_adaptive_dry_run_has_no_calibration_or_filesystem_mutation(self):
        sampler = self.sampler()
        with tempfile.TemporaryDirectory() as tmp:
            output = pathlib.Path(tmp) / "must-not-exist"
            with mock.patch.object(runner, "load_models", return_value=[dict(self.MODEL)]), \
                 mock.patch.object(runner, "create_sampler", return_value=sampler), \
                 mock.patch.object(runner, "run_metadata", return_value=dict(self.METADATA)), \
                 mock.patch.object(runner, "calibrate_adaptive_contexts") as calibrate, \
                 mock.patch.object(runner, "run_task") as inference, \
                 mock.patch.object(runner, "stop_model") as stop, \
                 contextlib.redirect_stdout(io.StringIO()) as printed:
                code = runner.main([
                    "--thinking", "paired", "--adaptive-native-context",
                    "--limit-tasks", "1", "--output-dir", str(output),
                    "--dry-run",
                ])

            self.assertFalse(output.exists())
        self.assertEqual(0, code)
        self.assertIn("unresolved", printed.getvalue())
        calibrate.assert_not_called()
        inference.assert_not_called()
        stop.assert_not_called()
        sampler.start.assert_not_called()

    def test_adaptive_remote_endpoint_fails_before_report_directory_creation(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = pathlib.Path(tmp) / "must-not-exist"
            with mock.patch.object(
                runner, "require_local_linux_adaptive_endpoint",
                side_effect=RuntimeError("requires a loopback Ollama URL"),
            ), mock.patch.object(runner, "load_models") as load, \
                 contextlib.redirect_stdout(io.StringIO()), \
                 self.assertRaisesRegex(RuntimeError, "loopback"):
                runner.main([
                    "--thinking", "paired", "--adaptive-native-context",
                    "--ollama-url", "http://10.77.7.227:11434",
                    "--output-dir", str(output), "--run",
                ])
            self.assertFalse(output.exists())
        load.assert_not_called()

    def test_adaptive_rows_use_each_frozen_context_and_resume_never_recalibrates(self):
        models = [
            {**self.MODEL, "context_length": 16384},
            dict(self.SECOND_MODEL),
        ]

        def load_attempt(model, candidate, _base_url, **_kwargs):
            if model["name"] == self.SECOND_MODEL["name"] and candidate > 16384:
                return self.capacity_attempt(candidate)
            return self.successful_attempt(candidate)

        def fake_result(_model, task, _timeout, _base_url, *, treatment, **_kwargs):
            return self.result(task, treatment)

        sampler = self.sampler()
        with tempfile.TemporaryDirectory() as tmp, \
             mock.patch.object(runner, "load_models", return_value=models), \
             mock.patch.object(runner, "create_sampler", return_value=sampler), \
             mock.patch.object(runner, "run_metadata", return_value=dict(self.METADATA)), \
             mock.patch.object(runner, "context_load_calibration_attempt", side_effect=load_attempt) as calibration_probe, \
             mock.patch.object(runner, "run_task", side_effect=fake_result) as inference, \
             mock.patch.object(runner, "stop_model", return_value=True), \
             contextlib.redirect_stdout(io.StringIO()):
            code = runner.main([
                "--thinking", "paired", "--adaptive-native-context",
                "--limit-tasks", "1", "--no-stop",
                "--output-dir", tmp, "--run",
            ])
            csv_path = next(pathlib.Path(tmp).glob("*.csv"))
            plan_path = next(pathlib.Path(tmp).glob("*.plan.json"))
            calibration_path = next(pathlib.Path(tmp).glob("*.context-calibration.json"))
            markdown = next(pathlib.Path(tmp).glob("*.md")).read_text(encoding="utf-8")
            with csv_path.open(newline="", encoding="utf-8") as handle:
                rows = list(csv.DictReader(handle))
            by_model = {}
            for row in rows:
                by_model.setdefault(row["model"], set()).add(row["requested_num_ctx"])
            self.assertEqual({"16384"}, by_model[self.MODEL["name"]])
            self.assertEqual({"16384"}, by_model[self.SECOND_MODEL["name"]])
            second_rows = [row for row in rows if row["model"] == self.SECOND_MODEL["name"]]
            self.assertTrue(all(row["context_adjusted"] == "true" for row in second_rows))
            sent_by_model = {}
            for call in inference.call_args_list:
                sent_by_model.setdefault(call.args[0]["name"], set()).add(call.kwargs["num_ctx"])
            self.assertEqual({16384}, sent_by_model[self.MODEL["name"]])
            self.assertEqual({16384}, sent_by_model[self.SECOND_MODEL["name"]])
            self.assertIn("## Context calibration", markdown)
            self.assertIn(
                "| `qwen-thinking-second:fixture` | 32768 | 16384 | `adjusted-fit` | true | 50.0% | 4 |",
                markdown,
            )

            resume_sampler = self.sampler()
            with mock.patch.object(runner, "create_sampler", return_value=resume_sampler), \
                 mock.patch.object(runner, "context_load_calibration_attempt") as resumed_calibration, \
                 mock.patch.object(runner, "run_task") as resumed_inference, \
                 contextlib.redirect_stdout(io.StringIO()):
                resumed = runner.main([
                    "--thinking", "paired", "--adaptive-native-context",
                    "--limit-tasks", "1", "--no-stop",
                    "--resume-plan", str(plan_path), "--run",
                ])
            self.assertEqual(0, resumed)
            resumed_calibration.assert_not_called()
            resumed_inference.assert_not_called()
            resume_sampler.start.assert_not_called()

            calibration_path.write_text("tampered", encoding="utf-8")
            with mock.patch.object(runner, "run_task") as tampered_inference, \
                 contextlib.redirect_stdout(io.StringIO()), \
                 self.assertRaisesRegex(RuntimeError, "artifact SHA-256 mismatch"):
                runner.main([
                    "--thinking", "paired", "--adaptive-native-context",
                    "--limit-tasks", "1", "--no-stop",
                    "--resume-plan", str(plan_path), "--run",
                ])
            tampered_inference.assert_not_called()

        self.assertEqual(0, code)
        self.assertGreater(calibration_probe.call_count, 2)

    def test_adaptive_no_fit_records_terminal_plan_without_benchmark_inference(self):
        sampler = self.sampler()

        def inconclusive(model, candidate, _base_url, **_kwargs):
            return {
                **self.capacity_attempt(candidate),
                "status": "inconclusive", "capacity_failure": False,
                "error": "runner connection failed",
            }

        with tempfile.TemporaryDirectory() as tmp, \
             mock.patch.object(runner, "load_models", return_value=[dict(self.MODEL)]), \
             mock.patch.object(runner, "create_sampler", return_value=sampler), \
             mock.patch.object(runner, "run_metadata", return_value=dict(self.METADATA)), \
             mock.patch.object(runner, "context_load_calibration_attempt", side_effect=inconclusive), \
             mock.patch.object(runner, "run_task") as inference, \
             mock.patch.object(runner, "stop_model", return_value=True), \
             contextlib.redirect_stdout(io.StringIO()):
            code = runner.main([
                "--thinking", "paired", "--adaptive-native-context",
                "--limit-tasks", "1", "--no-stop",
                "--output-dir", tmp, "--run",
            ])
            plan = json.loads(next(pathlib.Path(tmp).glob("*.plan.json")).read_text())
            csv_path = next(pathlib.Path(tmp).glob("*.csv"))
            markdown = next(pathlib.Path(tmp).glob("*.md")).read_text(encoding="utf-8")
            with csv_path.open(newline="", encoding="utf-8") as handle:
                rows = list(csv.DictReader(handle))

        self.assertEqual(0, code)
        self.assertEqual([], rows)
        inference.assert_not_called()
        sampler.start.assert_not_called()
        self.assertEqual("no-fit", plan["models"][0]["context_calibration_status"])
        self.assertTrue(plan.get("terminal_dispositions"))
        self.assertIn("## Context calibration", markdown)
        self.assertIn("| `qwen-thinking:fixture` | 8192 | no-fit | `no-fit` |", markdown)
        self.assertIn("runner connection failed", markdown)


if __name__ == "__main__":
    unittest.main()
