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


if __name__ == "__main__":
    unittest.main()
