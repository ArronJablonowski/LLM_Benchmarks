"""Small, dependency-free safeguards for text persisted in benchmark evidence.

Benchmark runners must retain enough command output to diagnose a failed
provider call.  That output can also contain credentials echoed by a CLI or
gateway.  These helpers redact common credential forms at the report boundary
without changing the in-memory response used for grading, timeout detection,
or recovery decisions.
"""
from __future__ import annotations

import re
from typing import Any


REDACTED = "[REDACTED]"

# Deliberately target credential-shaped values instead of broad words such as
# "token", which occur frequently in ordinary benchmark responses and metrics.
_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    # HTTP logs: Authorization: Bearer <credential> (or Bearer <credential>).
    (re.compile(r"(?i)(\b(?:authorization\s*:\s*)?bearer\s+)[A-Za-z0-9._~+/=-]{8,}"), r"\1" + REDACTED),
    # JSON, CLI, and env forms such as OPENAI_API_KEY=..., "password": "...".
    (
        re.compile(
            r"(?i)(\b(?:api[_-]?key|access[_-]?token|refresh[_-]?token|client[_-]?secret|"
            r"password|secret[_-]?key)\b[\"']?\s*(?:[:=]\s*)[\"']?)[^\s\",'}]{6,}"
        ),
        r"\1" + REDACTED,
    ),
    # Provider keys that may be printed without a surrounding key name.
    (re.compile(r"\bsk-[A-Za-z0-9_-]{16,}\b"), REDACTED),
    (re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9_]{20,}|github_pat_[A-Za-z0-9_]{20,})\b"), REDACTED),
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), REDACTED),
)


def redact_sensitive_text(value: Any) -> str:
    """Return text with common credential values replaced by ``[REDACTED]``.

    ``subprocess`` timeout payloads may be bytes, so accepting arbitrary input
    keeps report writers robust while ensuring serialized report fields remain
    strings.  The function is intentionally idempotent.
    """
    if value is None:
        text = ""
    elif isinstance(value, bytes):
        text = value.decode("utf-8", "replace")
    else:
        text = str(value)
    for pattern, replacement in _PATTERNS:
        text = pattern.sub(replacement, text)
    return text
