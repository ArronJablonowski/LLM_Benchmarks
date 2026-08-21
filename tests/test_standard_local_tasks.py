import contextlib
import importlib.util
import io
import json
import pathlib
import shutil
import sys
import tempfile
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import standard_local_tasks as local_tasks


def load_direct():
    path = SCRIPTS / "ollama_standardized_local_benchmarks.py"
    spec = importlib.util.spec_from_file_location("standard_local_direct", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class StandardLocalTaskTests(unittest.TestCase):
    def test_full_official_snapshots_load_with_unique_ids(self):
        aime = local_tasks.load_aime_2026_tasks()
        gpqa = local_tasks.load_gpqa_diamond_tasks()
        combined = local_tasks.load_standard_local_tasks("standard-local")
        self.assertEqual(30, len(aime))
        self.assertEqual(198, len(gpqa))
        self.assertEqual(228, len(combined))
        self.assertEqual(228, len({task["id"] for task in combined}))
        self.assertEqual({"AIME 2026", "GPQA Diamond"}, {task["family"] for task in combined})

    def test_items_use_deterministic_local_graders(self):
        aime = local_tasks.load_aime_2026_tasks()
        gpqa = local_tasks.load_gpqa_diamond_tasks()
        self.assertTrue(all(task.get("final_answer_any") for task in aime))
        self.assertTrue(all(task.get("final_answer") in "ABCD" for task in gpqa))
        self.assertTrue(all("A. " in task["prompt"] and "D. " in task["prompt"] for task in gpqa))
        loader_source = (SCRIPTS / "standard_local_tasks.py").read_text(encoding="utf-8")
        self.assertNotIn("urllib", loader_source)
        self.assertNotIn("requests", loader_source)

    def test_integrity_failure_is_fail_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            copied = pathlib.Path(directory)
            for source in local_tasks.DATA_DIR.iterdir():
                if source.is_file():
                    shutil.copy2(source, copied / source.name)
            with (copied / "aime_2026.jsonl").open("a", encoding="utf-8") as handle:
                handle.write("{}\n")
            with self.assertRaisesRegex(local_tasks.StandardLocalDataError, "Integrity failure"):
                local_tasks.load_aime_2026_tasks(copied)

    def test_manifest_declares_offline_runtime_and_expected_licenses(self):
        manifest = json.loads((local_tasks.DATA_DIR / "manifest.json").read_text(encoding="utf-8"))
        self.assertFalse(manifest["runtime_network_required"])
        self.assertEqual("CC BY-NC-SA 4.0", manifest["benchmarks"]["aime2026"]["license"])
        self.assertEqual("CC BY 4.0", manifest["benchmarks"]["gpqa_diamond"]["license"])

    def test_list_tasks_is_local_and_profile_specific(self):
        direct = load_direct()
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            result = direct.main(["--task-profile", "aime2026", "--list-tasks"])
        self.assertEqual(0, result)
        lines = output.getvalue().splitlines()
        self.assertEqual(30, len(lines))
        self.assertTrue(lines[0].startswith("aime2026_"))

    def test_official_profile_rejects_paired_mode_before_network(self):
        direct = load_direct()
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            direct.main([
                "--task-profile", "standard-local", "--thinking", "paired",
                "--num-ctx", "65536", "--dry-run",
            ])


if __name__ == "__main__":
    unittest.main()
