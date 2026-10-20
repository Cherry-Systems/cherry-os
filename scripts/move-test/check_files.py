#!/usr/bin/env python3
"""Checks files restored by Cherry Move against the SHA-256 list printed by make-files.ps1 (Windows) or
make_files.py (macOS, Linux).
  check_files.py HASHES [HOME]
Lines look like "<sha256>  DOCUMENTS/School report.txt" (or HOME/.ssh/id_ed25519)."""
import hashlib
import subprocess
import sys
from pathlib import Path

FALLBACK = {"DOCUMENTS": "Documents", "PICTURES": "Pictures", "MUSIC": "Music", "VIDEOS": "Videos",
            "DESKTOP": "Desktop", "DOWNLOAD": "Downloads"}


def folder(key: str, home: Path) -> Path:
    if key == "HOME":
        return home
    if home == Path.home():
        try:
            out = subprocess.run(["xdg-user-dir", key], capture_output=True, text=True).stdout.strip()
            if out and Path(out) != home:
                return Path(out)
        except FileNotFoundError:
            pass
    return home / FALLBACK[key]


def main(hashes: str, home: str | None = None) -> int:
    base = Path(home) if home else Path.home()
    good, bad = 0, []
    for line in Path(hashes).read_text(encoding="utf-8-sig").splitlines():
        if "  " not in line:
            continue
        digest, name = line.strip().split("  ", 1)
        key, rel = name.split("/", 1)
        path = folder(key, base) / rel
        if not path.is_file():
            bad.append(f"missing: {path}")
        elif hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            bad.append(f"different: {path}")
        else:
            good += 1
    print(f"{good} files match")
    for b in bad:
        print(b)
    return 1 if bad or not good else 0


if __name__ == "__main__":
    sys.exit(main(*sys.argv[1:]))
