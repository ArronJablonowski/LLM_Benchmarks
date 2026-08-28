from __future__ import annotations

import ast
import json
import sqlite3
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from grader_support import Checks, python_files_parse, run_project_tests


def main(workspace: Path) -> int:
    checks = Checks(); sys.path.insert(0, str(workspace))

    def lifecycle():
        from job_service import Store
        with tempfile.TemporaryDirectory() as directory:
            store = Store(Path(directory) / "jobs.db")
            first = store.create("key-1", {"nested": {"x": 1}})
            assert store.create("key-1", {"different": True})["id"] == first["id"]
            assert store.get(first["id"])["payload"] == {"nested": {"x": 1}}
            assert store.start(first["id"])["state"] == "running"
            due_at = datetime.now(timezone.utc) - timedelta(seconds=1)
            assert store.fail(first["id"], retry_at=due_at)["state"] == "failed"
            assert first["id"] in {job["id"] for job in store.due(datetime.now(timezone.utc))}
            assert store.retry(first["id"])["state"] == "queued"
            try: store.succeed(first["id"])
            except (ValueError, RuntimeError): pass
            else: raise AssertionError("invalid queued→succeeded transition accepted")

    checks.call("persistent lifecycle and idempotency", lifecycle)

    def sql_safety():
        sources = "\n".join(path.read_text(encoding="utf-8") for path in workspace.rglob("*.py"))
        tree = ast.parse(sources)
        suspicious = [node for node in ast.walk(tree) if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in {"execute", "executemany"} and node.args and isinstance(node.args[0], ast.JoinedStr)]
        assert not suspicious

    checks.call("no f-string SQL", sql_safety)
    ok, detail = run_project_tests(workspace); checks.check("project tests pass", ok, detail)
    checks.check("project contains package, CLI, and tests", (workspace / "job_service" / "__init__.py").is_file() and (workspace / "job_service" / "__main__.py").is_file() and (workspace / "tests").is_dir())
    proc = subprocess.run([sys.executable, "-m", "job_service", "--help"], cwd=workspace, text=True, capture_output=True, timeout=20)
    checks.check("CLI is runnable", proc.returncode == 0, proc.stderr[-500:])
    ok, detail = python_files_parse(workspace); checks.check("all Python parses", ok, detail)
    return checks.emit()


if __name__ == "__main__":
    raise SystemExit(main(Path(sys.argv[1]).resolve()))
