from __future__ import annotations

import hashlib
import os
import stat
import sys
import tarfile
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from grader_support import Checks, python_files_parse, run_project_tests


def digest(path): return hashlib.sha256(path.read_bytes()).hexdigest()


def main(workspace: Path) -> int:
    checks = Checks(); sys.path.insert(0, str(workspace))

    def reproducible_and_safe():
        from packsmith import build_archive
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); source = root / "src"; source.mkdir()
            script = source / "run.sh"; script.write_text("#!/bin/sh\necho ok\n", encoding="utf-8"); script.chmod(0o755)
            (source / "data.txt").write_text("hello\n", encoding="utf-8")
            one, two = root / "one.tar.gz", root / "two.tar.gz"
            build_archive(source, one); os.utime(script, None); build_archive(source, two)
            assert digest(one) == digest(two)
            with tarfile.open(one) as archive:
                names = archive.getnames(); assert all(not name.startswith("/") and ".." not in Path(name).parts for name in names)
                member = next(item for item in archive.getmembers() if item.name.endswith("run.sh")); assert member.mode & stat.S_IXUSR
            marker = root / "pwned"
            try: build_archive(source, root / "three.tar.gz", ["printf", "x;touch", str(marker)])
            except Exception: pass
            assert not marker.exists()

    checks.call("reproducible secure archive", reproducible_and_safe)
    source = "\n".join(path.read_text(encoding="utf-8") for path in (workspace / "packsmith").glob("*.py"))
    checks.check("shell execution disabled", "shell=True" not in source)
    checks.check("usage documentation updated", "dry-run" in (workspace / "README.md").read_text(encoding="utf-8").lower() and "verify" in (workspace / "README.md").read_text(encoding="utf-8").lower())
    ok, detail = run_project_tests(workspace); checks.check("project tests pass", ok, detail)
    checks.check("student added regression tests", len(list((workspace / "tests").glob("test*.py"))) >= 2)
    ok, detail = python_files_parse(workspace); checks.check("all Python parses", ok, detail)
    return checks.emit()


if __name__ == "__main__":
    raise SystemExit(main(Path(sys.argv[1]).resolve()))
