import copy
import importlib.util
import os
import pathlib
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "scripts" / "thinking_pair_support.py"
SPEC = importlib.util.spec_from_file_location("qualification_planner", MODULE_PATH)
planner = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(planner)


def model(name, digest, *, family="fixture", context=131072, **extra):
    return {
        "name": name,
        "digest": digest,
        "family": family,
        "context_length": context,
        "capabilities": ["completion", "thinking"],
        "capabilities_known": True,
        "context_calibration_profile": planner.CONTEXT_CALIBRATION_PROFILE,
        "context_calibration_algorithm": planner.CONTEXT_CALIBRATION_ALGORITHM,
        "context_headroom_min_bytes": planner.CONTEXT_HEADROOM_MIN_BYTES,
        "context_headroom_fraction": planner.CONTEXT_HEADROOM_FRACTION,
        "context_cancellation_guard_bytes": planner.CONTEXT_CANCELLATION_GUARD_BYTES,
        "context_swap_growth_limit_bytes": planner.CONTEXT_SWAP_GROWTH_LIMIT_BYTES,
        "context_system_page_size_bytes": int(os.sysconf("SC_PAGE_SIZE")),
        "context_pressure_poll_interval_seconds": planner.CONTEXT_PRESSURE_POLL_INTERVAL_SECONDS,
        "context_gpu_poll_interval_seconds": planner.CONTEXT_GPU_POLL_INTERVAL_SECONDS,
        "context_gpu_exclusivity_policy": planner.GPU_COMPUTE_EXCLUSIVITY_POLICY,
        **extra,
    }


def calibration_attempt(num_ctx, *, success):
    return {
        "num_ctx": num_ctx,
        "success": success,
        "status": "success" if success else "capacity-failure",
        "capacity_failure": not success,
        "loaded_context_length": num_ctx if success else 0,
        "size": 1,
        "size_vram": 1,
        "load_duration_seconds": 1,
        "total_duration_seconds": 1,
        "wall_seconds": 1,
        "error": "" if success else "capacity",
        "unload_verified": success,
        "attempted": True,
        "request_issued": True,
        "admitted": True,
        "watchdog_join_verified": True,
        "memory_watchdog_ready_verified": True,
        "gpu_watchdog_ready_verified": True,
        "memory_watchdog_join_verified": True,
        "gpu_watchdog_join_verified": True,
        "memory_watchdog_error": "",
        "gpu_watchdog_error": "",
        "memory_recovery_verified": True,
        "watchdog_triggered": False,
        "infrastructure_failure": False,
        "oom_kill_delta": 0,
        "resource_pressure_reason": "",
        "system_page_size_bytes": int(os.sysconf("SC_PAGE_SIZE")),
    }


MATRIX = [
    model("muse-glimmer:30b", "sha:muse"),
    model("huihui-qwen3.6-35b-abliterated:q8", "sha:hui35", family="qwen3.6"),
    model("huihui-qwen3.6-27b-abliterated:q8", "sha:hui27", family="qwen3.6"),
    model("mistral-medium-3.5:128b", "sha:mistral", family="mistral"),
    model("qwen3.6:27b-q8", "sha:qwen27", family="qwen3.6"),
    model("ornith:35b", "sha:ornith"),
    model("foundation-sec:8b-reasoning-q8", "sha:foundation"),
    model("hf.co/fdtn-ai/Foundation-Sec-8B-Reasoning-Q8_0-GGUF:Q8_0", "sha:foundation"),
    model("deepseek-r1:32b", "sha:deepseek"),
    model("gemma4:31b", "sha:gemma"),
    model("qwen3.5:122b", "sha:qwen35", family="qwen3.5"),
    model("gpt-oss:120b", "sha:gpt", family="gptoss"),
    model("nemotron-3-super:120b", "sha:nemotron"),
    model("qwen3.6:35b", "sha:qwen36", family="qwen3.6"),
]

TASKS = [
    {"id": "simple_reasoning", "prompt": "Solve the primary probe."},
    {"id": "math500_mini", "prompt": "Solve the fallback probe."},
    {"id": "exact_reply", "prompt": "Reply exactly OK."},
]


def build(models=MATRIX, tasks=TASKS, *, num_ctx=65536, **extra):
    arguments = {
        "num_ctx": num_ctx,
        "timeout": 1800,
        "ollama_version": "fixture",
        "ollama_url": "http://fixture:11434",
        "host": "spark",
        "host_label": "DGX Spark",
        "platform": "linux",
        "os_version": "fixture",
        "architecture": "aarch64",
        "telemetry_backend": "none",
        "telemetry_interval_ms": 1000,
        "no_stop": False,
        "keep_alive": "0s",
        "residency_policy": "cold-unload-every-task",
        "suite_version": "fixture",
        "benchmark_profile": "accuracy-first-v2",
        "grading_profile": "behavioral-v1",
        "output_token_policy": "unlimited",
        "runner_sha256": "runner",
        "grader_sha256": "grader",
        "planner_sha256": "planner",
        "experiment_id": "schema3-fixture",
    }
    arguments.update(extra)
    return planner.build_paired_plan(models, tasks, **arguments)


def canonical_record(work, *, thinking="", response="answer", status="ok", **row_overrides):
    row = {
        "row_id": work["row_id"],
        "experiment_id": work["experiment_id"],
        "plan_sha256": work["plan_sha256"],
        "pair_id": work["pair_id"],
        "task_id": work["task"]["id"],
        "treatment_id": work["treatment"]["treatment_id"],
        "treatment_key": work["treatment"]["treatment_key"],
        "treatment_role": work["treatment"]["treatment_role"],
        "status": status,
        "done": "true" if status == "ok" else "false",
        "protocol_valid": "true",
        **planner.qualification_fields_for_work(work),
        **row_overrides,
    }
    return {"row": row, "thinking": thinking, "response": response}


class QualificationPlannerTests(unittest.TestCase):
    def test_exact_matrix_deduplicates_alias_and_freezes_payload_contracts(self):
        plan = build()
        self.assertEqual(3, plan["pair_schema_version"])
        self.assertEqual({
            "pending", "observable-toggle-qualified", "off-control-unobservable",
            "off-control-ineffective", "on-control-unverified", "control-inconclusive",
            "level-range-qualified", "level-range-unverified",
        }, planner.QUALIFICATION_STATUSES)
        self.assertEqual(["simple_reasoning", "math500_mini"], plan["qualification_task_ids"])
        self.assertEqual(13, len(plan["models"]))
        by_name = {item["name"]: item for item in plan["models"]}
        foundation = by_name["foundation-sec:8b-reasoning-q8"]
        self.assertEqual(2, len(foundation["aliases"]))
        self.assertEqual("diagnostic-boolean-toggle", foundation["control_policy"])
        self.assertEqual("unsupported-native-off", foundation["off_observability"])

        mistral = by_name["mistral-medium-3.5:128b"]
        self.assertEqual([False, "high"], [arm["think_value"] for arm in mistral["treatments"]])
        self.assertEqual("mistral-bool-true-medium-template-none", mistral["evidence_code"])
        gpt = by_name["gpt-oss:120b"]
        self.assertEqual(["low", "high"], [arm["think_value"] for arm in gpt["treatments"]])
        self.assertEqual("reasoning-level", gpt["control_policy"])
        self.assertEqual("not-applicable", gpt["off_observability"])

        self.assertEqual("unobservable", by_name["muse-glimmer:30b"]["off_observability"])
        self.assertEqual("muse-parser-hidden", by_name["muse-glimmer:30b"]["evidence_code"])
        self.assertEqual("unobservable", by_name["gemma4:31b"]["off_observability"])
        self.assertEqual("ornith-packaged-system-conflict", by_name["ornith:35b"]["evidence_code"])
        self.assertEqual(
            "nemotron-directive-sensitive-boolean-toggle",
            by_name["nemotron-3-super:120b"]["evidence_code"],
        )

    def test_adaptive_context_freezes_native_adjustment_and_no_fit(self):
        adjusted = model(
            "qwen3.6:35b", "sha:adjusted", context=32768,
            requested_num_ctx=16384,
            context_adjusted=True,
            context_adjustment_reason="native allocation did not fit",
            context_calibration_status="adjusted-fit",
            context_calibration_attempts=[
                calibration_attempt(8192, success=True),
                calibration_attempt(16384, success=True),
                calibration_attempt(32768, success=False),
                calibration_attempt(24576, success=False),
            ],
        )
        no_fit = model(
            "nemotron-3-super:120b", "sha:no-fit", context=262144,
            requested_num_ctx=None,
            context_calibration_status="no-fit",
            context_calibration_attempts=[calibration_attempt(8192, success=False)],
            context_adjustment_reason="no candidate fit",
        )
        plan = build([adjusted, no_fit], num_ctx=None)
        self.assertEqual("adaptive-native-per-model", plan["context_policy"])
        self.assertEqual(
            int(os.sysconf("SC_PAGE_SIZE")),
            plan["runtime_resource_safety_policy"]["system_page_size_bytes"],
        )
        self.assertIsNone(plan["num_ctx"])
        by_name = {item["name"]: item for item in plan["models"]}
        first = by_name["qwen3.6:35b"]
        second = by_name["nemotron-3-super:120b"]
        self.assertEqual(32768, first["model_context_length"])
        self.assertEqual(16384, first["requested_num_ctx"])
        self.assertTrue(first["context_adjusted"])
        self.assertEqual(50.0, first["context_reduction_pct"])
        self.assertEqual("no-fit", second["context_calibration_status"])
        self.assertEqual(1, len(plan["terminal_dispositions"]))
        terminal = plan["terminal_dispositions"][0]
        self.assertEqual(second["pair_id"], terminal["pair_id"])
        self.assertEqual("control-inconclusive", terminal["model_qualification_status"])
        self.assertEqual("context-calibration", terminal["source"])
        self.assertEqual(6, terminal["omitted_remaining_work_count"])
        state = planner.qualification_schedule(plan, TASKS, [])
        self.assertEqual("control-inconclusive", state["dispositions_by_model"][second["name"]]["status"])
        self.assertEqual(6, state["dispositions_by_model"][second["name"]]["omitted_remaining_work_count"])
        self.assertNotIn(second["pair_id"], {work["pair_id"] for work in state["work_items"]})

    def test_adaptive_adjustment_requires_reason_and_success(self):
        bad = model(
            "qwen3.6:35b", "sha:bad", context=262144,
            requested_num_ctx=65536,
            context_calibration_status="adjusted-fit",
            context_calibration_attempts=[calibration_attempt(65536, success=False)],
        )
        with self.assertRaisesRegex(ValueError, "context_adjustment_reason"):
            build([bad], num_ctx=None)

        native_without_probe = model("qwen3.6:27b", "sha:unprobed", context=262144)
        with self.assertRaisesRegex(ValueError, "successful calibration attempt"):
            build([native_without_probe], num_ctx=None)
        native_fit = model(
            "qwen3.6:27b", "sha:probed", context=262144,
            context_calibration_status="native-fit",
            context_calibration_attempts=[
                calibration_attempt(value, success=True)
                for value in (8192, 16384, 32768, 65536, 131072, 262144)
            ],
        )
        frozen = build([native_fit], num_ctx=None)["models"][0]
        self.assertEqual("native-fit", frozen["context_calibration_status"])
        self.assertEqual(262144, frozen["requested_num_ctx"])

        changed_page = copy.deepcopy(native_fit)
        changed_page["context_calibration_attempts"][0]["system_page_size_bytes"] *= 2
        with self.assertRaisesRegex(ValueError, "attempt system page size mismatch"):
            build([changed_page], num_ctx=None)

    def test_adaptive_resume_freezes_resolution_and_native_metadata(self):
        calibrated = model(
            "qwen3.6:35b", "sha:resume", context=32768,
            requested_num_ctx=16384,
            context_adjusted=True,
            context_adjustment_reason="native allocation did not fit",
            context_calibration_status="adjusted-fit",
            context_calibration_attempts=[
                calibration_attempt(8192, success=True),
                calibration_attempt(16384, success=True),
                calibration_attempt(32768, success=False),
                calibration_attempt(24576, success=False),
            ],
        )
        plan = build([calibrated], num_ctx=None)
        validation = {
            "num_ctx": None, "timeout": 1800, "ollama_version": "fixture",
            "ollama_url": "http://fixture:11434", "host": "spark", "host_label": "DGX Spark",
            "platform": "linux", "os_version": "fixture", "architecture": "aarch64",
            "telemetry_backend": "none", "telemetry_interval_ms": 1000,
            "no_stop": False, "keep_alive": "0s", "residency_policy": "cold-unload-every-task",
            "suite_version": "fixture", "benchmark_profile": "accuracy-first-v2",
            "grading_profile": "behavioral-v1", "output_token_policy": "unlimited",
            "runner_sha256": "runner", "grader_sha256": "grader", "planner_sha256": "planner",
        }
        planner.validate_resume_plan(plan, [calibrated], TASKS, **validation)
        changed_native = {**calibrated, "context_length": 16384}
        with self.assertRaisesRegex(ValueError, "model context changed"):
            planner.validate_resume_plan(plan, [changed_native], TASKS, **validation)

    def test_classifier_distinguishes_separated_inline_both_and_recomputes_resume(self):
        self.assertEqual("none", planner.classify_reasoning_trace("  ", "answer")["reasoning_transport"])
        self.assertEqual("separated", planner.classify_reasoning_trace("reason", "answer")["reasoning_transport"])
        inline = planner.classify_reasoning_trace("", "<think>abc</think>answer")
        self.assertEqual("inline", inline["reasoning_transport"])
        self.assertEqual(3, inline["inline_thinking_chars"])
        both = planner.trace_evidence_from_record({
            "thinking": "xy", "response": "<think>z</think>",
            "row": {"reasoning_trace_observed": "false"},
        })
        self.assertTrue(both["reasoning_trace_observed"])
        self.assertEqual("both", both["reasoning_transport"])

    def test_global_primary_barrier_fallback_reuse_and_full_work(self):
        plan = build([MATRIX[1], MATRIX[4]])
        all_work = planner.ordered_work_items(plan, TASKS)
        state = planner.qualification_schedule(plan, TASKS, [])
        self.assertEqual("primary", state["phase"])
        self.assertEqual(4, len(state["work_items"]))
        self.assertEqual({"simple_reasoning"}, {work["task"]["id"] for work in state["work_items"]})

        first_pair, second_pair = [item["pair_id"] for item in plan["models"]]
        primary = [work for work in all_work if work["task"]["id"] == "simple_reasoning"]
        records = []
        for work in primary:
            # First model demonstrates on; second requires fallback.
            trace = "reason" if work["pair_id"] == first_pair and work["treatment"]["treatment_role"] == "on" else ""
            records.append(canonical_record(work, thinking=trace))
        state = planner.qualification_schedule(plan, TASKS, records)
        self.assertEqual("fallback", state["phase"])
        self.assertEqual(2, len(state["work_items"]))
        self.assertEqual({second_pair}, {work["pair_id"] for work in state["work_items"]})
        self.assertTrue(all(work["qualification_probe"] == "fallback" for work in state["work_items"]))

        fallback_ids = set()
        for work in state["work_items"]:
            fallback_ids.add(work["row_id"])
            records.append(canonical_record(
                work,
                thinking="fallback reason" if work["treatment"]["treatment_role"] == "on" else "",
            ))
        state = planner.qualification_schedule(plan, TASKS, records)
        self.assertEqual("benchmark", state["phase"])
        self.assertTrue(all(work["row_id"] not in {record["row"]["row_id"] for record in records} for work in state["work_items"]))
        self.assertTrue(fallback_ids.isdisjoint({work["row_id"] for work in state["work_items"]}))
        self.assertEqual("observable-toggle-qualified", state["dispositions_by_pair"][first_pair]["status"])
        self.assertEqual("observable-toggle-qualified", state["dispositions_by_pair"][second_pair]["status"])

    def test_terminal_dispositions_omit_remaining_rows(self):
        plan = build([MATRIX[4]])
        work = planner.ordered_work_items(plan, TASKS)
        primary = [item for item in work if item["task"]["id"] == "simple_reasoning"]
        records = [
            canonical_record(item, thinking="leak" if item["treatment"]["treatment_role"] == "off" else "reason")
            for item in primary
        ]
        state = planner.qualification_schedule(plan, TASKS, records)
        disposition = next(iter(state["dispositions_by_pair"].values()))
        self.assertEqual("off-control-ineffective", disposition["status"])
        self.assertEqual(4, disposition["omitted_remaining_work_count"])
        self.assertEqual([], state["work_items"])
        self.assertTrue(state["campaign_complete"])

    def test_static_unsupported_off_is_diagnostic_then_terminal(self):
        plan = build([MATRIX[6]])
        primary = [item for item in planner.ordered_work_items(plan, TASKS) if item["task"]["id"] == "simple_reasoning"]
        records = [canonical_record(item, thinking="reason" if item["treatment"]["treatment_role"] == "on" else "") for item in primary]
        disposition = planner.qualification_schedule(plan, TASKS, records)["dispositions_by_model"][plan["models"][0]["name"]]
        self.assertEqual("off-control-ineffective", disposition["status"])
        self.assertIn("unsupported native off", disposition["reason"])

    def test_gpt_range_requires_both_trace_channels(self):
        plan = build([MATRIX[11]])
        primary = [item for item in planner.ordered_work_items(plan, TASKS) if item["task"]["id"] == "simple_reasoning"]
        records = [canonical_record(item, thinking="range") for item in primary]
        disposition = planner.qualification_schedule(plan, TASKS, records)["dispositions_by_model"][plan["models"][0]["name"]]
        self.assertEqual("level-range-qualified", disposition["status"])

    def test_nemotron_rejects_prompt_directives(self):
        with self.assertRaisesRegex(ValueError, "Nemotron.*forbids"):
            build([MATRIX[12]], [{"id": "bad", "prompt": "Please /no_think and answer."}])

    def test_resume_index_rejects_duplicate_unknown_and_identity_tampering(self):
        plan = build([MATRIX[4]])
        work = planner.qualification_schedule(plan, TASKS, [])["work_items"][0]
        record = canonical_record(work)
        with self.assertRaisesRegex(ValueError, "duplicate"):
            planner.qualification_schedule(plan, TASKS, [record, copy.deepcopy(record)])
        unknown = copy.deepcopy(record)
        unknown["row"]["row_id"] = "unknown"
        with self.assertRaisesRegex(ValueError, "absent"):
            planner.qualification_schedule(plan, TASKS, [unknown])
        changed = copy.deepcopy(record)
        changed["row"]["treatment_key"] = "thinking-high"
        with self.assertRaisesRegex(ValueError, "provenance mismatch"):
            planner.qualification_schedule(plan, TASKS, [changed])


if __name__ == "__main__":
    unittest.main()
