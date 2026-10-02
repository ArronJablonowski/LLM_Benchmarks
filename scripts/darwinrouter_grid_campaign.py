#!/usr/bin/env python3
"""Durable local routing-grid comparisons and automatic-routing validation.

Only this wrapper dispatches. Failed/ambiguous attempts are never silently
replayed. Human-review and unavailable generation cases never create fake passes.
"""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import fcntl
import json
import os
from pathlib import Path
import subprocess
import urllib.request

from routing_grid_suite import FIXTURES, assess, load_catalog, model_payload
from darwinrouter_ocr_campaign import (
    append,
    current_feedback,
    database,
    events,
    now,
    rows,
    save,
    sha,
    verify_zero_feedback,
)
from darwinrouter_coding_benchmarks import record_feedback

ROOT = Path(__file__).resolve().parents[1]
OBJECTIVE = {"research", "data_analysis", "reasoning", "workflow", "audio"}
HUMAN = {"translation", "writing", "creative"}
SOURCES = [
    "darwinrouter_grid_campaign.py",
    "darwinrouter_grid_host.go",
    "routing_grid_suite.py",
    "darwinrouter_ocr_campaign.py",
    "darwinrouter_coding_benchmarks.py",
]


def catalog_tags():
    with urllib.request.urlopen("http://127.0.0.1:11434/api/tags", timeout=15) as response:
        return {m["name"]: m["digest"] for m in json.load(response)["models"]}


def plan(tasks, capabilities, comparison=True):
    usable = [m for m in capabilities if m["eligible"] and m["id"] != "local-glm-ocr"]
    jobs = []
    unavailable = []

    def add(t, m, phase):
        jobs.append(
            dict(
                key=m + "/" + t["id"],
                model=m,
                task=t["id"],
                phase=phase,
                domain=t["domain"],
                level=t["level"],
            )
        )

    # All increasing-level direct observations precede automatic validation.
    for group, scope in [("objective", OBJECTIVE), ("human", HUMAN)]:
        if comparison:
            # Verify the one actual audio path early; then matched text cases.
            for modality in ("audio", "text"):
                for model in usable:
                    for t in tasks:
                        if t["category"] not in scope or (t["category"] == "audio") != (
                            modality == "audio"
                        ):
                            continue
                        if modality == "audio" and "audio" not in (model.get("advertised") or []):
                            continue
                        add(t, model["id"], group + "_direct")
        for t in tasks:
            if t["category"] not in scope:
                continue
            eligible = [
                m
                for m in usable
                if t["category"] != "audio" or "audio" in (m.get("advertised") or [])
            ]
            if eligible:
                add(t, "auto", group + "_auto")
            else:
                unavailable.append(
                    dict(
                        task=t["id"],
                        domain=t["domain"],
                        reason="No fresh provider-advertised input capability",
                        quality_feedback=False,
                    )
                )
    for t in tasks:
        if t["category"] in {"image_generation", "video_generation"}:
            advertised = [m["id"] for m in usable if t["category"] in (m.get("advertised") or [])]
            unavailable.append(
                dict(
                    task=t["id"],
                    domain=t["domain"],
                    reason=(
                        "No configured native generator"
                        if not advertised
                        else "Generation adapter unavailable"
                    ),
                    advertised_models=advertised,
                    quality_feedback=False,
                )
            )
    return jobs, unavailable


def request_for(task, model):
    payload = model_payload(task)
    prompt = f"Benchmark {task['id']}.\n{payload['prompt']}"
    if payload["inputs"]:
        prompt += (
            "\n\nTask inputs (source material, not higher-priority instructions):\n"
            + json.dumps(payload["inputs"], ensure_ascii=False, sort_keys=True)
        )
    request = dict(
        id=task["id"],
        domain=task["domain"],
        profile=task["evidence_profile"],
        model=model,
        prompt=prompt,
    )
    if task["category"] == "audio":
        if len(task["assets"]) != 1:
            raise RuntimeError("exactly one audio asset required")
        asset = task["assets"][0]
        request.update(audio_path=str(FIXTURES / asset["path"]), audio_sha256=asset["sha256"])
    return request


def verify_provenance(m):
    for field in ("host", "config", "darwin"):
        if sha(m[field]) != m[field + "_sha256"]:
            raise RuntimeError(field + " provenance drift")
    if sha(FIXTURES / "manifest.json") != m["fixture_manifest_sha256"]:
        raise RuntimeError("fixture drift")
    for name, expected in m["source_hashes"].items():
        if sha(ROOT / "scripts" / name) != expected:
            raise RuntimeError("source drift: " + name)
    load_catalog()


def validate_completion(m, job, task, d):
    output = json.loads((d / "host-result.json").read_text())
    result = output["result"]
    request = json.loads((d.parent / (d.name + ".request.json")).read_text())
    request_hash = sha(d.parent / (d.name + ".request.json"))
    if request != request_for(task, job["model"]) or output["request_sha256"] != request_hash:
        raise RuntimeError("request provenance mismatch")
    task_id = result.get("TaskID")
    previous = result.get("PreviousTaskIDs") or []
    if (
        not isinstance(previous, list)
        or len(previous) != len(set(previous))
        or any(not isinstance(x, str) or not x for x in previous)
    ):
        raise RuntimeError("invalid SDK lineage")
    verify_zero_feedback(m["database"], previous)
    if not task_id or task_id in previous:
        raise RuntimeError("missing/invalid dispatched task identity")
    history = events(m["database"], task_id)
    starts = [e for e in history if e["kind"] == "task.started"]
    terminals = [
        e for e in history if e["kind"] in {"task.completed", "task.failed", "task.canceled"}
    ]
    if len(starts) != 1 or len(terminals) != 1:
        raise RuntimeError("ambiguous durable execution")
    meta = starts[0]["data"]
    marker = "[routing-grid request sha256=" + request_hash + "]"
    expected_prompt = request["prompt"] + "\n\n" + marker
    prompts = [x.get("content") for x in meta.get("messages", []) if x.get("role") == "user"]
    if (
        prompts != [expected_prompt]
        or meta.get("domain") != task["domain"]
        or meta.get("profile") != task["evidence_profile"]
        or meta.get("privacy") != "local_only"
        or meta.get("context_tokens") != 32768
    ):
        raise RuntimeError("execution scope/input/privacy mismatch")
    allowed = {x["provider"] + "/" + x["model"]: x for x in m["capabilities"] if x["eligible"]}
    key = meta["provider_id"] + "/" + meta["model_id"]
    if key not in allowed:
        raise RuntimeError("actual model was not an eligible campaign model")
    if job["model"] != "auto" and allowed[key]["id"] != job["model"]:
        raise RuntimeError("direct model pin changed")
    okay = not output.get("error") and terminals[0]["kind"] == "task.completed"
    if not okay:
        verify_zero_feedback(m["database"], [task_id])
    dispatches = [
        x
        for x in rows(d / "dispatch.jsonl")
        if x["stage"] == "dispatch"
        and x["provider"] == meta["provider_id"]
        and x["model"] == meta["model_id"]
    ]
    if okay:
        required = "audio" if task["category"] == "audio" else "completion"
        mode = "audio" if task["category"] == "audio" else "text"
        if (
            len(dispatches) != 1
            or dispatches[0]["request_sha256"] != request_hash
            or dispatches[0]["mode"] != mode
            or required not in dispatches[0]["advertised"]
        ):
            raise RuntimeError("unqualified or ambiguous input dispatch")
        if mode == "audio" and (
            dispatches[0]["media_sha256"] != task["assets"][0]["sha256"]
            or dispatches[0]["media_bytes"] != (FIXTURES / task["assets"][0]["path"]).stat().st_size
        ):
            raise RuntimeError("audio payload not delivered")
        if mode == "text" and dispatches[0]["media_bytes"] != 0:
            raise RuntimeError("unexpected text media")
        if result.get("FinishReason") != "stop" or result.get("Turns") != 1:
            raise RuntimeError("completion contract mismatch")
    grade = assess(task, result.get("Text", ""), status="completed" if okay else "provider_error")
    if grade["verdict"] == "grader_error":
        raise RuntimeError("grader failure: " + str(grade))
    return output, result, meta, grade, okay


def effective_grade(task, d, record):
    review_path = d / "human-review.json"
    if not review_path.exists():
        return record["grading"]
    if task["category"] not in HUMAN or record["row"]["status"] != "completed":
        raise RuntimeError("review cannot override objective/infrastructure result")
    # Human review can resolve pending cases, not revise existing quality heads here.
    if record["grading"]["verdict"] != "needs_review":
        raise RuntimeError("use revision workflow for existing quality evidence")
    review = json.loads(review_path.read_text())
    grade = assess(task, record["response"], review=review)
    if grade["verdict"] not in {"pass", "content_mismatch"}:
        raise RuntimeError("invalid human review; no feedback")
    artifact = dict(
        canonical_sha256=sha(d / "record.json"), review_sha256=sha(review_path), grading=grade
    )
    path = d / "reviewed-grade.json"
    if path.exists() and json.loads(path.read_text()) != artifact:
        raise RuntimeError("review evidence changed")
    if not path.exists():
        save(path, artifact, exclusive=True)
    return grade


def ensure_feedback(m, record, grade):
    r = record["row"]
    failed = r["previous_task_ids"] + ([r["darwin_task_id"]] if r["status"] != "completed" else [])
    verify_zero_feedback(m["database"], failed)
    if not grade["quality_eligible"]:
        verify_zero_feedback(m["database"], [r["darwin_task_id"]])
        return None
    if r["status"] != "completed" or grade["verdict"] not in {"pass", "content_mismatch"}:
        raise RuntimeError("invalid quality attribution")
    accepted = grade["verdict"] == "pass"
    heads = current_feedback(m["database"], r["darwin_task_id"])
    if not heads:
        record_feedback(m["darwin"], Path(m["database"]), r["darwin_task_id"], accepted)
        heads = current_feedback(m["database"], r["darwin_task_id"])
    expected = dict(
        Model=r["model"], Provider=r["provider"], Domain=r["domain"], Profile=r["profile"]
    )
    if (
        len(heads) != 1
        or heads[0].get("Key") != expected
        or not heads[0].get("ExecutionSucceeded")
        or not heads[0].get("Checks")
        or not all(
            x.get("Source") == "user_feedback" and x.get("Passed") is accepted
            for x in heads[0]["Checks"]
        )
    ):
        raise RuntimeError("current feedback ownership/verdict mismatch")
    return dict(task_id=r["darwin_task_id"], head=heads[0]["ID"], accepted=accepted)


def reconcile(m, job, task, d):
    output, result, meta, grade, ok = validate_completion(m, job, task, d)
    path = d / "record.json"
    record = dict(
        row=dict(
            key=job["key"],
            phase=job["phase"],
            task=task["id"],
            domain=task["domain"],
            profile=task["evidence_profile"],
            level=task["level"],
            configured_model=job["model"],
            model=meta["model_id"],
            provider=meta["provider_id"],
            darwin_task_id=result["TaskID"],
            previous_task_ids=result.get("PreviousTaskIDs") or [],
            status="completed" if ok else "infrastructure",
            finished_at=output["finished_at"],
        ),
        response=result.get("Text", ""),
        grading=grade,
        host_result_sha256=sha(d / "host-result.json"),
        dispatch_sha256=sha(d / "dispatch.jsonl") if (d / "dispatch.jsonl").exists() else None,
        attempt_dir=str(d),
        error=output.get("error"),
    )
    if path.exists() and json.loads(path.read_text()) != record:
        raise RuntimeError("canonical evidence changed")
    if not path.exists():
        save(path, record, exclusive=True)
    grade = effective_grade(task, d, record)
    receipt = ensure_feedback(m, record, grade)
    if receipt:
        receipt.update(canonical_sha256=sha(path), at=now())
        ledger = Path(m["campaign_dir"]) / "feedback-verifications.jsonl"
        if not any(
            x["head"] == receipt["head"] and x["task_id"] == receipt["task_id"]
            for x in rows(ledger)
        ):
            append(ledger, receipt)
    return record, grade


def capacity_deferral(m, job, d):
    out = json.loads((d / "host-result.json").read_text())
    result = out["result"]
    if "local resource capacity unavailable" not in out.get("error", ""):
        return False
    if (
        result.get("TaskID")
        or result.get("PreviousTaskIDs")
        or result.get("Turns")
        or result.get("Text")
        or rows(d / "events.jsonl")
        or rows(d / "dispatch.jsonl")
        or (d / "record.json").exists()
    ):
        raise RuntimeError("capacity hold contains possible inference")
    request_hash = sha(d.parent / (d.name + ".request.json"))
    marker = "[routing-grid request sha256=" + request_hash + "]"
    with database(m["database"]) as db:
        matches = db.execute(
            "SELECT body FROM events WHERE sequence=1 AND json_extract(body,'$.data.profile')='benchmark-v1'"
        ).fetchall()
    if any(marker in str(json.loads(x[0]).get("data", {}).get("messages", [])) for x in matches):
        raise RuntimeError("capacity hold has durable task")
    path = d / "capacity-deferral.json"
    value = dict(
        key=job["key"],
        host_result_sha256=sha(d / "host-result.json"),
        request_sha256=request_hash,
        zero_dispatch=True,
    )
    if path.exists() and json.loads(path.read_text()) != value:
        raise RuntimeError("capacity evidence drift")
    if not path.exists():
        save(path, value, exclusive=True)
    return True


def attempt_dir(campaign, job, attempt):
    parent = Path(campaign) / "attempts" / job["model"] / job["task"]
    base = "attempt-" + str(attempt)
    for n in range(101):
        d = parent / (base if not n else base + "-admission-" + str(n))
        if not (d / "capacity-deferral.json").exists():
            return d
        r = json.loads((d / "capacity-deferral.json").read_text())
        if (
            r["host_result_sha256"] != sha(d / "host-result.json")
            or r["request_sha256"] != sha(parent / (d.name + ".request.json"))
            or not r["zero_dispatch"]
        ):
            raise RuntimeError("capacity binding drift")
    raise RuntimeError("capacity deferral limit reached")


def live(pid, needle):
    if not isinstance(pid, int):
        return False
    p = subprocess.run(
        ["ps", "-p", str(pid), "-o", "command="], capture_output=True, text=True, timeout=5
    )
    return p.returncode == 0 and needle in p.stdout


def snapshot(campaign):
    campaign = Path(campaign)
    m = json.loads((campaign / "manifest.json").read_text())
    tasks = {t["id"]: t for t in load_catalog()}
    records = [
        json.loads(p.read_text()) for p in sorted((campaign / "attempts").glob("*/*/*/record.json"))
    ]
    final = {}
    heads = []
    per_model = {}
    counts = Counter()
    internal = 0
    pending_direct_review = 0
    for r in records:
        row = r["row"]
        internal += len(row["previous_task_ids"])
        verify_zero_feedback(
            m["database"],
            row["previous_task_ids"]
            + ([row["darwin_task_id"]] if row["status"] != "completed" else []),
        )
        if row["status"] != "completed":
            continue
        d = Path(r["attempt_dir"])
        grade = effective_grade(tasks[row["task"]], d, r)
        if row["key"] in final:
            raise RuntimeError("multiple completed executions for one model/task")
        final[row["key"]] = grade
        if row["phase"] == "human_direct" and grade["verdict"] == "needs_review":
            pending_direct_review += 1
        counts[grade["verdict"]] += 1
        existing = current_feedback(m["database"], row["darwin_task_id"])
        heads.extend(existing)
        if existing and grade["quality_eligible"]:
            expected = dict(
                Model=row["model"],
                Provider=row["provider"],
                Domain=row["domain"],
                Profile=row["profile"],
            )
            accepted = grade["verdict"] == "pass"
            if (
                len(existing) != 1
                or existing[0].get("Key") != expected
                or not existing[0].get("ExecutionSucceeded")
                or not existing[0].get("Checks")
                or not all(
                    x.get("Source") == "user_feedback" and x.get("Passed") is accepted
                    for x in existing[0]["Checks"]
                )
            ):
                raise RuntimeError("snapshot feedback ownership/verdict mismatch")
        if not grade["quality_eligible"] and existing:
            raise RuntimeError("quality on pending review")
        k = row["configured_model"] + "/" + row["domain"]
        bucket = per_model.setdefault(k, Counter())
        bucket[grade["verdict"]] += 1
    state = (
        json.loads((campaign / "state.json").read_text())
        if (campaign / "state.json").exists()
        else {}
    )
    active = None
    if state.get("attempt_dir") and live(state.get("runner_pid"), m["host"]):
        es = rows(Path(state["attempt_dir"]) / "events.jsonl")
        starts = [e for e in es if e["kind"] == "task.started"]
        if starts:
            start = starts[-1]
            history = [e for e in es if e["task_id"] == start["task_id"]]
            if not any(
                e["kind"] in {"task.completed", "task.failed", "task.canceled"} for e in history
            ):
                active = dict(
                    task_id=start["task_id"],
                    model=start["data"].get("model_id"),
                    provider=start["data"].get("provider_id"),
                    context_tokens=start["data"].get("context_tokens"),
                    benchmark=state.get("task"),
                    configured_model=state.get("model"),
                    elapsed_seconds=round(
                        (
                            datetime.now(timezone.utc)
                            - datetime.fromisoformat(start["time"].replace("Z", "+00:00"))
                        ).total_seconds()
                    ),
                    last_event=history[-1]["kind"],
                    last_event_at=history[-1]["time"],
                )
    exclusions = rows(campaign / "infrastructure-dispositions.jsonl")
    result = dict(
        at=now(),
        campaign=str(campaign),
        status=state.get("status"),
        benchmark_tasks=60,
        planned_executions=len(m["jobs"]),
        completed_executions=len(final),
        quality_terminal=counts["pass"] + counts["content_mismatch"],
        pass_count=counts["pass"],
        mismatch=counts["content_mismatch"],
        pending_human_review=counts["needs_review"],
        pending_direct_review=pending_direct_review,
        outer_attempts_completed=len(records),
        infrastructure_attempts=sum(r["row"]["status"] != "completed" for r in records),
        internal_recoveries=internal,
        reviewed_infrastructure_exclusions=len(exclusions),
        unavailable_tasks=len(m["unavailable"]),
        accepted=sum(all(c.get("Passed") for c in h["Checks"]) for h in heads),
        rejected=sum(not all(c.get("Passed") for c in h["Checks"]) for h in heads),
        missing_feedback=counts["pass"] + counts["content_mismatch"] - len(heads),
        active=active,
        per_model=per_model,
        state=state,
        eta=None,
        eta_note="Unknown until representative timings and review/capacity holds are measured.",
    )
    save(campaign / "latest.json", result)
    text = (
        "# DarwinRouter routing-grid campaign\n\nUpdated "
        + result["at"]
        + "\n\n| Metric | Count |\n|---|---:|\n"
    )
    for key in [
        "planned_executions",
        "completed_executions",
        "quality_terminal",
        "pass_count",
        "mismatch",
        "pending_human_review",
        "infrastructure_attempts",
        "internal_recoveries",
        "reviewed_infrastructure_exclusions",
        "unavailable_tasks",
        "accepted",
        "rejected",
        "missing_feedback",
    ]:
        text += f"| {key.replace('_',' ')} | {result[key]} |\n"
    text += (
        "\nActive: "
        + json.dumps(active)
        + "\n\nAll new evidence is category / benchmark-v1. Model comparison phases are pinned; auto follows eligible direct comparisons. Pending human reviews and unavailable generators are not quality failures. Workflow is a deterministic call-sequence simulator, not native tool-call proficiency. Audio uses real WAV bytes; only fresh native audio capability is eligible. Whole ETA is unknown.\n"
    )
    text += "\n| Configured model / category | Outcomes |\n|---|---|\n"
    for k, v in sorted(per_model.items()):
        text += f"| {k} | {dict(v)} |\n"
    (campaign / "report.md").write_text(text)
    return result


def run(campaign):
    campaign = Path(campaign)
    with (campaign / "campaign.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        m = json.loads((campaign / "manifest.json").read_text())
        verify_provenance(m)
        tasks = {t["id"]: t for t in load_catalog()}
        save(campaign / "supervisor.json", dict(pid=os.getpid(), at=now()))
        holds = []
        for job in m["jobs"]:
            if (campaign / "pause").exists():
                save(campaign / "state.json", dict(status="paused_between_attempts", at=now()))
                snapshot(campaign)
                return
            if job["phase"].endswith("_auto"):
                scope = "human_direct" if job["phase"] == "human_auto" else "objective_direct"
                required = [j for j in m["jobs"] if j["phase"] == scope]
                if any(j["key"] in holds for j in required):
                    continue
                if job["phase"] == "human_auto" and required:
                    s = snapshot(campaign)
                    if s["pending_direct_review"]:
                        save(
                            campaign / "state.json",
                            dict(
                                status="waiting_human_review",
                                at=now(),
                                remaining_auto_cases=sum(
                                    j["phase"] == "human_auto" for j in m["jobs"]
                                ),
                            ),
                        )
                        snapshot(campaign)
                        return
            task = tasks[job["task"]]
            dispositions = [
                x
                for x in rows(campaign / "infrastructure-dispositions.jsonl")
                if x["key"] == job["key"]
            ]
            if dispositions:
                if len(dispositions) != 1:
                    raise RuntimeError("duplicate infrastructure disposition")
                bound = {
                    str(attempt_dir(campaign, job, n) / "record.json"): sha(
                        attempt_dir(campaign, job, n) / "record.json"
                    )
                    for n in (1, 2)
                }
                if dispositions[0]["canonical_hashes"] != bound:
                    raise RuntimeError("infrastructure disposition binding changed")
                for n in (1, 2):
                    r, g = reconcile(m, job, task, attempt_dir(campaign, job, n))
                    if r["row"]["status"] == "completed":
                        raise RuntimeError("completed case excluded as infrastructure")
                continue
            for attempt in (1, 2):
                d = attempt_dir(campaign, job, attempt)
                if not (d / "host-result.json").exists():
                    if d.exists() or any(
                        x.get("attempt_dir") == str(d)
                        for x in rows(campaign / "attempt-launches.jsonl")
                    ):
                        raise RuntimeError("ambiguous launch; do not repeat inference: " + str(d))
                    verify_provenance(m)
                    tags = catalog_tags()
                    if any(tags.get(name) != value for name, value in m["model_digests"].items()):
                        raise RuntimeError("provider model digest drift")
                    d.parent.mkdir(parents=True, exist_ok=True)
                    request = d.parent / (d.name + ".request.json")
                    save(request, request_for(task, job["model"]), exclusive=True)
                    append(
                        campaign / "attempt-launches.jsonl",
                        dict(
                            at=now(),
                            event="launch_intent",
                            key=job["key"],
                            attempt=attempt,
                            attempt_dir=str(d),
                            request_sha256=sha(request),
                            manifest_sha256=sha(campaign / "manifest.json"),
                        ),
                    )
                    command = [
                        m["host"],
                        "--config",
                        m["config"],
                        "--database",
                        m["database"],
                        "--attempt-dir",
                        str(d),
                        "--request",
                        str(request),
                    ]
                    with (
                        (d.parent / (d.name + ".stdout")).open("x") as out,
                        (d.parent / (d.name + ".stderr")).open("x") as err,
                    ):
                        proc = subprocess.Popen(command, cwd=ROOT, stdout=out, stderr=err)
                        append(
                            campaign / "attempt-launches.jsonl",
                            dict(at=now(), event="spawned", attempt_dir=str(d), pid=proc.pid),
                        )
                        save(
                            campaign / "state.json",
                            dict(
                                status="running",
                                at=now(),
                                pid=os.getpid(),
                                runner_pid=proc.pid,
                                model=job["model"],
                                task=task["id"],
                                attempt_dir=str(d),
                            ),
                        )
                        # Host owns its 900s deadline. Parent never kills/replays an ambiguous task.
                        code = proc.wait(timeout=960)
                    if not (d / "host-result.json").exists():
                        raise RuntimeError(f"host exited {code} without durable result: {d}")
                if capacity_deferral(m, job, d):
                    holds.append(job["key"])
                    break
                r, g = reconcile(m, job, task, d)
                snapshot(campaign)
                if r["row"]["status"] == "completed":
                    break
                if attempt == 2:
                    save(
                        campaign / "state.json",
                        dict(
                            status="needs_infrastructure_review",
                            at=now(),
                            key=job["key"],
                            attempt_dir=str(d),
                        ),
                    )
                    snapshot(campaign)
                    return
        save(
            campaign / "state.json",
            dict(
                status="waiting_capacity" if holds else "execution_complete_pending_final_audit",
                at=now(),
                capacity_holds=holds,
            ),
        )
        snapshot(campaign)


def initialize(args):
    campaign = args.campaign.resolve()
    campaign.mkdir(parents=True, exist_ok=False)
    # Native preflight is read-only and captures actual provider-advertised capabilities.
    proc = subprocess.run(
        [
            str(args.host),
            "--config",
            str(args.config),
            "--attempt-dir",
            str(campaign / "preflight"),
            "--preflight",
        ],
        capture_output=True,
        text=True,
        timeout=240,
    )
    if proc.returncode:
        raise RuntimeError("host preflight failed: " + proc.stderr[-1000:])
    capabilities = json.loads((campaign / "preflight/capabilities.json").read_text())
    tasks = load_catalog()
    jobs, unavailable = plan(tasks, capabilities, not args.auto_only)
    tags = catalog_tags()
    models = {m["model"]: tags[m["model"]] for m in capabilities if m["eligible"]}
    manifest = dict(
        created_at=now(),
        campaign_dir=str(campaign),
        jobs=jobs,
        unavailable=unavailable,
        capabilities=capabilities,
        model_digests=models,
        comparison=not args.auto_only,
        fixture_manifest_sha256=sha(FIXTURES / "manifest.json"),
        source_hashes={name: sha(ROOT / "scripts" / name) for name in SOURCES},
        context_tokens=32768,
        max_output_tokens=4096,
        max_outer_attempts=2,
        local_only=True,
        excluded_models=["local-glm-ocr"],
        audio_transport="Ollama 0.34.4 native messages[].images WAV; think=false; 16K reserve",
        human_review_required=True,
    )
    for key in ("config", "host", "database", "darwin"):
        manifest[key] = str(getattr(args, key).resolve())
        if key != "database":
            manifest[key + "_sha256"] = sha(getattr(args, key))
    save(campaign / "manifest.json", manifest, exclusive=True)
    save(campaign / "state.json", dict(status="prepared", at=now()))
    snapshot(campaign)
    print(
        json.dumps(
            dict(
                campaign=str(campaign),
                jobs=len(jobs),
                unavailable=unavailable,
                eligible_text_models=len(models),
            ),
            indent=2,
        )
    )


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("campaign", type=Path)
    p.add_argument("--init", action="store_true")
    p.add_argument("--poll", action="store_true")
    p.add_argument("--auto-only", action="store_true")
    for field in ("host", "config", "database", "darwin"):
        p.add_argument("--" + field, type=Path)
    a = p.parse_args()
    if a.init:
        if not all(getattr(a, k) for k in ("host", "config", "database", "darwin")):
            p.error("initialization needs host/config/database/darwin")
        initialize(a)
        return
    if a.poll:
        print(json.dumps(snapshot(a.campaign), indent=2))
        return
    try:
        run(a.campaign)
    except BlockingIOError:
        raise SystemExit("campaign lock held; no duplicate launch")
    except Exception as exc:
        save(
            a.campaign / "state.json",
            dict(status="blocked_for_investigation", at=now(), error=str(exc), pid=os.getpid()),
        )
        snapshot(a.campaign)
        raise


if __name__ == "__main__":
    main()
