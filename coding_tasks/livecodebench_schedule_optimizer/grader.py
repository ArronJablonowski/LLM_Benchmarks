from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from grader_support import Checks, import_path, python_files_parse, run_project_tests


def main(workspace: Path) -> int:
    checks = Checks()
    module = import_path(workspace / "scheduler" / "optimizer.py", "graded_optimizer")

    def correctness():
        jobs = [
            {"id": "z", "start": 0, "end": 5, "value": 9},
            {"id": "a", "start": 0, "end": 2, "value": 5},
            {"id": "b", "start": 2, "end": 5, "value": 5},
            {"id": "c", "start": 5, "end": 6, "value": 1},
        ]
        assert module.optimize(jobs) == {"jobs": ["a", "b", "c"], "total_value": 11}
        tie = [{"id": "b", "start": 0, "end": 1, "value": 2}, {"id": "a", "start": 0, "end": 1, "value": 2}]
        assert module.optimize(tie) == {"jobs": ["a"], "total_value": 2}

    checks.call("hidden correctness and tie breaking", correctness)

    def validation():
        for bad in ([{"id": "x", "start": 2, "end": 1, "value": 3}], [{"id": "x", "start": 1, "end": 1, "value": 3}], [{"start": 0, "end": 1, "value": 1}]):
            try:
                module.optimize(bad)
            except (TypeError, ValueError, KeyError):
                continue
            raise AssertionError(f"accepted invalid input {bad}")

    checks.call("input validation", validation)

    def scale():
        jobs = [{"id": f"j{i:05d}", "start": i, "end": i + 1, "value": 1} for i in range(10_000)]
        started = time.monotonic(); result = module.optimize(jobs); elapsed = time.monotonic() - started
        assert result["total_value"] == 10_000 and len(result["jobs"]) == 10_000
        assert elapsed < 5, elapsed

    checks.call("10k-job efficiency", scale)
    proc = subprocess.run([sys.executable, "-m", "scheduler"], cwd=workspace, input='[{"id":"x","start":0,"end":1,"value":7}]', text=True, capture_output=True, timeout=20)
    checks.check("CLI emits compact JSON", proc.returncode == 0 and proc.stdout.strip() == '{"jobs":["x"],"total_value":7}', proc.stderr[-500:])
    ok, detail = run_project_tests(workspace); checks.check("project tests pass", ok, detail)
    ok, detail = python_files_parse(workspace); checks.check("all Python parses", ok, detail)
    return checks.emit()


if __name__ == "__main__":
    raise SystemExit(main(Path(sys.argv[1]).resolve()))
