# Job Service Specification

Build an offline Python package named `job_service`.

- `Store(path)` uses SQLite and creates its schema safely.
- `create(idempotency_key, payload)` returns a job dictionary. Reusing a key
  returns the original job without creating a duplicate.
- States are `queued`, `running`, `succeeded`, `failed`. Only queued→running
  and running→succeeded/failed are valid.
- `fail(job_id, retry_at=None)` records failure; when `retry_at` is supplied,
  `due(now)` returns that job once due and `retry(job_id)` moves it to queued.
- Persist JSON payloads losslessly and use timezone-aware ISO-8601 timestamps.
- `python -m job_service --db PATH COMMAND` supports create/get/start/succeed/
  fail/due and emits one compact JSON object per invocation.
- Use parameterized SQL, type hints, clear exceptions, and standard library
  dependencies only. Include tests and an offline-installable `pyproject.toml`.
