#!/usr/bin/env python3
"""Pure planning helpers for capability-aware paired thinking benchmarks."""
from __future__ import annotations

import hashlib
import json
import os
import re
import uuid
from copy import deepcopy
from datetime import datetime, timezone


PAIR_SCHEMA_VERSION = 3
DEFAULT_CAMPAIGN_SEED = 42
QUALIFICATION_POLICY_VERSION = 1
PRIMARY_QUALIFICATION_TASK_ID = "simple_reasoning"
FALLBACK_QUALIFICATION_TASK_ID = "math500_mini"
TRACE_RULE_VERSION = "separated-or-inline-think-v1"
QUALIFICATION_TASK_IDS = [PRIMARY_QUALIFICATION_TASK_ID, FALLBACK_QUALIFICATION_TASK_ID]
CONTEXT_CALIBRATION_PROFILE = "ollama-empty-load-small-buffer-v3"
CONTEXT_CALIBRATION_ALGORITHM = "ascending-small-buffer-swap-watchdog-v3"
CONTEXT_HEADROOM_MIN_BYTES = 4 * 1024**3
CONTEXT_HEADROOM_FRACTION = 0.0
CONTEXT_CANCELLATION_GUARD_BYTES = 0
CONTEXT_SWAP_GROWTH_LIMIT_BYTES = 1 * 1024**3
CONTEXT_PRESSURE_POLL_INTERVAL_SECONDS = 0.10
CONTEXT_GPU_POLL_INTERVAL_SECONDS = 1.0
GPU_COMPUTE_EXCLUSIVITY_POLICY = "nvidia-compute-apps-empty-fail-closed-v2"
CONTEXT_ESTIMATOR_POLICY_VERSION = "known-architecture-f16-kv-v1"
CONTEXT_CALIBRATION_STEP = 8192
QUALIFICATION_STATUSES = {
    "pending",
    "observable-toggle-qualified",
    "off-control-unobservable",
    "off-control-ineffective",
    "on-control-unverified",
    "control-inconclusive",
    "level-range-qualified",
    "level-range-unverified",
}
QUALIFICATION_TRACE_RULES = {
    "version": TRACE_RULE_VERSION,
    "separated": "non-whitespace Ollama thinking channel",
    "inline": "non-whitespace content exposed by literal <think> markup",
    "observed": "separated_thinking_chars > 0 or inline_thinking_chars > 0",
}

QUALIFIED_STATUSES = {
    "observable-toggle-qualified",
    "off-control-unobservable",
    "level-range-qualified",
}
TERMINAL_UNQUALIFIED_STATUSES = {
    "off-control-ineffective",
    "on-control-unverified",
    "control-inconclusive",
    "level-range-unverified",
}

# These statuses qualify a model for the rest of the requested benchmark.  The
# unobservable Muse/Gemma result is deliberately eligible for descriptive data,
# but never establishes a causal off/on comparison.
FULL_RUN_STATUSES = QUALIFIED_STATUSES


def _validated_system_page_size_bytes(value: int | None = None) -> int:
    """Return a canonical host page size suitable for hashed safety evidence."""
    raw = os.sysconf("SC_PAGE_SIZE") if value is None else value
    try:
        page_size = int(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError("system_page_size_bytes must be a positive power of two") from exc
    if page_size <= 0 or page_size & (page_size - 1):
        raise ValueError("system_page_size_bytes must be a positive power of two")
    return page_size


def _runtime_resource_safety_policy(system_page_size_bytes: int | None = None) -> dict:
    return {
        "profile": CONTEXT_CALIBRATION_PROFILE,
        "algorithm": CONTEXT_CALIBRATION_ALGORITHM,
        "headroom_min_bytes": CONTEXT_HEADROOM_MIN_BYTES,
        "headroom_fraction": CONTEXT_HEADROOM_FRACTION,
        "cancellation_guard_bytes": CONTEXT_CANCELLATION_GUARD_BYTES,
        "swap_growth_limit_bytes": CONTEXT_SWAP_GROWTH_LIMIT_BYTES,
        "system_page_size_bytes": _validated_system_page_size_bytes(system_page_size_bytes),
        "pressure_poll_interval_seconds": CONTEXT_PRESSURE_POLL_INTERVAL_SECONDS,
        "gpu_poll_interval_seconds": CONTEXT_GPU_POLL_INTERVAL_SECONDS,
        "gpu_exclusivity_policy": GPU_COMPUTE_EXCLUSIVITY_POLICY,
        "context_estimator_policy_version": CONTEXT_ESTIMATOR_POLICY_VERSION,
        "cold_task_watchdog": True,
    }


def _stable_hash(*parts: object, length: int = 24) -> str:
    payload = "\x1f".join(str(part) for part in parts).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()[:length]


def is_thinking_capable(model: dict) -> bool:
    return "thinking" in {str(item).lower() for item in model.get("capabilities") or []}


def is_gpt_oss(model: dict) -> bool:
    identity = f"{model.get('name', '')} {model.get('family', '')}".lower().replace("_", "-")
    return "gpt-oss" in identity or "gptoss" in identity


def model_control_policy(model: dict) -> dict:
    """Return the installed-runtime thinking-control contract for one model.

    Capability advertising alone does not prove that Boolean false/true maps to
    an observable off/on pair. These policies mirror Ollama 0.32.13's renderer
    behavior for the model packages used by this suite; runtime qualification
    remains authoritative when a package reopens or suppresses a trace.
    """
    identity = f"{model.get('name', '')} {model.get('family', '')}".lower().replace("_", "-")
    if is_gpt_oss(model):
        return {
            "control_policy": "reasoning-level",
            "off_observability": "not-applicable",
            "evidence_code": "gpt-oss-levels-no-off",
            "pair_kind": "minimum-vs-maximum",
            "off_available": False,
            "off_value": "low",
            "on_value": "high",
        }
    if "mistral-medium" in identity or "mistral3" in identity:
        return {
            "control_policy": "boolean-off-high-on",
            "off_observability": "observable",
            "evidence_code": "mistral-bool-true-medium-template-none",
            "pair_kind": "off-vs-on",
            "off_available": True,
            "off_value": False,
            "on_value": "high",
        }
    if "foundation-sec" in identity or "fdtn-ai/foundation-sec" in identity:
        return {
            "control_policy": "diagnostic-boolean-toggle",
            "off_observability": "unsupported-native-off",
            "evidence_code": "foundation-unsupported-native-off",
            "pair_kind": "off-vs-on",
            "off_available": False,
            "off_value": False,
            "on_value": True,
        }
    if "deepseek-r1" in identity:
        return {
            "control_policy": "diagnostic-boolean-toggle",
            "off_observability": "unsupported-native-off",
            "evidence_code": "deepseek-empty-prefill-unsupported-native-off",
            "pair_kind": "off-vs-on",
            "off_available": False,
            "off_value": False,
            "on_value": True,
        }
    if "muse-glimmer" in identity or "glimmer" in identity:
        return {
            "control_policy": "boolean-toggle",
            "off_observability": "unobservable",
            "evidence_code": "muse-parser-hidden",
            "pair_kind": "off-vs-on",
            "off_available": True,
            "off_value": False,
            "on_value": True,
        }
    if "gemma4" in identity:
        return {
            "control_policy": "boolean-toggle",
            "off_observability": "unobservable",
            "evidence_code": "gemma4-ghost-thinking-parser-hidden",
            "pair_kind": "off-vs-on",
            "off_available": True,
            "off_value": False,
            "on_value": True,
        }
    if "ornith" in identity:
        evidence = "ornith-packaged-system-conflict"
    elif "nemotron" in identity:
        evidence = "nemotron-directive-sensitive-boolean-toggle"
    elif "qwen" in identity or "huihui" in identity:
        evidence = "qwen-boolean-toggle"
    else:
        evidence = "advertised-thinking-boolean-toggle"
    return {
        "control_policy": "boolean-toggle",
        "off_observability": "observable",
        "evidence_code": evidence,
        "pair_kind": "off-vs-on",
        "off_available": True,
        "off_value": False,
        "on_value": True,
    }


def task_control_conflict(model: dict, task: dict) -> str:
    """Return a frozen control-conflict reason for a model/task combination."""
    identity = f"{model.get('name', '')} {model.get('family', '')}".lower()
    prompt = str(task.get("prompt") or "")
    if "nemotron" in identity and re.search(r"(?i)(?<!\w)/(?:think|no[_-]?think)\b", prompt):
        return "Nemotron qualification forbids prompt-level /think or /no_think directives"
    return ""


def _adaptive_context_record(
    model: dict,
    native_context: int,
    expected_system_page_size_bytes: int | None = None,
) -> dict:
    """Validate and normalize a resource-guarded adaptive calibration."""
    attempts = deepcopy(model.get("context_calibration_attempts") or [])
    if not isinstance(attempts, list) or any(not isinstance(item, dict) for item in attempts):
        raise ValueError(f"context_calibration_attempts must be a list of objects for {model.get('name')}")
    status = str(model.get("context_calibration_status") or "native-fit")
    requested_raw = model.get("requested_num_ctx", native_context if status != "no-fit" else None)
    try:
        requested = int(requested_raw) if requested_raw is not None else None
    except (TypeError, ValueError) as exc:
        raise ValueError(f"invalid resolved requested_num_ctx for {model.get('name')}") from exc
    if requested is not None and (requested <= 0 or requested > native_context):
        raise ValueError(f"resolved requested_num_ctx must be positive and <= native context for {model.get('name')}")
    adjusted = requested is not None and requested < native_context
    expected_status = "no-fit" if requested is None else "adjusted-fit" if adjusted else "native-fit"
    if status != expected_status:
        raise ValueError(
            f"context calibration status mismatch for {model.get('name')}: {status!r} != {expected_status!r}"
        )
    declared_adjusted = model.get("context_adjusted")
    if declared_adjusted is not None and bool(declared_adjusted) != adjusted:
        raise ValueError(f"context_adjusted mismatch for {model.get('name')}")
    reason = str(model.get("context_adjustment_reason") or "")
    if adjusted and not reason:
        raise ValueError(f"adjusted context requires context_adjustment_reason for {model.get('name')}")
    expected_policy={
        "context_calibration_profile": CONTEXT_CALIBRATION_PROFILE,
        "context_calibration_algorithm": CONTEXT_CALIBRATION_ALGORITHM,
        "context_headroom_min_bytes": CONTEXT_HEADROOM_MIN_BYTES,
        "context_headroom_fraction": CONTEXT_HEADROOM_FRACTION,
        "context_cancellation_guard_bytes": CONTEXT_CANCELLATION_GUARD_BYTES,
        "context_swap_growth_limit_bytes": CONTEXT_SWAP_GROWTH_LIMIT_BYTES,
        "context_pressure_poll_interval_seconds": CONTEXT_PRESSURE_POLL_INTERVAL_SECONDS,
        "context_gpu_poll_interval_seconds": CONTEXT_GPU_POLL_INTERVAL_SECONDS,
        "context_gpu_exclusivity_policy": GPU_COMPUTE_EXCLUSIVITY_POLICY,
    }
    for field,value in expected_policy.items():
        if model.get(field) != value:
            raise ValueError(f"adaptive context safety policy mismatch for {model.get('name')}: {field}")
    try:
        system_page_size=int(model.get("context_system_page_size_bytes"))
    except (TypeError,ValueError) as exc:
        raise ValueError(f"adaptive context lacks frozen system page size for {model.get('name')}") from exc
    if system_page_size <= 0 or system_page_size & (system_page_size-1):
        raise ValueError(f"adaptive context system page size is invalid for {model.get('name')}")
    if (
        expected_system_page_size_bytes is not None
        and system_page_size != _validated_system_page_size_bytes(expected_system_page_size_bytes)
    ):
        raise ValueError(f"adaptive context system page size differs from campaign policy for {model.get('name')}")
    for attempt in attempts:
        try:
            attempt_page_size = int(attempt.get("system_page_size_bytes"))
        except (TypeError, ValueError) as exc:
            raise ValueError(f"calibration attempt lacks frozen system page size for {model.get('name')}") from exc
        if attempt_page_size != system_page_size:
            raise ValueError(f"calibration attempt system page size mismatch for {model.get('name')}")
        candidate = attempt.get("num_ctx", attempt.get("requested_num_ctx"))
        try:
            candidate = int(candidate)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"calibration attempt lacks a valid num_ctx for {model.get('name')}") from exc
        if candidate <= 0 or candidate > native_context:
            raise ValueError(f"calibration attempt num_ctx is outside the native context for {model.get('name')}")
        if type(attempt.get("success")) is not bool:
            raise ValueError(f"calibration attempt requires an explicit Boolean success for {model.get('name')}")
        attempt_status = str(attempt.get("status") or "")
        if attempt_status not in {"success", "capacity-failure", "inconclusive"}:
            raise ValueError(f"calibration attempt has an invalid status for {model.get('name')}")
        if attempt["success"]:
            try:
                loaded_context = int(attempt.get("loaded_context_length"))
            except (TypeError, ValueError) as exc:
                raise ValueError(f"successful calibration lacks loaded_context_length for {model.get('name')}") from exc
            if (
                attempt_status != "success"
                or loaded_context < candidate
                or attempt.get("attempted") is not True
                or attempt.get("request_issued") is not True
                or attempt.get("admitted") is not True
                or attempt.get("watchdog_join_verified") is not True
                or attempt.get("memory_watchdog_ready_verified") is not True
                or attempt.get("gpu_watchdog_ready_verified") is not True
                or attempt.get("memory_watchdog_join_verified") is not True
                or attempt.get("gpu_watchdog_join_verified") is not True
                or bool(str(attempt.get("memory_watchdog_error") or "").strip())
                or bool(str(attempt.get("gpu_watchdog_error") or "").strip())
                or attempt.get("memory_recovery_verified") is not True
                or not _row_bool(attempt.get("unload_verified"))
                or _row_bool(attempt.get("infrastructure_failure"))
                or _row_bool(attempt.get("watchdog_triggered"))
                or int(attempt.get("oom_kill_delta") or 0) != 0
                or bool(str(attempt.get("resource_pressure_reason") or "").strip())
                or bool(str(attempt.get("error") or "").strip())
            ):
                raise ValueError(
                    f"successful calibration is not a verified load/unload at requested_num_ctx for {model.get('name')}"
                )
        elif attempt_status == "success":
            raise ValueError(f"failed calibration attempt cannot have success status for {model.get('name')}")
    if attempts:
        expected=min(native_context,CONTEXT_CALIBRATION_STEP)
        low=None; high=None
        for index,attempt in enumerate(attempts):
            candidate=int(attempt.get("num_ctx",attempt.get("requested_num_ctx")))
            if candidate != expected:
                raise ValueError(
                    f"calibration attempt sequence is not the frozen guarded-ascending policy for {model.get('name')}"
                )
            if high is None:
                if attempt.get("success"):
                    low=candidate
                    if candidate >= native_context:
                        if index != len(attempts)-1:
                            raise ValueError(f"calibration attempts continue after native fit for {model.get('name')}")
                        continue
                    grown=max(candidate+CONTEXT_CALIBRATION_STEP,candidate*2)
                    expected=min(
                        native_context,
                        (grown//CONTEXT_CALIBRATION_STEP)*CONTEXT_CALIBRATION_STEP,
                    )
                    if expected <= low:
                        expected=min(native_context,low+CONTEXT_CALIBRATION_STEP)
                elif attempt.get("capacity_failure"):
                    high=candidate
                    if low is None:
                        if index != len(attempts)-1:
                            raise ValueError(f"calibration attempts continue after minimum no-fit for {model.get('name')}")
                    elif high-low > CONTEXT_CALIBRATION_STEP:
                        expected=((low+high)//2//CONTEXT_CALIBRATION_STEP)*CONTEXT_CALIBRATION_STEP
                elif index != len(attempts)-1:
                    raise ValueError(f"calibration attempts continue after inconclusive result for {model.get('name')}")
            else:
                if attempt.get("success"):
                    low=candidate
                elif attempt.get("capacity_failure"):
                    high=candidate
                elif index != len(attempts)-1:
                    raise ValueError(f"calibration attempts continue after inconclusive refinement for {model.get('name')}")
                if low is not None and high-low > CONTEXT_CALIBRATION_STEP:
                    expected=((low+high)//2//CONTEXT_CALIBRATION_STEP)*CONTEXT_CALIBRATION_STEP
    # A final hashed plan accepts only the runner's explicit success signal.
    # Human-readable status strings are descriptive and cannot fabricate a
    # calibration result on resume.
    successful_attempts = [attempt for attempt in attempts if _row_bool(attempt.get("success"))]
    if status in {"native-fit", "adjusted-fit"} and not successful_attempts:
        raise ValueError(f"fit context requires a successful calibration attempt for {model.get('name')}")
    if status == "no-fit" and (not attempts or not reason):
        raise ValueError(f"no-fit context requires attempts and context_adjustment_reason for {model.get('name')}")
    if status == "no-fit" and successful_attempts:
        raise ValueError(f"no-fit context cannot contain a successful calibration attempt for {model.get('name')}")
    if requested is not None and successful_attempts:
        last_success_context = successful_attempts[-1].get("num_ctx", successful_attempts[-1].get("requested_num_ctx"))
        if last_success_context is not None and int(last_success_context) != requested:
            raise ValueError(f"successful calibration attempt does not match requested_num_ctx for {model.get('name')}")
    reduction = native_context - requested if requested is not None else native_context
    return {
        "model_context_length": native_context,
        "native_context_length": native_context,
        "requested_num_ctx": requested,
        **expected_policy,
        "context_system_page_size_bytes": system_page_size,
        "context_kv_parallelism": model.get("context_kv_parallelism"),
        "context_kv_parallelism_source": model.get("context_kv_parallelism_source") or "",
        "context_workspace_min_bytes": model.get("context_workspace_min_bytes"),
        "context_workspace_fraction": model.get("context_workspace_fraction"),
        "context_empirical_safety_factor": model.get("context_empirical_safety_factor"),
        "context_calibration_status": status,
        "context_calibration_attempt_count": len(attempts),
        "context_calibration_attempts": attempts,
        "context_adjusted": adjusted,
        "context_reduction_tokens": reduction,
        "context_reduction_pct": round((reduction / native_context) * 100, 6),
        "context_adjustment_reason": reason,
    }


def _canonical_name(names: list[str]) -> str:
    """Prefer a short local alias to a long hf.co spelling."""
    return sorted(names, key=lambda name: (name.lower().startswith("hf.co/"), len(name), name.lower()))[0]


def dedupe_thinking_models(models: list[dict]) -> tuple[list[dict], list[dict]]:
    """Return unique thinking checkpoints plus excluded/non-thinking models.

    A non-empty Ollama digest is the checkpoint identity. Models without a digest
    remain distinct so provenance is never guessed.
    """
    thinking = [deepcopy(model) for model in models if is_thinking_capable(model)]
    excluded = [deepcopy(model) for model in models if not is_thinking_capable(model)]
    grouped: dict[str, list[dict]] = {}
    for index, model in enumerate(thinking):
        key = model.get("digest") or f"missing-digest:{index}:{model.get('name', '')}"
        grouped.setdefault(str(key), []).append(model)
    unique = []
    for _, aliases in grouped.items():
        names = sorted({str(item.get("name") or "") for item in aliases if item.get("name")})
        canonical = _canonical_name(names)
        model = next(item for item in aliases if item.get("name") == canonical)
        model["aliases"] = names
        unique.append(model)
    return sorted(unique, key=lambda model: str(model.get("name") or "").lower()), excluded


def treatments_for_model(model: dict) -> list[dict]:
    """Build exact, honest paired controls for one thinking-capable model."""
    if not is_thinking_capable(model):
        return []
    policy = model_control_policy(model)
    if policy["pair_kind"] == "minimum-vs-maximum":
        return [
            {
                "treatment_key": "thinking-low",
                "treatment_role": "minimum",
                "pair_kind": "minimum-vs-maximum",
                "off_available": False,
                "think_present": True,
                "think_value": policy["off_value"],
                "thinking_requested": "low",
                "thinking_resolved": "low",
                "thinking_effective": "low",
            },
            {
                "treatment_key": "thinking-high",
                "treatment_role": "maximum",
                "pair_kind": "minimum-vs-maximum",
                "off_available": False,
                "think_present": True,
                "think_value": policy["on_value"],
                "thinking_requested": "high",
                "thinking_resolved": "high",
                "thinking_effective": "high",
            },
        ]
    return [
        {
            "treatment_key": "thinking-off",
            "treatment_role": "off",
            "pair_kind": "off-vs-on",
            "off_available": policy["off_available"],
            "think_present": True,
            "think_value": policy["off_value"],
            "thinking_requested": "off",
            "thinking_resolved": "disabled",
            "thinking_effective": "disabled",
        },
        {
            "treatment_key": "thinking-on",
            "treatment_role": "on",
            "pair_kind": "off-vs-on",
            "off_available": policy["off_available"],
            "think_present": True,
            "think_value": policy["on_value"],
            "thinking_requested": "on",
            "thinking_resolved": (
                policy["on_value"] if isinstance(policy["on_value"], str) else "enabled"
            ),
            "thinking_effective": (
                policy["on_value"] if isinstance(policy["on_value"], str) else "enabled"
            ),
        },
    ]


_COMPLETE_THINK_RE = re.compile(r"(?is)<think\b[^>]*>(.*?)</think\s*>")
_OPEN_THINK_RE = re.compile(r"(?is)<think\b[^>]*>")
_CLOSE_THINK_RE = re.compile(r"(?is)</think\s*>")


def classify_reasoning_trace(thinking: str | None, response: str | None) -> dict:
    """Classify only observable separated or literal inline reasoning traces."""
    separated_text = str(thinking or "")
    response_text = str(response or "")
    separated_chars = len(separated_text) if separated_text.strip() else 0
    inline_chars = 0
    inline_marker_detected = False
    consumed = []
    for match in _COMPLETE_THINK_RE.finditer(response_text):
        inline_marker_detected = True
        consumed.append(match.span())
        content = match.group(1)
        if content.strip():
            inline_chars += len(content)
    # An incomplete opening tag still exposes the rest of the response as a
    # reasoning channel. A closing-only trace exposes the preceding text.
    unmatched_open = None
    for match in _OPEN_THINK_RE.finditer(response_text):
        if not any(start <= match.start() < end for start, end in consumed):
            unmatched_open = match
            break
    if unmatched_open:
        inline_marker_detected = True
        tail = response_text[unmatched_open.end():]
        if tail.strip():
            inline_chars += len(tail)
    elif not consumed:
        closing = _CLOSE_THINK_RE.search(response_text)
        if closing:
            inline_marker_detected = True
            prefix = response_text[:closing.start()]
            if prefix.strip():
                inline_chars += len(prefix)
    separated = separated_chars > 0
    inline = inline_chars > 0
    if separated and inline:
        transport = "both"
    elif separated:
        transport = "separated"
    elif inline:
        transport = "inline"
    else:
        transport = "none"
    return {
        "reasoning_trace_observed": separated or inline,
        "reasoning_transport": transport,
        "separated_thinking_chars": separated_chars,
        "inline_thinking_chars": inline_chars,
        "inline_thinking_detected": inline,
        "inline_thinking_marker_detected": inline_marker_detected,
    }


def trace_evidence_from_record(record: dict) -> dict:
    """Recompute trace evidence from a canonical JSONL record when possible.

    Resume must not trust mutable CSV summary flags.  Canonical records retain
    the complete top-level ``thinking`` and ``response`` strings, so those are
    authoritative even when both are empty.  Row-only callers receive a strict
    numeric-field fallback for compatibility with already materialized data.
    """
    if "thinking" in record or "response" in record:
        return classify_reasoning_trace(record.get("thinking"), record.get("response"))
    row = record.get("row") if isinstance(record.get("row"), dict) else record
    if "thinking" in row or "response" in row:
        return classify_reasoning_trace(row.get("thinking"), row.get("response"))
    try:
        separated = int(row.get("separated_thinking_chars") or row.get("thinking_chars") or 0)
        inline = int(row.get("inline_thinking_chars") or 0)
    except (TypeError, ValueError) as exc:
        raise ValueError("malformed recorded reasoning-trace counts") from exc
    observed = separated > 0 or inline > 0
    transport = "both" if separated > 0 and inline > 0 else "separated" if separated > 0 else "inline" if inline > 0 else "none"
    return {
        "reasoning_trace_observed": observed,
        "reasoning_transport": transport,
        "separated_thinking_chars": separated,
        "inline_thinking_chars": inline,
        "inline_thinking_detected": inline > 0,
        "inline_thinking_marker_detected": inline > 0,
    }


def _row_bool(value: object) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _row_trace_observed(row: dict) -> bool:
    return bool(trace_evidence_from_record(row)["reasoning_trace_observed"])


def _qualification_rows(rows: list[dict], task_id: str, *, fallback: bool = False) -> list[dict]:
    selected = []
    for row in rows:
        if row.get("task_id") != task_id:
            continue
        phase = str(row.get("qualification_phase") or "")
        probe = str(row.get("qualification_probe") or "")
        required = _row_bool(row.get("qualification_required"))
        if fallback:
            if probe == "fallback" or (phase == "qualification" and required):
                selected.append(row)
        else:
            # Primary qualification is intrinsic to schema v3. This fallback
            # accepts early schema-v3 fixtures that omitted the explicit flag.
            if probe == "primary" or phase in {"", "primary"} or required:
                selected.append(row)
    return selected


def derive_model_qualification(model: dict, rows: list[dict], plan: dict | None = None) -> dict:
    """Derive the model-scoped control disposition from canonical observations."""
    normalized_rows = []
    for record in rows:
        row = deepcopy(record.get("row") if isinstance(record.get("row"), dict) else record)
        row.update(trace_evidence_from_record(record))
        normalized_rows.append(row)
    rows = normalized_rows
    policy = {
        "control_policy": model.get("control_policy"),
        "off_observability": model.get("off_observability"),
        "evidence_code": model.get("evidence_code"),
        "pair_kind": (model.get("treatments") or [{}])[0].get("pair_kind"),
    }
    if not policy["control_policy"]:
        policy.update(model_control_policy(model))
    primary_id = (plan or {}).get("primary_qualification_task_id", PRIMARY_QUALIFICATION_TASK_ID)
    fallback_id = (plan or {}).get("fallback_qualification_task_id", FALLBACK_QUALIFICATION_TASK_ID)
    primary = _qualification_rows(rows, primary_id)
    fallback = _qualification_rows(rows, fallback_id, fallback=True)
    expected_roles = {"minimum", "maximum"} if policy["pair_kind"] == "minimum-vs-maximum" else {"off", "on"}

    def disposition(status: str, reason: str, *, fallback_required: bool = False) -> dict:
        eligible = status in FULL_RUN_STATUSES
        terminal = status in TERMINAL_UNQUALIFIED_STATUSES
        return {
            "status": status,
            "model_qualification_status": status,
            "reason": reason,
            "model_qualification_reason": reason,
            "control_policy": policy["control_policy"],
            "off_observability": policy["off_observability"],
            "evidence_code": policy["evidence_code"],
            "fallback_required": fallback_required,
            "qualification_complete": status != "pending",
            "eligible": eligible,
            "full_run": eligible,
            "terminal": terminal,
            "omit_remaining": terminal,
        }

    if model.get("context_calibration_status") == "no-fit":
        return disposition(
            "control-inconclusive",
            "adaptive context calibration found no context that could load on this host",
        )

    def probe_result(probe_rows: list[dict]) -> dict:
        roles = [str(row.get("treatment_role") or "") for row in probe_rows]
        if any(roles.count(role) > 1 for role in expected_roles):
            return {"complete": True, "malformed": True}
        by_role = {str(row.get("treatment_role") or ""): row for row in probe_rows}
        if not expected_roles.issubset(by_role):
            return {"complete": False}
        chosen = [by_role[role] for role in expected_roles]
        failed = [
            row for row in chosen
            if str(row.get("status") or "").lower() != "ok"
            or _row_bool(row.get("timed_out"))
            or str(row.get("protocol_valid") or "true").lower() in {"0", "false", "invalid", "failed"}
            or bool(str(row.get("protocol_error") or row.get("grader_error") or row.get("error") or "").strip())
            or ("done" in row and not _row_bool(row.get("done")))
        ]
        return {
            "complete": True,
            "failed": failed,
            "by_role": by_role,
            "first_trace": _row_trace_observed(by_role["minimum"] if "minimum" in by_role else by_role["off"]),
            "second_trace": _row_trace_observed(by_role["maximum"] if "maximum" in by_role else by_role["on"]),
        }

    primary_result = probe_result(primary)
    if not primary_result.get("complete"):
        return disposition("pending", "primary qualification pair incomplete")
    # Trace evidence is scientifically stronger than a row-level protocol flag
    # that was itself derived from that trace.  Preserve the specific off-control
    # disposition instead of collapsing it into a generic execution failure.
    all_off_rows = [row for row in rows if row.get("treatment_role") == "off"]
    if any(_row_trace_observed(row) for row in all_off_rows):
        return disposition("off-control-ineffective", "requested off treatment emitted an observable reasoning trace")
    if primary_result.get("malformed") or primary_result.get("failed"):
        return disposition("control-inconclusive", "primary qualification request was malformed or did not complete successfully")
    if policy["off_observability"] == "unsupported-native-off":
        return disposition("off-control-ineffective", "installed model package has an unsupported native off control")

    # A visible trace under an off request is conclusive even if the model had
    # previously qualified on an easier prompt.

    def qualified_status() -> str:
        if policy["pair_kind"] == "minimum-vs-maximum":
            return "level-range-qualified"
        if policy["off_observability"] == "unobservable":
            return "off-control-unobservable"
        return "observable-toggle-qualified"

    if policy["pair_kind"] == "minimum-vs-maximum":
        primary_observed = primary_result["first_trace"] and primary_result["second_trace"]
    else:
        primary_observed = primary_result["second_trace"]
    if primary_observed:
        status = qualified_status()
        return disposition(status, "primary qualification demonstrated the requested reasoning control")

    fallback_result = probe_result(fallback)
    if not fallback_result.get("complete"):
        return disposition("pending", "fallback qualification pair required", fallback_required=True)
    if fallback_result.get("malformed") or fallback_result.get("failed"):
        return disposition("control-inconclusive", "fallback qualification request was malformed or did not complete successfully")
    if fallback_result["first_trace"] and policy["pair_kind"] != "minimum-vs-maximum":
        return disposition("off-control-ineffective", "fallback off treatment emitted an observable reasoning trace")
    fallback_observed = (
        fallback_result["first_trace"] and fallback_result["second_trace"]
        if policy["pair_kind"] == "minimum-vs-maximum"
        else fallback_result["second_trace"]
    )
    if fallback_observed:
        status = qualified_status()
        return disposition(status, "fallback qualification demonstrated the requested reasoning control")
    status = "level-range-unverified" if policy["pair_kind"] == "minimum-vs-maximum" else "on-control-unverified"
    return disposition(status, "no observable reasoning trace under the enabled/high treatment")


def make_experiment_id(now: datetime | None = None) -> str:
    moment = now or datetime.now(timezone.utc)
    return f"{moment.strftime('%Y%m%dT%H%M%S.%fZ')}-{uuid.uuid4().hex[:8]}"


def build_paired_plan(
    models: list[dict],
    tasks: list[dict],
    *,
    num_ctx: int | None,
    timeout: int,
    ollama_version: str,
    ollama_url: str,
    host: str,
    host_label: str,
    platform: str,
    os_version: str,
    architecture: str,
    telemetry_backend: str,
    telemetry_interval_ms: int,
    no_stop: bool,
    keep_alive: str | None,
    residency_policy: str,
    suite_version: str,
    benchmark_profile: str,
    grading_profile: str,
    output_token_policy: str,
    runner_sha256: str,
    grader_sha256: str,
    planner_sha256: str = "",
    experiment_id: str | None = None,
    campaign_seed: int = DEFAULT_CAMPAIGN_SEED,
    system_page_size_bytes: int | None = None,
) -> dict:
    if num_ctx is not None and (isinstance(num_ctx, bool) or int(num_ctx) <= 0):
        raise ValueError("paired thinking benchmarks require a positive num_ctx or native per-model context")
    adaptive_context = num_ctx is None
    runtime_resource_safety_policy = _runtime_resource_safety_policy(system_page_size_bytes)
    frozen_system_page_size = runtime_resource_safety_policy["system_page_size_bytes"]
    selected, excluded = dedupe_thinking_models(models)
    if not selected:
        raise ValueError("no thinking-capable models were selected")
    experiment_id = experiment_id or make_experiment_id()
    planned_models = []
    for model_index, source_model in enumerate(selected):
        model = deepcopy(source_model)
        digest = str(model.get("digest") or "")
        if not digest:
            raise ValueError(
                f"paired thinking benchmarks require a model digest: {model.get('name') or 'unnamed model'}"
            )
        advertised_context = model.get("context_length") or model.get("model_context_length")
        try:
            advertised_context = int(advertised_context)
        except (TypeError, ValueError):
            advertised_context = 0
        if adaptive_context and advertised_context <= 0:
            raise ValueError(
                "native-per-model-full num_ctx requires a positive advertised context_length: "
                f"{model.get('name') or 'unnamed model'}"
            )
        for task in tasks:
            conflict = task_control_conflict(model, task)
            if conflict:
                raise ValueError(f"thinking-control task conflict for {model.get('name')}: {task.get('id')}: {conflict}")
        policy = model_control_policy(model)
        if adaptive_context:
            context_record = _adaptive_context_record(
                model, advertised_context, frozen_system_page_size
            )
        else:
            context_record = {
                "model_context_length": advertised_context or "",
                "native_context_length": advertised_context or "",
                "requested_num_ctx": int(num_ctx),
                "context_calibration_profile": "explicit-uniform",
                "context_calibration_status": "not-required",
                "context_calibration_attempt_count": 0,
                "context_calibration_attempts": [],
                "context_adjusted": False,
                "context_reduction_tokens": 0,
                "context_reduction_pct": 0,
                "context_adjustment_reason": "",
            }
        pair_id = _stable_hash(experiment_id, digest, model.get("name"), "pair")
        treatments = []
        for treatment in treatments_for_model(model):
            treatment = deepcopy(treatment)
            treatment["pair_id"] = pair_id
            treatment["treatment_id"] = _stable_hash(pair_id, treatment["treatment_key"])
            treatment["think_payload_json"] = json.dumps(treatment["think_value"], separators=(",", ":"))
            treatments.append(treatment)
        planned_models.append({
            **model,
            "model_index": model_index,
            "pair_id": pair_id,
            **context_record,
            "control_policy": policy["control_policy"],
            "off_observability": policy["off_observability"],
            "evidence_code": policy["evidence_code"],
            "treatments": treatments,
        })
    terminal_dispositions = []
    for model in planned_models:
        if model.get("context_calibration_status") != "no-fit":
            continue
        detail = str(model.get("context_adjustment_reason") or "no context candidate fit")
        terminal_dispositions.append({
            "pair_id": model["pair_id"],
            "model": model["name"],
            "model_digest": model.get("digest") or "",
            "model_qualification_status": "control-inconclusive",
            "model_qualification_reason": (
                "adaptive context calibration found no safely verified context under the frozen host policy: "
                + detail
            ),
            "source": "context-calibration",
            "omitted_remaining_work_count": len(tasks) * len(model["treatments"]),
        })
    plan_core = {
        "pair_schema_version": PAIR_SCHEMA_VERSION,
        "experiment_id": experiment_id,
        "campaign_seed": int(campaign_seed),
        "context_policy": "adaptive-native-per-model" if adaptive_context else "explicit-uniform",
        "runtime_resource_safety_policy": runtime_resource_safety_policy,
        "num_ctx": None if adaptive_context else int(num_ctx),
        "timeout": int(timeout),
        "ollama_version": ollama_version,
        "ollama_url": ollama_url,
        "host": host,
        "host_label": host_label,
        "platform": platform,
        "os_version": os_version,
        "architecture": architecture,
        "telemetry_backend": telemetry_backend,
        "telemetry_interval_ms": int(telemetry_interval_ms),
        "no_stop": bool(no_stop),
        "keep_alive": keep_alive,
        "residency_policy": residency_policy,
        "suite_version": suite_version,
        "benchmark_profile": benchmark_profile,
        "grading_profile": grading_profile,
        "output_token_policy": output_token_policy,
        "runner_sha256": runner_sha256,
        "grader_sha256": grader_sha256,
        "planner_sha256": planner_sha256,
        "temperature": 0,
        "generation_seed": 42,
        "num_predict": -1,
        "task_ids": [task["id"] for task in tasks],
        "task_set_sha256": hashlib.sha256(
            json.dumps([task["id"] for task in tasks], separators=(",", ":")).encode("utf-8")
        ).hexdigest(),
        "qualification_policy_version": QUALIFICATION_POLICY_VERSION,
        "qualification_task_ids": list(QUALIFICATION_TASK_IDS),
        "primary_qualification_task_id": PRIMARY_QUALIFICATION_TASK_ID,
        "fallback_qualification_task_id": FALLBACK_QUALIFICATION_TASK_ID,
        "qualification_trace_rules": deepcopy(QUALIFICATION_TRACE_RULES),
        "models": planned_models,
        # A context-calibration no-fit model has no scientifically valid task
        # row to emit.  Preserve its terminal campaign disposition in the
        # frozen plan instead of fabricating a benchmark observation.
        "terminal_dispositions": terminal_dispositions,
        "excluded_non_thinking": [model.get("name") for model in excluded],
    }
    plan_sha256 = hashlib.sha256(
        json.dumps(plan_core, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    ).hexdigest()
    return {**plan_core, "plan_sha256": plan_sha256}


def ordered_work_items(plan: dict, tasks: list[dict]) -> list[dict]:
    """Return every frozen row, with all primary probes globally first.

    This is the immutable row-ID universe used for provenance validation.  Use
    :func:`qualification_ordered_work_items` for the dynamically eligible next
    phase of a schema-v3 campaign.
    """
    task_by_id = {task["id"]: task for task in tasks}
    items = []
    seed = int(plan.get("campaign_seed") or 0)
    task_ids = list(plan["task_ids"])
    if PRIMARY_QUALIFICATION_TASK_ID in task_ids:
        ordered_task_ids = [PRIMARY_QUALIFICATION_TASK_ID]
        if FALLBACK_QUALIFICATION_TASK_ID in task_ids:
            ordered_task_ids.append(FALLBACK_QUALIFICATION_TASK_ID)
        ordered_task_ids.extend(task_id for task_id in task_ids if task_id not in ordered_task_ids)
        schedule = [(model, task_id) for task_id in ordered_task_ids for model in plan["models"]]
    else:
        # Compatibility for synthetic/legacy task sets that predate schema v3.
        schedule = [(model, task_id) for model in plan["models"] for task_id in task_ids]
    for model, task_id in schedule:
        treatments = model["treatments"]
        task_index = task_ids.index(task_id)
        order = list(range(len(treatments)))
        if len(order) == 2 and (seed + int(model["model_index"]) + task_index) % 2:
            order.reverse()
        for order_index, treatment_index in enumerate(order):
            treatment = treatments[treatment_index]
            row_id = _stable_hash(
                plan["experiment_id"], treatment["treatment_id"], task_id, "attempt-1"
            )
            items.append({
                "experiment_id": plan["experiment_id"],
                "plan_sha256": plan["plan_sha256"],
                "pair_schema_version": plan["pair_schema_version"],
                "pair_id": model["pair_id"],
                "treatment": treatment,
                "treatment_order": order_index + 1,
                "attempt": 1,
                "row_id": row_id,
                "model": model,
                "task": task_by_id[task_id],
                "qualification_phase": "qualification" if task_id in QUALIFICATION_TASK_IDS else "benchmark",
                "qualification_task": task_id in QUALIFICATION_TASK_IDS,
                "qualification_required": task_id in QUALIFICATION_TASK_IDS,
                "qualification_probe": (
                    "primary" if task_id == PRIMARY_QUALIFICATION_TASK_ID
                    else "fallback" if task_id == FALLBACK_QUALIFICATION_TASK_ID
                    else "none"
                ),
            })
    return items


def qualification_fields_for_work(work: dict) -> dict:
    """Return canonical CSV/JSON qualification fields for a work item."""
    return {
        "qualification_phase": work.get("qualification_phase", "benchmark"),
        "qualification_task": bool(work.get("qualification_task")),
        "qualification_required": bool(work.get("qualification_required")),
        "qualification_probe": work.get("qualification_probe", "none"),
    }


def _completed_record_index(plan: dict, tasks: list[dict], records: list[dict]) -> tuple[dict, dict]:
    planned = {work["row_id"]: work for work in ordered_work_items(plan, tasks)}
    completed = {}
    for record in records:
        row = record.get("row") if isinstance(record.get("row"), dict) else record
        row_id = str(row.get("row_id") or "")
        if not row_id:
            raise ValueError("completed qualification record lacks row_id")
        if row_id in completed:
            raise ValueError(f"duplicate completed row_id: {row_id}")
        work = planned.get(row_id)
        if not work:
            raise ValueError(f"completed row_id is absent from the frozen plan: {row_id}")
        # The runner performs the complete schema-v3 provenance validation.
        # These identity checks keep this pure scheduling API fail-closed too.
        identity = {
            "experiment_id": plan["experiment_id"],
            "plan_sha256": plan["plan_sha256"],
            "pair_id": work["pair_id"],
            "task_id": work["task"]["id"],
            "treatment_id": work["treatment"]["treatment_id"],
            "treatment_key": work["treatment"]["treatment_key"],
        }
        mismatches = [
            field for field, expected in identity.items()
            if str(row.get(field) or "") and str(row.get(field)) != str(expected)
        ]
        if mismatches:
            raise ValueError(f"completed row provenance mismatch for {row_id}: {', '.join(mismatches)}")
        normalized = deepcopy(row)
        normalized.update({
            "experiment_id": plan["experiment_id"],
            "plan_sha256": plan["plan_sha256"],
            "pair_id": work["pair_id"],
            "task_id": work["task"]["id"],
            "treatment_id": work["treatment"]["treatment_id"],
            "treatment_key": work["treatment"]["treatment_key"],
            "treatment_role": work["treatment"]["treatment_role"],
        })
        for field, value in qualification_fields_for_work(work).items():
            normalized.setdefault(field, value)
        evidence_record = {"row": normalized}
        if "thinking" in record:
            evidence_record["thinking"] = record.get("thinking")
        if "response" in record:
            evidence_record["response"] = record.get("response")
        completed[row_id] = evidence_record
    return planned, completed


def qualification_schedule(plan: dict, tasks: list[dict], completed_records: list[dict]) -> dict:
    """Derive the next eligible schema-v3 work without rerunning evidence rows.

    The returned ``work_items`` are one global phase only: every unfinished
    primary pair, then every required fallback pair, then the remaining full
    benchmark rows for eligible models.  A terminally invalid model contributes
    no further work; its omitted frozen rows are counted and retained by ID.
    """
    planned, completed = _completed_record_index(plan, tasks, completed_records)
    all_items = list(planned.values())
    by_pair_records = {model["pair_id"]: [] for model in plan["models"]}
    for row_id, record in completed.items():
        by_pair_records[planned[row_id]["pair_id"]].append(record)
    dispositions_by_pair = {
        model["pair_id"]: derive_model_qualification(model, by_pair_records[model["pair_id"]], plan)
        for model in plan["models"]
    }

    def unfinished(predicate) -> list[dict]:
        return [work for work in all_items if predicate(work) and work["row_id"] not in completed]

    primary = unfinished(
        lambda work: work["task"]["id"] == PRIMARY_QUALIFICATION_TASK_ID
        and work["model"].get("context_calibration_status") != "no-fit"
    )
    phase = "primary"
    ready = primary
    if not ready:
        fallback_pairs = {
            pair_id for pair_id, disposition in dispositions_by_pair.items()
            if disposition.get("fallback_required")
        }
        ready = unfinished(
            lambda work: work["pair_id"] in fallback_pairs
            and work["task"]["id"] == FALLBACK_QUALIFICATION_TASK_ID
        )
        phase = "fallback"
    if not ready:
        eligible_pairs = {
            pair_id for pair_id, disposition in dispositions_by_pair.items()
            if disposition.get("eligible")
        }
        ready = unfinished(lambda work: work["pair_id"] in eligible_pairs)
        phase = "benchmark" if ready else "complete"
        if ready:
            # Yield one adjacent treatment pair.  Recomputing after every pair
            # lets a late off-arm leak terminally omit that model before any of
            # its subsequent benchmark tasks can start.
            first = ready[0]
            ready = [
                work for work in ready
                if work["pair_id"] == first["pair_id"]
                and work["task"]["id"] == first["task"]["id"]
            ]

    # A fallback task becomes ordinary benchmark work when the primary probe
    # already qualified the model; its row ID remains unchanged and therefore
    # can never be executed twice.
    annotated = []
    for work in ready:
        work = deepcopy(work)
        if phase == "primary":
            work.update(qualification_phase="qualification", qualification_task=True,
                        qualification_required=True, qualification_probe="primary")
        elif phase == "fallback":
            work.update(qualification_phase="qualification", qualification_task=True,
                        qualification_required=True, qualification_probe="fallback")
        else:
            work.update(qualification_phase="benchmark", qualification_task=False,
                        qualification_required=False, qualification_probe="none")
        annotated.append(work)

    omitted_by_pair = {}
    for model in plan["models"]:
        pair_id = model["pair_id"]
        if dispositions_by_pair[pair_id].get("omit_remaining"):
            omitted_by_pair[pair_id] = [
                work["row_id"] for work in all_items
                if work["pair_id"] == pair_id and work["row_id"] not in completed
            ]
        else:
            omitted_by_pair[pair_id] = []
        dispositions_by_pair[pair_id]["omitted_remaining_work_count"] = len(omitted_by_pair[pair_id])

    return {
        "phase": phase,
        "work_items": annotated,
        "dispositions_by_pair": dispositions_by_pair,
        "dispositions_by_model": {
            model["name"]: dispositions_by_pair[model["pair_id"]] for model in plan["models"]
        },
        "completed_row_ids": set(completed),
        "omitted_row_ids_by_pair": omitted_by_pair,
        "omitted_remaining_work_count": sum(map(len, omitted_by_pair.values())),
        "campaign_complete": phase == "complete",
    }


def qualification_ordered_work_items(plan: dict, tasks: list[dict], completed_records: list[dict]) -> list[dict]:
    """Return only the next globally eligible qualification/benchmark phase."""
    return qualification_schedule(plan, tasks, completed_records)["work_items"]


def planned_counts(plan: dict, tasks: list[dict]) -> dict:
    rows = calls = skips = 0
    task_by_id = {task["id"]: task for task in tasks}
    for model in plan["models"]:
        for _treatment in model["treatments"]:
            for task_id in plan["task_ids"]:
                rows += 1
                task = task_by_id[task_id]
                caps = {str(value).lower() for value in model.get("capabilities") or []}
                if task.get("requires_image") and not (caps & {"image", "vision", "ocr"}):
                    skips += 1
                else:
                    calls += 1
    return {"rows": rows, "inference_calls": calls, "capability_skips": skips}


def validate_resume_plan(
    plan: dict,
    current_models: list[dict],
    tasks: list[dict],
    *,
    num_ctx: int | None,
    timeout: int,
    ollama_version: str,
    ollama_url: str,
    host: str,
    host_label: str,
    platform: str,
    os_version: str,
    architecture: str,
    telemetry_backend: str,
    telemetry_interval_ms: int,
    no_stop: bool,
    keep_alive: str | None,
    residency_policy: str,
    suite_version: str,
    benchmark_profile: str,
    grading_profile: str,
    output_token_policy: str,
    runner_sha256: str,
    grader_sha256: str,
    planner_sha256: str,
    system_page_size_bytes: int | None = None,
) -> None:
    """Reject a resume when any inference-relevant provenance changed."""
    adaptive_context = num_ctx is None
    runtime_resource_safety_policy = _runtime_resource_safety_policy(system_page_size_bytes)
    frozen_system_page_size = runtime_resource_safety_policy["system_page_size_bytes"]
    expected = {
        "pair_schema_version": PAIR_SCHEMA_VERSION,
        "context_policy": "adaptive-native-per-model" if adaptive_context else "explicit-uniform",
        "runtime_resource_safety_policy": runtime_resource_safety_policy,
        "num_ctx": None if adaptive_context else int(num_ctx),
        "timeout": int(timeout),
        "ollama_version": ollama_version,
        "ollama_url": ollama_url,
        "host": host,
        "host_label": host_label,
        "platform": platform,
        "os_version": os_version,
        "architecture": architecture,
        "telemetry_backend": telemetry_backend,
        "telemetry_interval_ms": int(telemetry_interval_ms),
        "no_stop": bool(no_stop),
        "keep_alive": keep_alive,
        "residency_policy": residency_policy,
        "suite_version": suite_version,
        "benchmark_profile": benchmark_profile,
        "grading_profile": grading_profile,
        "output_token_policy": output_token_policy,
        "runner_sha256": runner_sha256,
        "grader_sha256": grader_sha256,
        "planner_sha256": planner_sha256,
        "temperature": 0,
        "generation_seed": 42,
        "num_predict": -1,
        "task_ids": [task["id"] for task in tasks],
        "task_set_sha256": hashlib.sha256(
            json.dumps([task["id"] for task in tasks], separators=(",", ":")).encode("utf-8")
        ).hexdigest(),
        "qualification_policy_version": QUALIFICATION_POLICY_VERSION,
        "qualification_task_ids": list(QUALIFICATION_TASK_IDS),
        "primary_qualification_task_id": PRIMARY_QUALIFICATION_TASK_ID,
        "fallback_qualification_task_id": FALLBACK_QUALIFICATION_TASK_ID,
        "qualification_trace_rules": QUALIFICATION_TRACE_RULES,
    }
    mismatches = []
    for field, value in expected.items():
        if plan.get(field) != value:
            mismatches.append(f"{field}: plan={plan.get(field)!r}, current={value!r}")
    core = {
        key: value for key, value in plan.items()
        if key not in {"plan_sha256", "run_id", "report_prefix"}
    }
    computed = hashlib.sha256(
        json.dumps(core, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    ).hexdigest()
    if plan.get("plan_sha256") != computed:
        mismatches.append("plan_sha256 does not match the manifest contents")
    current_by_name = {
        str(model.get("name") or ""): model for model in current_models if model.get("name")
    }
    for planned in plan.get("models") or []:
        digest = str(planned.get("digest") or "")
        name = str(planned.get("name") or "")
        current = current_by_name.get(name)
        if not current:
            mismatches.append(f"planned model tag is no longer installed: {name} / {digest}")
            continue
        current_digest = str(current.get("digest") or "")
        if not digest or current_digest != digest:
            mismatches.append(
                f"planned model tag digest changed: {name} / plan={digest!r}, current={current_digest!r}"
            )
            continue
        expected_treatments = treatments_for_model(current)
        planned_payloads = [
            (item.get("treatment_key"), item.get("think_present"), item.get("think_value"))
            for item in planned.get("treatments") or []
        ]
        current_payloads = [
            (item.get("treatment_key"), item.get("think_present"), item.get("think_value"))
            for item in expected_treatments
        ]
        if planned_payloads != current_payloads:
            mismatches.append(f"thinking treatment policy changed for {planned.get('name')}")
        expected_policy = model_control_policy(current)
        for field in ("control_policy", "off_observability", "evidence_code"):
            if planned.get(field) != expected_policy[field]:
                mismatches.append(f"{field} changed for {planned.get('name')}")
        if adaptive_context:
            try:
                current_context = int(current.get("context_length") or current.get("model_context_length"))
            except (TypeError, ValueError):
                current_context = 0
            if current_context <= 0:
                mismatches.append(f"native context is unavailable for {planned.get('name')}")
            if planned.get("model_context_length") != current_context:
                mismatches.append(
                    f"model context changed for {planned.get('name')}: "
                    f"plan={planned.get('model_context_length')!r}, current={current_context!r}"
                )
            try:
                frozen_context = _adaptive_context_record(
                    planned, current_context, frozen_system_page_size
                )
            except ValueError as exc:
                mismatches.append(str(exc))
            else:
                for field, value in frozen_context.items():
                    if planned.get(field) != value:
                        mismatches.append(f"frozen context calibration field changed for {planned.get('name')}: {field}")
        elif planned.get("requested_num_ctx") != int(num_ctx):
            mismatches.append(f"requested uniform context changed for {planned.get('name')}")
    if mismatches:
        raise ValueError("resume plan provenance mismatch: " + "; ".join(mismatches))
