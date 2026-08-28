"""Helpers used only by hidden coding-suite graders."""
from __future__ import annotations

import ast
import importlib.util
import json
import subprocess
import sys
import traceback
from pathlib import Path


class Checks:
    def __init__(self) -> None:
        self.details: list[dict[str, object]] = []

    def check(self, name: str, condition: object, detail: str = "") -> None:
        self.details.append({"name": name, "passed": bool(condition), "detail": detail})

    def call(self, name: str, function) -> None:
        try:
            function()
        except Exception as exc:  # graders must preserve useful failure evidence
            self.check(name, False, f"{type(exc).__name__}: {exc}")
        else:
            self.check(name, True)

    def emit(self) -> int:
        passed = sum(1 for item in self.details if item["passed"])
        payload = {
            "passed": passed,
            "total": len(self.details),
            "verdict": "pass" if passed == len(self.details) else "fail",
            "details": self.details,
        }
        print(json.dumps(payload, sort_keys=True))
        return 0


def import_path(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ImportError(path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def run_project_tests(workspace: Path) -> tuple[bool, str]:
    proc = subprocess.run(
        [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v"],
        cwd=workspace, text=True, capture_output=True, timeout=120, check=False,
    )
    output = (proc.stdout + "\n" + proc.stderr).strip()
    return proc.returncode == 0, output[-2000:]


def python_files_parse(workspace: Path) -> tuple[bool, str]:
    try:
        for path in workspace.rglob("*.py"):
            if any(part.startswith(".") for part in path.relative_to(workspace).parts):
                continue
            ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except Exception:
        return False, traceback.format_exc(limit=1)
    return True, ""


def has_student_tests(workspace: Path) -> bool:
    tests = workspace / "tests"
    return tests.is_dir() and any(path.name.startswith("test") for path in tests.rglob("*.py"))
