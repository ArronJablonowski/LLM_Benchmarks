from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from grader_support import Checks, import_path, python_files_parse, run_project_tests


class GuardedRecords:
    def __init__(self, rows): self.rows, self.started = rows, False
    def __iter__(self):
        if self.started: raise AssertionError("iterated twice")
        self.started = True
        yield from self.rows


def main(workspace: Path) -> int:
    checks = Checks()
    module = import_path(workspace / "streamlog" / "pipeline.py", "graded_pipeline")

    def behavior():
        source = GuardedRecords([
            '{"timestamp":"2026-01-02T03:04:05-05:00","service":"api","severity":"ERROR","details":{"token":"abc","n":1}}',
            '2026-01-02T08:04:06Z worker INFO ready',
            '{broken',
        ])
        normalized, summary, errors = module.process(source, {"token"})
        assert not source.started, "input consumed eagerly"
        rows = list(normalized)
        assert len(rows) == 2 and len(errors) == 1
        first = json.loads(rows[0]) if isinstance(rows[0], str) else rows[0]
        assert first["timestamp"] == "2026-01-02T08:04:05Z"
        assert first["details"]["token"] == "[REDACTED]" and first["details"]["n"] == 1
        assert summary == {"api": {"ERROR": 1}, "worker": {"INFO": 1}}
        if all(isinstance(row, str) for row in rows):
            assert rows == sorted(rows, key=lambda row: json.loads(row)["timestamp"])

    checks.call("streaming parse, normalize, redact, aggregate", behavior)
    source = (workspace / "streamlog" / "pipeline.py").read_text(encoding="utf-8")
    checks.check("does not materialize input with list(records)", "list(records)" not in source.replace(" ", ""))
    ok, detail = run_project_tests(workspace); checks.check("project tests pass", ok, detail)
    checks.check("student added tests", len(list((workspace / "tests").glob("test*.py"))) >= 2)
    ok, detail = python_files_parse(workspace); checks.check("all Python parses", ok, detail)
    return checks.emit()


if __name__ == "__main__":
    raise SystemExit(main(Path(sys.argv[1]).resolve()))
