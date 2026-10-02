"""Deterministic image-only OCR grading; no judge model or fuzzy pass threshold."""
from __future__ import annotations

import json
import re
import unicodedata

OCR_PROFILE = "ocr-progressive-v1"
MAX_RESPONSE_CHARS = 32000


def normalize(value: str) -> str:
    # Preserve case, punctuation, signs, decimal points and digit/letter distinctions.
    return re.sub(r"\s+", " ", unicodedata.normalize("NFC", value)).strip()


def distance(expected, actual):
    """Levenshtein edit count, bounded by the response limit at the entry point."""
    if len(expected) > len(actual):
        expected, actual = actual, expected
    previous = list(range(len(expected) + 1))
    for index, item in enumerate(actual, 1):
        current = [index]
        for column, reference in enumerate(expected, 1):
            current.append(min(current[-1] + 1, previous[column] + 1,
                               previous[column - 1] + (reference != item)))
        previous = current
    return previous[-1]


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def grade_ocr(task: dict, text: str) -> dict:
    spec = task["ocr_grader"]
    expected = spec["expected"]
    result = {
        "verdict": "content_mismatch", "grader_type": "ocr_fields",
        "grader_version": OCR_PROFILE, "tests_passed": 0,
        "tests_total": len(expected), "error": "", "failures": [],
        "difficulty_level": task["difficulty_level"],
        "field_accuracy": 0.0, "transcription_metrics": {},
    }
    try:
        if len(text) > MAX_RESPONSE_CHARS:
            raise ValueError("response exceeds OCR grading limit")
        obj = json.loads(text, object_pairs_hook=_object)
        if not isinstance(obj, dict) or set(obj) != set(expected):
            raise ValueError("response must be one JSON object with exactly the requested keys")
        if any(not isinstance(value, str) for value in obj.values()):
            raise ValueError("all answer values must be strings")
        # Cap per-field comparison complexity; the longest reference is under 2K.
        if any(len(value) > 4000 for value in obj.values()):
            raise ValueError("answer field exceeds OCR grading limit")
    except (ValueError, TypeError, RecursionError) as exc:
        result["error"] = str(exc)
        result["failures"] = ["response_schema"]
        return result
    for field, value in expected.items():
        reference, answer = normalize(value), normalize(obj[field])
        if reference == answer:
            result["tests_passed"] += 1
        else:
            result["failures"].append(field)
        if field in spec.get("transcription_fields", []):
            chars = distance(reference, answer)
            words = distance(reference.split(), answer.split())
            result["transcription_metrics"][field] = {
                "character_edits": chars, "reference_characters": len(reference),
                "cer": chars / max(1, len(reference)),
                "word_edits": words, "reference_words": len(reference.split()),
                "wer": words / max(1, len(reference.split())),
            }
    result["field_accuracy"] = result["tests_passed"] / len(expected)
    if not result["failures"]:
        result["verdict"] = "pass"
    else:
        result["error"] = "incorrect fields: " + ", ".join(result["failures"])
    return result
