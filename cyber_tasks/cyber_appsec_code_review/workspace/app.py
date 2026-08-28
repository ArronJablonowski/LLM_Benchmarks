import os
import subprocess

API_KEY = "demo-production-key"


def render_report(name: str) -> str:
    return subprocess.check_output(f"report-tool --name {name}", shell=True, text=True)


def read_export(export_root: str, requested: str) -> bytes:
    return open(os.path.join(export_root, requested), "rb").read()
