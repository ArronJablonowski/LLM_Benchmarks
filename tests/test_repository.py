import ast
import importlib.util
import pathlib
import sys
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
DIRECT = ROOT / "scripts" / "ollama_standardized_local_benchmarks.py"
OPENCLAW = ROOT / "scripts" / "openclaw_18_test_benchmarks.py"
GENERATOR = ROOT / "dashboard" / "generate_local_llm_dashboard.py"
PLATFORM_SUPPORT = ROOT / "scripts" / "platform_support.py"
ACCURACY_GRADING = ROOT / "scripts" / "accuracy_grading.py"
THINKING_PAIR_SUPPORT = ROOT / "scripts" / "thinking_pair_support.py"
STANDARD_LOCAL_TASKS = ROOT / "scripts" / "standard_local_tasks.py"
HERMES = ROOT / "scripts" / "hermes_agent_17_test_benchmarks.py"
VISION_SUPPORT = ROOT / "scripts" / "vision_benchmark_support.py"
BENCHMARK_REGISTRY = ROOT / "scripts" / "benchmark_tests" / "registry.py"
CODING_RUNNER = ROOT / "scripts" / "coding_agent_benchmarks.py"
CODING_REPORT = ROOT / "dashboard" / "generate_coding_report.py"
OPENHANDS_CODING_AGENT = ROOT / "scripts" / "openhands_coding_agent.py"
CREATIVE_RUNNER = ROOT / "scripts" / "creative_agent_benchmarks.py"
CREATIVE_REPORT = ROOT / "dashboard" / "generate_creative_review.py"
CYBERSECURITY_RUNNER = ROOT / "scripts" / "cybersecurity_agent_benchmarks.py"
CYBERSECURITY_REPORT = ROOT / "dashboard" / "generate_cybersecurity_report.py"
CYBERSECURITY_GRADER = ROOT / "cyber_tasks" / "grader.py"
EXPLOITGYM_RUNNER = ROOT / "scripts" / "exploitgym_benchmarks.py"


def literal_assignment(path, name):
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == name:
                    return ast.literal_eval(node.value)
    raise AssertionError(f"{name} was not found in {path}")


def load_source_module(path, name):
    scripts = str(ROOT / "scripts")
    if scripts not in sys.path:
        sys.path.insert(0, scripts)
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class RepositoryTests(unittest.TestCase):
    def test_python_sources_compile(self):
        for path in (
            DIRECT, OPENCLAW, GENERATOR, PLATFORM_SUPPORT, ACCURACY_GRADING,
            THINKING_PAIR_SUPPORT, STANDARD_LOCAL_TASKS, HERMES, VISION_SUPPORT,
            BENCHMARK_REGISTRY, CODING_RUNNER, CODING_REPORT,
            OPENHANDS_CODING_AGENT, CREATIVE_RUNNER, CREATIVE_REPORT,
            CYBERSECURITY_RUNNER, CYBERSECURITY_REPORT, CYBERSECURITY_GRADER,
            EXPLOITGYM_RUNNER,
        ):
            compile(path.read_text(encoding="utf-8"), str(path), "exec")

    def test_direct_and_openclaw_task_sets_align(self):
        direct = load_source_module(DIRECT, "repository_direct").TASKS
        openclaw = load_source_module(OPENCLAW, "repository_openclaw").TASKS
        self.assertEqual(18, len(direct))
        self.assertEqual(18, len(openclaw))
        direct_by_id = {task["id"]: task for task in direct}
        openclaw_by_id = {task["id"]: task for task in openclaw}
        self.assertEqual(direct_by_id, openclaw_by_id)

    def test_sources_are_portable_and_public_safe(self):
        source = "\n".join(
            path.read_text(encoding="utf-8")
            for path in (
                DIRECT, OPENCLAW, GENERATOR, PLATFORM_SUPPORT,
                ACCURACY_GRADING, THINKING_PAIR_SUPPORT, STANDARD_LOCAL_TASKS,
                HERMES, VISION_SUPPORT, CODING_RUNNER, CODING_REPORT,
                OPENHANDS_CODING_AGENT, CREATIVE_RUNNER, CREATIVE_REPORT,
                CYBERSECURITY_RUNNER, CYBERSECURITY_REPORT,
                CYBERSECURITY_GRADER, EXPLOITGYM_RUNNER,
            )
        )
        self.assertNotIn("/Users/", source)
        self.assertIn("Path.home()", source)

    def test_paired_thinking_protocol_is_present_in_source(self):
        direct_source = DIRECT.read_text(encoding="utf-8")
        planner_source = THINKING_PAIR_SUPPORT.read_text(encoding="utf-8")
        self.assertIn("--thinking", direct_source)
        self.assertIn("'paired'", direct_source)
        self.assertIn("build_paired_plan", direct_source)
        self.assertIn("ordered_work_items", direct_source)
        for protocol_field in (
            "PAIR_SCHEMA_VERSION", "plan_sha256", "pair_id", "treatment_id",
            "row_id", "think_payload_json", "excluded_non_thinking",
        ):
            self.assertIn(protocol_field, planner_source)

    def test_real_benchmark_execution_requires_explicit_run_flag(self):
        direct_source = DIRECT.read_text(encoding="utf-8")
        openclaw_source = OPENCLAW.read_text(encoding="utf-8")
        self.assertIn("execution.add_argument('--run'", direct_source)
        self.assertIn("execution.add_argument('--run'", openclaw_source)
        self.assertIn("if args.dry_run or not args.run", direct_source)
        self.assertIn("if args.dry_run or not args.run", openclaw_source)

    def test_all_paths_expose_task_listing_and_selection(self):
        for path in (DIRECT, HERMES, OPENCLAW):
            source = path.read_text(encoding="utf-8")
            with self.subTest(path=path.name):
                self.assertIn("--list-tasks", source)
                self.assertIn("--test", source)

    def test_documentation_covers_both_supported_platforms(self):
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        self.assertIn("macOS", readme)
        self.assertIn("DGX Spark", readme)
        self.assertIn("nvidia-smi", readme)
        self.assertIn("--run", readme)

    def test_documentation_covers_paired_thinking_campaigns(self):
        paths = [
            ROOT / "README.md",
            ROOT / "docs" / "DGX_SPARK.md",
            ROOT / "docs" / "METHODOLOGY.md",
            ROOT / "docs" / "PROVENANCE.md",
        ]
        for path in paths:
            with self.subTest(path=path.name):
                self.assertIn("paired", path.read_text(encoding="utf-8").lower())
        self.assertIn("--thinking paired", paths[0].read_text(encoding="utf-8"))
        self.assertIn("--thinking paired", paths[1].read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
