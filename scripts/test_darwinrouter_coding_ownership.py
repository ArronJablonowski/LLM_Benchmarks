import unittest
from unittest.mock import patch
import darwinrouter_coding_benchmarks as bench


class OwnershipTests(unittest.TestCase):
    def test_only_explicit_unique_models_are_unloaded(self):
        with patch.object(bench, "stop_model") as stop:
            bench.stop_owned_models(["owned:a", "owned:a", "owned:b"])
        self.assertEqual([call.args[0] for call in stop.call_args_list], ["owned:a", "owned:b"])

    def test_no_ownership_means_no_unloads(self):
        with patch.object(bench, "stop_model") as stop:
            bench.stop_owned_models([])
        stop.assert_not_called()


class QualityClassificationTests(unittest.TestCase):
    def test_partial_checks_never_override_infrastructure_error(self):
        for grade in ("pass", "fail"):
            self.assertEqual(bench.quality_verdict(1, "context window exceeded", "", {"verdict": grade}), "infrastructure_error")
            self.assertEqual(bench.quality_verdict(0, "transport error", "", {"verdict": grade}), "infrastructure_error")

    def test_grader_errors_are_not_model_quality(self):
        self.assertEqual(bench.quality_verdict(0, "", "grader crashed", {"verdict": "fail"}), "grader_error")
        self.assertEqual(bench.quality_verdict(0, "", "", {}), "grader_error")

    def test_completed_grades_are_preserved(self):
        for grade in ("pass", "fail"):
            self.assertEqual(bench.quality_verdict(0, "", "", {"verdict": grade}), grade)


if __name__ == "__main__":
    unittest.main()
