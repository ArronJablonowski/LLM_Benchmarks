#!/usr/bin/env python3
"""Grade offline GitHub CLI use against the frozen task descriptor."""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "coding_tasks"))
from grader_support import Checks


def subsequence(required: list[str], actual: list[str]) -> bool:
    iterator = iter(actual)
    return all(any(item == candidate for candidate in iterator) for item in required)


def main(workspace: Path) -> int:
    checks = Checks()
    task_id = (workspace / ".benchmark-task-id").read_text(encoding="utf-8").strip()
    descriptor = json.loads((
        Path(__file__).resolve().parents[1]
        / "scripts" / "benchmark_tests" / "github" / f"{task_id}.json"
    ).read_text(encoding="utf-8"))
    grading = descriptor["grading"]
    required_commands = grading["required_commands"]
    required_findings = grading["required_findings"]
    try:
        answer = json.loads((workspace / "answer.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        answer = {}
        checks.check("valid answer.json", False, str(exc))
    else:
        checks.check("valid answer.json", isinstance(answer, dict))
    commands = answer.get("commands", []) if isinstance(answer, dict) else []
    findings = answer.get("findings", []) if isinstance(answer, dict) else []
    actions = answer.get("actions", []) if isinstance(answer, dict) else []
    checks.check("ordered command sequence", isinstance(commands, list) and subsequence(required_commands, commands))
    checks.check("safe action summary", isinstance(actions, list) and bool(actions))
    evidence = " ".join(map(str, findings)).lower() if isinstance(findings, list) else ""
    checks.check("evidence-backed findings", all(token.lower() in evidence for token in required_findings))
    events = []
    transcript = workspace / "transcript.jsonl"
    if transcript.is_file():
        for line in transcript.read_text(encoding="utf-8").splitlines():
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    successful = [event.get("command", "") for event in events if event.get("ok")]
    checks.check("commands exercised in offline simulator", subsequence(required_commands, successful))
    observed = " ".join(str(event.get("output", "")) for event in events if event.get("ok")).lower()
    checks.check("findings appeared in simulator output", all(token.lower() in observed for token in required_findings))
    checks.check("only fixture commands succeeded", all(command in descriptor["lab"]["commands"] for command in successful))
    return checks.emit()


if __name__ == "__main__":
    raise SystemExit(main(Path(sys.argv[1]).resolve()))
