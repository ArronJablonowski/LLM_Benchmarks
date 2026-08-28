import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from ollama_workspace_agent import WorkspaceTools


class OllamaWorkspaceAgentTests(unittest.TestCase):
    def test_file_tools_are_scoped_and_exact(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            agent = WorkspaceTools(root)
            self.assertEqual("wrote src/app.py", agent.call("write_file", {"path": "src/app.py", "content": "old\n"}))
            self.assertEqual("old\n", agent.call("read_file", {"path": "src/app.py"}))
            agent.call("replace_text", {"path": "src/app.py", "old_text": "old", "new_text": "new"})
            self.assertEqual("new\n", (root / "src/app.py").read_text())
            with self.assertRaisesRegex(ValueError, "escapes"):
                agent.call("read_file", {"path": "../outside"})

    def test_command_tool_has_no_shell_and_stays_in_workspace(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            agent = WorkspaceTools(root)
            output = agent.call("run_command", {"argv": ["python3", "-c", "print('ok')"]})
            self.assertIn('"exit_code": 0', output)
            self.assertIn("ok", output)
            with self.assertRaisesRegex(ValueError, "allowlist"):
                agent.call("run_command", {"argv": ["curl", "https://example.com"]})


if __name__ == "__main__":
    unittest.main()
