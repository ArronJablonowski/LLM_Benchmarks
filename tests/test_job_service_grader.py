import importlib.util
import unittest
from pathlib import Path
spec = importlib.util.spec_from_file_location("job_grader", Path(__file__).resolve().parents[1] / "coding_tasks/featurebench_job_service/grader.py")
g = importlib.util.module_from_spec(spec)
spec.loader.exec_module(g)
class CustomTransitionError(Exception): pass
class Store:
    def __init__(self, mode): self.mode, self.state = mode, "queued"
    def get(self, job_id): return {"id":job_id, "state":self.state}
    def succeed(self, job_id):
        if self.mode != "reject": self.state = "succeeded"
        if self.mode != "accept": raise CustomTransitionError("invalid transition")
class GraderTests(unittest.TestCase):
    def test_custom_rejection(self): g.assert_invalid_transition(Store("reject"), 1)
    def test_invalid_acceptance(self):
        with self.assertRaises(AssertionError): g.assert_invalid_transition(Store("accept"), 1)
    def test_mutation_before_rejection(self):
        with self.assertRaises(AssertionError): g.assert_invalid_transition(Store("mutate"), 1)
if __name__ == "__main__": unittest.main()
