"""Export the staged source tree without private Git history or hosted operations.

Usage: python deploy/export_source.py /absolute/new-output.tar
The output must not exist. Review/stage the candidate before running this tool.
"""
import argparse
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import subprocess
import tarfile

ROOT = Path(__file__).resolve().parents[1]
HOSTED_WORKFLOWS: set = set()


def export(destination):
    tree = subprocess.check_output(["git", "write-tree"], cwd=ROOT, text=True).strip()
    data = subprocess.check_output(["git", "archive", "--format=tar", tree], cwd=ROOT)
    files = {}
    modes = {}
    with tarfile.open(fileobj=io.BytesIO(data)) as source:
        for member in source:
            path = PurePosixPath(member.name)
            if path.is_absolute() or ".." in path.parts:
                raise ValueError("Unsafe archive path")
            if member.isdir():
                continue
            if not member.isfile():
                raise ValueError("Only regular source files are supported")
            if member.name in HOSTED_WORKFLOWS or member.name == "docs/source-export.json":
                continue
            if "internal" in path.parts or ".git" in path.parts:
                raise ValueError("Private source exclusion failed")
            if path.name.startswith(".env") and path.name != ".env.example":
                raise ValueError("Environment file entered source export")
            files[member.name] = source.extractfile(member).read()
            modes[member.name] = 0o755 if member.mode & 0o111 else 0o644
    manifest = {
        "distribution": "clean-source-snapshot",
        "project_license": "Apache-2.0; bundled fonts retain their SIL OFL notices",
        "exclusions": ["private Git history", "internal evidence and assets", "private product memory (internal/)"],
        "files": {name: hashlib.sha256(content).hexdigest() for name, content in sorted(files.items())},
    }
    files["docs/source-export.json"] = (json.dumps(manifest, indent=2) + "\n").encode()
    with destination.open("xb") as output, tarfile.open(fileobj=output, mode="w") as archive:
        for name, content in sorted(files.items()):
            member = tarfile.TarInfo(name)
            member.size = len(content)
            member.mode = modes.get(name, 0o644)
            archive.addfile(member, io.BytesIO(content))
    print(f"Exported {len(files)} files; Apache-2.0 source snapshot; bundled font notices retained.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("destination", type=Path)
    args = parser.parse_args()
    export(args.destination.resolve())
