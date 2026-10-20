#!/usr/bin/env python3
"""Cherry Move soak test: sends and restores random home folders over and over, checking every file arrives.

Each round builds a new random home folder (odd and very long names, names that aren't valid text, every kind of
link, permissions, timestamps, duplicates, live databases, files the new computer already has…), sends it to a
random place (one stick, several small sticks, a pretend NAS in memory or rclone serving a folder), sometimes stops
the send or the restore part way and starts again, then checks the new home folder file by file against what
Cherry Move's rules say should be there.

Run: PYTHONPATH=desktop/lib python3 tests/soak.py [--minutes N | --rounds N] [--seed S] [--dir D]
A failing round's folders are kept, and its seed printed, so it can be run again with --seed S --rounds 1."""
from __future__ import annotations

import argparse
import collections
import hashlib
import itertools
import json
import os
import random
import shutil
import signal
import socket
import sqlite3
import stat
import subprocess
import sys
import threading
import time
import traceback
import urllib.parse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "desktop/lib"))

from cherryos import remote, secret_store, system_extras, transfer  # noqa: E402

MB = 1024 * 1024
WORDS = ["notes", "photo", "report", "Song", "draft", "budget", "holiday", "IMG_0042", "backup", "todo", "Résumé",
         "日本語のファイル", "emoji 🍒", "with space", "trailing ", " leading", "-dash", "#hash%20", "a&b", "brackets [1]",
         "(copy)", "semi;colon", "quote'single", 'dq"', "back\\slash", "tab\tname", "new\nline", "UPPER", "dots...",
         "x" * 90, "essay (from old computer)", "file (before Cherry Move)", "café", "ß", "Ω", "~tilde", "$dollar",
         "star*", "q?mark", "colon:name", "pipe|name", "<angle>", "@at", "=eq", "+plus", ",comma"]
EXTS = ["", ".txt", ".jpg", ".tar.gz", ".json", ".mp3", ".PDF", ".db", ".md", ".bin", ".", ".tmp"]
TOPS = ["Documents", "Pictures", "Music", "Videos", "Desktop", "Downloads", "Projects", "Stuff", "Old Disk",
        ".config/someapp", ".local/share/game", ".myapp", "snap/thing/common", ".ssh", ".gnupg", ".fonts"]
XDG_TOP = {fallback: name for name, fallback in transfer.XDG_NAMES.values()}
# What user-dirs.dirs may call those folders: on the old computer (German, Japanese…) and on the new one (French…).
OLD_NAMES = {"DOCUMENTS": ["Dokumente", "書類", "My Documents"], "PICTURES": ["Bilder", "Fotos 📷", "写真"],
             "MUSIC": ["Musik", "音楽", "My Music"], "VIDEOS": ["Videos und Filme", "ビデオ", "Filme"],
             "DESKTOP": ["Schreibtisch", "デスクトップ", "Arbeitsfläche"], "DOWNLOAD": ["Herunterladen", "ダウンロード", "Dls"]}
NEW_NAMES = {"DOCUMENTS": ["Documents personnels", "Documentos"], "PICTURES": ["Images", "Imágenes"],
             "MUSIC": ["Musique", "Música"], "VIDEOS": ["Vidéos", "Películas"], "DESKTOP": ["Bureau", "Escritorio"],
             "DOWNLOAD": ["Téléchargements", "Descargas"]}
REAL_XDG_DIR = transfer.xdg_dir


def xdg_dir(name: str, fallback: str, home: Path) -> Path:
    """transfer.xdg_dir, reading the home folder's own user-dirs.dirs (rclone rounds set XDG_CONFIG_HOME)."""
    saved = os.environ.pop("XDG_CONFIG_HOME", None)
    try:
        return REAL_XDG_DIR(name, fallback, home)
    finally:
        if saved is not None:
            os.environ["XDG_CONFIG_HOME"] = saved


def user_dirs(home: Path, names: dict) -> None:
    lines = [f'XDG_{x}_DIR="$HOME/{names.get(x, fallback)}"' for x, fallback in transfer.XDG_NAMES.values()]
    (home / ".config").mkdir(exist_ok=True)
    (home / ".config" / "user-dirs.dirs").write_text("# folders\n" + "\n".join(lines) + "\n")


def xdg_places(old: Path, new: Path):
    """places(rel): where an old file or folder goes, as (name in the transfer, path here); twice for a folder
    the old computer had inside another of these folders (it's sent with both)."""
    def inside(home, x, fallback, must_exist):
        p = xdg_dir(x, fallback, home)
        if must_exist and not p.is_dir() or p.is_symlink():
            return None
        try:
            return str(p.relative_to(home))
        except ValueError:
            return None
    old_x = {x: r for x, f in transfer.XDG_NAMES.values() if (r := inside(old, x, f, True))}
    new_x = {x: inside(new, x, f, False) for x, f in transfer.XDG_NAMES.values()}

    def places(rel: str) -> list[tuple[str, str]]:
        hits = [(f"xdg/{x}{rel[len(r):]}", new_x[x] + rel[len(r):]) for x, r in old_x.items()
                if (rel == r or rel.startswith(r + "/")) and new_x[x]]
        return hits or [(f"home/{rel}", dest_of(rel))]
    return places


class Patch:
    """Sets attributes for a round and puts them back afterwards."""

    def __init__(self):
        self.saved = []

    def set(self, obj, name, value):
        self.saved.append((obj, name, getattr(obj, name)))
        setattr(obj, name, value)

    def undo(self):
        for obj, name, value in reversed(self.saved):
            setattr(obj, name, value)
        self.saved.clear()


class FakeRemote:
    """A NAS in memory."""
    title = "Soak NAS"

    def __init__(self):
        self.files, self.dirs = {}, set()

    def descriptor(self):
        return {"kind": "nas", "title": self.title, "type": "webdav", "url": "http://nas.local/"}

    def mkdirs(self, path):
        self.dirs.add(path)

    def put(self, path, data):
        self.files[path] = bytes(data)

    def get(self, path):
        return self.files.get(path)

    def delete(self, path):
        for k in [k for k in self.files if k.startswith(path + "/")]:
            del self.files[k]

    def list_dirs(self, path):
        names = {k[len(path) + 1:].split("/", 1)[0] for k in self.files if k.startswith(path + "/")}
        return sorted(n for n in names if any(k.startswith(f"{path}/{n}/") for k in self.files))


class FolderNas(FakeRemote):
    """The NAS share the Cherry Move app wrote to, read as a folder."""

    def __init__(self, root: Path):
        super().__init__()
        self.root = root

    def get(self, path):
        p = self.root / path
        return p.read_bytes() if p.is_file() else None


APP = None  # the Windows/macOS/Linux Cherry Move app (move/), built at the start when Go is installed
NO_EXTRAS = 'import json; print(json.dumps({"debs": [], "system": {}, "docker": {}}))\n'


# ---------------------------------------------------------------- a random old computer

class Home:
    def __init__(self, root: Path, rng: random.Random, profile: str, names: dict | None = None):
        self.root, self.rng, self.profile = root, rng, profile
        self.names = names or {}  # what user-dirs.dirs calls Pictures, Music…
        self.files: list[Path] = []
        self.dirs: list[Path] = []
        self.live_dbs: list[tuple[Path, sqlite3.Connection]] = []
        self.dir_modes: list[tuple[Path, int]] = []
        self.db_dirs: set[Path] = set()

    def name(self, parent: Path) -> str:
        rng = self.rng
        while True:
            r = rng.random()
            if r < 0.02:
                n = os.fsdecode(b"bad\xff\xfe name " + str(rng.randint(0, 99)).encode())
            elif r < 0.04:  # as long as a name can be (in bytes), sometimes in letters of two bytes each
                n = rng.choice(["L", "é"]) * 254
                n = os.fsencode(n)[:rng.randint(150, 254)].decode("utf-8", "ignore") + str(rng.randint(0, 9))
            else:
                n = rng.choice(WORDS) + (f" {rng.randint(1, 999)}" if rng.random() < 0.6 else "")
                n += rng.choice(EXTS)
            if n in (".", "..") or "/" in n or len(os.fsencode(n)) > 255 or n in transfer.SKIP_REASONS:
                continue
            if not os.path.lexists(parent / n):
                return n

    def content(self, size: int) -> bytes:
        kind = self.rng.random()
        if kind < 0.35:
            return self.rng.randbytes(size)
        if kind < 0.7:
            line = f"{self.rng.choice(WORDS)} line {self.rng.randint(0, 10**6)}\n".encode()
            return (line * (size // len(line) + 1))[:size]
        if kind < 0.8:
            return bytes(size)
        return os.urandom(size)

    def size(self) -> int:
        r = self.rng.random()
        chunk = transfer.CHUNK
        if r < 0.1:
            return 0
        if r < 0.2:
            return self.rng.choice([1, 2, 511, 512, 513, 4095, 4096, 4097])
        if r < 0.3:
            return max(0, chunk + self.rng.randint(-3, 3))  # right at a chunk's edge
        if r < 0.35:
            return transfer.DEDUP_MIN + self.rng.randint(-1, 1)
        if self.profile == "many":
            return self.rng.randint(0, 3000)
        if r < 0.85:
            return self.rng.randint(0, 64 * 1024)
        return self.rng.randint(64 * 1024, 3 * MB)

    def build(self, n_files: int, n_dirs: int, big: int = 0):
        rng, root = self.rng, self.root
        root.mkdir(parents=True)
        for top in rng.sample(TOPS, rng.randint(4, len(TOPS))):
            top = self.names.get(XDG_TOP.get(top), top)
            (root / top).mkdir(parents=True, exist_ok=True)
            self.dirs.append(root / top)
        if self.names or rng.random() < 0.3:
            user_dirs(root, self.names)
        for _ in range(n_dirs):
            parent = rng.choice(self.dirs)
            if len(str(parent)) > 2500:
                continue
            d = parent / self.name(parent)
            d.mkdir()
            self.dirs.append(d)
        deep = root / "Projects"
        deep.mkdir(exist_ok=True)
        for _ in range(rng.randint(5, 12)):  # a path far longer than tar's 100 bytes
            deep = deep / ("deep folder " + "d" * rng.randint(20, 60))
        deep.mkdir(parents=True)
        self.dirs.append(deep)
        for rel in (".bashrc", ".profile", ".zshrc"):
            if rng.random() < 0.7:
                (root / rel).write_text(f"# {rel} from the old computer {rng.random()}\n")
                self.files.append(root / rel)
        for _ in range(n_files):
            parent = rng.choice(self.dirs)
            p = parent / self.name(parent)
            r = rng.random()
            if r < 0.12 and self.files:
                src = rng.choice(self.files)
                if src.stat().st_size >= transfer.DEDUP_MIN or rng.random() < 0.3:
                    shutil.copyfile(src, p)  # the same file twice: stored once
                    st = src.stat()
                    os.utime(p, ns=(st.st_atime_ns, st.st_mtime_ns))
                else:
                    p.write_bytes(self.content(self.size()))
            elif r < 0.17 and self.files:
                os.link(rng.choice(self.files), p)
                continue
            elif r < 0.19:
                with open(p, "wb") as f:  # sparse
                    f.seek(rng.randint(1, 4 * MB))
                    f.write(b"end")
            else:
                p.write_bytes(self.content(self.size()))
            os.chmod(p, rng.choice([0o644] * 6 + [0o600, 0o755, 0o444, 0o700, 0o640, 0o400]))
            ns = rng.randint(315532800, 1_900_000_000) * 10**9 + rng.randint(0, 10**9 - 1)
            os.utime(p, ns=(ns, ns))
            self.files.append(p)
        if big:
            p = root / "Videos" / "big film.mkv"
            p.parent.mkdir(exist_ok=True)
            with open(p, "wb") as f:
                left = big
                while left:
                    n = min(left, 8 * MB)
                    f.write(os.urandom(n) if rng.random() < 0.7 else bytes(n))
                    left -= n
            self.files.append(p)
        self.links()
        self.databases()
        if rng.random() < 0.3:
            fifo = rng.choice(self.dirs) / "a pipe"
            if not fifo.exists():
                os.mkfifo(fifo)
        for _ in range(rng.randint(0, 4)):  # left behind on purpose
            parent = rng.choice(self.dirs)
            kind = rng.choice(["node_modules", "__pycache__", ".cache", "Trash", ".venv", "venv"])
            d = parent / kind
            if d.exists():
                continue
            d.mkdir()
            (d / "junk.bin").write_bytes(b"junk" * 100)
            if kind == "node_modules":
                (parent / "package.json").write_text("{}\n")
            if kind == "venv":
                (d / "pyvenv.cfg").write_text("home = /usr/bin\n")
        for _ in range(rng.randint(0, 6)):  # empty folders
            parent = rng.choice(self.dirs)
            d = parent / self.name(parent)
            d.mkdir()
            self.dirs.append(d)
        for d in rng.sample(self.dirs, min(len(self.dirs), rng.randint(0, 6))):
            if d != root and d not in self.db_dirs:  # a database's folder has to be writable to use it
                self.dir_modes.append((d, rng.choice([0o700, 0o750, 0o555, 0o511])))
        for d, mode in sorted(self.dir_modes, key=lambda x: -len(str(x[0]))):
            os.chmod(d, mode)

    def links(self):
        rng = self.rng
        for _ in range(rng.randint(3, 20)):
            parent = rng.choice(self.dirs)
            p = parent / self.name(parent)
            r = rng.random()
            target = rng.choice(self.files + self.dirs) if self.files else self.root
            if r < 0.4:
                os.symlink(os.path.relpath(target, parent), p)
            elif r < 0.6:
                os.symlink(str(target), p)  # absolute, inside the old home folder
            elif r < 0.7:
                os.symlink("/usr/share/doc", p)
            elif r < 0.85:
                os.symlink("nowhere/at/all", p)
            else:
                q = parent / self.name(parent)
                os.symlink(q.name, p)
                if not os.path.lexists(q):
                    os.symlink(p.name, q)  # a loop

    def databases(self):
        rng = self.rng
        roomy = [d for d in self.dirs if len(os.fsencode(str(d))) < 400]  # SQLite's longest path is 512 bytes
        for i in range(rng.randint(0, 3)):
            parent = rng.choice(roomy or [self.root])
            self.db_dirs.add(parent)
            p = parent / f"database {i}.sqlite"
            con = sqlite3.connect(p, check_same_thread=False)
            live = rng.random() < 0.7
            if live:
                con.execute("PRAGMA journal_mode=WAL")
                con.execute("PRAGMA wal_autocheckpoint=0")
            con.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, body BLOB)")
            for _ in range(rng.randint(1, 400)):
                con.execute("INSERT INTO t (body) VALUES (?)", (rng.randbytes(rng.randint(0, 3000)),))
            con.commit()
            if live:
                self.live_dbs.append((p, con))  # stays open with its changes in the write-ahead log
            else:
                con.close()
                self.files.append(p)

    def close(self):
        for _p, con in self.live_dbs:
            con.close()


def db_rows(path: Path):
    con = sqlite3.connect(f"file:{urllib.parse.quote(os.fsencode(path))}?mode=ro", uri=True)
    try:
        return con.execute("SELECT id, body FROM t ORDER BY id").fetchall()
    finally:
        con.close()


# ---------------------------------------------------------------- what the new computer should end up with

def digest(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        while b := f.read(MB):
            h.update(b)
    return h.hexdigest()


def snapshot(root: Path) -> dict:
    """rel -> (kind, sha or link target, mode, mtime_ns, size) for everything under root."""
    out = {}
    for dirpath, dirs, names in os.walk(root):
        for n in dirs + names:
            p = Path(dirpath) / n
            st = os.lstat(p)
            rel = str(p.relative_to(root))
            if stat.S_ISLNK(st.st_mode):
                out[rel] = ("l", os.readlink(p), 0, 0, 0)
            elif stat.S_ISDIR(st.st_mode):
                out[rel] = ("d", "", stat.S_IMODE(st.st_mode), 0, 0)
            elif stat.S_ISREG(st.st_mode):
                out[rel] = ("f", digest(p) if os.access(p, os.R_OK) else "?", stat.S_IMODE(st.st_mode), st.st_mtime_ns,
                           st.st_size)
            else:
                out[rel] = ("o", "", 0, 0, 0)
    return out


def dest_of(rel: str) -> str:
    return transfer.RENAME_ON_RESTORE.get(rel, rel)


def beside(rel: str, why: str, folder: bool = False) -> str:
    return str(transfer._beside_name(Path(rel), why, 1, folder))


def check(old: Path, new: Path, before: dict, home: Home, problems: list, places, touched=frozenset()):
    """Compares the new home folder with the old one, by Cherry Move's rules."""
    src, got = snapshot(old), snapshot(new)
    why = transfer.left_behind_rules(old)
    live = {str(p.relative_to(old)) for p, _ in home.live_dbs}
    sides = {f"{d}{e}" for d in live for e in ("-wal", "-shm", "-journal")}
    expected = set()
    old_home, new_home = str(old), str(new)
    folders: dict[str, str] = {}  # where old folders whose name something else has here went instead

    def remap(rel: str) -> str:
        if folders:
            parts = rel.split("/")
            for i in range(len(parts), 0, -1):
                top = "/".join(parts[:i])
                if top in folders:
                    return folders[top] + rel[len(top):]
        return rel
    old_dirs = [(r, a, d) for r, v in src.items() if v[0] == "d" for a, d in places(r)]
    for rel, arc, d0 in sorted(old_dirs, key=lambda t: t[2].count("/")):
        if why(rel) or (old / rel / "pyvenv.cfg").is_file():
            continue
        d = remap(d0)
        pre = before.get(d)
        if pre is None or pre[0] == "d" or pre[0] == "f" and transfer._old_wins(arc):
            continue
        folders[d0] = beside(d, "from old computer", folder=True)

    for rel, (kind, val, mode, mtime, size), arc, d in ((r, v, a, remap(d0)) for r, v in src.items()
                                                         for a, d0 in places(r)):
        venv = kind == "d" and (old / rel / "pyvenv.cfg").is_file()
        if rel in touched:
            continue
        if why(rel) or venv or rel in sides or kind == "o":
            restores_own = d in (".cache", ".cache/cherry-move")  # where the restore keeps its checkpoint
            if d in got and d not in before and not restores_own:
                problems.append(f"left-behind thing came along: {rel!r}")
            continue
        expected.add(d)
        g, pre = got.get(d), before.get(d)
        if g is None:
            problems.append(f"MISSING {kind} {rel!r}")
            continue
        if kind == "d":
            if g[0] != "d":
                problems.append(f"not a folder: {rel!r}")
                continue
            if pre is not None and pre[0] != "d":  # the old folder won: the file here was set aside
                aside = beside(d, "before Cherry Move")
                expected.add(aside)
                if got.get(aside, (0, 0))[1] != pre[1]:
                    problems.append(f"file where the old folder goes wasn't set aside: {rel!r}")
            if (pre is None or pre[0] != "d") and g[2] != mode:
                problems.append(f"folder mode {oct(mode)} -> {oct(g[2])}: {rel!r}")
            continue
        if kind == "l":
            if pre is not None:
                if g != pre:
                    problems.append(f"existing thing replaced by a link: {rel!r}")
                continue
            want = new_home + val[len(old_home):] if val == old_home or val.startswith(old_home + "/") else val
            if g[0] != "l" or g[1] != want:
                problems.append(f"link {rel!r}: want -> {want!r}, got {g}")
            continue
        if rel in live:
            if g[0] != "f" or db_rows(old / rel) != db_rows(new / d):
                problems.append(f"live database differs: {rel!r}")
            continue
        if pre is not None and pre[0] == "d":  # a folder here where the old computer had a file
            if transfer._old_wins(arc):
                aside = beside(d, "before Cherry Move", folder=True)
                expected.add(aside)
                expected.update(aside + b[len(d):] for b in before if b.startswith(d + "/"))
                if g[0] != "f" or g[1] != val or got.get(aside, ("",))[0] != "d":
                    problems.append(f"old file should have won, the folder set aside: {rel!r}")
            else:
                copy = beside(d, "from old computer", folder=True)
                expected.add(copy)
                if g[0] != "d" or got.get(copy, (0, 0))[1] != val:
                    problems.append(f"folder here should stay, the old file beside it: {rel!r}")
            continue
        if pre is not None and pre[0] == "l":
            if g != pre:
                problems.append(f"link already here was replaced: {rel!r}")
            there = new / d
            if there.is_file() and digest(there) == val:
                continue
            copy = beside(d, "from old computer", there.is_dir())
            expected.add(copy)
            if got.get(copy, (0, 0))[1] != val:
                problems.append(f"file where a link already was didn't go beside it: {rel!r}")
            continue
        if pre is not None and pre[0] == "f":
            if pre[1] == val or abs(pre[3] - mtime) < 2 * 10**9 and pre[4] == size:
                if g[1] != pre[1]:
                    problems.append(f"file already here was changed: {rel!r}")
                continue
            if transfer._old_wins(arc):
                aside = beside(d, "before Cherry Move")
                expected.add(aside)
                if g[1] != val or got.get(aside, (0, 0))[1] != pre[1]:
                    problems.append(f"old should have won, new set aside: {rel!r}")
            else:
                copy = beside(d, "from old computer")
                expected.add(copy)
                if g[1] != pre[1] or got.get(copy, (0, 0))[1] != val:
                    problems.append(f"new should stay, old beside it: {rel!r}")
            continue
        if g[0] != "f" or g[1] != val:
            problems.append(f"CONTENT differs: {rel!r}")
        elif g[2] != mode:
            problems.append(f"mode {oct(mode)} -> {oct(g[2])}: {rel!r}")
        elif abs(g[3] - mtime) > 10**7:
            problems.append(f"time {mtime} -> {g[3]}: {rel!r}")
    for rel in touched:
        expected.update(remap(d0) for _a, d0 in places(rel))
    for rel, g in got.items():
        if rel.endswith(".cherry-part") or rel.endswith("-links") and rel.startswith(".cache/cherry-move/"):
            problems.append(f"restore's working file left behind: {rel!r}")
        elif rel in expected or rel in before or rel in (".config", ".cache"):
            continue
        elif not (rel == ".config/cherry" or rel.startswith((".config/cherry/", ".cache/"))):
            problems.append(f"unexpected {g[0]}: {rel!r}")


def prepare_new(new: Path, old: Path, rng: random.Random, home: Home, touched: set):
    """The new computer, sometimes with some of the same files already (same, edited, or a link there)."""
    new.mkdir(parents=True)
    names = {x: rng.choice(NEW_NAMES[x]) for x, _f in transfer.XDG_NAMES.values() if rng.random() < 0.3}
    user_dirs(new, names)  # every desktop makes one
    for x, fallback in transfer.XDG_NAMES.values():
        if rng.random() < 0.7:
            (new / names.get(x, fallback)).mkdir(exist_ok=True)
    places = xdg_places(old, new)
    if rng.random() < 0.5:
        return places
    (new / ".bashrc").write_text("# Cherry OS's own .bashrc\n")
    why = transfer.left_behind_rules(old)

    def free_here(q: Path) -> bool:
        """Nothing at q yet, and nothing but real folders on the way to it."""
        if os.path.lexists(q):
            return False
        for p in q.relative_to(new).parents:
            here = new / p
            if here.is_symlink() or os.path.lexists(here) and not here.is_dir():
                return False
        return True

    candidates = [p for p in home.files if p.is_file() and not p.is_symlink()
                  and str(p.relative_to(old)) not in touched]
    for p in rng.sample(candidates, min(len(candidates), rng.randint(1, 25))):
        rel = str(p.relative_to(old))
        if why(rel) or rel in transfer.RENAME_ON_RESTORE:
            continue
        q = new / places(rel)[0][1]
        if not free_here(q):
            continue
        q.parent.mkdir(parents=True, exist_ok=True)
        r = rng.random()
        if r < 0.35:
            shutil.copyfile(p, q)  # the same file, copied at another time
        elif r < 0.8:
            q.write_bytes(b"edited on the new computer " + rng.randbytes(rng.randint(0, 5000)))
        elif r < 0.9:
            os.symlink("/usr/share/doc", q)
        else:
            q.mkdir()  # a folder where the old computer had a file
            (q / "inside.txt").write_text("the new computer's own\n")

    folders = [d for d in home.dirs if d != old and not why(str(d.relative_to(old)))
               and not (d / "pyvenv.cfg").exists()]
    for d in rng.sample(folders, min(len(folders), rng.randint(0, 3))):  # its name taken here by something else
        q = new / places(str(d.relative_to(old)))[0][1]
        if not free_here(q):
            continue
        q.parent.mkdir(parents=True, exist_ok=True)
        r = rng.random()
        if r < 0.5:
            q.write_text("a file on the new computer where the old one had a folder\n")
        elif r < 0.7:
            os.symlink("nowhere", q)
        else:
            os.symlink("/usr/share/doc", q)
    return places


class Busy:
    """Someone still using the old computer while it sends: files edited, cut short, replaced, deleted, renamed and
    made. Records every name it touched (all names of a hard-linked file)."""

    def __init__(self, home: Home, seed: int):
        self.home, self.rng = home, random.Random(seed)
        self.touched: set[str] = set()
        self.stop = threading.Event()
        self.files = [p for p in home.files if p.is_file() and not p.is_symlink()]
        self.names: dict[tuple, list[Path]] = collections.defaultdict(list)
        for dirpath, _dirs, names in os.walk(home.root):  # hard links aren't all in home.files
            for n in names:
                p = Path(dirpath) / n
                st = os.lstat(p)
                if stat.S_ISREG(st.st_mode) and st.st_nlink > 1:
                    self.names[(st.st_dev, st.st_ino)].append(p)
        self.thread = threading.Thread(target=self.run, daemon=True)

    def touch(self, p: Path):
        try:
            st = os.lstat(p)
            for q in self.names.get((st.st_dev, st.st_ino), [p]):
                self.touched.add(str(q.relative_to(self.home.root)))
        except OSError:
            pass
        self.touched.add(str(p.relative_to(self.home.root)))

    def run(self):
        rng, root = self.rng, self.home.root
        while not self.stop.wait(rng.uniform(0.001, 0.03)):
            try:
                act = rng.random()
                if act < 0.85 and not self.files:
                    continue
                p = rng.choice(self.files) if self.files else None
                if act < 0.25:
                    self.touch(p)
                    with open(p, "ab") as f:
                        f.write(rng.randbytes(rng.randint(1, 100_000)))
                elif act < 0.4:
                    self.touch(p)
                    os.truncate(p, rng.randint(0, max(0, p.stat().st_size - 1)))
                elif act < 0.6:
                    self.touch(p)
                    p.write_bytes(rng.randbytes(rng.randint(0, 200_000)))
                elif act < 0.72:
                    self.touch(p)
                    p.unlink()
                    self.files.remove(p)
                elif act < 0.85:
                    q = p.with_name(f"renamed {rng.randint(0, 10**6)}")
                    self.touch(p)
                    p.rename(q)
                    self.files[self.files.index(p)] = q
                    self.touch(q)
                else:
                    d = rng.choice(self.home.dirs)
                    q = d / f"made while sending {rng.randint(0, 10**6)}"
                    self.touched.add(str(q.relative_to(root)))
                    q.write_bytes(rng.randbytes(rng.randint(0, 50_000)))
                    self.files.append(q)
            except (OSError, ValueError):
                pass

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *_exc):
        self.stop.set()
        self.thread.join()


# ---------------------------------------------------------------- one round

def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def wipe(path: Path):
    """Deletes a round's folders, read-only ones included."""
    if not path.exists():
        return
    os.chmod(path, 0o755)
    for dirpath, dirs, _names in os.walk(path):
        for d in dirs:
            p = os.path.join(dirpath, d)
            if not os.path.islink(p):
                os.chmod(p, 0o755)
    shutil.rmtree(path)


def run_round(seed: int, base: Path) -> tuple[bool, str, list]:
    rng = random.Random(seed)
    work = base / f"round-{seed}"
    wipe(work)
    work.mkdir(parents=True)
    patch = Patch()
    problems: list[str] = []
    notes = []
    proc = nas = None
    home = None
    try:
        profile = rng.choices(["small", "many", "big"], [0.7, 0.15, 0.15])[0]
        patch.set(transfer, "CHUNK", rng.choice([16 * 1024, 64 * 1024, 256 * 1024, MB, 8 * MB]))
        patch.set(transfer, "CHECKPOINT_EVERY", rng.choice([0, 0.01, 0.2]))
        patch.set(transfer, "xdg_dir", xdg_dir)
        patch.set(transfer, "installed_apps", lambda: {})
        patch.set(transfer, "network_mounts", lambda *_: frozenset())
        patch.set(system_extras, "collect", lambda out_dir: {"debs": [], "system": {}, "docker": {}})
        keyring = [{"label": f"item {i}", "attributes": {"service": f"s{i}", "xdg:schema": "org.freedesktop.Secret.Generic"},
                    "secret": "c29haw==", "type": "text/plain"} for i in range(rng.randint(0, 5))]
        added = {}

        def add_all(items):  # like the real keyring: an item with the same attributes replaces the old one
            added.update((tuple(sorted(i["attributes"].items())), i) for i in items)
            return items
        patch.set(secret_store, "read_all", lambda: list(keyring))
        patch.set(secret_store, "add_all", add_all)

        old = work / "old" / "me"
        new = work / "new" / "someone"
        lay = random.Random(seed ^ 0xD15)  # its own, so rounds without other names stay as they were
        names = {}
        if lay.random() < 0.4:
            for x, _f in transfer.XDG_NAMES.values():
                r = lay.random()
                if r < 0.4:
                    names[x] = lay.choice(OLD_NAMES[x])
                elif r < 0.5 and x != "DOCUMENTS":
                    names[x] = "Documents/" + lay.choice(OLD_NAMES[x])
        home = Home(old, rng, profile, names)
        if profile == "many":
            home.build(rng.randint(3000, 12000), rng.randint(50, 400))
        elif profile == "big":
            home.build(rng.randint(20, 80), rng.randint(5, 30), big=rng.randint(30, 200) * MB)
        else:
            home.build(rng.randint(30, 400), rng.randint(5, 80))
        os.environ["HOME"] = str(old)
        cats = transfer.categories(old)
        level = rng.choice([transfer.NORMAL, transfer.NORMAL, transfer.STRONG])
        app = APP is not None and random.Random(seed ^ 0xA77).random() < 0.4
        if app:
            keyring.clear()  # the app leaves the keyring's items to its files in the home folder

        where = rng.choices(["stick", "sticks", "memory-nas", "rclone-nas"], [0.35, 0.25, 0.25, 0.15])[0]
        if where == "rclone-nas" and not shutil.which("rclone"):
            where = "memory-nas"
        if app and where.endswith("nas"):
            where = "app-folder"
        notes.append(f"{profile} chunk={transfer.CHUNK // 1024}K {where}" + (" (Cherry Move app)" if app else "")
                     + (f" folders {'/'.join(sorted(names.values()))}" if names else ""))
        sticks_used = []

        def stick(n):
            p = work / f"stick{n}"
            p.mkdir(exist_ok=True)
            return p

        if where == "sticks":
            capacity = transfer.STICK_RESERVE + transfer.CHUNK * rng.randint(3, 8) + rng.randint(0, transfer.CHUNK)

            def free(mount):
                return capacity - sum(f.stat().st_size for f in Path(mount).rglob("*") if f.is_file())
            patch.set(transfer, "free_space", free)
        elif where == "memory-nas":
            nas = FakeRemote()
        elif where == "rclone-nas":
            folder = work / "nas"
            folder.mkdir()
            port = free_port()
            patch.set(remote, "CONFIG", work / "rclone.conf")
            os.environ["XDG_CONFIG_HOME"] = str(work / "rclone-home")  # rclone makes ~/.config/rclone otherwise
            proc = subprocess.Popen(["rclone", "serve", "webdav", str(folder), "--addr", f"127.0.0.1:{port}",
                                     "--user", "soak", "--pass", "soakpass", "--config", "/dev/null"],
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                    env=dict(os.environ, HOME=str(work / "rclone-home")))
            for _ in range(100):
                try:
                    socket.create_connection(("127.0.0.1", port), timeout=1).close()
                    break
                except OSError:
                    time.sleep(0.1)
            nas = remote.Remote("nas", {"type": "webdav", "url": f"http://127.0.0.1:{port}/", "user": "soak"})
            nas.sign_in("soakpass")

        def send(cancel_at=None):
            stop = threading.Event()

            def progress(done, total, name):
                if cancel_at is not None and done >= cancel_at * total:
                    stop.set()
            counter = itertools.count(2)

            def next_stick(part, problem):
                n = next(counter)
                sticks_used.append(n)
                return stick(n)
            if where in ("stick", "sticks"):
                return transfer.send(cats, "usb", drive=stick(1), progress=progress, cancel=stop, level=level,
                                     next_stick=next_stick)
            return transfer.send(cats, "remote", remote=nas, progress=progress, cancel=stop, level=level,
                                 drive=stick(1) if rng.random() < 0.5 else None)

        def app_send(kill_after=None):
            env = {k: v for k, v in os.environ.items() if not k.startswith("XDG_")}
            env.update(HOME=str(old), XDG_DATA_HOME=str(work / "app-state"))
            extras = work / "system_extras.py"
            extras.write_text(NO_EXTRAS)
            env["CHERRY_MOVE_EXTRAS"] = str(extras)
            args = [str(APP), "send"] + (["--strong"] if level == transfer.STRONG else [])
            if where == "app-folder":
                (work / "share").mkdir(exist_ok=True)
                args += ["--to", "folder", "--folder", str(work / "share")]
            else:
                args += ["--to", "usb", "--usb", str(stick(1))]
            if where == "sticks":
                raw = sum(os.lstat(f).st_size for f in old.rglob("*") if f.is_file() and not f.is_symlink())
                args += ["--next-sticks", ",".join(str(stick(n)) for n in range(2, raw // (8 * MB) + 12)),
                         "--stick-capacity", str(app_capacity)]
            if kill_after is not None:
                proc = subprocess.Popen(args, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                try:
                    proc.wait(kill_after)
                    return False
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait()
                    return True
            out = subprocess.run(args, env=env, capture_output=True, text=True, timeout=3 * 3600)
            if out.returncode:
                raise RuntimeError(f"the app failed ({out.returncode}): {out.stderr[-3000:]}")
            res = json.loads(out.stdout)
            sticks_used[:] = range(2, res.get("sticks", 1) + 1)
            return argparse.Namespace(code=res["code"])

        app_capacity = 4 * MB + 8 * MB * rng.randint(2, 5) + rng.randint(0, 8 * MB)
        if app and rng.random() < 0.3:
            killed = app_send(kill_after=rng.uniform(0.05, 3))
            notes.append("app switched off part way, sent again" if killed else "app switch-off came too late")
        elif rng.random() < 0.25 and not app:
            try:
                send(cancel_at=rng.random() * 0.9)
                notes.append("send stop came too late")
            except InterruptedError:
                notes.append("send stopped+again")
            for n in list(sticks_used):
                shutil.rmtree(stick(n), ignore_errors=True)
            sticks_used.clear()
        touched: set[str] = set()
        if rng.random() < 0.25:
            notes.append("busy while sending")
            with Busy(home, rng.randrange(2**32)) as busy:
                result = app_send() if app else send()
            touched = busy.touched
        else:
            result = app_send() if app else send()
        home.close()  # the old computer is switched off: databases' last changes are on disk now

        places = prepare_new(new, old, rng, home, touched)
        before = snapshot(new)
        os.environ["HOME"] = str(new)

        def give(part, problem):
            return stick(1 if part == 1 else sticks_used[part - 2])
        if where == "app-folder":
            nas = FolderNas(work / "share")
        kwargs = {"drive": stick(1), "next_stick": give} if where in ("stick", "sticks") else {"remote": nas}
        stops = rng.choice([0, 0, 1, 2, 3])
        for i in range(stops):
            stop = threading.Event()
            at = rng.random()

            def progress(done, total, name, stop=stop, at=at):
                if done >= at * total:
                    stop.set()
            try:
                transfer.restore(result.code, home=new, progress=progress, cancel=stop, **kwargs)
                notes.append("restore stop came too late")
                break
            except InterruptedError:
                notes.append("restore stopped")
        got = transfer.restore(result.code, home=new, **kwargs)
        if list(added.values()) != keyring:
            problems.append(f"keyring: sent {len(keyring)}, added {len(added)}")
        if "keyring" in got:
            problems.append("keyring left in the returned manifest")
        check(old, new, before, home, problems, places, touched)
        if not problems and rng.random() < 0.3:
            after = snapshot(new)
            again = transfer.restore(result.code, home=new, **kwargs)
            notes.append("restored twice")
            if snapshot(new) != after:
                problems.append("restoring a second time changed the files")
            if again["restored_files"]:
                problems.append(f"second restore restored {again['restored_files']} files")
        files = sum(1 for p in home.files)
        notes.append(f"{files} files")
    except Exception:
        problems.append("CRASH " + traceback.format_exc())
    finally:
        patch.undo()
        os.environ.pop("XDG_CONFIG_HOME", None)
        if home is not None:
            home.close()
        if isinstance(nas, remote.Remote):
            nas.close()
        if proc is not None:
            proc.terminate()
            proc.wait()
    ok = not problems
    if ok:
        wipe(work)
    return ok, ", ".join(notes), problems


def failed_report(work: Path, seed: int, notes: str, problems: list, rerun: str) -> None:
    """Everything about a failed round, beside the folders it kept, for someone to look at later."""
    work.mkdir(parents=True, exist_ok=True)
    (work / "FAILED.txt").write_text(
        f"{time.strftime('%Y-%m-%d %H:%M:%S')} seed {seed}\n{notes}\n\nRun it again:\n  {rerun}\n\n"
        f"{len(problems)} problems:\n" + "\n".join(problems) + "\n")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--minutes", type=float, default=0)
    ap.add_argument("--rounds", type=int, default=0)
    ap.add_argument("--seed", type=int, default=None)
    ap.add_argument("--no-app", action="store_true", help="only send with Cherry OS's Cherry Move")
    ap.add_argument("--dir", default=str(Path(__file__).resolve().parent.parent / "build" / "soak"))
    args = ap.parse_args()
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(143))  # so each round's cleanup still runs
    base = Path(args.dir).resolve()
    base.mkdir(parents=True, exist_ok=True)
    global APP
    if os.environ.get("CHERRY_MOVE_BIN"):
        APP = Path(os.environ["CHERRY_MOVE_BIN"]).resolve()
    elif shutil.which("go") and not args.no_app:
        APP = base / "cherry-move-app"
        subprocess.run(["go", "build", "-o", str(APP), "."], cwd=Path(__file__).resolve().parent.parent / "move",
                       check=True)
    seed = args.seed if args.seed is not None else random.SystemRandom().randrange(10**9)
    end = time.monotonic() + args.minutes * 60 if args.minutes else None
    rounds = passed = 0
    real_home = os.environ.get("HOME")
    while True:
        if args.rounds and rounds >= args.rounds or end and time.monotonic() > end:
            break
        if not args.rounds and not end and rounds:
            break
        t = time.monotonic()
        ok, notes, problems = run_round(seed, base)
        os.environ["HOME"] = real_home or ""
        rounds += 1
        passed += ok
        print(f"{time.strftime('%H:%M:%S')} round {rounds} seed {seed}: {'ok' if ok else 'FAILED'} "
              f"({time.monotonic() - t:.0f}s; {notes})", flush=True)
        for p in problems[:30]:
            print("    " + p.replace("\n", "\n    "), flush=True)
        if len(problems) > 30:
            print(f"    … and {len(problems) - 30} more", flush=True)
        if not ok:
            failed_report(base / f"round-{seed}", seed, notes, problems,
                          f"PYTHONPATH=desktop/lib python3 tests/soak.py --seed {seed} --rounds 1 --dir build/soak-rerun")
        seed = random.Random(seed).randrange(10**9)
    print(f"{passed}/{rounds} rounds passed", flush=True)
    sys.exit(0 if passed == rounds else 1)


if __name__ == "__main__":
    main()
