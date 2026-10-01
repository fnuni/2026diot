"""Verify the publication package against RELEASE_MANIFEST.sha256."""
from __future__ import annotations

import hashlib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    manifest = ROOT / "RELEASE_MANIFEST.sha256"
    if not manifest.exists():
        print("RELEASE_MANIFEST.sha256 is missing")
        return 1
    bad = []
    for line in manifest.read_text().splitlines():
        expected, name = line.split("  ", 1)
        path = ROOT / name
        if not path.is_file() or digest(path) != expected:
            bad.append(name)
    if bad:
        print(f"release integrity mismatch: {bad[:10]}")
        return 1
    print("release package integrity OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
