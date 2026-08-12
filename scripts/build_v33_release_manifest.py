#!/usr/bin/env python3
"""Build and immediately verify the V3.3 release SHA-256 manifest."""

from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
OUTPUTS = {Path("release/MANIFEST.sha256"), Path("MANIFEST.sha256")}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def main() -> int:
    completed = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
        cwd=ROOT, capture_output=True, check=True,
    )
    files = sorted(
        Path(item.decode("utf-8"))
        for item in completed.stdout.split(b"\0") if item
    )
    files = [path for path in files if path not in OUTPUTS and (ROOT / path).is_file()]
    lines = [f"{sha256(ROOT / path)}  {path.as_posix()}" for path in files]
    text = "\n".join(lines) + "\n"
    for relative in OUTPUTS:
        target = ROOT / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8", newline="\n")
    failures = [path.as_posix() for path in files if sha256(ROOT / path) != lines[files.index(path)].split()[0]]
    print(f"manifest_files={len(files)} failures={len(failures)}")
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
