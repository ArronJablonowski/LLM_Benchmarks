"""Frozen, offline routing-card fixtures and graders. No provider or feedback writes.

Callers supply trusted execution status separately from model output. These
assessment results are not SDK execution/capability/image-delivery evidence.
"""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
import shutil
import sqlite3
import subprocess
import unicodedata
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "data/routing_grid_v1"
VERSION = "routing-grid-v1"
CATEGORIES = (
    "research",
    "data_analysis",
    "reasoning",
    "workflow",
    "translation",
    "audio",
    "image_generation",
    "video_generation",
    "writing",
    "creative",
)
MAX_RESPONSE_BYTES = 65536


class ReviewError(ValueError):
    """Invalid evaluator evidence, never a model-quality failure."""


def digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def strict_json(text: str) -> Any:
    if len(text.encode("utf-8")) > MAX_RESPONSE_BYTES:
        raise ValueError("response exceeds 64 KiB")

    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("duplicate JSON key")
            result[key] = value
        return result

    def constant(value):
        raise ValueError("non-finite JSON number: " + value)

    try:
        return json.loads(text, object_pairs_hook=pairs, parse_constant=constant)
    except (json.JSONDecodeError, RecursionError) as exc:
        raise ValueError("expected one bare JSON value") from exc


def bounded_file(root: Path, relative: str, limit: int) -> Path:
    if not isinstance(relative, str) or not relative or Path(relative).is_absolute():
        raise ValueError("expected relative artifact path")
    path = root / relative
    if ".." in Path(relative).parts or any(
        (root / Path(*Path(relative).parts[:n])).is_symlink()
        for n in range(1, len(Path(relative).parts) + 1)
    ):
        # Resolve containment as well; refuse links instead of following mutable artifacts.
        raise ValueError("path traversal or symlink forbidden")
    resolved = path.resolve()
    if not resolved.is_relative_to(root.resolve()) or not resolved.is_file():
        raise ValueError("missing or outside-root artifact")
    if resolved.stat().st_size > limit:
        raise ValueError("artifact exceeds size limit")
    return resolved


def load_catalog(root: Path = FIXTURES) -> list[dict[str, Any]]:
    manifest = strict_json((root / "manifest.json").read_text())
    if manifest.get("schema_version") != 1 or manifest.get("suite_version") != VERSION:
        raise ValueError("unsupported routing-grid manifest")
    if manifest.get("grader_sha256") != digest(Path(__file__).read_bytes()):
        raise ValueError("routing-grid grading source hash mismatch; use a versioned pack")
    if manifest.get("categories") != list(CATEGORIES):
        raise ValueError("category inventory mismatch")
    records = manifest["tasks"]
    expected_ids = [f"grid_{c}_l{n}" for c in CATEGORIES for n in range(1, 7)]
    if [r["id"] for r in records] != expected_ids:
        raise ValueError("expected 60 unique tasks ordered by category and level")
    tasks = []
    for record in records:
        task_id = record["id"]
        values = []
        for directory, field in [("tasks", "task_sha256"), ("oracles", "oracle_sha256")]:
            path = bounded_file(root, f"{directory}/{task_id}.json", MAX_RESPONSE_BYTES)
            raw = path.read_bytes()
            if digest(raw) != record[field]:
                raise ValueError(f"{task_id}: {directory} hash mismatch")
            values.append(strict_json(raw.decode("utf-8")))
        t, oracle = values
        if (
            t["id"] != task_id
            or t["level"] != record["level"]
            or t["category"] != record["category"]
            or t["domain"] != t["category"]
            or t["evidence_profile"] != "benchmark-v1"
            or t["suite_version"] != VERSION
        ):
            raise ValueError("task/manifest scope mismatch")
        for asset in t["assets"]:
            path = bounded_file(root, asset["path"], 16 * 1024**2)
            if digest(path.read_bytes()) != asset["sha256"]:
                raise ValueError(f"{task_id}: asset hash mismatch")
        t["_oracle"] = oracle
        t["_task_sha256"] = record["task_sha256"]
        t["_oracle_sha256"] = record["oracle_sha256"]
        t["_root"] = str(root)
        tasks.append(t)
    return tasks


def model_payload(task: dict[str, Any]) -> dict[str, Any]:
    """Allowlist export: no evaluator oracle, source transcript, reference SQL or solution."""
    return copy.deepcopy(
        {
            key: task[key]
            for key in (
                "id",
                "domain",
                "evidence_profile",
                "level",
                "name",
                "challenge",
                "prompt",
                "inputs",
                "assets",
                "required_capabilities",
            )
        }
    )


def eligible(task: dict, advertised: list[str] | None) -> bool:
    """Conservative capability gate. Audio input != TTS; vision != generation.

    A runner must obtain this metadata freshly from the actual provider; a
    model name or an old fitness sample is not a capability advertisement.
    """
    if not advertised:
        return False
    caps = {str(c).lower() for c in advertised}
    aliases = {
        "audio_input": {"audio_input", "speech_to_text", "transcription"},
        "chat": {"chat", "text", "completion"},
    }
    return all(
        bool(caps & aliases.get(required, {required})) for required in task["required_capabilities"]
    )


def same_json(actual, expected):
    if type(actual) is not type(expected):
        return False
    if isinstance(expected, dict):
        return actual.keys() == expected.keys() and all(
            same_json(actual[k], v) for k, v in expected.items()
        )
    if isinstance(expected, list):
        return len(actual) == len(expected) and all(
            same_json(a, b) for a, b in zip(actual, expected)
        )
    if isinstance(expected, str):
        norm = lambda s: " ".join(unicodedata.normalize("NFC", s).split())
        return norm(actual) == norm(expected)
    return actual == expected


def query_rows(tables: dict, sql: str) -> list[list]:
    """Read-only, bounded SQLite with no external access or extension loading."""
    if not isinstance(sql, str) or not 1 <= len(sql) <= 12000:
        raise ValueError("SQL must be a string of 1..12000 characters")
    db = sqlite3.connect(":memory:")
    try:
        for name, data in tables.items():
            # Schema is trusted, frozen evaluator input, never supplied by a model.
            db.execute(f'CREATE TABLE {name} ({",".join(data["columns"])})')
            marks = ",".join("?" for _ in data["columns"])
            db.executemany(f"INSERT INTO {name} VALUES ({marks})", data["rows"])
        db.commit()
        db.execute("PRAGMA query_only=ON")
        if not hasattr(db, "setlimit"):
            raise RuntimeError("bounded SQL grading requires Python 3.11 or later")
        for limit, value in (
            (sqlite3.SQLITE_LIMIT_LENGTH, MAX_RESPONSE_BYTES),
            (sqlite3.SQLITE_LIMIT_SQL_LENGTH, 12000),
            (sqlite3.SQLITE_LIMIT_COLUMN, 100),
            (sqlite3.SQLITE_LIMIT_EXPR_DEPTH, 100),
            (sqlite3.SQLITE_LIMIT_COMPOUND_SELECT, 100),
            (sqlite3.SQLITE_LIMIT_VDBE_OP, 100000),
        ):
            db.setlimit(limit, value)
        allowed = {
            sqlite3.SQLITE_SELECT,
            sqlite3.SQLITE_READ,
            sqlite3.SQLITE_FUNCTION,
            sqlite3.SQLITE_RECURSIVE,
        }

        def authorize(action, arg1, arg2, database, trigger):
            if action not in allowed:
                return sqlite3.SQLITE_DENY
            if action == sqlite3.SQLITE_FUNCTION and (arg2 or "").lower() in {
                "load_extension",
                "readfile",
                "writefile",
            }:
                return sqlite3.SQLITE_DENY
            return sqlite3.SQLITE_OK

        budget = 0

        def progress():
            nonlocal budget
            budget += 1
            return int(budget > 1000)

        db.set_authorizer(authorize)
        db.set_progress_handler(progress, 1000)
        rows = db.execute(sql).fetchmany(201)
        if len(rows) > 200:
            raise ValueError("SQL row budget exceeded")
        if sum(len(str(cell)) for row in rows for cell in row) > MAX_RESPONSE_BYTES:
            raise ValueError("SQL output budget exceeded")
        return [list(row) for row in rows]
    except MemoryError as exc:
        raise ValueError("SQL virtual machine budget exceeded") from exc
    finally:
        db.close()


def sql_variants(tables):
    yield tables
    # Hidden metamorphic cases prevent answering with constants copied from the
    # visible data. Preserve schema, foreign keys and task semantics.
    for variant in (1, 2):
        altered = copy.deepcopy(tables)
        for name, table in altered.items():
            cols = [c.split()[0] for c in table["columns"]]
            for row in table["rows"]:
                if "cents" in cols:
                    i = cols.index("cents")
                    if row[i] is not None:
                        row[i] = row[i] * (variant + 1)
                if name == "customers":
                    row[cols.index("region")] = "zone-" + row[cols.index("region")]
        if variant == 2:
            if "orders" in altered:
                altered["orders"]["rows"].append([108, 4, "2025-01-04", 3100, "paid"])
            if "events" in altered:
                altered["events"]["rows"].extend(
                    [["e8", 108, 1, "paid"], ["e9", 102, 2, "refunded"]]
                )
        yield altered


def execute_workflow(level, calls):
    """Execute declared API calls in an offline simulator and retain the trace.

    This tests workflow semantics, not native SDK function-call generation.
    Invalid or surplus writes fail even if a later action repairs the state.
    """
    if not isinstance(calls, list) or not 1 <= len(calls) <= 24:
        raise ValueError("calls must contain 1..24 actions")
    state = dict(
        version=4 if level == 2 else 1,
        read_version=None,
        closed=False,
        pages=[],
        total=None,
        charges={},
        confirmed=False,
        retries=0,
        conflict=False,
        reserved=False,
        failed=False,
        recorded=False,
        created=False,
    )
    trace = []
    signatures = {
        "create_ticket": {"op", "id", "title"},
        "read_ticket": {"op", "id"},
        "close_ticket": {"op", "id", "version"},
        "list_items": {"op", "page"},
        "save_total": {"op", "total"},
        "charge": {"op", "cents", "key"},
        "confirm": {"op", "key"},
        "reserve": {"op", "item"},
        "pay": {"op", "cents", "key"},
        "release": {"op", "item"},
        "record_failure": {"op", "key"},
    }
    allowed = {
        1: {"create_ticket"},
        2: {"read_ticket", "close_ticket"},
        3: {"list_items", "save_total"},
        4: {"charge", "confirm"},
        5: {"read_ticket", "close_ticket"},
        6: {"reserve", "pay", "release", "record_failure"},
    }[level]
    for call in calls:
        if not isinstance(call, dict) or not isinstance(call.get("op"), str):
            raise ValueError("malformed call")
        op = call["op"]
        if op not in allowed or set(call) != signatures[op]:
            raise ValueError("unknown operation or arguments")
        for key, value in call.items():
            expected_type = int if key in {"version", "page", "total", "cents"} else str
            if type(value) is not expected_type:
                raise ValueError("wrong argument type")
        effect = "none"
        result = "ok"
        if op == "create_ticket":
            if call["id"] != "T1" or call["title"] != "Printer offline" or state["created"]:
                raise ValueError("wrong or duplicate ticket")
            state["created"] = True
            effect = "created"
        elif op == "read_ticket":
            if call["id"] != f"T{level}":
                raise ValueError("wrong ticket")
            state["read_version"] = state["version"]
            result = {"version": state["version"]}
        elif op == "close_ticket":
            if (
                call["id"] != f"T{level}"
                or state["closed"]
                or call["version"] != state["read_version"]
            ):
                raise ValueError("blind or duplicate close")
            if level == 5 and not state["conflict"]:
                state.update(conflict=True, version=2, read_version=None)
                result = "conflict"
            else:
                if call["version"] != state["version"]:
                    raise ValueError("stale version")
                state["closed"] = True
                effect = "closed"
        elif op == "list_items":
            if call["page"] not in (1, 2):
                raise ValueError("unknown page")
            state["pages"].append(call["page"])
            result = [12, 7] if call["page"] == 1 else [9, 4]
        elif op == "save_total":
            if set(state["pages"]) != {1, 2} or state["total"] is not None or call["total"] != 32:
                raise ValueError("incomplete read or wrong/duplicate total")
            state["total"] = call["total"]
            effect = "saved"
        elif op == "charge":
            if (
                call["key"] != "K4"
                or call["cents"] != 25
                or state["confirmed"]
                or state["retries"] >= 2
            ):
                raise ValueError("wrong or excess charge")
            state["retries"] += 1
            if not state["charges"]:
                state["charges"]["K4"] = 25
                effect = "charged"
                result = "timeout_after_effect"
            else:
                result = "deduplicated_receipt"
        elif op == "confirm":
            if call["key"] != "K4" or state["retries"] != 2 or state["confirmed"]:
                raise ValueError("unconfirmed or duplicate receipt")
            state["confirmed"] = True
            effect = "confirmed"
        elif op == "reserve":
            if call["item"] != "I6" or state["reserved"] or state["failed"]:
                raise ValueError("wrong or duplicate reservation")
            state["reserved"] = True
            effect = "reserved"
        elif op == "pay":
            if (
                call["cents"] != 40
                or call["key"] != "K6"
                or not state["reserved"]
                or state["failed"]
            ):
                raise ValueError("wrong or repeated payment")
            state["failed"] = True
            result = "payment_declined"
        elif op == "release":
            if call["item"] != "I6" or not state["reserved"] or not state["failed"]:
                raise ValueError("wrong or premature release")
            state["reserved"] = False
            effect = "released"
        elif op == "record_failure":
            if call["key"] != "K6" or not state["failed"] or state["reserved"] or state["recorded"]:
                raise ValueError("failure recorded before compensation or twice")
            state["recorded"] = True
            effect = "recorded"
        trace.append(dict(call=call, result=result, effect=effect))
    complete = {
        1: state["created"],
        2: state["closed"],
        3: state["total"] == 32,
        4: state["confirmed"] and sum(state["charges"].values()) == 25,
        5: state["closed"] and state["conflict"],
        6: state["recorded"] and not state["reserved"],
    }[level]
    if not complete:
        raise ValueError("desired final state not reached")
    return trace


def media_artifact(task, response, workspace):
    parsed = strict_json(response)
    if (
        not isinstance(parsed, dict)
        or set(parsed) != {"artifact"}
        or not isinstance(parsed["artifact"], str)
    ):
        raise ValueError("expected artifact filename object")
    if workspace is None:
        raise RuntimeError("artifact workspace required")
    oracle = task["_oracle"]
    spec = oracle["spec"]
    path = bounded_file(workspace, parsed["artifact"], 100 * 1024**2)
    if path.suffix.lower() != "." + spec["format"]:
        raise ValueError("wrong artifact format; text/SVG substitutes are not accepted")
    if oracle["media_kind"] == "image":
        try:
            from PIL import Image
        except ImportError as exc:
            raise RuntimeError("Pillow is required to decode generated images") from exc
        Image.MAX_IMAGE_PIXELS = 32_000_000
        try:
            with Image.open(path) as im:
                if im.format != "PNG" or getattr(im, "n_frames", 1) != 1:
                    raise ValueError("expected one static PNG image")
                width, height = im.size
                im.verify()
            with Image.open(path) as im:
                im.load()
        except (OSError, Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
            raise ValueError("image decode failed") from exc
    else:
        ffprobe, ffmpeg = shutil.which("ffprobe"), shutil.which("ffmpeg")
        if not ffprobe or not ffmpeg:
            raise RuntimeError("ffprobe and ffmpeg required to validate video")
        proc = subprocess.run(
            [
                ffprobe,
                "-v",
                "error",
                "-protocol_whitelist",
                "file",
                "-show_entries",
                "format=format_name,duration:stream=codec_type,width,height,avg_frame_rate",
                "-of",
                "json",
                str(path),
            ],
            capture_output=True,
            timeout=20,
        )
        if proc.returncode:
            raise ValueError("video probe failed")
        info = json.loads(proc.stdout)
        streams = [s for s in info.get("streams", []) if s.get("codec_type") == "video"]
        if len(streams) != 1 or "mp4" not in info["format"]["format_name"]:
            raise ValueError("expected one MP4 video stream")
        stream = streams[0]
        width, height = stream["width"], stream["height"]
        duration = float(info["format"]["duration"])
        a, b = stream["avg_frame_rate"].split("/")
        fps = float(a) / float(b) if float(b) else 0
        if not spec["min_seconds"] <= duration <= spec["max_seconds"] or fps < spec["min_fps"]:
            raise ValueError("duration or frame rate outside task limits")
        if width * height > 32_000_000:
            raise ValueError("video exceeds decoded frame size budget")
        decoded = subprocess.run(
            [
                ffmpeg,
                "-v",
                "error",
                "-xerror",
                "-nostdin",
                "-protocol_whitelist",
                "file",
                "-i",
                str(path),
                "-map",
                "0:v:0",
                "-f",
                "null",
                "-",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=30,
        )
        if decoded.returncode:
            raise ValueError("video frame decode failed")
    if width < spec["min_width"] or height < spec["min_height"]:
        raise ValueError("artifact resolution below task requirement")
    return {parsed["artifact"]: digest(path.read_bytes())}


def review_binding(task, response, artifacts=None):
    return dict(
        task_id=task["id"],
        suite_version=VERSION,
        task_sha256=task["_task_sha256"],
        oracle_sha256=task["_oracle_sha256"],
        response_sha256=digest(response.encode("utf-8")),
        artifacts=artifacts or {},
    )


def human_review(task, response, artifacts, review):
    binding = review_binding(task, response, artifacts)
    if review is None:
        return "needs_review", {
            "review_binding": binding,
            "dimensions": task["_oracle"]["dimensions"],
        }
    if not isinstance(review, dict):
        raise ReviewError("review must be an object")
    if review.get("binding") != binding:
        raise ReviewError("review is not bound to this exact submission and rubric")
    if not isinstance(review.get("reviewer"), str) or not review["reviewer"].strip():
        raise ReviewError("reviewer identity required")
    dimensions = task["_oracle"]["dimensions"]
    scores = review.get("scores", {})
    if not isinstance(scores, dict) or set(scores) != set(dimensions):
        raise ReviewError("review must cover every dimension")
    for rating in scores.values():
        if not isinstance(rating, dict) or set(rating) != {"score", "evidence"}:
            raise ReviewError("each rating needs score and evidence")
        if type(rating["score"]) is not int or not 0 <= rating["score"] <= 4:
            raise ReviewError("review scores must be integers 0..4")
        if not isinstance(rating["evidence"], str) or len(rating["evidence"].strip()) < 12:
            raise ReviewError("review needs specific supporting evidence")
    verdict = "pass" if all(r["score"] >= 3 for r in scores.values()) else "content_mismatch"
    return verdict, {"review": review}


def assess(task, response, *, status="completed", workspace=None, review=None):
    """Grade only completed output; no provider calls and no routing feedback writes.

    Infrastructure status and disqualification are supplied by the trusted
    caller, not read from the candidate response. Wrong execution evidence
    cannot be made valid by a passing saved artifact.
    """
    base = dict(
        task_id=task["id"],
        domain=task["domain"],
        evidence_profile=task["evidence_profile"],
        level=task["level"],
        grader_version=VERSION,
        feedback_written=False,
    )
    if status == "disqualified":
        return dict(base, verdict="disqualified", quality_eligible=False)
    if status != "completed":
        return dict(base, verdict="infrastructure", quality_eligible=False, error=status)
    oracle = task["_oracle"]
    try:
        if (
            not isinstance(response, str)
            or not response.strip()
            or len(response.encode("utf-8")) > MAX_RESPONSE_BYTES
        ):
            raise ValueError("empty or oversized response")
        kind = oracle["kind"]
        details = {}
        if kind == "exact_json":
            answer = strict_json(response)
            verdict = "pass" if same_json(answer, oracle["expected"]) else "content_mismatch"
        elif kind == "sql":
            answer = strict_json(response)
            if not isinstance(answer, dict) or set(answer) != {"sql"}:
                raise ValueError("expected only sql key")
            results = []
            for tables in sql_variants(task["inputs"]["tables"]):
                try:
                    expected = query_rows(tables, oracle["reference_sql"])
                except (ValueError, sqlite3.Error) as exc:
                    raise RuntimeError("reference SQL failed") from exc
                try:
                    actual = query_rows(tables, answer["sql"])
                except sqlite3.Error as exc:
                    raise ValueError("candidate SQL invalid or exceeds execution budget") from exc
                results.append(same_json(actual, expected))
            verdict = "pass" if all(results) else "content_mismatch"
            details = {"checks_passed": sum(results), "checks_total": len(results)}
        elif kind == "workflow":
            answer = strict_json(response)
            if not isinstance(answer, dict) or set(answer) != {"calls"}:
                raise ValueError("expected only calls key")
            details = {"simulation_trace": execute_workflow(task["level"], answer["calls"])}
            verdict = "pass"
        elif kind == "human_text":
            # Word limit contract is Unicode whitespace-separated tokens; no
            # bogus English tokenizer limits are imposed on CJK/Arabic translation.
            if task["category"] != "translation":
                words = len(response.split())
                lo, hi = oracle["word_range"]
                if not lo <= words <= hi:
                    raise ValueError(f"word count {words} outside {lo}..{hi}")
            if any(literal not in response for literal in oracle["protected"]):
                raise ValueError("missing protected literal")
            verdict, details = human_review(task, response, {}, review)
        elif kind == "human_media":
            artifacts = media_artifact(task, response, workspace)
            verdict, details = human_review(task, response, artifacts, review)
        else:
            raise RuntimeError("unknown grader")
        return dict(
            base,
            verdict=verdict,
            quality_eligible=verdict in {"pass", "content_mismatch"},
            **details,
        )
    except ReviewError as exc:
        return dict(base, verdict="grader_error", quality_eligible=False, error=str(exc))
    except (ValueError, sqlite3.Error) as exc:
        return dict(base, verdict="content_mismatch", quality_eligible=True, error=str(exc))
    except (
        RuntimeError,
        OSError,
        subprocess.TimeoutExpired,
        KeyError,
        TypeError,
        ZeroDivisionError,
    ) as exc:
        return dict(base, verdict="grader_error", quality_eligible=False, error=str(exc))


def summary(results):
    """Never silently mix repeats or fold unfinished reviews into accuracy."""
    ids = [r["task_id"] for r in results]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate task results; summarize one model/run at a time")
    totals = {}
    for result in results:
        if result.get("grader_version") != VERSION:
            raise ValueError("mixed grader versions")
        key = (result["domain"], result["evidence_profile"], result["level"])
        row = totals.setdefault(key, {})
        verdict = result["verdict"]
        row[verdict] = row.get(verdict, 0) + 1
    return [
        dict(domain=d, evidence_profile=p, level=n, counts=counts)
        for (d, p, n), counts in sorted(totals.items())
    ]
