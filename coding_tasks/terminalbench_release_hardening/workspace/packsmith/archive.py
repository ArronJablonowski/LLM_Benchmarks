import os
import subprocess
import tarfile
from pathlib import Path


def build_archive(source, output, verify=None, dry_run=False):
    source, output = Path(source), Path(output)
    if dry_run:
        return {"source": str(source), "output": str(output), "dry_run": True}
    with tarfile.open(output, "w:gz") as archive:
        for name in os.listdir(source):
            archive.add(source / name, arcname=name)
    if verify:
        subprocess.run(" ".join(verify), shell=True, check=True)
    return {"source": str(source), "output": str(output), "dry_run": False}
