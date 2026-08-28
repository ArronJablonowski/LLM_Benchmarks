"""Load and validate one-file-per-test benchmark components.

Core tests are deliberately declarative JSON files.  A component is easy to
review, replace, or add without modifying a harness runner.  The registry
turns the stable descriptor schema into the legacy task mappings consumed by
the existing runners and graders.
"""
from __future__ import annotations

import copy
import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from accuracy_grading import COUNT_UNIQUE_IPS_GRADER, PRIVATE_IPV4_GRADER


COMPONENT_DIRECTORY = Path(__file__).with_name("core")
CODING_COMPONENT_DIRECTORY = Path(__file__).with_name("coding")
CREATIVE_COMPONENT_DIRECTORY = Path(__file__).with_name("creative")
CYBERSECURITY_COMPONENT_DIRECTORY = Path(__file__).with_name("cybersecurity")
DEFAULT_SUITE = "standard"
SUITE_CHOICES = (DEFAULT_SUITE, "coding", "creative", "cybersecurity")
REQUIRED_FIELDS = {"id", "family", "category", "name", "prompt", "grading"}
CORE_TASK_ORDER = (
    "exact_reply", "simple_reasoning", "coding_micro", "ifeval_exact",
    "ifeval_json", "gsm8k_mini", "math500_mini", "mmlu_pro_security",
    "arc_challenge_mini", "hellaswag_mini", "truthfulqa_mini",
    "humaneval_mini", "mbpp_mini", "bfcl_mini", "ragas_mini",
    "prompt_injection_mini", "cyber_soc_mini", "ocrbench_mini",
)
CODING_TASK_ORDER = (
    "swe_issue_config_merge",
    "repobench_dependency_refactor",
    "livecodebench_schedule_optimizer",
    "bigcodebench_log_pipeline",
    "featurebench_job_service",
    "terminalbench_release_hardening",
    "web_accessible_incident_dashboard",
    "web_component_storefront",
    "web_fullstack_kanban",
)
CREATIVE_TASK_ORDER = (
    "creative_brand_launch_site",
    "creative_key_art_campaign",
    "creative_threejs_dreamscape",
    "creative_scroll_motion_story",
    "creative_nextjs_motion_experience",
    "creative_microinteraction_lab",
)
CYBERSECURITY_TASK_ORDER = (
    "cyber_foundations_architecture",
    "cyber_advanced_protocol_reasoning",
    "cyber_governance_risk_prioritization",
    "cyber_cti_attack_mapping",
    "cyber_vulnerability_cvss_triage",
    "cyber_threat_report_synthesis",
    "cyber_soc_alert_triage",
    "cyber_incident_timeline",
    "cyber_malware_static_analysis",
    "cyber_sigma_detection",
    "cyber_spl_detection",
    "cyber_sentinel_kql_detection",
    "cyber_elastic_esql_detection",
    "cyber_chronicle_yaral_detection",
    "cyber_appsec_code_review",
    "cyber_appsec_secure_patch",
    "cyber_api_bola_remediation",
    "cyber_exploit_crash_analysis",
    "cyber_exploit_toy_poc",
    "cyber_pentest_attack_path",
    "cyber_ctf_multidiscipline",
    "cyber_llm_prompt_injection",
    "cyber_llm_tool_rag_security",
    "cyber_cloud_kubernetes_hardening",
)


class BenchmarkComponentError(ValueError):
    """A test component cannot safely participate in a benchmark run."""


def _fail(path: Path, message: str) -> BenchmarkComponentError:
    return BenchmarkComponentError(f"{path.name}: {message}")


def _load_descriptor(path: Path) -> dict[str, Any]:
    try:
        descriptor = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise _fail(path, f"invalid JSON: {exc.msg}") from exc
    if not isinstance(descriptor, dict):
        raise _fail(path, "component must be a JSON object")
    missing = REQUIRED_FIELDS - descriptor.keys()
    if missing:
        raise _fail(path, "missing required fields: " + ", ".join(sorted(missing)))
    task_id = descriptor["id"]
    if not isinstance(task_id, str) or not task_id:
        raise _fail(path, "id must be a non-empty string")
    if path.stem != task_id:
        raise _fail(path, "file name must match the component id")
    for field in ("family", "category", "name", "prompt"):
        if not isinstance(descriptor[field], str) or not descriptor[field]:
            raise _fail(path, f"{field} must be a non-empty string")
    if not isinstance(descriptor["grading"], dict):
        raise _fail(path, "grading must be an object")
    return descriptor


def _compile_task(path: Path, descriptor: dict[str, Any]) -> dict[str, Any]:
    task = {key: copy.deepcopy(value) for key, value in descriptor.items() if key != "grading"}
    grading = descriptor["grading"]
    kind = grading.get("kind")
    expected = grading.get("expected")
    if kind == "exact":
        if not isinstance(expected, str):
            raise _fail(path, "exact grading requires a string expected value")
        task["expected_exact"] = expected
    elif kind == "final_answer":
        if not isinstance(expected, str):
            raise _fail(path, "final_answer grading requires a string expected value")
        task["final_answer"] = expected
    elif kind == "json":
        if expected is None:
            raise _fail(path, "json grading requires expected")
        task["json_expected"] = copy.deepcopy(expected)
        for field in ("strict_json", "exact_json_keys", "compact_json"):
            if field in grading:
                task[field] = bool(grading[field])
    elif kind == "python":
        fixture = grading.get("fixture")
        fixtures = {
            "private_ipv4": PRIVATE_IPV4_GRADER,
            "count_unique_ips": COUNT_UNIQUE_IPS_GRADER,
        }
        if fixture not in fixtures:
            raise _fail(path, "python grading requires a supported fixture")
        task["python_grader"] = copy.deepcopy(fixtures[fixture])
        if "line_limit" in grading:
            limit = grading["line_limit"]
            if not isinstance(limit, int) or limit < 1:
                raise _fail(path, "python line_limit must be a positive integer")
            task["python_grader"]["line_limit"] = limit
    else:
        raise _fail(path, "grading.kind must be exact, final_answer, json, or python")
    return task


@lru_cache(maxsize=1)
def _core_tasks() -> tuple[dict[str, Any], ...]:
    paths = sorted(COMPONENT_DIRECTORY.glob("*.json"))
    if not paths:
        raise BenchmarkComponentError("no core benchmark components found")
    tasks = [_compile_task(path, _load_descriptor(path)) for path in paths]
    task_ids = [task["id"] for task in tasks]
    if len(task_ids) != len(set(task_ids)):
        raise BenchmarkComponentError("duplicate core benchmark component ids")
    actual_ids = set(task_ids)
    expected_ids = set(CORE_TASK_ORDER)
    if actual_ids != expected_ids:
        missing = sorted(expected_ids - actual_ids)
        extra = sorted(actual_ids - expected_ids)
        details = (["missing " + ", ".join(missing)] if missing else []) + (["unlisted " + ", ".join(extra)] if extra else [])
        raise BenchmarkComponentError("core task order is out of sync: " + "; ".join(details))
    by_id = {task["id"]: task for task in tasks}
    return tuple(by_id[task_id] for task_id in CORE_TASK_ORDER)


@lru_cache(maxsize=1)
def _coding_tasks() -> tuple[dict[str, Any], ...]:
    paths = sorted(CODING_COMPONENT_DIRECTORY.glob("*.json"))
    if not paths:
        raise BenchmarkComponentError("no coding benchmark components found")
    tasks = [_load_descriptor(path) for path in paths]
    required = {
        "fixture", "grader", "time_class", "benchmark_origin",
        "best_practices",
    }
    for path, task in zip(paths, tasks):
        missing = required - task.keys()
        if missing:
            raise _fail(path, "missing coding fields: " + ", ".join(sorted(missing)))
        if task["grading"].get("kind") != "workspace":
            raise _fail(path, "coding grading.kind must be workspace")
        for field in ("fixture", "grader", "time_class", "benchmark_origin"):
            if not isinstance(task[field], str) or not task[field]:
                raise _fail(path, f"{field} must be a non-empty string")
        if not isinstance(task["best_practices"], list) or not all(
            isinstance(item, str) and item for item in task["best_practices"]
        ):
            raise _fail(path, "best_practices must be a non-empty string list")
    task_ids = [task["id"] for task in tasks]
    if len(task_ids) != len(set(task_ids)):
        raise BenchmarkComponentError("duplicate coding benchmark component ids")
    overlap = set(task_ids) & set(CORE_TASK_ORDER)
    if overlap:
        raise BenchmarkComponentError(
            "coding tasks overlap standard task ids: " + ", ".join(sorted(overlap))
        )
    actual_ids = set(task_ids)
    expected_ids = set(CODING_TASK_ORDER)
    if actual_ids != expected_ids:
        missing = sorted(expected_ids - actual_ids)
        extra = sorted(actual_ids - expected_ids)
        details = (["missing " + ", ".join(missing)] if missing else []) + (["unlisted " + ", ".join(extra)] if extra else [])
        raise BenchmarkComponentError("coding task order is out of sync: " + "; ".join(details))
    by_id = {task["id"]: task for task in tasks}
    return tuple(by_id[task_id] for task_id in CODING_TASK_ORDER)


@lru_cache(maxsize=1)
def _creative_tasks() -> tuple[dict[str, Any], ...]:
    paths = sorted(CREATIVE_COMPONENT_DIRECTORY.glob("*.json"))
    if not paths:
        raise BenchmarkComponentError("no creative benchmark components found")
    tasks = [_load_descriptor(path) for path in paths]
    required = {
        "fixture", "time_class", "creative_medium", "deliverables",
        "review_dimensions", "preview_entry",
    }
    for path, task in zip(paths, tasks):
        missing = required - task.keys()
        if missing:
            raise _fail(path, "missing creative fields: " + ", ".join(sorted(missing)))
        if task["grading"].get("kind") != "human":
            raise _fail(path, "creative grading.kind must be human")
        for field in ("fixture", "time_class", "creative_medium", "preview_entry"):
            if not isinstance(task[field], str) or not task[field]:
                raise _fail(path, f"{field} must be a non-empty string")
        for field in ("deliverables", "review_dimensions"):
            if not isinstance(task[field], list) or not all(
                isinstance(item, str) and item for item in task[field]
            ):
                raise _fail(path, f"{field} must be a non-empty string list")
    task_ids = [task["id"] for task in tasks]
    if len(task_ids) != len(set(task_ids)):
        raise BenchmarkComponentError("duplicate creative benchmark component ids")
    overlap = set(task_ids) & (set(CORE_TASK_ORDER) | set(CODING_TASK_ORDER))
    if overlap:
        raise BenchmarkComponentError(
            "creative tasks overlap another suite: " + ", ".join(sorted(overlap))
        )
    if set(task_ids) != set(CREATIVE_TASK_ORDER):
        raise BenchmarkComponentError("creative task order is out of sync")
    by_id = {task["id"]: task for task in tasks}
    return tuple(by_id[task_id] for task_id in CREATIVE_TASK_ORDER)


@lru_cache(maxsize=1)
def _cybersecurity_tasks() -> tuple[dict[str, Any], ...]:
    paths = sorted(CYBERSECURITY_COMPONENT_DIRECTORY.glob("*.json"))
    if not paths:
        raise BenchmarkComponentError("no cybersecurity benchmark components found")
    tasks = [_load_descriptor(path) for path in paths]
    required = {
        "fixture", "grader", "time_class", "benchmark_origin", "track",
        "difficulty", "safety_scope", "best_practices",
    }
    for path, task in zip(paths, tasks):
        missing = required - task.keys()
        if missing:
            raise _fail(path, "missing cybersecurity fields: " + ", ".join(sorted(missing)))
        if task["grading"].get("kind") != "workspace":
            raise _fail(path, "cybersecurity grading.kind must be workspace")
        for field in (
            "fixture", "grader", "time_class", "benchmark_origin", "track",
            "difficulty", "safety_scope",
        ):
            if not isinstance(task[field], str) or not task[field]:
                raise _fail(path, f"{field} must be a non-empty string")
        if not isinstance(task["best_practices"], list) or not all(
            isinstance(item, str) and item for item in task["best_practices"]
        ):
            raise _fail(path, "best_practices must be a non-empty string list")
    task_ids = [task["id"] for task in tasks]
    if len(task_ids) != len(set(task_ids)):
        raise BenchmarkComponentError("duplicate cybersecurity benchmark component ids")
    overlap = set(task_ids) & (
        set(CORE_TASK_ORDER) | set(CODING_TASK_ORDER) | set(CREATIVE_TASK_ORDER)
    )
    if overlap:
        raise BenchmarkComponentError(
            "cybersecurity tasks overlap another suite: " + ", ".join(sorted(overlap))
        )
    if set(task_ids) != set(CYBERSECURITY_TASK_ORDER):
        raise BenchmarkComponentError("cybersecurity task order is out of sync")
    by_id = {task["id"]: task for task in tasks}
    return tuple(by_id[task_id] for task_id in CYBERSECURITY_TASK_ORDER)


def core_task_catalog() -> list[dict[str, Any]]:
    """Return fresh task mappings so callers cannot mutate the registry."""
    return copy.deepcopy(list(_core_tasks()))


def suite_task_catalog(suite: str = DEFAULT_SUITE) -> list[dict[str, Any]]:
    """Return fresh task mappings for a named benchmark suite.

    ``standard`` is the public name for the existing 18-task suite. Routing
    selection here allows future suites to coexist without changing it.
    """
    if suite == DEFAULT_SUITE:
        return core_task_catalog()
    if suite == "coding":
        return copy.deepcopy(list(_coding_tasks()))
    if suite == "creative":
        return copy.deepcopy(list(_creative_tasks()))
    if suite == "cybersecurity":
        return copy.deepcopy(list(_cybersecurity_tasks()))
    choices = ", ".join(SUITE_CHOICES)
    raise BenchmarkComponentError(
        f"unknown benchmark suite: {suite}; choose from: {choices}"
    )


def get_core_task(task_id: str) -> dict[str, Any]:
    for task in core_task_catalog():
        if task["id"] == task_id:
            return task
    raise KeyError(f"unknown core benchmark task: {task_id}")


def list_core_components() -> list[Path]:
    """Return the component files used for the deterministic core profile."""
    _core_tasks()
    return sorted(COMPONENT_DIRECTORY.glob("*.json"))
