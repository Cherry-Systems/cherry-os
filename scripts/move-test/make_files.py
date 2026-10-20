#!/usr/bin/env python3
"""Fills a home folder with files for Cherry Move to export (macOS or Linux), then prints their SHA-256s in
the format check_files.py reads. Same files as ../win-test/make-files.ps1.
  make_files.py [HOME] [--small] [--mac]
--small: about 60 MB instead of 180 MB, for live sessions in RAM; --mac: lay it out as on a Mac (to copy to one)."""
import hashlib
import os
import random
import shutil
import subprocess
import sys
from pathlib import Path

args = [a for a in sys.argv[1:] if not a.startswith("--")]
small = "--small" in sys.argv
home = Path(args[0]) if args else Path.home()
mac = sys.platform == "darwin" or "--mac" in sys.argv
rng = random.Random(42)


def folder(key: str, fallback: str) -> Path:
    if not mac and home == Path.home():
        try:
            out = subprocess.run(["xdg-user-dir", key], capture_output=True, text=True).stdout.strip()
            if out and Path(out) != home:
                return Path(out)
        except FileNotFoundError:
            pass
    return home / fallback


roots = {"DOCUMENTS": folder("DOCUMENTS", "Documents"), "PICTURES": folder("PICTURES", "Pictures"),
         "MUSIC": folder("MUSIC", "Music"), "VIDEOS": folder("VIDEOS", "Movies" if mac else "Videos"),
         "DESKTOP": folder("DESKTOP", "Desktop"), "DOWNLOAD": folder("DOWNLOAD", "Downloads")}
made: list[tuple[str, Path]] = []


def put(key: str, rel: str, data: bytes):
    path = (home if key == "HOME" else roots[key]) / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    made.append((f"{key}/{rel}", path))


def noise(mb: float) -> bytes:
    return rng.randbytes(int(mb * 1024 * 1024))


text = "\r\n".join(f"Line {i}: notes about the move to the new computer, nothing secret here." for i in range(1, 3001))
put("DOCUMENTS", "School report.txt", text.encode())
put("DOCUMENTS", "School report (1).txt", text.encode())
put("DOCUMENTS", "Café menu – winter.txt", "Flat white\r\nLong black\r\nBabycino".encode())
put("DOCUMENTS", "Projects/Robot arm/notes.md", b"# Robot arm\r\nServo 3 is wired backwards.")
put("DOCUMENTS", "Tax 2025/statement.pdf", noise(3))
photos = 15 if small else 50
for i in range(1, photos + 1):
    put("PICTURES", f"Camera/IMG_{i:04d}.jpg", noise(2))
for i in range(1, 6):
    src = roots["PICTURES"] / f"Camera/IMG_{i:04d}.jpg"
    dst = roots["PICTURES"] / f"Holiday/IMG_{i:04d}.jpg"
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(src, dst)
    made.append((f"PICTURES/Holiday/IMG_{i:04d}.jpg", dst))
put("MUSIC", "Band - Song.mp3", noise(9 if not small else 3))
put("MUSIC", "Band - Another song.mp3", noise(7 if not small else 2))
put("VIDEOS", "Birthday.mp4", noise(30 if not small else 10))
put("DESKTOP", "Shopping list.txt", b"Milk\r\nEggs\r\nA bigger USB stick")
put("DOWNLOAD", "setup-tool.dmg" if mac else "setup-tool.AppImage", noise(5 if not small else 2))
put("HOME", ".ssh/id_ed25519", b"-----BEGIN OPENSSH PRIVATE KEY-----\nnot a real key\n-----END OPENSSH PRIVATE KEY-----\n")
os.chmod(home / ".ssh", 0o700)
os.chmod(home / ".ssh/id_ed25519", 0o600)
put("HOME", ".gitconfig", b"[user]\n\tname = Alex\n")
put("HOME", "Code/hello/hello.py", b"print('hello from the old computer')\n")

# A Chrome profile, where Cherry OS expects it after the move.
chrome = home / ("Library/Application Support/Google/Chrome" if mac else ".config/google-chrome")
bookmarks = b'{"roots": {"bookmark_bar": {"children": [{"name": "Cherry OS", "type": "url", "url": "https://example.org/"}], "name": "Bookmarks bar", "type": "folder"}}, "version": 1}'
(chrome / "Default").mkdir(parents=True, exist_ok=True)
(chrome / "Default/Bookmarks").write_bytes(bookmarks)
(chrome / "Local State").write_bytes(b"{}")
made.append(("HOME/.config/google-chrome/Default/Bookmarks", chrome / "Default/Bookmarks"))

for name, path in made:
    print(f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {name}")
