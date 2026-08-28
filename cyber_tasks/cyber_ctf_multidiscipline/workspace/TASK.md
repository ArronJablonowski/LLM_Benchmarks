# Four-part offline CTF

Solve all local subtasks and write `submission.json` with keys `web_flag`, `crypto_flag`, `forensics_flag`, `reverse_flag`, and `methods`. The challenges are original and purpose-built. No external lookup or service is needed.

- Web: decode the signed-looking but intentionally unsigned cookie in `web.txt`.
- Crypto: recover plaintext from the repeating-key XOR values in `crypto.json`.
- Forensics: identify the out-of-order DNS labels in `forensics.log` and concatenate their chunks by sequence.
- Reverse: evaluate the simple transformation in `reverse.py` without modifying it.
