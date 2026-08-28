from __future__ import annotations

import copy
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from grader_support import Checks, has_student_tests, import_path, python_files_parse, run_project_tests


def main(workspace: Path) -> int:
    checks = Checks()
    module = import_path(workspace / "layered_config.py", "graded_layered_config")

    def behavior():
        base = {"db": {"host": "a", "opts": {"ssl": True}}, "tags": ["old"], "keep": 1}
        overlay = {"db": {"host": "b", "opts": {"timeout": 5}}, "tags": ["new"], "keep": None}
        before_base, before_overlay = copy.deepcopy(base), copy.deepcopy(overlay)
        got = module.merge(base, overlay)
        assert got == {"db": {"host": "b", "opts": {"ssl": True, "timeout": 5}}, "tags": ["new"], "keep": None}
        assert base == before_base and overlay == before_overlay
        got["db"]["opts"]["timeout"] = 99
        assert overlay == before_overlay

    checks.call("hidden recursive behavior and immutability", behavior)
    ok, detail = run_project_tests(workspace)
    checks.check("project tests pass", ok, detail)
    checks.check("student added regression tests", has_student_tests(workspace) and len(list((workspace / "tests").glob("test*.py"))) >= 2)
    ok, detail = python_files_parse(workspace)
    checks.check("all Python parses", ok, detail)
    return checks.emit()


if __name__ == "__main__":
    raise SystemExit(main(Path(sys.argv[1]).resolve()))
