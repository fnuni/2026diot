"""Verify the publication package against RELEASE_MANIFEST.sha256."""
from __future__ import annotations

import hashlib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def digest(path: Path) -> str:
    data = path.read_bytes()
    # Git text normalisation must not invalidate scientific tables. CSV hashes
    # deliberately identify LF-normalised content; other files are byte-exact.
    if path.suffix == ".csv":
        data = data.replace(b"\r\n", b"\n")
    return hashlib.sha256(data).hexdigest()


def write_manifest(root: Path = ROOT) -> None:
    excluded = {".git", "__pycache__", ".pytest_cache", ".venv",
                "events", "scenarios", "checkpoints"}
    files = sorted(p for p in root.rglob("*") if p.is_file()
                   and not excluded.intersection(p.relative_to(root).parts)
                   and p.name not in {".gitignore", "RELEASE_MANIFEST.sha256"}
                   and p.suffix != ".pyc")
    (root / "RELEASE_MANIFEST.sha256").write_text(
        "".join(f"{digest(p)}  {p.relative_to(root).as_posix()}\n" for p in files))


def main() -> int:
    if "--write" in sys.argv:
        write_manifest()
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
