from __future__ import annotations

import ast
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from grader_support import Checks, import_path, python_files_parse, run_project_tests


def main(workspace: Path) -> int:
    checks = Checks()
    sys.path.insert(0, str(workspace))

    def behavior():
        from eventer.routing import send_event
        assert send_event({"z": 2, "a": 1}) == 'OUT:{"a":1,"z":2}'
        assert send_event({"ok": True}, "audit") == 'AUDIT:{"ok":true}'
        try:
            send_event({}, "missing")
        except (KeyError, ValueError) as exc:
            assert "missing" in str(exc)
        else:
            raise AssertionError("unknown channel accepted")

    checks.call("public behavior", behavior)
    imports = {}
    for path in (workspace / "eventer").glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        imports[path.stem] = {
            alias.name.lstrip(".").split(".")[0]
            for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)
            for alias in node.names
        }
    checks.check("formatting and delivery no longer import each other", not ({"delivery"} <= imports.get("formatting", set()) and {"formatting"} <= imports.get("delivery", set())))
    source = "\n".join(path.read_text(encoding="utf-8") for path in (workspace / "eventer").glob("*.py"))
    checks.check("typed delivery abstraction present", "Protocol" in source or "ABC" in source or "Callable" in source)
    ok, detail = run_project_tests(workspace)
    checks.check("project tests pass", ok, detail)
    ok, detail = python_files_parse(workspace)
    checks.check("all Python parses", ok, detail)
    return checks.emit()


if __name__ == "__main__":
    raise SystemExit(main(Path(sys.argv[1]).resolve()))
