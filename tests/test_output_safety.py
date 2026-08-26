import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from output_safety import REDACTED, redact_sensitive_text


class OutputSafetyTests(unittest.TestCase):
    def test_redacts_common_credentials_and_preserves_diagnostics(self):
        value = (
            "gateway failed; Authorization: Bearer abcdefghijklmnop; "
            "OPENAI_API_KEY=sk-abcdefghijklmnopqrstuvwxyz; "
            '"client_secret": "super-secret-value"; '
            "github=ghp_abcdefghijklmnopqrstuvwxyz123456; request_id=abc123"
        )
        redacted = redact_sensitive_text(value)
        self.assertNotIn("abcdefghijklmnop", redacted)
        self.assertNotIn("super-secret-value", redacted)
        self.assertNotIn("ghp_abcdefghijklmnopqrstuvwxyz123456", redacted)
        self.assertIn("gateway failed", redacted)
        self.assertIn("request_id=abc123", redacted)
        self.assertGreaterEqual(redacted.count(REDACTED), 4)

    def test_handles_bytes_none_and_is_idempotent(self):
        self.assertEqual("", redact_sensitive_text(None))
        redacted = redact_sensitive_text(b"password=hunter2")
        self.assertEqual(f"password={REDACTED}", redacted)
        self.assertEqual(redacted, redact_sensitive_text(redacted))

    def test_does_not_redact_ordinary_token_metrics(self):
        value = "output_tokens=42; token count: 17; answer=BENCH_OK"
        self.assertEqual(value, redact_sensitive_text(value))


if __name__ == "__main__":
    unittest.main()
