# Secure patch

Repair `app.py` while preserving `render_report(name, runner=None)` and `read_export(export_root, requested)` APIs. `render_report` must invoke `report-tool --name <name>` without a shell and allow a test runner injection. `read_export` must resolve paths, block absolute paths and traversal, and only return files within the export root. Add tests and `submission.json` with `fixed_cwes`.
