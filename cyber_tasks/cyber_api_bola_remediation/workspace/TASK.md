# API authorization remediation

Repair `api.py`. A caller may read only records whose `owner_id` matches the authenticated subject, unless the trusted server-side `is_admin` argument is true. Ignore any role supplied inside request data. Deletion must be admin-only. Preserve function signatures, add negative tests, and write `submission.json` with `fixed_risks`.
