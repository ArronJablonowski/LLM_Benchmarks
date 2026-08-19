import csv
import hashlib
import importlib.util
import json
import pathlib
import tempfile
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
DASHBOARD_PATH = ROOT / "dashboard" / "generate_local_llm_dashboard.py"
SPEC = importlib.util.spec_from_file_location("qualification_dashboard", DASHBOARD_PATH)
dashboard = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(dashboard)


class QualificationDashboardTests(unittest.TestCase):
    FIELDS = [
        "model", "task_id", "status", "verdict", "wall_seconds",
        "benchmark_family", "category", "host", "host_label", "model_digest",
        "suite_version", "ollama_version", "telemetry_backend", "grading_profile",
        "runner_sha256", "grader_sha256", "planner_sha256", "context_policy",
        "requested_num_ctx", "model_context_length", "experiment_id", "plan_sha256",
        "context_adjusted", "context_reduction_tokens", "context_reduction_pct",
        "context_adjustment_reason", "context_calibration_profile",
        "context_calibration_status", "context_calibration_attempt_count",
        "context_calibration_attempts_json",
        "pair_schema_version", "campaign_seed", "pair_id", "row_id", "treatment_id",
        "treatment_key", "treatment_role", "treatment_order", "pair_kind",
        "off_available", "think_field_present", "think_payload_json",
        "thinking_requested", "thinking_resolved", "thinking_effective",
        "thinking_chars", "eval_count", "grader_tests_passed", "grader_tests_total",
        "protocol_valid", "protocol_error", "qualification_phase", "qualification_task",
        "qualification_required", "qualification_probe", "reasoning_trace_observed",
        "reasoning_transport", "separated_thinking_chars", "inline_thinking_chars",
        "reasoning_trace_evidence",
        "model_qualification_status", "model_qualification_reason",
        "omitted_remaining_work_count", "control_policy", "off_observability",
        "evidence_code",
    ]

    @staticmethod
    def plan(
        *, experiment_id, task_ids, models, schema=3, context_policy=None,
        terminal_dispositions=None,
    ):
        core = {
            "pair_schema_version": schema,
            "experiment_id": experiment_id,
            "task_ids": list(task_ids),
            "qualification_task_ids": ["simple_reasoning", "math500_mini"],
            "models": models,
        }
        if context_policy:
            core["context_policy"] = context_policy
        if terminal_dispositions is not None:
            core["terminal_dispositions"] = terminal_dispositions
        digest = hashlib.sha256(
            json.dumps(core, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
        ).hexdigest()
        return {**core, "plan_sha256": digest, "run_id": "fixture-run", "report_prefix": "/fixture"}

    def row(
        self, *, model, experiment_id, plan_sha256, pair_id, task_id,
        treatment="off", pair_kind="off-vs-on", qualification_status="pending",
        qualification_reason="", verdict="pass", separated=0, inline=0,
        omitted=0, schema=3, phase="benchmark", probe="none",
        control_policy="boolean-toggle", off_observability="observable",
        evidence_code="fixture", grader_passed=1, context_policy="explicit",
        requested_num_ctx="65536", model_context_length="262144", wall_seconds=1,
        context_adjusted=None, context_adjustment_reason=None,
        context_calibration_attempt_count=None, context_calibration_attempts_json=None,
    ):
        role = {"off": "off", "on": "on", "low": "minimum", "high": "maximum"}[treatment]
        think_value = {"off": "false", "on": "true", "low": '"low"', "high": '"high"'}[treatment]
        effective = {"off": "disabled", "on": "enabled", "low": "low", "high": "high"}[treatment]
        observed = separated > 0 or inline > 0
        transport = "both" if separated and inline else "separated" if separated else "inline" if inline else "none"
        requested_context = int(requested_num_ctx)
        native_context = int(model_context_length)
        adaptive = context_policy == "adaptive-native-per-model"
        adjusted = requested_context < native_context if context_adjusted is None else bool(context_adjusted)
        reduction_tokens = native_context - requested_context
        reduction_pct = round(reduction_tokens * 100 / native_context, 6) if native_context else 0
        attempt_count = (
            (2 if adjusted else 1)
            if context_calibration_attempt_count is None else context_calibration_attempt_count
        )
        attempts_json = context_calibration_attempts_json
        if attempts_json is None and adaptive:
            attempts_json = json.dumps(
                [{"num_ctx": native_context - index, "fit": index == attempt_count - 1} for index in range(attempt_count)],
                separators=(",", ":"),
            )
        adjustment_reason = context_adjustment_reason
        if adjustment_reason is None and adaptive and adjusted:
            adjustment_reason = "native context did not fit the verified load calibration"
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
            "suite_version": "0.5.0",
            "ollama_version": "0.32.13",
            "telemetry_backend": "nvidia-smi",
            "grading_profile": "behavioral-v1",
            "runner_sha256": "runner-fixture",
            "grader_sha256": "grader-fixture",
            "planner_sha256": "planner-fixture",
            "context_policy": context_policy,
            "requested_num_ctx": str(requested_num_ctx),
            "model_context_length": str(model_context_length),
            "context_adjusted": str(adjusted).lower() if adaptive else "",
            "context_reduction_tokens": str(reduction_tokens) if adaptive else "",
            "context_reduction_pct": str(reduction_pct) if adaptive else "",
            "context_adjustment_reason": adjustment_reason or "",
            "context_calibration_profile": "ollama-empty-load-v1" if adaptive else "",
            "context_calibration_status": ("adjusted-fit" if adjusted else "native-fit") if adaptive else "",
            "context_calibration_attempt_count": str(attempt_count) if adaptive else "",
            "context_calibration_attempts_json": attempts_json or "",
            "experiment_id": experiment_id,
            "plan_sha256": plan_sha256,
            "pair_schema_version": str(schema),
            "campaign_seed": "42",
            "pair_id": pair_id,
            "row_id": f"{experiment_id}-{pair_id}-{treatment}-{task_id}",
            "treatment_id": f"{pair_id}-{treatment}",
            "treatment_key": f"thinking-{treatment}",
            "treatment_role": role,
            "treatment_order": "1" if treatment in {"off", "low"} else "2",
            "pair_kind": pair_kind,
            "off_available": str(pair_kind == "off-vs-on").lower(),
            "think_field_present": "true",
            "think_payload_json": think_value,
            "thinking_requested": effective,
            "thinking_resolved": effective,
            "thinking_effective": effective,
            "thinking_chars": str(separated),
            "eval_count": "10",
            "grader_tests_passed": str(grader_passed),
            "grader_tests_total": "1",
            "protocol_valid": "true",
            "protocol_error": "",
            "qualification_phase": phase,
            "qualification_task": str(phase == "qualification").lower(),
            "qualification_required": "true",
            "qualification_probe": probe,
            "reasoning_trace_observed": str(observed).lower(),
            "reasoning_transport": transport,
            "separated_thinking_chars": str(separated),
            "inline_thinking_chars": str(inline),
            "reasoning_trace_evidence": (
                f"{transport}: fixture reasoning trace" if observed else ""
            ),
            "model_qualification_status": qualification_status,
            "model_qualification_reason": qualification_reason,
            "omitted_remaining_work_count": str(omitted),
            "control_policy": control_policy,
            "off_observability": off_observability,
            "evidence_code": evidence_code,
        }

    def load(self, rows, plan):
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "paired.csv"
            with path.open("w", newline="", encoding="utf-8") as handle:
                writer = csv.DictWriter(handle, fieldnames=self.FIELDS)
                writer.writeheader()
                writer.writerows(rows)
            path.with_suffix(".plan.json").write_text(json.dumps(plan), encoding="utf-8")
            summary, _ = dashboard.load_standardized_summary(path)
        return summary

    @staticmethod
    def planned_model(
        model, pair_id, treatments=("off", "on"),
        requested_num_ctx=None, model_context_length=None, **extra,
    ):
        result = {
            "name": model,
            "pair_id": pair_id,
            "treatments": [{"treatment_key": f"thinking-{item}"} for item in treatments],
        }
        if requested_num_ctx is not None:
            result["requested_num_ctx"] = int(requested_num_ctx)
        if model_context_length is not None:
            result["model_context_length"] = int(model_context_length)
        result.update(extra)
        return result

    def complete_rows(
        self, *, model, experiment, plan_sha, pair_id, tasks, qualification_status,
        pair_kind="off-vs-on", context_policy="explicit", requested_num_ctx="65536",
        model_context_length="262144", wall_seconds=1,
    ):
        treatments = ("low", "high") if pair_kind == "minimum-vs-maximum" else ("off", "on")
        rows = []
        for treatment in treatments:
            for index, task in enumerate(tasks):
                rows.append(self.row(
                    model=model, experiment_id=experiment, plan_sha256=plan_sha,
                    pair_id=pair_id, task_id=task, treatment=treatment,
                    pair_kind=pair_kind, qualification_status=qualification_status,
                    separated=(25 if treatment in {"on", "low", "high"} and index == 0 else 0),
                    inline=(14 if treatment in {"on", "high"} and index == 1 else 0),
                    control_policy="reasoning-level" if pair_kind == "minimum-vs-maximum" else "boolean-toggle",
                    off_observability="not-applicable" if pair_kind == "minimum-vs-maximum" else "observable",
                    context_policy=context_policy, requested_num_ctx=requested_num_ctx,
                    model_context_length=model_context_length, wall_seconds=wall_seconds,
                ))
        return rows

    def test_observable_toggle_is_valid_rankable_and_includes_trace_evidence(self):
        model, experiment, pair_id = "qwen:fixture", "exp-qualified", "pair-qualified"
        tasks = ["simple_reasoning", "math500_mini"]
        plan = self.plan(
            experiment_id=experiment, task_ids=tasks,
            models=[self.planned_model(model, pair_id)],
        )
        rows = self.complete_rows(
            model=model, experiment=experiment, plan_sha=plan["plan_sha256"],
            pair_id=pair_id, tasks=tasks,
            qualification_status="observable-toggle-qualified",
        )
        # Make the on arm strictly better so its permitted causal delta is visible.
        next(row for row in rows if row["treatment_role"] == "off" and row["task_id"] == "math500_mini")["verdict"] = "content_mismatch"
        summary = self.load(rows, plan)
        comparison = summary[model]["treatment_comparison"]

        self.assertEqual("observable-toggle-qualified", comparison["status"])
        self.assertTrue(comparison["valid"])
        self.assertTrue(comparison["rankable"])
        self.assertTrue(comparison["campaign_complete"])
        self.assertEqual(1, comparison["strict_delta"])
        self.assertIsNone(comparison["descriptive_strict_delta"])
        self.assertEqual(2, comparison["treatments"]["on"]["reasoning_trace_task_count"])
        self.assertEqual(25, comparison["treatments"]["on"]["separated_thinking_chars"])
        self.assertEqual(14, comparison["treatments"]["on"]["inline_thinking_chars"])
        self.assertIn(
            "fixture reasoning trace",
            comparison["treatments"]["on"]["trace_evidence"][0]["excerpt"],
        )
        self.assertIn("inline 14 chars", dashboard.treatment_trace_evidence_text(comparison["treatments"]["on"]))
        ranks, total = dashboard.rank_benchmarks(summary)
        self.assertEqual({model: 1}, ranks)
        self.assertEqual(1, total)

    def test_terminal_off_control_ineffective_omits_work_without_delta_or_rank(self):
        model, experiment, pair_id = "muse:fixture", "exp-ineffective", "pair-ineffective"
        tasks = ["simple_reasoning", "math500_mini", "exact_reply"]
        plan = self.plan(experiment_id=experiment, task_ids=tasks, models=[self.planned_model(model, pair_id)])
        rows = [
            self.row(
                model=model, experiment_id=experiment, plan_sha256=plan["plan_sha256"],
                pair_id=pair_id, task_id="simple_reasoning", treatment=treatment,
                qualification_status="off-control-ineffective",
                qualification_reason="off arm exposed an inline reasoning span",
                separated=20 if treatment == "on" else 0,
                inline=18 if treatment == "off" else 0,
                omitted=4, phase="qualification", probe="primary",
                evidence_code="off-trace-observed",
            )
            for treatment in ("off", "on")
        ]
        summary = self.load(rows, plan)
        comparison = summary[model]["treatment_comparison"]

        self.assertEqual("off-control-ineffective", comparison["status"])
        self.assertTrue(comparison["terminally_dispositioned"])
        self.assertTrue(comparison["model_complete"])
        self.assertTrue(comparison["campaign_complete"])
        self.assertFalse(comparison["full_benchmark_complete"])
        self.assertFalse(comparison["valid"])
        self.assertFalse(comparison["rankable"])
        self.assertEqual(4, comparison["omitted_remaining_work_count"])
        self.assertIsNone(comparison["strict_delta"])
        self.assertIsNone(comparison["wall_multiplier"])
        self.assertEqual(({}, 0), dashboard.rank_benchmarks(summary))
        self.assertIn("4 work rows omitted", dashboard.comparison_status_text(comparison))

    def test_late_off_control_leak_overrides_an_earlier_qualified_status(self):
        model, experiment, pair_id = "late-leak:fixture", "exp-late-leak", "pair-late-leak"
        tasks = ["simple_reasoning", "math500_mini", "exact_reply", "coding_micro"]
        plan = self.plan(
            experiment_id=experiment, task_ids=tasks,
            models=[self.planned_model(model, pair_id)],
        )
        rows = []
        for task_id, disposition, omitted in (
            ("simple_reasoning", "observable-toggle-qualified", 0),
            ("math500_mini", "off-control-ineffective", 4),
        ):
            for treatment in ("off", "on"):
                rows.append(self.row(
                    model=model, experiment_id=experiment, plan_sha256=plan["plan_sha256"],
                    pair_id=pair_id, task_id=task_id, treatment=treatment,
                    qualification_status=disposition,
                    qualification_reason="later off-arm inline trace invalidated the qualified toggle"
                    if disposition == "off-control-ineffective" else "primary probe qualified",
                    inline=18 if disposition == "off-control-ineffective" and treatment == "off" else 0,
                    separated=20 if treatment == "on" else 0,
                    omitted=omitted,
                ))
        comparison = self.load(rows, plan)[model]["treatment_comparison"]

        self.assertEqual("off-control-ineffective", comparison["qualification_status"])
        self.assertEqual(
            ["observable-toggle-qualified", "off-control-ineffective"],
            comparison["recorded_qualification_statuses"],
        )
        self.assertFalse(comparison["qualification_status_conflict"])
        self.assertTrue(comparison["terminally_dispositioned"])
        self.assertTrue(comparison["model_complete"])
        self.assertTrue(comparison["campaign_complete"])
        self.assertFalse(comparison["rankable"])
        self.assertIsNone(comparison["strict_delta"])

    def test_conflicting_terminal_dispositions_are_invalid_not_generic_inconclusive(self):
        model, experiment, pair_id = "conflict:fixture", "exp-conflict", "pair-conflict"
        tasks = ["simple_reasoning", "math500_mini"]
        plan = self.plan(
            experiment_id=experiment, task_ids=tasks,
            models=[self.planned_model(model, pair_id)],
        )
        rows = [
            self.row(
                model=model, experiment_id=experiment, plan_sha256=plan["plan_sha256"],
                pair_id=pair_id, task_id=task_id, treatment=treatment,
                qualification_status=disposition, omitted=2,
            )
            for task_id, treatment, disposition in (
                ("simple_reasoning", "off", "off-control-ineffective"),
                ("simple_reasoning", "on", "off-control-ineffective"),
                ("math500_mini", "off", "on-control-unverified"),
                ("math500_mini", "on", "on-control-unverified"),
            )
        ]
        comparison = self.load(rows, plan)[model]["treatment_comparison"]

        self.assertTrue(comparison["qualification_status_conflict"])
        self.assertFalse(comparison["terminally_dispositioned"])
        self.assertFalse(comparison["model_complete"])
        self.assertFalse(comparison["campaign_complete"])
        self.assertIn(
            "conflicting model qualification statuses at the same disposition stage",
            comparison["invalid_reasons"],
        )

    def test_unverified_and_inconclusive_controls_are_terminal_and_unranked(self):
        for disposition in ("on-control-unverified", "control-inconclusive", "level-range-unverified"):
            with self.subTest(disposition=disposition):
                model = f"{disposition}:fixture"
                experiment = f"exp-{disposition}"
                pair_id = f"pair-{disposition}"
                is_range = disposition.startswith("level-range")
                treatments = ("low", "high") if is_range else ("off", "on")
                pair_kind = "minimum-vs-maximum" if is_range else "off-vs-on"
                plan = self.plan(
                    experiment_id=experiment, task_ids=["simple_reasoning", "math500_mini"],
                    models=[self.planned_model(model, pair_id, treatments)],
                )
                rows = [
                    self.row(
                        model=model, experiment_id=experiment, plan_sha256=plan["plan_sha256"],
                        pair_id=pair_id, task_id="simple_reasoning", treatment=treatment,
                        pair_kind=pair_kind, qualification_status=disposition,
                        qualification_reason="fallback probe did not establish the control",
                        omitted=2, phase="qualification", probe="fallback",
                        control_policy="reasoning-level" if is_range else "boolean-toggle",
                        off_observability="not-applicable" if is_range else "observable",
                    )
                    for treatment in treatments
                ]
                comparison = self.load(rows, plan)[model]["treatment_comparison"]
                self.assertEqual(disposition, comparison["status"])
                self.assertTrue(comparison["terminally_dispositioned"])
                self.assertTrue(comparison["model_complete"])
                self.assertFalse(comparison["valid"])
                self.assertFalse(comparison["rankable"])
                self.assertIsNone(comparison["strict_delta"])

    def test_unobservable_off_control_runs_full_but_never_reports_causal_delta(self):
        model, experiment, pair_id = "gemma:fixture", "exp-unobservable", "pair-unobservable"
        tasks = ["simple_reasoning", "math500_mini"]
        plan = self.plan(experiment_id=experiment, task_ids=tasks, models=[self.planned_model(model, pair_id)])
        rows = self.complete_rows(
            model=model, experiment=experiment, plan_sha=plan["plan_sha256"],
            pair_id=pair_id, tasks=tasks, qualification_status="off-control-unobservable",
        )
        comparison = self.load(rows, plan)[model]["treatment_comparison"]

        self.assertEqual("off-control-unobservable", comparison["status"])
        self.assertTrue(comparison["full_benchmark_complete"])
        self.assertTrue(comparison["model_complete"])
        self.assertFalse(comparison["valid"])
        self.assertFalse(comparison["rankable"])
        self.assertIsNone(comparison["strict_delta"])
        self.assertEqual("not reportable", dashboard.comparison_delta_text(comparison))

    def test_gpt_low_high_is_a_descriptive_level_range_not_an_off_on_delta(self):
        model, experiment, pair_id = "gpt-oss:fixture", "exp-range", "pair-range"
        tasks = ["simple_reasoning", "math500_mini"]
        plan = self.plan(
            experiment_id=experiment, task_ids=tasks,
            models=[self.planned_model(model, pair_id, ("low", "high"))],
        )
        rows = self.complete_rows(
            model=model, experiment=experiment, plan_sha=plan["plan_sha256"],
            pair_id=pair_id, tasks=tasks, qualification_status="level-range-qualified",
            pair_kind="minimum-vs-maximum",
        )
        next(row for row in rows if row["treatment_role"] == "minimum" and row["task_id"] == "math500_mini")["verdict"] = "content_mismatch"
        comparison = self.load(rows, plan)[model]["treatment_comparison"]

        self.assertEqual(["low", "high"], comparison["expected_treatments"])
        self.assertEqual("level-range-qualified", comparison["status"])
        self.assertTrue(comparison["valid"])
        self.assertTrue(comparison["rankable"])
        self.assertIsNone(comparison["strict_delta"])
        self.assertEqual(1, comparison["descriptive_strict_delta"])
        self.assertIn("descriptive low/high", dashboard.comparison_delta_text(comparison))
        self.assertIn("GPT low/high range", dashboard.comparison_status_text(comparison))

    def test_campaign_is_complete_when_one_model_finishes_and_another_is_terminal(self):
        experiment = "exp-campaign"
        tasks = ["simple_reasoning", "math500_mini"]
        qualified = ("qualified:fixture", "pair-qualified")
        terminal = ("terminal:fixture", "pair-terminal")
        plan = self.plan(
            experiment_id=experiment, task_ids=tasks,
            models=[self.planned_model(*qualified), self.planned_model(*terminal)],
        )
        rows = self.complete_rows(
            model=qualified[0], experiment=experiment, plan_sha=plan["plan_sha256"],
            pair_id=qualified[1], tasks=tasks,
            qualification_status="observable-toggle-qualified",
        )
        rows.extend([
            self.row(
                model=terminal[0], experiment_id=experiment, plan_sha256=plan["plan_sha256"],
                pair_id=terminal[1], task_id="simple_reasoning", treatment=treatment,
                qualification_status="on-control-unverified", omitted=2,
                phase="qualification", probe="fallback",
            )
            for treatment in ("off", "on")
        ])
        summary = self.load(rows, plan)

        for model in (qualified[0], terminal[0]):
            comparison = summary[model]["treatment_comparison"]
            self.assertTrue(comparison["campaign_complete"])
            self.assertEqual(2, comparison["campaign_models_total"])
            self.assertEqual(2, comparison["campaign_models_complete"])
            self.assertEqual(0, comparison["campaign_models_remaining"])
            self.assertEqual(2, comparison["campaign_omitted_remaining_work_count"])

    def test_campaign_stays_incomplete_for_an_untouched_planned_model(self):
        experiment = "exp-campaign-pending"
        tasks = ["simple_reasoning", "math500_mini"]
        finished = ("finished:fixture", "pair-finished")
        untouched = ("untouched:fixture", "pair-untouched")
        plan = self.plan(
            experiment_id=experiment, task_ids=tasks,
            models=[self.planned_model(*finished), self.planned_model(*untouched)],
        )
        rows = self.complete_rows(
            model=finished[0], experiment=experiment, plan_sha=plan["plan_sha256"],
            pair_id=finished[1], tasks=tasks,
            qualification_status="observable-toggle-qualified",
        )
        comparison = self.load(rows, plan)[finished[0]]["treatment_comparison"]
        self.assertFalse(comparison["campaign_complete"])
        self.assertEqual(1, comparison["campaign_models_complete"])
        self.assertEqual(1, comparison["campaign_models_remaining"])

    def test_adaptive_native_context_is_per_model_and_accuracy_ranking_ignores_speed(self):
        experiment = "exp-native-context"
        tasks = ["simple_reasoning", "math500_mini"]
        slow = ("a-slow:fixture", "pair-slow", "131072", "131072", 20)
        fast = ("z-fast:fixture", "pair-fast", "196608", "262144", 1)
        plan = self.plan(
            experiment_id=experiment, task_ids=tasks,
            models=[
                self.planned_model(slow[0], slow[1], requested_num_ctx=slow[2], model_context_length=slow[3]),
                self.planned_model(fast[0], fast[1], requested_num_ctx=fast[2], model_context_length=fast[3]),
            ],
            context_policy="adaptive-native-per-model",
        )
        rows = []
        for model, pair_id, requested_context, native_context, wall_seconds in (slow, fast):
            rows.extend(self.complete_rows(
                model=model, experiment=experiment, plan_sha=plan["plan_sha256"],
                pair_id=pair_id, tasks=tasks,
                qualification_status="observable-toggle-qualified",
                context_policy="adaptive-native-per-model",
                requested_num_ctx=requested_context,
                model_context_length=native_context,
                wall_seconds=wall_seconds,
            ))
        summary = self.load(rows, plan)

        for model, _, requested_context, native_context, _ in (slow, fast):
            comparison = summary[model]["treatment_comparison"]
            self.assertTrue(comparison["context_contract_valid"])
            self.assertTrue(comparison["adaptive_context_policy"])
            self.assertEqual(requested_context, comparison["requested_num_ctx"])
            self.assertEqual(native_context, comparison["model_context_length"])
            self.assertEqual({}, comparison["invariant_mismatches"])
            self.assertIn("adaptive-native-per-model", dashboard.comparison_context_text(comparison))
            self.assertTrue(comparison["campaign_complete"])
        self.assertFalse(summary[slow[0]]["treatment_comparison"]["context_adjusted"])
        self.assertTrue(summary[fast[0]]["treatment_comparison"]["context_adjusted"])
        self.assertEqual(25.0, summary[fast[0]]["treatment_comparison"]["context_reduction_pct"])
        self.assertIn("2 calibration attempts", dashboard.comparison_context_calibration_text(summary[fast[0]]["treatment_comparison"]))
        ranks, total = dashboard.rank_benchmarks(summary)
        self.assertEqual({slow[0]: 1, fast[0]: 2}, ranks)
        self.assertEqual(2, total)

    def test_adaptive_no_fit_is_a_plan_only_terminal_capacity_disposition(self):
        model, experiment, pair_id = "too-large:fixture", "exp-no-fit", "pair-no-fit"
        tasks = ["simple_reasoning", "math500_mini"]
        native = 262144
        adjustment_reason = "all calibrated context candidates exceeded host capacity"
        attempts = [{
            "num_ctx": 65536,
            "success": False,
            "status": "capacity-failure",
            "error": "model allocation failed",
        }]
        planned = self.planned_model(
            model, pair_id, model_context_length=native,
            context_calibration_profile="ollama-empty-load-v1",
            context_calibration_status="no-fit",
            context_calibration_attempt_count=1,
            context_calibration_attempts=attempts,
            context_adjusted=False,
            context_reduction_tokens=native,
            context_reduction_pct=100.0,
            context_adjustment_reason=adjustment_reason,
            control_policy="boolean-toggle",
            off_observability="observable",
            evidence_code="fixture-capacity",
        )
        plan = self.plan(
            experiment_id=experiment, task_ids=tasks, models=[planned],
            context_policy="adaptive-native-per-model",
            terminal_dispositions=[{
                "model": model,
                "pair_id": pair_id,
                "model_qualification_status": "control-inconclusive",
                "model_qualification_reason": "adaptive context calibration found no context that could load on this host",
                "source": "context-calibration",
                "omitted_remaining_work_count": 4,
            }],
        )
        summary = self.load([], plan)
        self.assertIn(model, summary)
        self.assertEqual(0, summary[model]["rows"])
        self.assertEqual([], summary[model]["test_details"])
        comparison = summary[model]["treatment_comparison"]

        self.assertTrue(comparison["plan_only"])
        self.assertTrue(comparison["capacity_disposition"])
        self.assertEqual({}, comparison["treatments"])
        self.assertEqual(0, comparison["observed_row_count"])
        self.assertEqual("no-fit", comparison["context_calibration_status"])
        self.assertTrue(comparison["context_contract_valid"])
        self.assertTrue(comparison["terminally_dispositioned"])
        self.assertTrue(comparison["model_complete"])
        self.assertTrue(comparison["campaign_complete"])
        self.assertEqual(4, comparison["omitted_remaining_work_count"])
        self.assertFalse(comparison["rankable"])
        self.assertIsNone(comparison["strict_delta"])
        self.assertIsNone(comparison["wall_multiplier"])
        self.assertEqual(({}, 0), dashboard.rank_benchmarks(summary))
        self.assertIn("Not benchmarked: no context fit", dashboard.bench_score_text(summary[model]))
        self.assertIn("no verified fit", dashboard.comparison_context_text(comparison))
        self.assertIn("no context fit", dashboard.comparison_context_calibration_text(comparison))

    def test_plan_only_no_fit_requires_terminal_calibration_evidence(self):
        model, experiment, pair_id = "invalid-no-fit:fixture", "exp-invalid-no-fit", "pair-invalid-no-fit"
        planned = self.planned_model(
            model, pair_id, model_context_length=131072,
            context_calibration_profile="ollama-empty-load-v1",
            context_calibration_status="no-fit",
            context_calibration_attempt_count=1,
            context_calibration_attempts=[{
                "num_ctx": 32768, "success": False, "status": "capacity-failure",
            }],
            context_adjusted=False,
            context_reduction_tokens=131072,
            context_reduction_pct=100.0,
            context_adjustment_reason="no candidate fit",
        )
        plan = self.plan(
            experiment_id=experiment,
            task_ids=["simple_reasoning", "math500_mini"],
            models=[planned], context_policy="adaptive-native-per-model",
        )
        comparison = self.load([], plan)[model]["treatment_comparison"]

        self.assertFalse(comparison["context_contract_valid"])
        self.assertFalse(comparison["terminally_dispositioned"])
        self.assertFalse(comparison["model_complete"])
        self.assertFalse(comparison["campaign_complete"])
        self.assertIn(
            "no-fit plan lacks its terminal context-calibration disposition",
            comparison["invalid_reasons"],
        )

    def test_native_context_mismatch_invalidates_the_pair(self):
        model, experiment, pair_id = "context-mismatch:fixture", "exp-context-mismatch", "pair-context-mismatch"
        tasks = ["simple_reasoning", "math500_mini"]
        plan = self.plan(
            experiment_id=experiment, task_ids=tasks,
            models=[self.planned_model(model, pair_id, requested_num_ctx="262144", model_context_length="131072")],
            context_policy="adaptive-native-per-model",
        )
        rows = self.complete_rows(
            model=model, experiment=experiment, plan_sha=plan["plan_sha256"],
            pair_id=pair_id, tasks=tasks,
            qualification_status="observable-toggle-qualified",
            context_policy="adaptive-native-per-model",
            requested_num_ctx="262144", model_context_length="131072",
        )
        comparison = self.load(rows, plan)[model]["treatment_comparison"]
        self.assertFalse(comparison["context_contract_valid"])
        self.assertFalse(comparison["valid"])
        self.assertFalse(comparison["rankable"])
        self.assertFalse(comparison["model_complete"])
        self.assertFalse(comparison["campaign_complete"])
        self.assertIn(
            "requested context exceeds native model context",
            comparison["invalid_reasons"],
        )

    def test_adjusted_context_without_reason_or_calibration_is_invalid(self):
        model, experiment, pair_id = "uncalibrated:fixture", "exp-uncalibrated", "pair-uncalibrated"
        tasks = ["simple_reasoning", "math500_mini"]
        plan = self.plan(
            experiment_id=experiment, task_ids=tasks,
            models=[self.planned_model(model, pair_id, requested_num_ctx="131072", model_context_length="262144")],
            context_policy="adaptive-native-per-model",
        )
        rows = []
        for treatment in ("off", "on"):
            for task_id in tasks:
                rows.append(self.row(
                    model=model, experiment_id=experiment, plan_sha256=plan["plan_sha256"],
                    pair_id=pair_id, task_id=task_id, treatment=treatment,
                    qualification_status="observable-toggle-qualified",
                    separated=10 if treatment == "on" else 0,
                    context_policy="adaptive-native-per-model",
                    requested_num_ctx="131072", model_context_length="262144",
                    context_adjustment_reason="",
                    context_calibration_attempt_count=0,
                    context_calibration_attempts_json="[]",
                ))
        comparison = self.load(rows, plan)[model]["treatment_comparison"]
        self.assertFalse(comparison["context_contract_valid"])
        self.assertFalse(comparison["rankable"])
        self.assertIn("adjusted context requires an adjustment reason", comparison["invalid_reasons"])
        self.assertIn("adjusted context requires valid calibration attempts", comparison["invalid_reasons"])

    def test_schema_v2_and_legacy_reports_remain_readable(self):
        model, experiment, pair_id = "schema2:fixture", "exp-v2", "pair-v2"
        tasks = ["simple_reasoning"]
        plan = self.plan(
            experiment_id=experiment, task_ids=tasks,
            models=[self.planned_model(model, pair_id)], schema=2,
        )
        rows = [
            self.row(
                model=model, experiment_id=experiment, plan_sha256=plan["plan_sha256"],
                pair_id=pair_id, task_id=tasks[0], treatment=treatment,
                schema=2, qualification_status="", separated=10 if treatment == "on" else 0,
            )
            for treatment in ("off", "on")
        ]
        comparison = self.load(rows, plan)[model]["treatment_comparison"]
        self.assertTrue(comparison["valid"])
        self.assertEqual("valid", comparison["status"])

        legacy = self.load([{
            **{field: "" for field in self.FIELDS},
            "model": "legacy:fixture", "task_id": "simple_reasoning",
            "status": "ok", "verdict": "pass", "wall_seconds": "1",
        }], {"experiment_id": "unused", "plan_sha256": "invalid", "models": []})
        self.assertEqual(1, legacy["legacy:fixture"]["passed"])
        self.assertNotIn("treatment_comparison", legacy["legacy:fixture"])


if __name__ == "__main__":
    unittest.main()
