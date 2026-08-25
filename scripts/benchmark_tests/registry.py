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
REQUIRED_FIELDS = {"id", "family", "category", "name", "prompt", "grading"}
CORE_TASK_ORDER = (
    "exact_reply", "simple_reasoning", "coding_micro", "ifeval_exact",
    "ifeval_json", "gsm8k_mini", "math500_mini", "mmlu_pro_security",
    "arc_challenge_mini", "hellaswag_mini", "truthfulqa_mini",
    "humaneval_mini", "mbpp_mini", "bfcl_mini", "ragas_mini",
    "prompt_injection_mini", "cyber_soc_mini", "ocrbench_mini",
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


def core_task_catalog() -> list[dict[str, Any]]:
    """Return fresh task mappings so callers cannot mutate the registry."""
    return copy.deepcopy(list(_core_tasks()))


def get_core_task(task_id: str) -> dict[str, Any]:
    for task in core_task_catalog():
        if task["id"] == task_id:
            return task
    raise KeyError(f"unknown core benchmark task: {task_id}")


def list_core_components() -> list[Path]:
    """Return the component files used for the deterministic core profile."""
    _core_tasks()
    return sorted(COMPONENT_DIRECTORY.glob("*.json"))
