"""Tests for shared, environment-derived benchmark defaults."""
from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from benchmark_settings import BenchmarkSettings  # noqa: E402


class BenchmarkSettingsTests(unittest.TestCase):
    def test_defaults_use_hermes_reports_under_home(self):
        with patch.dict(os.environ, {"LLM_BENCHMARK_HOME": "/tmp/benchmark-home"}, clear=True):
            settings = BenchmarkSettings.from_environment()
        self.assertEqual(Path("/tmp/benchmark-home"), settings.home)
        self.assertEqual(Path("/tmp/benchmark-home/.hermes/reports"), settings.reports_root)
        self.assertEqual("http://127.0.0.1:11434", settings.ollama_url)

    def test_environment_overrides_are_resolved_once(self):
        with patch.dict(os.environ, {
            "LLM_BENCHMARK_HOME": "/tmp/benchmark-home",
            "LLM_BENCHMARK_REPORTS_ROOT": "/tmp/custom-reports",
            "LLM_BENCHMARK_OLLAMA_URL": "http://ollama.example:11434/",
        }, clear=True):
            settings = BenchmarkSettings.from_environment()
        self.assertEqual(Path("/tmp/custom-reports"), settings.reports_root)
        self.assertEqual("http://ollama.example:11434", settings.ollama_url)
        self.assertEqual(Path("/tmp/custom-reports/direct"), settings.report_dir("direct"))

    def test_rejects_nested_report_directory_name(self):
        settings = BenchmarkSettings.from_environment()
        with self.assertRaises(ValueError):
            settings.report_dir("nested/name")
