"""Resolve benchmark runtime defaults without mutating campaign provenance.

The runners keep their command-line options as the final authority.  This module
only provides a single, documented place for their environment-derived defaults.
Every runner must continue to persist its resolved values in its plan/report
artifacts so that a later environment change cannot silently alter a resume.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


DEFAULT_OLLAMA_URL = "http://127.0.0.1:11434"


def _path_from_env(name: str, default: Path) -> Path:
    value = os.environ.get(name)
    return Path(value).expanduser() if value else default


@dataclass(frozen=True)
class BenchmarkSettings:
    """Environment-derived defaults shared by local benchmark entry points."""

    home: Path
    reports_root: Path
    ollama_url: str

    @classmethod
    def from_environment(cls) -> "BenchmarkSettings":
        home = _path_from_env("LLM_BENCHMARK_HOME", Path.home())
        reports_root = _path_from_env("LLM_BENCHMARK_REPORTS_ROOT", home / ".hermes" / "reports")
        ollama_url = os.environ.get("LLM_BENCHMARK_OLLAMA_URL", DEFAULT_OLLAMA_URL).rstrip("/")
        if not ollama_url:
            raise ValueError("LLM_BENCHMARK_OLLAMA_URL must not be empty")
        return cls(home=home, reports_root=reports_root, ollama_url=ollama_url)

    def report_dir(self, name: str) -> Path:
        if not name or Path(name).name != name:
            raise ValueError("report directory name must be a single path component")
        return self.reports_root / name


SETTINGS = BenchmarkSettings.from_environment()
