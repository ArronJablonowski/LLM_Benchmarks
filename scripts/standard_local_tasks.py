#!/usr/bin/env python3
"""Load frozen official benchmark snapshots using only the Python standard library."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path


STANDARD_LOCAL_PROFILE = "standard-local-official-v1"
DATA_DIR = Path(__file__).resolve().parents[1] / "data" / "standard_local"
PROFILE_CHOICES = ("core", "standard-local", "aime2026", "gpqa-diamond")


class StandardLocalDataError(RuntimeError):
    """Raised when a vendored benchmark snapshot fails integrity validation."""


def _manifest(data_dir: Path) -> dict:
    path = data_dir / "manifest.json"
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise StandardLocalDataError(f"Cannot read benchmark manifest {path}: {exc}") from exc
    if manifest.get("schema_version") != 1:
        raise StandardLocalDataError("Unsupported standard-local manifest schema")
    return manifest


def _load_jsonl(data_dir: Path, key: str) -> list[dict]:
    manifest = _manifest(data_dir)
    try:
        spec = manifest["benchmarks"][key]
    except KeyError as exc:
        raise StandardLocalDataError(f"Manifest is missing benchmark {key}") from exc
    path = data_dir / spec["file"]
    try:
        payload = path.read_bytes()
    except OSError as exc:
        raise StandardLocalDataError(f"Cannot read benchmark snapshot {path}: {exc}") from exc
    actual = hashlib.sha256(payload).hexdigest()
    if actual != spec["sha256"]:
        raise StandardLocalDataError(
            f"Integrity failure for {path.name}: expected {spec['sha256']}, got {actual}"
        )
    try:
        # Split only on the JSONL record delimiter. Some scientific text uses
        # Unicode line-separator code points that str.splitlines() would
        # incorrectly treat as record boundaries inside a JSON string.
        rows = [json.loads(line) for line in payload.decode("utf-8").split("\n") if line]
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise StandardLocalDataError(f"Invalid JSONL snapshot {path}: {exc}") from exc
    if len(rows) != spec["item_count"]:
        raise StandardLocalDataError(
            f"Item-count failure for {path.name}: expected {spec['item_count']}, got {len(rows)}"
        )
    return rows


def load_aime_2026_tasks(data_dir: Path = DATA_DIR) -> list[dict]:
    tasks = []
    for row in _load_jsonl(data_dir, "aime2026"):
        index = int(row["problem_idx"])
        answer = int(row["answer"])
        tasks.append({
            "id": f"aime2026_{index:03d}",
            "family": "AIME 2026",
            "category": "math_reasoning",
            "name": f"AIME 2026 problem {index}",
            "prompt": (
                "AIME 2026. Solve the following problem without external tools. "
                "End with FINAL: <integer>.\n\n" + str(row["problem"])
            ),
            # Official AIME answers are integers in [000, 999]. Accept the
            # ordinary integer spelling and the contest's three-digit spelling.
            "final_answer_any": [str(answer), f"{answer:03d}"],
            "source_benchmark": "AIME 2026",
            "source_item_id": str(index),
            "source_license": "CC BY-NC-SA 4.0",
        })
    return tasks


def load_gpqa_diamond_tasks(data_dir: Path = DATA_DIR) -> list[dict]:
    tasks = []
    for row in _load_jsonl(data_dir, "gpqa_diamond"):
        ordinal = int(row["ordinal"])
        choices = list(row["choices"])
        if len(choices) != 4 or str(row["answer"]) not in "ABCD":
            raise StandardLocalDataError(f"Invalid GPQA item at ordinal {ordinal}")
        rendered = "\n".join(f"{letter}. {choice}" for letter, choice in zip("ABCD", choices))
        tasks.append({
            "id": f"gpqa_diamond_{ordinal:03d}",
            "family": "GPQA Diamond",
            "category": "expert_knowledge",
            "name": f"GPQA Diamond question {ordinal}",
            "prompt": (
                "GPQA Diamond. Select the single best answer. End with FINAL: <letter>.\n\n"
                + str(row["question"]) + "\n\n" + rendered
            ),
            "final_answer": str(row["answer"]),
            "source_benchmark": "GPQA Diamond",
            "source_item_id": str(row.get("record_id") or ordinal),
            "source_license": "CC BY 4.0",
        })
    return tasks


def load_standard_local_tasks(profile: str, data_dir: Path = DATA_DIR) -> list[dict]:
    if profile == "aime2026":
        return load_aime_2026_tasks(data_dir)
    if profile == "gpqa-diamond":
        return load_gpqa_diamond_tasks(data_dir)
    if profile == "standard-local":
        return load_aime_2026_tasks(data_dir) + load_gpqa_diamond_tasks(data_dir)
    if profile == "core":
        return []
    raise ValueError(f"Unknown task profile: {profile}")
