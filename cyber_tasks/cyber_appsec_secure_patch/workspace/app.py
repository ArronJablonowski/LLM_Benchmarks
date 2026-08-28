import os
import subprocess


def render_report(name: str, runner=None) -> str:
    runner = runner or subprocess.check_output
    return runner(f"report-tool --name {name}", shell=True, text=True)


def read_export(export_root: str, requested: str) -> bytes:
    return open(os.path.join(export_root, requested), "rb").read()
