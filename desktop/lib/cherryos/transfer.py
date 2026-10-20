"""Cherry Move: copy your files from one computer to another (any Linux distro to Cherry OS, or between
Cherry OS versions and PCs). Windows and macOS send with the Cherry Move app in move/, in the same format.

Your files are packed into a tar stream (identical files stored once), cut into chunks, compressed (zstd)
and encrypted on the old computer (codes.py), then stored in one of:
  - USB sticks   as many as it takes; Cherry Move says up front whether one stick is big enough.
  - Your cloud   Google Drive, OneDrive or MEGA, or a NAS at home (remote.py, via rclone).
Three ways to carry the code to the new computer:
  1. USB stick   CHERRY-TRANSFER/cherry-transfer.json on the stick; Cherry OS spots it automatically.
  2. Phone       a QR code of the code, to keep in your phone's notes or photos.
  3. Code        34 letters to write down or print.
"""
from __future__ import annotations

import base64
import collections
import configparser
import datetime as dt
import filecmp
import fnmatch
import hashlib
import io
import json
import os
import platform
import random
import re
import shutil
import socket
import stat
import subprocess
import tarfile
import threading
import time
import urllib.parse
import urllib.request
import zlib
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Callable, Iterable

from . import secret_store, system_extras
from .codes import TransferKeys, keys_from_code, new_code

if TYPE_CHECKING:
    from .remote import Remote

try:
    import zstandard
except ImportError:  # other distros without python3-zstandard can still send (zlib) and mostly receive
    zstandard = None

CHUNK = 8 * 1024 * 1024
FORMAT = "cherry-move/2"
MARKER_DIR = "CHERRY-TRANSFER"
MARKER = "cherry-transfer.json"
PENDING_SYSTEM = Path("/var/lib/cherry/pending-transfer.json")
REMOTE_ROOT = "CherryMove"

# zstd levels. STRONG is several times slower, so it's only used when that's what makes the files fit.
NORMAL, STRONG = 9, 17
RAW, ZLIB, ZSTD = b"\x00", b"\x01", b"\x02"
DEDUP_MIN = 16 * 1024
# Left free on each stick for the marker, README and filesystem bookkeeping.
STICK_RESERVE = 4 * 1024 * 1024

Progress = Callable[[int, int, str], None]
# Asked for the next USB stick: (stick number, problem with the last one offered or None) -> its mount point.
StickPrompt = Callable[[int, "str | None"], Path]


class TransferError(Exception):
    pass


# ---------------------------------------------------------------- what to bring

XDG_NAMES = {
    "documents": ("DOCUMENTS", "Documents"),
    "pictures": ("PICTURES", "Pictures"),
    "music": ("MUSIC", "Music"),
    "videos": ("VIDEOS", "Videos"),
    "desktop": ("DESKTOP", "Desktop"),
    "downloads": ("DOWNLOAD", "Downloads"),
}

BROWSER_PATHS = [
    ".mozilla/firefox", ".config/google-chrome", ".config/chromium", ".config/BraveSoftware",
    ".config/microsoft-edge", ".config/vivaldi", ".var/app/org.mozilla.firefox", ".var/app/com.google.Chrome",
    ".var/app/org.chromium.Chromium", ".var/app/com.brave.Browser",
]
# Where Firefox keeps its profiles: classic, Firefox 147+ on a fresh install, Ubuntu's Snap, Flatpak.
FIREFOX_PROFILES = [".mozilla/firefox", ".config/mozilla/firefox", "snap/firefox/common/.mozilla/firefox",
                    ".var/app/org.mozilla.firefox/.mozilla/firefox", ".var/app/org.mozilla.firefox/config/mozilla/firefox"]
PERSONAL_PATHS = [".ssh", ".gnupg", ".gitconfig", ".local/share/fonts", ".fonts", ".local/share/keyrings",
                  ".bashrc", ".zshrc", ".bash_aliases", ".profile"]
# Restored under a different name, so Cherry OS's own shell setup isn't replaced.
RENAME_ON_RESTORE = {".bashrc": ".bashrc.from-old-computer", ".zshrc": ".zshrc.from-old-computer",
                     ".profile": ".profile.from-old-computer"}

# Files and folders with these names are left behind, and why. The browser-integration ones are made by the
# desktop on every computer and point at programs on the old one.
SKIP_REASONS = {
    ".cache": "cache", "__pycache__": "cache", "Trash": "trash", ".Trash": "trash", "$RECYCLE.BIN": "trash",
    "node_modules": "node", ".venv": "python",
    "System Volume Information": "windows", "desktop.ini": "windows", "Thumbs.db": "windows", ".DS_Store": "windows",
    "NativeMessagingHosts": "desktop", "native-messaging-hosts": "desktop", "plasma-browser-integration-host": "desktop",
}
SKIP_NAMES = set(SKIP_REASONS)
# Folders in the home folder left behind on purpose (* stands for one folder name), and why. Everything in them
# stays too. Caches are rebuilt by their apps; "reinstall" ones are downloaded again on the new computer from
# the list in the transfer (Ollama models, Flatpak apps, Rust toolchains…).
LEAVE_BEHIND = [
    (".local/share/Trash", "trash"), (".var/app/*/cache", "cache"),
    (".npm/_cacache", "cache"), (".npm/_logs", "cache"), (".npm/_npx", "cache"),
    (".gradle/caches", "cache"), (".gradle/wrapper/dists", "cache"), (".gradle/daemon", "cache"),
    (".m2/repository", "cache"), (".cargo/registry", "cache"), (".cargo/git", "cache"),
    (".android/cache", "cache"), (".fgfs/TerraSync", "cache"),
    (".rustup/downloads", "cache"), (".rustup/tmp", "cache"), (".rustup/toolchains", "reinstall"),
    (".ollama/models", "reinstall"), (".local/share/flatpak", "reinstall"), (".rembg", "reinstall"),
    (".arduino15/staging", "cache"), (".arduino15/packages", "reinstall"),
    (".platformio/.cache", "cache"), (".platformio/packages", "reinstall"), (".platformio/platforms", "reinstall"),
    (".local/share/Steam/steamapps/shadercache", "cache"), (".local/share/Steam/steamapps/downloading", "cache"),
    (".local/share/Steam/steamapps/common", "reinstall"),
    (".var/app/com.valvesoftware.Steam/.local/share/Steam/steamapps/shadercache", "cache"),
    (".var/app/com.valvesoftware.Steam/.local/share/Steam/steamapps/downloading", "cache"),
    (".var/app/com.valvesoftware.Steam/.local/share/Steam/steamapps/common", "reinstall"),
]
_LEAVE_BY_TOP: dict = {}
for _pattern, _reason in LEAVE_BEHIND:
    _parts = _pattern.split("/")
    _LEAVE_BY_TOP.setdefault(_parts[0], []).append((_parts, _reason))
REASONS = {
    "cache": "cache, rebuilt by the app",
    "trash": "deleted files",
    "reinstall": "downloaded again on the new computer from the list in the transfer",
    "python": "Python environment, rebuilt from its package list",
    "node": "node_modules, rebuilt from package.json",
    "windows": "Windows or macOS bookkeeping",
    "desktop": "made by the desktop on every computer",
    "sqlite": "part of a database that was copied whole",
    "network": "a network drive or other mounted folder: its files stay where they are",
}

# Network drives (a NAS share, sshfs, rclone, a phone…) mounted inside the home folder stay behind: their files
# are still out there, and one may be the very place this transfer is being written to.
NETWORK_FS = {"autofs", "cifs", "smb3", "smbfs", "nfs", "nfs4", "afs", "ceph", "glusterfs", "9p", "davfs", "fuse",
              "sshfs"}


def network_mounts(mountinfo: str | None = None) -> frozenset[str]:
    """Where network drives and FUSE filesystems are mounted on this computer."""
    if mountinfo is None:
        try:
            with open("/proc/self/mountinfo", errors="surrogateescape") as f:
                mountinfo = f.read()
        except OSError:
            return frozenset()
    found = set()
    for line in mountinfo.splitlines():
        before, _, after = line.partition(" - ")
        fields, fstype = before.split(), (after.split() or [""])[0]
        if len(fields) > 4 and (fstype in NETWORK_FS or fstype.startswith("fuse.")):
            found.add(re.sub(r"\\([0-7]{3})", lambda m: chr(int(m.group(1), 8)), fields[4]))
    return frozenset(found)


def _rebuilt_node(parent: str, home: str) -> bool:
    """A node_modules in `parent` is left behind to be rebuilt only in your own projects (a package.json beside
    it, not inside an app's hidden folder): apps' own ones (editor extensions, MCP servers, npm -g) come along."""
    if not os.path.isfile(os.path.join(parent, "package.json")):
        return False
    rel = os.path.relpath(parent, home)
    return rel.startswith("..") or not any(part.startswith(".") for part in rel.split(os.sep) if part != ".")


def leave_reason(rel: str) -> str | None:
    """Why the folder or file at `rel` (relative to the home folder) is left behind on purpose, or None."""
    parts = rel.split("/")
    for parts_rule, reason in _LEAVE_BY_TOP.get(parts[0], ()):
        if len(parts) == len(parts_rule) and all(fnmatch.fnmatchcase(p, r) for p, r in zip(parts, parts_rule)):
            return reason
    return None


def left_behind_rules(home: Path | None = None) -> Callable[[str], "str | None"]:
    """For checking a transfer: why a path (relative to `home`) somewhere inside a left-behind folder stayed."""
    mounts = network_mounts()

    def why(rel: str) -> str | None:
        parts = rel.split("/")
        for i, name in enumerate(parts):
            if home is not None and str(home / "/".join(parts[:i + 1])) in mounts:
                return "network"
            if name == "node_modules" and home is not None:
                if _rebuilt_node(str(home / "/".join(parts[:i])), str(home)):
                    return "node"
                continue
            if name in SKIP_REASONS:
                return SKIP_REASONS[name]
            if reason := leave_reason("/".join(parts[:i + 1])):
                return reason
            if home is not None and i < len(parts) - 1 and (home / "/".join(parts[:i + 1]) / "pyvenv.cfg").is_file():
                return "python"
        return None
    return why


@dataclass
class Category:
    id: str
    label: str
    paths: list  # (archive prefix, absolute path)
    default: bool = True
    size: int = 0
    files: int = 0
    skip: frozenset = frozenset()  # folders inside `paths` that another category brings
    scan: Scan | None = field(default=None, repr=False, compare=False)  # from scan_category(), for make_plan()


@dataclass
class Scan:
    """One walk through a category: its size, and what make_plan() needs, so it needn't walk it again."""
    size: int = 0        # regular files only (Category.size counts symlinks too)
    files: int = 0
    types: dict = field(default_factory=dict)  # extension -> [bytes, files, sample paths]
    envs: list = field(default_factory=list)
    node: list = field(default_factory=list)
    # (archive prefix, folder, [(path, size, mtime_ns, (dev, inode) if hard-linked)]) for each of the category's
    # paths: the files that could be duplicates, in the order send() packs them
    items: list = field(default_factory=list)


def xdg_dir(name: str, fallback: str, home: Path) -> Path:
    env = None
    if home.resolve() != Path.home().resolve():  # someone else's folders: read its settings, not ours
        env = {k: v for k, v in os.environ.items() if k != "XDG_CONFIG_HOME"} | {"HOME": str(home)}
    try:
        out = subprocess.run(["xdg-user-dir", name], capture_output=True, text=True, timeout=5,
                             env=env).stdout.strip()
        if out and Path(out).resolve() != home.resolve():
            return Path(out)
    except (OSError, subprocess.SubprocessError):
        pass
    return home / fallback


def measure(path: Path, home: Path | None = None, skip: frozenset = frozenset()) -> tuple[int, int]:
    """Size and number of files send() would bring from `path`."""
    size = files = 0
    for p, _arc in _walk(path, "", home, skip):
        try:
            st = os.lstat(p)
        except OSError:
            continue
        if stat.S_ISREG(st.st_mode) or stat.S_ISLNK(st.st_mode):
            size += st.st_size
            files += 1
    return size, files


def scan_category(cat: Category, home: Path | None = None, counted: Callable[[int], None] | None = None,
                  cancel: threading.Event | None = None) -> Scan:
    """Walks `cat` once: sets its size and file count, and keeps cat.scan for make_plan(). `counted(files)`
    hears how many files have been counted so far, now and then."""
    home = home or Path.home()
    scan = Scan()
    rng = random.Random(0)
    size = files = 0

    def left(path: str, reason: str) -> None:
        if reason == "python" and not os.path.islink(path):
            scan.envs.append(path)
        elif reason == "node":
            scan.node.append(os.path.dirname(path))

    for prefix, base in cat.paths:
        found = []
        for p, arc in _walk(base, prefix, home, cat.skip, left):
            if cancel is not None and cancel.is_set():
                raise InterruptedError("cancelled")
            try:
                st = os.lstat(p)
            except OSError:
                continue
            mode = st.st_mode
            if stat.S_ISLNK(mode):
                size += st.st_size
                files += 1
                continue
            if not stat.S_ISREG(mode):
                continue
            size += st.st_size
            files += 1
            if counted is not None and files % 5000 == 0:
                counted(files)
            scan.size += st.st_size
            scan.files += 1
            kind = scan.types.setdefault(_ext(arc), [0, 0, []])
            kind[0] += st.st_size
            kind[1] += 1
            if len(kind[2]) < 8:
                kind[2].append(p)
            elif (j := rng.randrange(kind[1])) < 8:
                kind[2][j] = p
            if st.st_nlink > 1 or st.st_size >= DEDUP_MIN:
                found.append((p, st.st_size, st.st_mtime_ns, (st.st_dev, st.st_ino) if st.st_nlink > 1 else None))
        scan.items.append((prefix, str(base), found))
    cat.size, cat.files, cat.scan = size, files, scan
    return scan


def categories(home: Path | None = None, measure_sizes: bool = True) -> list[Category]:
    home = home or Path.home()
    cats: list[Category] = []
    used: set[Path] = set()
    mounts = network_mounts()
    for cid, (xdg, fallback) in XDG_NAMES.items():
        p = xdg_dir(xdg, fallback, home)
        used.add(p)
        cats.append(Category(cid, fallback, [(f"xdg/{xdg}", p)] if p.exists() and str(p) not in mounts else []))
    browsers = []
    for rel in BROWSER_PATHS:
        p = home / rel
        if rel == ".mozilla/firefox":
            p = next((home / r for r in FIREFOX_PROFILES if (home / r / "profiles.ini").exists()), p)
        elif rel == ".config/chromium" and not p.exists():
            p = home / "snap/chromium/common/chromium"
        if p.exists():
            browsers.append((f"home/{rel}", p))
    cats.append(Category("browsers", "Browser bookmarks, passwords and history", browsers))
    personal = [(f"home/{rel}", home / rel) for rel in PERSONAL_PATHS if (home / rel).exists()]
    cats.append(Category("personal", "Keys, fonts and shell settings", personal))
    taken = frozenset(str(p) for _, p in browsers + personal) | frozenset(str(p) for p in used)
    apps, others = [], []
    try:
        for child in sorted(home.iterdir()):
            skipped = SKIP_REASONS.get(child.name)
            if skipped == "node" and not _rebuilt_node(str(home), str(home)):
                skipped = None
            if str(child) in taken or skipped or leave_reason(child.name) or str(child) in mounts:
                continue
            if child.name.startswith(".") or child.name == "snap":
                apps.append((f"home/{child.name}", child))
            else:
                others.append((f"home/{child.name}", child))
    except OSError:
        pass
    cats.append(Category("apps", "App settings, sign-ins, game saves and servers", apps, skip=taken))
    cats.append(Category("other", "Other files and folders in your home folder", others, skip=taken))
    cats = [c for c in cats if c.paths]
    if measure_sizes:
        for c in cats:
            scan_category(c, home)
    return cats


def installed_apps() -> dict:
    """Lists apps so the new computer can offer to reinstall them."""
    def run(cmd):
        try:
            return subprocess.run(cmd, capture_output=True, text=True, timeout=60).stdout.split()
        except (OSError, subprocess.SubprocessError):
            return []
    apps = {"flatpak": run(["flatpak", "list", "--app", "--columns=application"]), "snap": [], "cherry": [], "apt": []}
    try:
        out = subprocess.run(["snap", "list"], capture_output=True, text=True, timeout=30).stdout.splitlines()[1:]
        apps["snap"] = [line.split()[0] for line in out if line.strip()]
    except (OSError, subprocess.SubprocessError):
        pass
    apps["apt"] = run(["apt-mark", "showmanual"])
    db = Path("/var/lib/cherry/installed")
    if db.is_dir():
        apps["cherry"] = sorted(p.stem for p in db.glob("*.json"))
    return apps


# Package sources from the distro itself (not brought: the new computer has its own).
DISTRO_SOURCES = re.compile(r"(^|[./])(ubuntu\.com|debian\.org|launchpad(content)?\.net|linuxmint\.com|"
                            r"pop-os\.org|elementary\.io|kde\.org/neon|cherry-os)(/|:|$)", re.I)
KEY_DIRS = ("/etc/apt/keyrings/", "/usr/share/keyrings/", "/etc/apt/trusted.gpg.d/")


def apt_sources() -> list[dict]:
    """Package sources added for apps (Cursor, Node.js, Tailscale…) and their signing keys, so the new computer
    can install the same apps from the same places."""
    out = []
    folder = Path("/etc/apt/sources.list.d")
    try:
        files = sorted(p for p in folder.iterdir() if p.suffix in (".list", ".sources") and p.is_file())
    except OSError:
        return out
    for f in files:
        try:
            text = f.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        lines = [ln for ln in text.splitlines() if ln.strip() and not ln.lstrip().startswith("#")]
        uris = re.findall(r"(?:https?|file|ftp)://\S+", "\n".join(lines))
        if not uris or any(DISTRO_SOURCES.search(u) for u in uris):
            continue
        keys = {}
        for path in re.findall(r"signed-by[=:]\s*(/[^\s\],]+)", text, re.I):
            if path.startswith(KEY_DIRS):
                try:
                    keys[path] = base64.b64encode(Path(path).read_bytes()).decode()
                except OSError:
                    pass
        out.append({"name": f.name, "text": text, "keys": keys})
    return out


def ollama_models(home: Path) -> list[str]:
    """Names of the Ollama models downloaded here (from Ollama's own list on disk, so Ollama needn't be running)."""
    models = home / ".ollama/models/manifests"
    names = []
    for m in sorted(models.glob("*/*/*/*")):
        if not m.is_file():
            continue
        registry, namespace, model, tag = m.relative_to(models).parts
        if registry == "registry.ollama.ai":
            names.append(f"{model}:{tag}" if namespace == "library" else f"{namespace}/{model}:{tag}")
        else:
            names.append(f"{registry}/{namespace}/{model}:{tag}")
    return names


def python_env(path: str) -> dict:
    """What's installed in a Python environment: its Python version and pip requirements."""
    env = {"path": path, "python": "", "packages": []}
    try:
        for line in Path(path, "pyvenv.cfg").read_text().splitlines():
            key, _, value = line.partition("=")
            if key.strip() in ("version", "version_info"):
                env["python"] = ".".join(value.strip().split(".")[:2])
    except OSError:
        pass
    for meta in sorted(Path(path).glob("lib/python*/site-packages/*.dist-info")):
        name, _, version = meta.name[:-len(".dist-info")].partition("-")
        if not version or name.lower() == "pip":
            continue
        try:
            direct = json.loads((meta / "direct_url.json").read_text())
        except (OSError, ValueError):
            direct = None
        if direct and direct.get("dir_info", {}).get("editable"):
            local = urllib.parse.unquote(urllib.parse.urlparse(direct.get("url", "")).path)
            env["packages"].append(f"-e {local}")
        else:
            env["packages"].append(f"{name}=={version}")
    return env


def reinstall_list(plan: Plan, home: Path | None = None) -> dict:
    """Everything left behind on purpose that the new computer can download or rebuild again."""
    home = home or Path.home()
    user = os.environ.get("USER", "") or home.name
    info = installed_apps()
    info["home"] = str(home)
    info["apt_sources"] = apt_sources()
    info["ollama"] = ollama_models(home)
    info["rustup"] = sorted(p.name for p in (home / ".rustup/toolchains").glob("*") if p.is_dir())
    info["venvs"] = [python_env(p) for p in plan.envs]
    node = set(plan.node)
    if (home / "package.json").is_file() and (home / "node_modules").is_dir():
        node.add(str(home))  # npm install run in the home folder itself (its node_modules isn't walked)
    info["node"] = sorted(node)
    info["arduino"] = sorted(f"{p.parts[-4]}:{p.parts[-2]}@{p.parts[-1]}"
                             for p in (home / ".arduino15/packages").glob("*/hardware/*/*") if p.is_dir())
    info["platformio"] = sorted(p.name for p in (home / ".platformio/platforms").glob("*") if p.is_dir())
    info["linger"] = Path(f"/var/lib/systemd/linger/{user}").exists()
    try:
        import grp
        info["groups"] = sorted(g.gr_name for g in grp.getgrall() if user in g.gr_mem)
    except (ImportError, KeyError):
        info["groups"] = []
    return info


EXTRAS_HOME = ".local/share/cherry/old-computer"  # where the files system_extras gathers arrive, in the home folder


def gather_extras(folder: Path) -> tuple[dict | None, list[Path]]:
    """system_extras.collect() into `folder` (emptied first): what it found, and the files to carry."""
    shutil.rmtree(folder, ignore_errors=True)
    try:
        found = system_extras.collect(str(folder))
    except Exception:  # these are extras: nothing in them may stop the move
        shutil.rmtree(folder, ignore_errors=True)
        return None, []
    return found, sorted(p for p in folder.rglob("*") if p.is_file() and not p.is_symlink())


def source_os() -> str:
    try:
        for line in Path("/etc/os-release").read_text().splitlines():
            if line.startswith("PRETTY_NAME="):
                return line.split("=", 1)[1].strip('"')
    except OSError:
        pass
    return platform.system()


# ---------------------------------------------------------------- compression

def pack(plain: bytes, level: int = NORMAL) -> bytes:
    """Compresses one chunk. The first byte says how, and files that don't shrink (photos, videos) are kept as is."""
    if zstandard is not None:
        body = ZSTD + zstandard.ZstdCompressor(level=level).compress(plain)
    else:
        body = ZLIB + zlib.compress(plain, 6 if level <= NORMAL else 9)
    return body if len(body) < len(plain) * 0.97 else RAW + plain


def unpack(body: bytes) -> bytes:
    kind, data = body[:1], body[1:]
    if kind == RAW:
        return data
    if kind == ZLIB:
        return zlib.decompress(data)
    if kind == ZSTD:
        if zstandard is None:
            raise TransferError("These files were packed with zstd. Install python3-zstandard "
                                "(python-zstandard on Fedora and Arch) and try again.")
        return zstandard.ZstdDecompressor().decompress(data)
    raise TransferError("This transfer was made by a newer Cherry Move. Update Cherry Move and try again.")


# ---------------------------------------------------------------- planning: duplicates and size

@dataclass
class Plan:
    """What send() will store: totals, identical files (stored once) and per-type samples for estimate()."""
    cats: list
    size: int = 0        # bytes stored, each set of identical files counted once
    files: int = 0       # files restored, duplicates included
    duplicates: int = 0
    saved: int = 0       # bytes not stored because they're duplicates
    same: dict = field(default_factory=dict)   # path -> (arcname of the first copy, size, mtime_ns)
    first: dict = field(default_factory=dict)  # arcname of a first copy -> (size, mtime_ns)
    types: dict = field(default_factory=dict)  # extension -> [bytes stored, files, sample paths]
    envs: list = field(default_factory=list)   # Python environments left behind, rebuilt on the new computer
    node: list = field(default_factory=list)   # folders whose node_modules is rebuilt there

    @property
    def raw_size(self) -> int:
        return self.size + self.saved


def _ext(name: str) -> str:
    suffix = os.path.splitext(name)[1].lower()
    return suffix if 1 < len(suffix) <= 8 else ""


HEAD = 64 * 1024
# (path, size, mtime_ns, whole file) -> digest, so working the size out again (after going back to pick other
# things) doesn't read every file again
_digests: dict[tuple, bytes] = {}


def _digest(path: str, size: int, mtime: int, whole: bool, buf: bytearray) -> bytes | None:
    key = (path, size, mtime, whole)
    if (known := _digests.get(key)) is not None:
        return known
    h = hashlib.sha256()
    try:
        with open(path, "rb", buffering=0) as f:
            if whole:
                view = memoryview(buf)
                while n := f.readinto(buf):
                    h.update(view[:n])
            else:
                h.update(f.read(HEAD))
    except OSError:
        return None
    _digests[key] = d = h.digest()
    return d


def _rotational(path: Path) -> bool:
    """Whether `path` is on a spinning disk, where reading several files at once is slower, not faster."""
    try:
        dev = os.stat(path).st_dev
        block = f"/sys/dev/block/{os.major(dev)}:{os.minor(dev)}"
        for flag in (f"{block}/queue/rotational", f"{block}/../queue/rotational"):
            if os.path.exists(flag):
                return Path(flag).read_text().strip() == "1"
    except OSError:
        pass
    return False


def _digest_all(items: list, whole: bool, workers: int, cancel: threading.Event | None,
                tick: Callable[[int], None]) -> list:
    """_digest() of each (path, size, mtime_ns), several files at once: hashing runs outside Python's lock, so
    it uses several cores. `tick(bytes)` after each file."""
    out: list = [None] * len(items)
    todo = iter(range(len(items)))
    lock = threading.Lock()

    def work() -> None:
        buf = bytearray(1024 * 1024)
        while cancel is None or not cancel.is_set():
            with lock:
                i = next(todo, None)
            if i is None:
                return
            path, size, mtime = items[i]
            out[i] = _digest(path, size, mtime, whole, buf)
            tick(size if whole else min(size, HEAD))

    threads = [threading.Thread(target=work, daemon=True) for _ in range(max(1, min(workers, len(items))))]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    if cancel is not None and cancel.is_set():
        raise InterruptedError("cancelled")
    return out


def make_plan(cats: Iterable[Category], progress: Progress | None = None,
              cancel: threading.Event | None = None, home: Path | None = None) -> Plan:
    """Finds identical files in the chosen categories (hard links, or same size, then same first 64 KB, then
    same contents), so each is stored once and copied back on the new computer. Categories already walked by
    scan_category() aren't walked again."""
    home = home or Path.home()
    plan = Plan(list(cats))
    rng = random.Random(0)
    by_size: dict[int, list] = {}
    inodes: dict[tuple, tuple] = {}
    order = 0

    def duplicate(path: str, arc: str, size: int, mtime: int, first_arc: str, first_mtime: int) -> None:
        plan.same[path] = (first_arc, size, mtime)
        plan.first[first_arc] = (size, first_mtime)
        plan.duplicates += 1
        plan.saved += size
        plan.size -= size
        plan.types[_ext(arc)][0] -= size

    for cat in plan.cats:
        scan = cat.scan if cat.scan is not None else scan_category(cat, home, cancel=cancel)
        plan.files += scan.files
        plan.size += scan.size
        plan.envs += scan.envs
        plan.node += scan.node
        for ext, (nbytes, n, samples) in scan.types.items():
            kind = plan.types.setdefault(ext, [0, 0, []])
            kind[0] += nbytes
            kind[1] += n
            pool = kind[2] + samples
            kind[2] = rng.sample(pool, 8) if len(pool) > 8 else pool
        for prefix, base, found in scan.items:
            cut = len(base)
            for path, size, mtime, inode in found:
                arc = prefix + path[cut:]
                if inode is not None:
                    seen = inodes.get(inode)
                    if seen is not None:
                        duplicate(path, arc, size, mtime, seen[0], seen[1])
                        continue
                    inodes[inode] = (arc, mtime)
                if size >= DEDUP_MIN:
                    by_size.setdefault(size, []).append((order, path, arc, mtime))
                    order += 1

    groups = [(size, g) for size, g in by_size.items() if len(g) > 1]
    heads = sum(min(size, HEAD) * len(g) for size, g in groups)
    total = [heads + sum(size * len(g) for size, g in groups if size > HEAD) or 1]
    done = [0]
    lock = threading.Lock()

    def tick(nbytes: int) -> None:
        with lock:
            done[0] += nbytes
        if progress:
            progress(min(done[0], total[0]), total[0], "Looking for duplicate files")

    workers = 1 if _rotational(home) else min(8, os.cpu_count() or 1)
    firsts = _digest_all([(p, size, m) for size, g in groups for _o, p, _a, m in g], False, workers, cancel, tick)
    same_sets, whole = [], []
    i = 0
    for size, group in groups:
        buckets: dict[bytes, list] = {}
        for item in group:
            if (d := firsts[i]) is not None:
                buckets.setdefault(d, []).append(item)
            i += 1
        for bucket in buckets.values():
            if len(bucket) > 1:
                (whole if size > HEAD else same_sets).append((size, bucket))
    total[0] = heads + sum(size * len(b) for size, b in whole) or 1
    fulls = _digest_all([(p, size, m) for size, b in whole for _o, p, _a, m in b], True, workers, cancel, tick)
    i = 0
    for size, bucket in whole:
        full: dict[bytes, list] = {}
        for item in bucket:
            if (d := fulls[i]) is not None:
                full.setdefault(d, []).append(item)
            i += 1
        same_sets += [(size, b) for b in full.values() if len(b) > 1]
    for size, items in same_sets:
        items.sort()
        _o, _p, first_arc, first_mtime = items[0]
        for _o, path, arc, mtime in items[1:]:
            duplicate(path, arc, size, mtime, first_arc, first_mtime)
    return plan


def _sample(path: str, piece: int = 512 * 1024) -> bytes:
    try:
        with open(path, "rb") as f:
            data = f.read(piece)
            size = os.fstat(f.fileno()).st_size
            if size > piece * 2:
                f.seek(size // 2)
                data += f.read(piece)
            return data
    except OSError:
        return b""


def estimate(plan: Plan, levels: Iterable[int] = (NORMAL, STRONG), budget: int = 48 * 1024 * 1024) -> dict:
    """Roughly how many bytes send() will store at each zstd level, by compressing samples of each kind of file.
    Errs on the large side. Already-compressed files (photos, videos, archives) barely shrink."""
    total = max(plan.size, 1)
    pieces: list[tuple[str, bytes]] = []
    used = 0
    for ext, (nbytes, _n, samples) in sorted(plan.types.items(), key=lambda kv: -kv[1][0]):
        if used >= budget:
            break
        if nbytes < total * 0.001:
            continue
        for p in samples:
            if data := _sample(p):
                pieces.append((ext, data))
                used += len(data)
    result = {}
    with ThreadPoolExecutor(max_workers=min(4, os.cpu_count() or 1)) as pool:
        for level in levels:
            seen: dict[str, list] = {}
            for (ext, data), packed in zip(pieces, pool.map(lambda pd, lv=level: len(pack(pd[1], lv)), pieces)):
                s = seen.setdefault(ext, [0, 0])
                s[0] += len(data)
                s[1] += packed
            raw = sum(s[0] for s in seen.values())
            overall = sum(s[1] for s in seen.values()) / raw if raw else 1.0
            stored = 0.0
            for ext, (nbytes, _n, _s) in plan.types.items():
                s = seen.get(ext)
                stored += nbytes * (s[1] / s[0] if s and s[0] else overall)
            stored += plan.files * 300 + (stored / CHUNK + 1) * 64
            result[level] = int(stored * 1.02) + 16 * 1024 * 1024
    return result


@dataclass
class Fit:
    level: int
    need: int    # estimated bytes stored
    sticks: int  # sticks this size it takes


def fit(sizes: dict, free: int) -> Fit:
    """Picks a compression level for a stick with `free` bytes: normal if that fits, stronger if that fits
    (or saves a stick), and how many sticks of this size it would take."""
    room = max(free - STICK_RESERVE, 1)

    def sticks(level):
        return max(1, -(-int(sizes[level] * 1.01) // room))
    normal, strong = sticks(NORMAL), sticks(STRONG)
    level = NORMAL if normal <= strong else STRONG
    return Fit(level, sizes[level], min(normal, strong))


# ---------------------------------------------------------------- where chunks go

def chunk_name(index: int) -> str:
    """data/00012/00012345.bin: no more than 1000 files per folder (FAT32 slows to a crawl with many more)."""
    return f"data/{index // 1000:05d}/{index:08d}.bin"


def _retry(fn, attempts: int = 4):
    for n in range(attempts):
        try:
            return fn()
        except (OSError, TransferError):
            if n == attempts - 1:
                raise
            time.sleep(2 ** n)


class Sink:
    def put(self, index: int, blob: bytes) -> None: ...
    def finish(self, chunks: int) -> None: ...
    def abandon(self) -> None: ...


class UsbSink(Sink):
    """Writes chunks in order, filling one stick after another; `next_stick` is asked for each new one."""

    def __init__(self, drive: Path, marker: dict, next_stick: StickPrompt | None = None):
        self.marker, self.next_stick = marker, next_stick
        self.waiting: dict[int, bytes] = {}
        self.expected = 0
        self.lock = threading.Lock()
        self.part = 0
        self._begin(Path(drive), 0)

    def _begin(self, drive: Path, first: int) -> None:
        clear_transfer(drive)
        (drive / MARKER_DIR).mkdir(parents=True, exist_ok=True)
        self.drive, self.first = drive, first
        self.part += 1
        self.folders: set = set()

    def put(self, index: int, blob: bytes) -> None:
        with self.lock:
            self.waiting[index] = blob
            while self.expected in self.waiting:
                self._write(self.expected, self.waiting.pop(self.expected))
                self.expected += 1

    def _write(self, index: int, blob: bytes) -> None:
        path = self.drive / MARKER_DIR / chunk_name(index)
        if free_space(self.drive) < len(blob) + STICK_RESERVE and index > self.first:
            self._next(index)
            path = self.drive / MARKER_DIR / chunk_name(index)
        if path.parent not in self.folders:
            path.parent.mkdir(parents=True, exist_ok=True)
            self.folders.add(path.parent)
        tmp = path.with_suffix(".tmp")
        try:
            tmp.write_bytes(blob)
        except OSError as err:
            tmp.unlink(missing_ok=True)
            if err.errno != 28 or index == self.first:  # ENOSPC
                raise
            self._next(index)
            return self._write(index, blob)
        tmp.rename(path)

    def _next(self, index: int) -> None:
        write_marker(self.drive, {**self.marker, "part": self.part, "first": self.first, "final": False})
        eject(self.drive)
        if self.next_stick is None:
            raise TransferError("The USB stick is full.")
        problem = None
        while True:
            drive = Path(self.next_stick(self.part + 1, problem))
            marker = read_marker(drive)
            if marker and marker.get("code") == self.marker["code"]:
                problem = f"That's stick {marker.get('part', 1)} again. Plug in another stick."
            elif usable_space(drive) < CHUNK * 2 + STICK_RESERVE:
                problem = "That stick is full. Plug in another one, or delete some files from it first."
            else:
                break
        self._begin(drive, index)

    def finish(self, chunks: int) -> None:
        write_marker(self.drive, {**self.marker, "part": self.part, "first": self.first, "final": True,
                                  "chunks": chunks, "parts": self.part})


class RemoteSink(Sink):
    """Your own cloud drive or NAS (remote.py): CherryMove/<transfer id>/data/… and info.json once complete."""

    def __init__(self, remote: "Remote", keys: TransferKeys, info: dict | None = None):
        self.remote, self.base = remote, f"{REMOTE_ROOT}/{keys.id}"
        self.info = info or {}
        self.folders: set = set()
        self.lock = threading.Lock()

    def put(self, index: int, blob: bytes) -> None:
        name = f"{self.base}/{chunk_name(index)}"
        folder = name.rsplit("/", 1)[0]
        with self.lock:
            if folder not in self.folders:
                _retry(lambda: self.remote.mkdirs(folder))
                self.folders.add(folder)
        _retry(lambda: self.remote.put(name, blob))

    def finish(self, chunks: int) -> None:
        info = {"format": FORMAT, "chunks": chunks, "created": now_iso(),
                **{k: self.info[k] for k in INFO_KEYS if k in self.info}}
        _retry(lambda: self.remote.put(f"{self.base}/info.json", json.dumps(info).encode()))

    def abandon(self) -> None:
        try:
            self.remote.delete(self.base)
        except (OSError, TransferError):
            pass


class ChunkWriter(io.RawIOBase):
    """File-like object for tarfile: buffers, compresses, encrypts and stores in chunks, several in parallel."""

    def __init__(self, keys: TransferKeys, sink: Sink, level: int = NORMAL, workers: int | None = None):
        cpus = os.cpu_count() or 2
        workers = workers or max(3, min(8 if level <= NORMAL else 4, cpus))
        self.keys, self.sink, self.level = keys, sink, level
        self.buf = bytearray()
        self.index = 1  # chunk 0 is the manifest
        self.pool = ThreadPoolExecutor(max_workers=workers)
        self.slots = threading.Semaphore(workers * 2)
        self.futures = []
        self.written = 0

    def writable(self) -> bool:
        return True

    def write(self, data) -> int:
        self.buf += data
        self.written += len(data)
        while len(self.buf) >= CHUNK:
            self._flush(bytes(self.buf[:CHUNK]))
            del self.buf[:CHUNK]
        return len(data)

    def _flush(self, plain: bytes) -> None:
        index = self.index
        self.index += 1
        self.slots.acquire()

        def job():
            try:
                self.sink.put(index, self.keys.seal(index, pack(plain, self.level)))
            finally:
                self.slots.release()
        self.futures.append(self.pool.submit(job))
        self._raise_errors()

    def _raise_errors(self) -> None:
        for f in [f for f in self.futures if f.done()]:
            f.result()
            self.futures.remove(f)

    def close_stream(self) -> int:
        """Stores what's left; returns the number of chunks including the manifest."""
        if self.buf:
            self._flush(bytes(self.buf))
            self.buf.clear()
        for f in self.futures:
            f.result()
        self.pool.shutdown()
        return self.index

    def abandon(self) -> None:
        self.pool.shutdown(wait=True, cancel_futures=True)


def open_chunk(keys: TransferKeys, index: int, blob: bytes) -> bytes:
    return unpack(keys.open(index, blob))


# ---------------------------------------------------------------- send

@dataclass
class SendResult:
    keys: TransferKeys
    storage: str
    size: int
    files: int
    chunks: int
    label: str = ""
    sticks: int = 0
    remote: dict | None = None
    skipped: list = field(default_factory=list)  # files that couldn't be read (another user's, or gone)

    @property
    def code(self) -> str:
        return self.keys.code


def default_label() -> str:
    return f"{socket.gethostname()} — {dt.date.today():%-d %b %Y}"


# What info.json says about a move, so the new computer can list the moves kept somewhere before the code is
# typed. Never the code.
INFO_KEYS = ("label", "hostname", "source_os", "size", "files", "created")


def marker_data(code: str, label: str, storage: str, size: int, files: int, remote: dict | None = None) -> dict:
    """What a stick says about a transfer. Never holds passwords or sign-in tokens."""
    return {
        "format": FORMAT, "code": code, "label": label, "storage": storage, "remote": remote, "expires": None,
        "created": now_iso(), "hostname": socket.gethostname(),
        "source_os": source_os(), "size": size, "files": files,
    }


def send(cats: Iterable[Category], storage: str = "usb", drive: Path | None = None,
         progress: Progress | None = None, label: str | None = None,
         cancel: threading.Event | None = None, plan: Plan | None = None, level: int = NORMAL,
         remote: "Remote | None" = None, next_stick: StickPrompt | None = None) -> SendResult:
    """Packs the chosen categories and stores them:
      storage="usb"     on the stick at `drive`, then the next stick `next_stick` returns, and so on
      storage="remote"  in `remote` (your cloud drive or NAS); a stick at `drive` (optional) gets just the code,
                        so the new computer finds it"""
    plan = plan or make_plan(cats, cancel=cancel)
    keys = new_code()
    label = label or default_label()
    descriptor = remote.descriptor() if remote is not None else None
    base = marker_data(keys.code, label, storage, plan.raw_size, plan.files, remote=descriptor)
    if storage == "usb":
        if drive is None:
            raise TransferError("No USB stick chosen.")
        sink: Sink = UsbSink(drive, base, next_stick)
    elif storage == "remote" and remote is not None:
        sink = RemoteSink(remote, keys, info=base)
    else:
        raise TransferError("Choose where your files should go.")
    home = Path.home()
    apps = reinstall_list(plan, home)
    extras_dir = home / ".cache/cherry-move/extras"
    extras: list[Path] = []
    if any(c.id == "apps" for c in plan.cats):
        if progress:
            progress(0, plan.raw_size or 1, "Your apps, system services and servers")
        apps["extras"], extras = gather_extras(extras_dir)
    extras_size = sum(p.stat().st_size for p in extras)
    manifest = {
        "format": FORMAT, "label": label, "created": base["created"], "hostname": socket.gethostname(),
        "user": os.environ.get("USER", ""), "home": str(home), "source_os": source_os(), "size": plan.size,
        "raw_size": plan.raw_size + extras_size, "files": plan.files + len(extras),
        "categories": [c.id for c in plan.cats], "apps": apps, "same": sorted(plan.first),
    }
    if {"browsers", "personal", "apps"} & {c.id for c in plan.cats}:
        manifest["keyring"] = secret_store.read_all()
    writer = ChunkWriter(keys, sink, level)
    total = plan.raw_size + extras_size or 1
    done = 0
    stored_ok: dict[str, bool] = {}
    snapshots: set[str] = set()
    skipped: list[str] = []
    snap_dir = home / ".cache/cherry-move"
    try:
        sink.put(0, keys.seal(0, pack(json.dumps(manifest).encode(), level)))
        with _StreamTar.open(fileobj=writer, mode="w|", format=tarfile.PAX_FORMAT) as tar:
            for cat in plan.cats:
                for prefix, base_path in cat.paths:
                    for p, arc in _walk(base_path, prefix, home, cat.skip):
                        if cancel is not None and cancel.is_set():
                            raise InterruptedError("cancelled")
                        try:
                            st = os.lstat(p)
                            stem, dash, end = p.rpartition("-")
                            if dash and end in ("wal", "shm", "journal") and stem in snapshots:
                                done += st.st_size
                                continue
                            same = plan.same.get(p) if stat.S_ISREG(st.st_mode) else None
                            if stat.S_ISREG(st.st_mode) and _live_sqlite(p):
                                snap = _snapshot_sqlite(p, snap_dir, st.st_size)
                                if snap is not None:
                                    try:
                                        info = tar.gettarinfo(p, arcname=arc)
                                        info.size = snap.stat().st_size
                                        with open(snap, "rb") as f:
                                            tar.addfile(info, f)
                                    finally:
                                        snap.unlink(missing_ok=True)
                                    snapshots.add(p)
                                    stored_ok[arc] = False
                                    done += st.st_size
                                    continue
                            if same and same[1:] == (st.st_size, st.st_mtime_ns) and stored_ok.get(same[0]):
                                info = tar.gettarinfo(p, arcname=arc)
                                info.type, info.linkname, info.size = tarfile.LNKTYPE, same[0], 0
                                tar.addfile(info)
                            elif stat.S_ISREG(st.st_mode):
                                info = tar.gettarinfo(p, arcname=arc)
                                with open(p, "rb") as f:
                                    data = _Exactly(f, info.size)
                                    tar.addfile(info, data)
                                if data.short:
                                    tar.addfile(_incomplete(arc))
                                    skipped.append(p)
                                if arc in plan.first:  # its copies can point at it unless it changed while read
                                    try:
                                        after = os.lstat(p)
                                    except OSError:
                                        after = None
                                    stored_ok[arc] = not data.short and after is not None and plan.first[arc] == (
                                        st.st_size, st.st_mtime_ns) == (after.st_size, after.st_mtime_ns)
                            else:
                                tar.add(p, arcname=arc, recursive=False)
                        except (OSError, tarfile.TarError):
                            if not os.path.isdir(p):
                                skipped.append(p)
                            continue
                        if stat.S_ISREG(st.st_mode):
                            done += st.st_size
                            if progress:
                                progress(min(done, total), total, arc.split("/", 2)[-1])
            for path in extras:
                if cancel is not None and cancel.is_set():
                    raise InterruptedError("cancelled")
                tar.add(str(path), arcname=f"home/{EXTRAS_HOME}/{path.relative_to(extras_dir)}", recursive=False)
                done += path.stat().st_size
                if progress:
                    progress(min(done, total), total, path.name)
        chunks = writer.close_stream()
        sink.finish(chunks)
    except BaseException:
        writer.abandon()
        sink.abandon()
        raise
    finally:
        shutil.rmtree(extras_dir, ignore_errors=True)
    result = SendResult(keys, storage, plan.raw_size, plan.files - len(skipped), chunks - 1, label,
                        sink.part if isinstance(sink, UsbSink) else 0, descriptor, skipped)
    if drive is not None and storage != "usb":
        clear_transfer(drive)
        write_marker(drive, base)
    return result


class _Exactly:
    """Reads exactly `size` bytes from a file that may change while it's packed: a file that shrinks or can't
    be read any more is padded with zeros, as the tar stream has already promised `size` bytes."""
    def __init__(self, f, size: int):
        self.f, self.left, self.short = f, size, False

    def read(self, n: int = -1) -> bytes:
        n = self.left if n < 0 else min(n, self.left)
        try:
            data = self.f.read(n) if n else b""
        except OSError:
            data = b""
        if len(data) < n:
            data += bytes(n - len(data))
            self.short = True
        self.left -= len(data)
        return data


# Follows a file padded with zeros (it couldn't be read to the end), as a link to its name: restore leaves it out.
# Older restores have nowhere to put it, so they pass over it.
INCOMPLETE = "cherry/incomplete"


def _incomplete(arc: str) -> tarfile.TarInfo:
    info = tarfile.TarInfo(INCOMPLETE)
    info.type, info.linkname = tarfile.SYMTYPE, arc
    return info


class _StreamTar(tarfile.TarFile):
    """A tar stream that forgets each member once it's written or read: TarFile keeps every one in .members,
    and for millions of files that is gigabytes, which the garbage collector then keeps walking."""

    def addfile(self, *args, **kwargs):
        super().addfile(*args, **kwargs)
        self.members.clear()

    def next(self):
        member = super().next()
        self.members.clear()
        return member


SQLITE_HEADER = b"SQLite format 3\x00"
SNAPSHOT_PATIENCE = 30  # seconds a locked database is waited for before it's packed as it is


def _live_sqlite(path: str) -> bool:
    """A SQLite database that may be open right now (it has a write-ahead log or journal beside it). Copying
    one of those file by file can catch it half-written, so it's copied through SQLite instead."""
    if not (os.path.exists(path + "-wal") or os.path.exists(path + "-journal")):
        return False
    try:
        with open(path, "rb") as f:
            return f.read(16) == SQLITE_HEADER
    except OSError:
        return False


def _snapshot_sqlite(path: str, folder: Path, size: int) -> Path | None:
    """A consistent copy of a live SQLite database (with its write-ahead log folded in), or None if it can't be
    made (no room for the copy, or the database is locked), in which case the files are packed as they are."""
    import sqlite3
    try:
        folder.mkdir(parents=True, exist_ok=True)
        free = shutil.disk_usage(folder).free
        wal = os.path.getsize(path + "-wal") if os.path.exists(path + "-wal") else 0
        if free < (size + wal) * 1.1 + 512 * 1024 * 1024:
            return None
    except OSError:
        return None
    snap = folder / f"snapshot-{os.getpid()}-{threading.get_ident()}.db"
    snap.unlink(missing_ok=True)
    src = dst = None
    give_up = time.monotonic() + SNAPSHOT_PATIENCE

    def patience(status: int, remaining: int, total: int) -> None:
        # Python's backup() retries a busy database forever, and Chromium-based browsers lock theirs for as long
        # as they run.
        if status in (5, 6) and time.monotonic() > give_up:  # SQLITE_BUSY, SQLITE_LOCKED
            raise TimeoutError(f"{path} stays locked")

    try:
        src = sqlite3.connect(f"file:{urllib.parse.quote(os.fsencode(path))}?mode=ro", uri=True, timeout=3)
        src.execute("SELECT count(*) FROM sqlite_master").fetchone()
        dst = sqlite3.connect(snap)
        src.backup(dst, progress=patience)
        dst.close()
        dst = None
        return snap
    except (sqlite3.Error, OSError):
        snap.unlink(missing_ok=True)
        return None
    finally:
        for conn in (src, dst):
            if conn is not None:
                conn.close()


def _walk(base: Path, prefix: str, home: Path | None = None, skip: frozenset = frozenset(),
          left: Callable[[str, str], None] | None = None):
    """Yields (path, archive name), both strings, for `base` and everything in it that comes along. Folders in
    `skip` are brought by another category; `left(path, reason)` hears about each folder or file left behind."""
    home_s = str(home or Path.home())
    mounts = network_mounts()

    def leave(root: str, name: str, is_dir: bool) -> str | None:
        path = os.path.join(root, name)
        if path in skip:
            return "other"
        reason = "network" if is_dir and path in mounts else SKIP_REASONS.get(name)
        if reason == "node" and not _rebuilt_node(root, home_s):
            reason = None
        if reason is None and is_dir and os.path.isfile(os.path.join(path, "pyvenv.cfg")):
            reason = "python"
        if reason is None and path.startswith(home_s + "/"):
            reason = leave_reason(path[len(home_s) + 1:])
        if reason and reason != "other" and left is not None:
            left(path, reason)
        return reason

    base = str(base)
    if base in mounts:
        if left is not None:
            left(base, "network")
        return
    yield base, prefix
    if not os.path.isdir(base) or os.path.islink(base):
        return
    cut = len(base)
    for root, dirs, names in os.walk(base):
        dirs[:] = sorted(d for d in dirs if not leave(root, d, not os.path.islink(os.path.join(root, d))))
        arc_root = prefix + root[cut:]
        for d in dirs:
            yield f"{root}/{d}", f"{arc_root}/{d}"
        for n in sorted(names):
            if leave(root, n, False):
                continue
            yield f"{root}/{n}", f"{arc_root}/{n}"


def clear_transfer(drive: Path) -> None:
    """Removes an earlier transfer from a stick (a new one replaces it)."""
    folder = Path(drive) / MARKER_DIR
    if folder.is_dir():
        shutil.rmtree(folder)


def write_marker(drive: Path, marker: dict) -> Path:
    folder = Path(drive) / MARKER_DIR
    folder.mkdir(exist_ok=True)
    (folder / MARKER).write_text(json.dumps(marker, indent=2))
    if marker["storage"] == "usb":
        where = "Your files are stored encrypted on this stick"
        if marker.get("part"):
            final = marker.get("final")
            where += (f" (stick {marker['part']} of {marker['parts']})" if final
                      else f" (stick {marker['part']}; the transfer carries on onto the next stick)")
        where += "."
    else:
        where = f"Your files are stored encrypted in {(marker.get('remote') or {}).get('title', 'your own storage')}."
    (folder / "README.txt").write_text(
        "This USB stick carries files from your old computer to Cherry OS.\n\n"
        "Plug it in while installing or using Cherry OS (or open Cherry Move on any Linux computer)\n"
        "and your files are restored automatically.\n\n"
        "Keep it safe: anyone with this stick can get your files back.\n"
        f"{where}\n")
    os.sync()
    return folder / MARKER


def eject(mount: Path) -> None:
    """Unmounts a full stick so it can be unplugged safely (only real removable mounts, never a plain folder)."""
    os.sync()
    if not os.path.ismount(mount) or not str(mount).startswith(("/media/", "/run/media/")):
        return
    try:
        dev = subprocess.run(["findmnt", "-n", "-o", "SOURCE", str(mount)], capture_output=True, text=True,
                             timeout=10).stdout.strip()
        if dev:
            subprocess.run(["udisksctl", "unmount", "-b", dev, "--no-user-interaction"], capture_output=True,
                           timeout=120)
    except (OSError, subprocess.SubprocessError):
        pass


def free_space(mount: Path) -> int:
    try:
        return shutil.disk_usage(mount).free
    except OSError:
        return 0


def usable_space(mount: Path) -> int:
    """Free space on a stick, counting an earlier transfer's folder (a new transfer replaces it)."""
    free = free_space(mount)
    for root, _dirs, names in os.walk(Path(mount) / MARKER_DIR):
        for n in names:
            try:
                free += os.lstat(os.path.join(root, n)).st_size
            except OSError:
                pass
    return free


# ---------------------------------------------------------------- receive

class NeedStick(TransferError):
    def __init__(self, part: int):
        super().__init__(f"Plug in USB stick {part} of this transfer.")
        self.part = part


class NeedRemote(TransferError):
    """The files are in someone's own cloud drive or NAS; sign in (remote.py) and pass `remote=`."""

    def __init__(self, descriptor: dict):
        super().__init__(f"These files are stored in {descriptor.get('title', 'your own storage')}. Sign in to get them.")
        self.descriptor = descriptor


class Source:
    parallel = True  # get() may run on several threads at once (prefetching)

    def get(self, index: int) -> bytes | None:
        """A chunk, or None past the end."""


class UsbSource(Source):
    """Reads chunks from one stick, or several in turn (asking for each with `next_stick`)."""
    parallel = False

    def __init__(self, drive: Path, next_stick: StickPrompt | None = None):
        self.next_stick = next_stick
        self._use(Path(drive))
        self.code = self.marker.get("code")
        if self.part != 1:
            self._ask(1)

    def _use(self, drive: Path) -> None:
        self.drive = drive
        self.marker = read_marker(drive) or {}
        self.part = self.marker.get("part", 1)
        self.first = self.marker.get("first", 0)
        self.final = self.marker.get("final", True)
        self.chunks = self.marker.get("chunks")

    def _ask(self, part: int) -> None:
        if self.next_stick is None:
            raise NeedStick(part)
        problem = None
        while True:
            drive = Path(self.next_stick(part, problem))
            marker = read_marker(drive)
            if not marker or marker.get("code") != self.code:
                problem = "That stick isn't part of this transfer."
            elif marker.get("part", 1) != part:
                problem = f"That's stick {marker.get('part', 1)}. Plug in stick {part}."
            else:
                self._use(drive)
                return

    def get(self, index: int) -> bytes | None:
        while True:
            path = self.drive / MARKER_DIR / chunk_name(index)
            if index >= self.first and path.exists():
                return path.read_bytes()
            if self.final:
                if self.chunks is not None and index >= self.chunks:
                    return None
                raise TransferError(f"Part of the transfer is missing from the stick ({chunk_name(index)}).")
            self._ask(self.part + 1)


@dataclass
class RemoteMove:
    id: str
    info: dict

    @property
    def label(self) -> str:
        return self.info.get("label") or (f"Files from {self.info['hostname']}" if self.info.get("hostname")
                                          else "Files from your old computer")


def list_remote_moves(remote: "Remote") -> list[RemoteMove]:
    """Complete moves kept in someone's cloud drive or NAS, newest first."""
    moves = []
    for name in remote.list_dirs(REMOTE_ROOT):
        if not re.fullmatch(r"[0-9a-f]{32}", name):
            continue
        try:
            raw = _retry(lambda n=name: remote.get(f"{REMOTE_ROOT}/{n}/info.json"), attempts=2)
            info = json.loads(raw) if raw is not None else None
        except (OSError, TransferError, ValueError):
            continue  # half-deleted or unreadable: list the others
        if info is None:
            continue
        moves.append(RemoteMove(name, info if isinstance(info, dict) else {}))
    oldest = dt.datetime.min.replace(tzinfo=dt.timezone.utc)
    return sorted(moves, key=lambda m: sent_at(m.info) or oldest, reverse=True)


def now_iso() -> str:
    return dt.datetime.now().astimezone().isoformat(timespec="seconds")


def sent_at(info: dict) -> dt.datetime | None:
    """When a transfer was sent, in this computer's time zone. Older transfers wrote local time without a zone."""
    try:
        return dt.datetime.fromisoformat(info["created"]).astimezone()
    except (KeyError, TypeError, ValueError):
        return None


class RemoteSource(Source):
    def __init__(self, remote: "Remote", keys: TransferKeys):
        self.remote, self.base = remote, f"{REMOTE_ROOT}/{keys.id}"
        info = remote.get(f"{self.base}/info.json")
        if info is None:
            raise TransferError(f"Couldn't find these files in {remote.title}. Check you signed in to the same "
                                "account as on the old computer, and that the copy wasn't deleted.")
        self.chunks = json.loads(info)["chunks"]

    def get(self, index: int) -> bytes | None:
        if index >= self.chunks:
            return None
        blob = _retry(lambda: self.remote.get(f"{self.base}/{chunk_name(index)}"))
        if blob is None:
            raise TransferError(f"Part of the transfer is missing from {self.remote.title}.")
        return blob

    def delete(self) -> None:
        self.remote.delete(self.base)


class ChunkReader(io.RawIOBase):
    """The tar stream, from chunk `start` on, `skip` bytes into it (where a stopped restore got to)."""

    def __init__(self, keys: TransferKeys, source: Source, prefetch: int | None = None, start: int = 1,
                 skip: int = 0):
        self.keys, self.source = keys, source
        self.prefetch = (4 if source.parallel else 0) if prefetch is None else prefetch
        self.pool = ThreadPoolExecutor(max_workers=self.prefetch) if self.prefetch else None
        self.next_index = start
        self.skip = skip
        self.buf = memoryview(b"")
        self.ahead: dict = {}
        self.ended = False
        self.read_bytes = 0
        self.starts: collections.deque = collections.deque(maxlen=16)  # (stream offset, chunk) of recent chunks

    def where(self, offset: int) -> tuple[int, int] | None:
        """The chunk a recent stream offset is in, and how far into it."""
        for start, index in reversed(self.starts):
            if start <= offset:
                return index, offset - start
        return None

    def readable(self) -> bool:
        return True

    def _load(self, index: int) -> bytes | None:
        blob = self.source.get(index)
        return None if blob is None else open_chunk(self.keys, index, blob)

    def _fill(self) -> bool:
        if self.ended:
            return False
        if self.pool:
            for i in range(self.next_index, self.next_index + self.prefetch):
                if i not in self.ahead:
                    self.ahead[i] = self.pool.submit(self._load, i)
            data = self.ahead.pop(self.next_index).result()
        else:
            data = self._load(self.next_index)
        if data is None:
            self.ended = True
            return False
        self.starts.append((self.read_bytes - self.skip, self.next_index))
        self.buf = memoryview(data)[self.skip:]
        self.skip = 0
        self.next_index += 1
        return True

    def readinto(self, b) -> int:
        while not len(self.buf):
            if not self._fill():
                return 0
        n = min(len(b), len(self.buf))
        b[:n] = self.buf[:n]
        self.buf = self.buf[n:]
        self.read_bytes += n
        return n

    def close(self) -> None:
        if self.pool:
            self.pool.shutdown(wait=False, cancel_futures=True)
        super().close()


def read_manifest(keys: TransferKeys, source: Source) -> dict:
    blob = source.get(0)
    if blob is None:
        raise TransferError("This transfer is empty.")
    return json.loads(open_chunk(keys, 0, blob))


def source_for(code: str, drive: Path | None = None, remote: "Remote | None" = None,
               next_stick: StickPrompt | None = None):
    keys = keys_from_code(code)
    if remote is not None:
        return keys, RemoteSource(remote, keys)
    marker = read_marker(drive) if drive is not None else None
    if marker and marker.get("code") == keys.code:
        if marker.get("storage") == "usb":
            return keys, UsbSource(drive, next_stick)
        if marker.get("storage") == "remote":
            raise NeedRemote(marker.get("remote") or {})
    raise TransferError("Plug in the transfer USB stick, or sign in to where your files are.")


def _safe(member: tarfile.TarInfo) -> bool:
    """Symlinks may point anywhere (restore() never writes through one); hard links only to stored files."""
    name = member.name
    if name.startswith("/") or ".." in Path(name).parts:
        return False
    if member.islnk():
        target = member.linkname
        return not target.startswith("/") and ".." not in Path(target).parts
    return member.issym() or member.isfile() or member.isdir()


# Restored apps' settings replace the ones a fresh Cherry OS made (kept as "… (before Cherry Move)"), except
# these, which belong to the new computer's desktop: the old computer's copy is kept beside them instead.
KEEP_NEW = (".config/dconf", ".config/monitors.xml", ".config/user-dirs.dirs", ".config/user-dirs.locale",
            ".config/mimeapps.list", ".config/autostart", ".config/cherry", ".local/share/keyrings",
            ".config/pulse", ".local/state/wireplumber", ".config/gnome-session", ".local/share/gnome-shell",
            ".config/systemd/user/default.target.wants/pipewire.service", ".config/goa-1.0",
            ".local/share/recently-used.xbel", ".config/kdeglobals", ".config/plasma-org.kde.plasma.desktop-appletsrc",
            ".config/kwinrc", ".config/xfce4", ".config/budgie-desktop", ".local/share/applications/mimeapps.list")


_NOT_TEXT = re.compile("[\udc80-\udcff]")


def _as_json_text(name: str) -> str:
    """A name as the Cherry Move app's manifest has it: Go's JSON turns each byte that isn't UTF-8 into U+FFFD."""
    return _NOT_TEXT.sub("\ufffd", name)


def _old_wins(arcname: str) -> bool:
    if not arcname.startswith(("home/.", "home/snap/")):
        return False
    rel = arcname.split("/", 1)[1]
    return not any(rel == k or rel.startswith(k + "/") for k in KEEP_NEW)


def _rehome(path: str, old_home: str | None, home: Path) -> str:
    """`path` from the old computer, moved into this home folder if it was in the old one."""
    if old_home and old_home != str(home) and (path == old_home or path.startswith(old_home + "/")):
        return str(home) + path[len(old_home):]
    return path


def _inside(path: Path, home: Path) -> bool:
    real, top = os.path.realpath(path), os.path.realpath(home)
    return real == top or real.startswith(top + "/")


def _destination(arcname: str, home: Path, xdg_cache: dict) -> Path | None:
    parts = arcname.split("/", 2)
    if parts[0] == "xdg" and len(parts) >= 2:
        if parts[1] not in xdg_cache:
            fallback = next((f for x, f in XDG_NAMES.values() if x == parts[1]), parts[1].title())
            xdg_cache[parts[1]] = xdg_dir(parts[1], fallback, home)
        base = xdg_cache[parts[1]]
        return base / parts[2] if len(parts) == 3 else base
    if parts[0] == "home" and len(parts) >= 2:
        rel = arcname.split("/", 1)[1]
        for old, new in RENAME_ON_RESTORE.items():
            if rel == old:
                rel = new
        return home / rel
    return None


NAME_MAX = 255  # bytes in one file name


def _fit(stem: str, tail: str) -> str:
    """stem + tail, the stem cut short (never mid-letter) if the name would be too long for a file."""
    room = NAME_MAX - len(os.fsencode(tail))
    raw = os.fsencode(stem)
    if len(raw) <= room:
        return stem + tail
    for back in range(4):
        try:
            return raw[:room - back].decode("utf-8") + tail
        except UnicodeDecodeError:
            continue
    return os.fsdecode(raw[:room]) + tail


def _beside_name(path: Path, why: str, n: int, folder: bool) -> Path:
    stem, suffix = (path.name, "") if folder else (path.stem, path.suffix)
    return path.with_name(_fit(stem, f" ({why}{'' if n == 1 else ' ' + str(n)}){suffix}"))


def _copy_beside(path: Path, copy: Path) -> Path | None:
    """The "… (from old computer)" file an earlier restore put beside `path`, if it's the same as `copy`."""
    size = copy.stat().st_size
    folder = path.is_dir()  # named as _unique() does
    for n in range(1, 1000):
        cand = _beside_name(path, "from old computer", n, folder)
        if not cand.exists():
            return None
        if cand.is_file() and cand.stat().st_size == size and filecmp.cmp(copy, cand, shallow=False):
            return cand
    return None


def _unique(path: Path, why: str = "from old computer") -> Path:
    folder = path.is_dir()
    for n in range(1, 1000):
        cand = _beside_name(path, why, n, folder)
        if not cand.exists() and not cand.is_symlink():
            return cand
    return path


def _folder_beside(path: Path) -> Path:
    """Where a folder goes when something else here has its name: "… (from old computer)", the same one each time."""
    for n in range(1, 1000):
        cand = _beside_name(path, "from old computer", n, True)
        if not cand.exists() and not cand.is_symlink() or cand.is_dir() and not cand.is_symlink():
            return cand
    return path


def _in_moved_folder(name: str, dest: Path, folders: dict[str, Path]) -> Path:
    parts = name.split("/")
    for i in range(len(parts) - 1, 1, -1):
        top = folders.get("/".join(parts[:i]))
        if top is not None:
            return top.joinpath(*parts[i:])
    return dest


def restore(code: str, drive: Path | None = None, home: Path | None = None, progress: Progress | None = None,
            cancel: threading.Event | None = None, remote: "Remote | None" = None,
            next_stick: StickPrompt | None = None) -> dict:
    """Decrypts and unpacks a transfer into `home`. Existing files are never overwritten.
    Files stored on several sticks need `next_stick`; files in someone's own cloud or NAS need `remote`."""
    home = home or Path.home()
    keys, source = source_for(code, drive, remote, next_stick)
    manifest = read_manifest(keys, source)
    total = manifest.get("raw_size") or manifest.get("size") or 1
    targets = {_as_json_text(name) for name in manifest.get("same", [])}  # stored once, for several names
    copies: dict[str, Path] = {}
    xdg_cache: dict = {}
    old_home = manifest.get("home") if str(manifest.get("home", "")).startswith("/") else None
    checkpoint = _checkpoint_file(home, keys)
    state = _load_checkpoint(checkpoint)
    done, restored, skipped, replaced = (state.get(k, 0) for k in ("done", "restored", "skipped", "replaced"))
    firefox = bool(state.get("firefox"))
    read_only = [(Path(p), m) for p, m in state.get("read_only", [])]
    # Duplicates restored before the stop are found where they belong, or where `moved` says they went instead.
    moved = {name: Path(p) for name, p in state.get("moved", {}).items()}
    folders = {name: Path(p) for name, p in state.get("folders", {}).items()}  # restored under another name
    saved_at = time.monotonic()
    mounted = [m for m in network_mounts() if m.startswith(f"{home}/")]
    kept = checkpoint.with_name(checkpoint.stem + "-links")

    def keep_for_links(member: tarfile.TarInfo) -> None:
        """A file stored once for several names that can't go where it was: kept aside to copy for the others."""
        if not member.isfile() or _as_json_text(member.name) not in targets:
            return
        kept.mkdir(parents=True, exist_ok=True)
        path = kept / hashlib.sha256(member.name.encode("utf-8", "surrogateescape")).hexdigest()
        with open(path, "wb") as out:
            shutil.copyfileobj(tar.extractfile(member), out, 1024 * 1024)
        copies[member.name] = moved[member.name] = path

    def save(offset: int) -> None:
        nonlocal saved_at
        spot = reader.where(offset)
        if spot is None:
            return
        tmp = checkpoint.with_name(checkpoint.name + ".part")
        tmp.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(json.dumps({
            "id": keys.id, "chunk": spot[0], "skip": spot[1], "done": done, "restored": restored,
            "skipped": skipped, "replaced": replaced, "firefox": firefox,
            "read_only": [(str(p), m) for p, m in read_only], "moved": {n: str(p) for n, p in moved.items()},
            "folders": {n: str(p) for n, p in folders.items()}}))
        tmp.replace(checkpoint)
        saved_at = time.monotonic()

    def put_down(p: tuple) -> None:
        """Moves a restored file from its temporary name to its place."""
        nonlocal skipped, done, replaced, restored, firefox
        member, dest, tmp, existing, same_kind, link_here, size = p
        if existing or link_here:
            if same_kind and existing.st_size == size and filecmp.cmp(tmp, dest, shallow=False):
                tmp.unlink()
                skipped += 1
                done += size
                if _as_json_text(member.name) in targets:
                    copies[member.name] = dest
                return
            if _old_wins(member.name) and not link_here:
                _set_aside(dest)
                replaced += 1
            elif (beside := _copy_beside(dest, tmp)) is not None:
                tmp.unlink()
                skipped += 1
                done += size
                if _as_json_text(member.name) in targets:
                    copies[member.name] = moved[member.name] = beside
                return
            else:
                dest = _unique(dest)
                if _as_json_text(member.name) in targets:
                    moved[member.name] = dest
        os.chmod(tmp, member.mode & 0o7777)
        os.utime(tmp, (member.mtime, member.mtime))
        tmp.rename(dest)
        if _as_json_text(member.name) in targets:
            copies[member.name] = dest
        firefox = firefox or member.name == "home/.mozilla/firefox/profiles.ini"
        restored += 1
        done += size
        if progress:
            progress(min(done, total), total, member.name.split("/", 2)[-1])

    pending = None  # the last file, put down once the next entry shows it wasn't cut short on the old computer
    with ChunkReader(keys, source, start=state.get("chunk", 1), skip=state.get("skip", 0)) as reader, \
            _StreamTar.open(fileobj=reader, mode="r|") as tar:
        for member in tar:
            if member.name == INCOMPLETE:
                if pending is not None and member.issym() and member.linkname == pending[0].name:
                    pending[2].unlink(missing_ok=True)
                    done += pending[6]
                    pending = None
                continue
            if pending is not None:
                put_down(pending)
                pending = None
            if cancel is not None and cancel.is_set():
                save(member.offset)
                raise InterruptedError("cancelled")
            if time.monotonic() - saved_at > CHECKPOINT_EVERY:
                save(member.offset)
            if not _safe(member):
                continue
            dest = _destination(member.name, home, xdg_cache)
            if folders and dest is not None:
                dest = _in_moved_folder(member.name, dest, folders)
            # Never into a network drive mounted here either (it may hold this very transfer).
            if (dest is None or TRANSFER_STORE.search(member.name)
                    or any(str(dest) == m or str(dest).startswith(m + "/") for m in mounted)):
                if member.isfile():
                    done += member.size
                    if progress:
                        progress(min(done, total), total, member.name.split("/", 2)[-1])
                continue
            if member.isdir():
                mode = member.mode & 0o7777
                # A file or link has the folder's name here (a link to a folder in this home folder is filled).
                taken = dest.is_symlink() and not (dest.is_dir() and _inside(dest, home)) \
                    or dest.exists() and not dest.is_dir()
                if taken and _inside(dest.parent, home):
                    if _old_wins(member.name) and not dest.is_symlink():
                        _set_aside(dest)
                    else:
                        folders[member.name] = dest = _folder_beside(dest)
                if not dest.exists() and not dest.is_symlink() and _inside(dest.parent, home):
                    dest.mkdir(parents=True)
                    os.chmod(dest, mode | 0o700)  # writable until everything inside it is back
                    if mode & 0o700 != 0o700:
                        read_only.append((dest, mode))
                elif dest.is_dir() and not dest.is_symlink() and dest != home and _inside(dest, home):
                    # A folder this computer made already: as private as it was on the old one, never less.
                    now = stat.S_IMODE(dest.stat().st_mode)
                    want = now & (mode | 0o700)
                    if want & 0o700 != 0o700:
                        read_only.append((dest, want))
                        want |= 0o700
                    if want != now:
                        os.chmod(dest, want)
                continue
            if not _inside(dest.parent, home):
                keep_for_links(member)
                continue
            dest.parent.mkdir(parents=True, exist_ok=True)
            if member.issym():
                if not dest.exists() and not dest.is_symlink():
                    os.symlink(_rehome(member.linkname, old_home, home), dest)
                    restored += 1
                continue
            original = copies.get(member.linkname) if member.islnk() else None
            if member.islnk() and original is None and state:
                there = moved.get(member.linkname)
                if there is None and (there := _destination(member.linkname, home, xdg_cache)) is not None:
                    there = _in_moved_folder(member.linkname, there, folders)
                if there is not None and there.is_file() and not there.is_symlink() and _inside(there, home):
                    original = copies[member.linkname] = there
            if member.islnk() and original is None:
                continue
            size = original.stat().st_size if original is not None else member.size
            # A link already here is never replaced (nor written through): the old file goes beside it.
            link_here = dest.is_symlink()
            existing = dest.stat() if dest.exists() else None
            same_kind = existing is not None and stat.S_ISREG(existing.st_mode)
            # FAT sticks keep times to 2 seconds.
            if same_kind and existing.st_size == size and abs(existing.st_mtime - member.mtime) < 2:
                skipped += 1
                done += size
                if _as_json_text(member.name) in targets:
                    copies[member.name] = dest
                continue
            tmp = dest.with_name(_fit(f".{dest.name}", ".cherry-part"))
            if original is not None:
                shutil.copyfile(original, tmp)
            else:
                with open(tmp, "wb") as out:
                    shutil.copyfileobj(tar.extractfile(member), out, 1024 * 1024)
            pending = (member, dest, tmp, existing, same_kind, link_here, size)
        if pending is not None:
            put_down(pending)
    for folder, mode in reversed(read_only):
        try:
            os.chmod(folder, mode)
        except OSError:
            pass
    shutil.rmtree(kept, ignore_errors=True)
    checkpoint.unlink(missing_ok=True)
    manifest["restored_files"] = restored
    manifest["skipped_files"] = skipped
    manifest["replaced_files"] = replaced
    if firefox:
        fix_firefox(home)
    fix_workspaces(home, old_home)
    added = secret_store.add_all(manifest.pop("keyring", None) or [])
    manifest["passwords_added"] = len(added)
    use_keyring_for_browsers(home, added)
    if old_home and old_home != str(home):
        fix_moved_home(home, old_home)
    remember_restored(keys, home)
    return manifest


def _set_aside(dest: Path) -> None:
    """Moves a file the restore replaces out of the way, with its SQLite journal files (left beside the
    restored database they'd corrupt it)."""
    try:
        with open(dest, "rb") as f:
            sqlite = f.read(16) == SQLITE_HEADER
    except OSError:
        sqlite = False
    aside = _unique(dest, "before Cherry Move")
    dest.rename(aside)
    if sqlite:
        for end in ("-wal", "-shm", "-journal"):
            side = Path(f"{dest}{end}")
            if side.exists():
                side.rename(Path(f"{aside}{end}"))


# Where VS Code and editors built on it (Cursor…) keep each folder's chats, open files and settings.
EDITOR_DIRS = (".config/Cursor", ".config/Code", ".config/Code - OSS", ".config/VSCodium",
               ".var/app/com.visualstudio.code/config/Code", ".var/app/com.vscodium.codium/config/VSCodium")


def _file_uri_path(uri: str) -> str | None:
    if not isinstance(uri, str) or not uri.startswith("file://"):
        return None
    return urllib.parse.unquote(urllib.parse.urlparse(uri).path)


def fix_workspaces(home: Path, old_home: str | None = None) -> int:
    """VS Code and Cursor find a folder's history (Cursor's chats among it) in workspaceStorage/<id>, where
    the id is an MD5 of the folder's path and inode number. Inodes are different on every disk, so after a move
    each folder would open empty: renames each one to the id its folder has here. Returns how many moved."""
    moved = 0
    for rel in EDITOR_DIRS:
        storage = home / rel / "User/workspaceStorage"
        if not storage.is_dir():
            continue
        for entry in sorted(storage.iterdir()):
            meta = entry / "workspace.json"
            try:
                data = json.loads(meta.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            path = _file_uri_path(data.get("folder"))
            if path is None:
                continue
            here = _rehome(path, old_home, home)
            if here != path:
                data["folder"] = "file://" + urllib.parse.quote(here)
                meta.write_text(json.dumps(data), encoding="utf-8")
            try:
                ino = os.stat(here).st_ino
            except OSError:
                continue
            key = hashlib.md5(f"{here}{ino}".encode()).hexdigest()
            if key == entry.name:
                continue
            target = storage / key
            if target.exists():
                target.rename(_unique(target, "before Cherry Move"))
            entry.rename(target)
            moved += 1
    return moved


# Where apps built on Chromium can be told which keyring holds the key to their saved passwords and cookies.
# Left to themselves they pick by desktop (KWallet on KDE, the Secret Service elsewhere), so on KDE, or after
# switching desktops, they'd look in the wrong place and find nothing.
CHROMIUM_KEY_SCHEMA = "chrome_libsecret_os_crypt_pass"  # …_password_v2 now, …_password before
PASSWORD_STORE = "gnome-libsecret"
KEYRING_SWITCHES = {
    "chrome": [(".var/app/com.google.Chrome/config/chrome-flags.conf", "flags")],
    "chromium": [(".var/app/org.chromium.Chromium/config/chromium-flags.conf", "flags")],
    "Cursor": [(".cursor/argv.json", "argv")],
    "Code": [(".vscode/argv.json", "argv")],
    "VSCodium": [(".vscode-oss/argv.json", "argv")],
}


def use_keyring_for_browsers(home: Path, added: list[dict]) -> list[str]:
    """Tells each Chromium-based app whose key was just added to the keyring to read it from there, whatever
    the desktop. Returns the files changed."""
    changed = []
    for item in added:
        attrs = item.get("attributes") or {}
        if not attrs.get("xdg:schema", "").startswith(CHROMIUM_KEY_SCHEMA):
            continue
        for rel, kind in KEYRING_SWITCHES.get(attrs.get("application", ""), []):
            path = home / rel
            if kind == "flags" and not path.parent.parent.is_dir():
                continue  # that Flatpak isn't here
            try:
                text = path.read_text(encoding="utf-8") if path.exists() else ""
                if kind == "flags":
                    if any(line.strip().startswith("--password-store") for line in text.splitlines()):
                        continue
                    text = (text if not text or text.endswith("\n") else text + "\n") + \
                        f"--password-store={PASSWORD_STORE}\n"
                else:
                    if '"password-store"' in text:
                        continue
                    start = text.find("{")
                    if start < 0 and text.strip():
                        continue
                    if start < 0:
                        text, start = "{\n}\n", 0
                    rest = text[start + 1:]
                    entry = f'\n\t"password-store": "{PASSWORD_STORE}"' + ("," if re.search(r'"\s*:', rest) else "")
                    text = text[:start + 1] + entry + rest
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(text, encoding="utf-8")
                changed.append(rel)
            except OSError:
                continue
    return changed


def fix_moved_home(home: Path, old_home: str) -> None:
    """When the home folder has a different path here (another user name): points Cursor's per-project chat
    folders, the editors' list of folders and your own background services at the new path."""
    old_slug, new_slug = old_home.strip("/").replace("/", "-"), str(home).strip("/").replace("/", "-")
    projects = home / ".cursor/projects"
    if projects.is_dir():
        for p in projects.iterdir():
            if p.name.startswith(old_slug) and not (projects / (new_slug + p.name[len(old_slug):])).exists():
                p.rename(projects / (new_slug + p.name[len(old_slug):]))
    old_uri, new_uri = "file://" + urllib.parse.quote(old_home), "file://" + urllib.parse.quote(str(home))
    for rel in EDITOR_DIRS:
        f = home / rel / "User/globalStorage/storage.json"
        try:
            text = f.read_text(encoding="utf-8")
        except OSError:
            continue
        if old_uri in text:
            f.write_text(text.replace(old_uri + "/", new_uri + "/").replace(f'"{old_uri}"', f'"{new_uri}"'),
                         encoding="utf-8")
    units = home / ".config/systemd/user"
    for f in units.glob("*.service") if units.is_dir() else ():
        try:
            text = f.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        new = re.sub(re.escape(old_home) + r"(?=[/\s\"']|$)", str(home), text, flags=re.M)
        if new != text:
            f.write_text(new, encoding="utf-8")


def _restored_file(home: Path) -> Path:
    return home / ".config/cherry/restored-transfers"


# Transfers kept in a cloud drive or NAS (CherryMove/<id>/…): sent along by older versions when that NAS was
# mounted inside the home folder, and no use as files.
TRANSFER_STORE = re.compile(rf"(^|/){REMOTE_ROOT}/[0-9a-f]{{32}}(/|$)")
CHECKPOINT_EVERY = 15  # seconds between notes of how far a restore got, so a stopped one carries on from there


def _checkpoint_file(home: Path, keys: TransferKeys) -> Path:
    return home / f".cache/cherry-move/restore-{keys.id}.json"


def _load_checkpoint(path: Path) -> dict:
    try:
        state = json.loads(path.read_text())
    except (OSError, ValueError):
        return {}
    ok = (isinstance(state, dict) and isinstance(state.get("chunk"), int) and state["chunk"] >= 1
          and isinstance(state.get("skip"), int) and state["skip"] >= 0)
    return state if ok else {}


def remember_restored(keys: TransferKeys, home: Path | None = None) -> None:
    """Notes the transfer's id (never its code), so plugging its stick in again doesn't offer it again."""
    path = _restored_file(home or Path.home())
    if was_restored(keys.code, home):
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a") as f:
        f.write(keys.id + "\n")


def was_restored(code: str, home: Path | None = None) -> bool:
    try:
        ident = keys_from_code(code).id
        return ident in _restored_file(home or Path.home()).read_text().split()
    except (ValueError, OSError, TypeError, AttributeError):
        return False


def _read_ini(path: Path) -> configparser.ConfigParser:
    ini = configparser.ConfigParser(interpolation=None, strict=False)
    ini.optionxform = str
    ini.read(path, encoding="utf-8")
    return ini


def _write_ini(ini: configparser.ConfigParser, path: Path) -> None:
    with open(path, "w", encoding="utf-8") as f:
        ini.write(f, space_around_delimiters=False)


def fix_firefox(home: Path) -> None:
    """Makes Cherry's Firefox open the profile brought from the old computer. Left alone, Firefox sees that
    profile is locked to the old computer's Firefox (an [Install…] section) and starts an empty one, and a
    profile last used by a newer Firefox trips its downgrade protection."""
    ff = home / ".mozilla" / "firefox"
    current = ff / "profiles.ini"
    old_path = ff / "profiles (from old computer).ini"
    if not old_path.exists():
        old_path = current
    if not old_path.exists():
        return
    old = _read_ini(old_path)
    profiles = [s for s in old.sections() if s.startswith("Profile")]
    used = next((old[s]["Default"] for s in old.sections() if s.startswith("Install") and old[s].get("Default")), None)
    used = used or next((old[s]["Path"] for s in profiles if old[s].get("Default") == "1"), None)
    used = used or (old[profiles[0]].get("Path") if profiles else None)
    if not used:
        return
    if old_path == current:
        for s in old.sections():
            if s.startswith("Install"):
                old.remove_section(s)
        for s in profiles:
            old.remove_option(s, "Default")
            if old[s].get("Path") == used:
                old[s]["Default"] = "1"
        _write_ini(old, current)
        (ff / "installs.ini").unlink(missing_ok=True)
    else:
        # Firefox was opened here before the restore: add the old profiles and make this Firefox use the old one.
        new = _read_ini(current)
        n = sum(1 for s in new.sections() if s.startswith("Profile"))
        for s in new.sections():
            if s.startswith("Profile"):
                new.remove_option(s, "Default")
        known = {new[s].get("Path") for s in new.sections() if s.startswith("Profile")}
        for s in profiles:
            if old[s].get("Path") in known:
                continue
            new[f"Profile{n}"] = {k: v for k, v in old[s].items() if k != "Default"}
            if old[s].get("Path") == used:
                new[f"Profile{n}"]["Default"] = "1"
            n += 1
        for s in new.sections():
            if s.startswith("Install"):
                new[s]["Default"] = used
        _write_ini(new, current)
        installs = ff / "installs.ini"
        if installs.exists():
            ini = _read_ini(installs)
            for s in ini.sections():
                ini[s]["Default"] = used
            _write_ini(ini, installs)
        old_path.unlink()
    # Firefox only adopts a default profile that this install used last, and refuses one a newer Firefox used:
    # stamp it as last used by an old version of this Firefox (it then treats it as an upgrade).
    binary = shutil.which("firefox-esr") or shutil.which("firefox")
    app = Path(os.path.realpath(binary)).parent if binary else Path("/usr/lib/firefox-esr")
    machine = {"aarch64": "aarch64"}.get(platform.machine(), "x86_64")
    (ff / used / "compatibility.ini").write_text(
        "[Compatibility]\n"
        "LastVersion=1.0_20000101000000/20000101000000\n"
        f"LastOSABI=Linux_{machine}-gcc3\n"
        f"LastPlatformDir={app}\n"
        f"LastAppDir={app / 'browser'}\n", encoding="utf-8")


# Packages never reinstalled from another distro's list: the new computer has its own kernel, boot loader,
# drivers, sound and desktop, and these would replace or fight them.
APT_NEVER = re.compile(r"^(linux-.*|grub.*|shim.*|nvidia-.*|ubuntu-.*|language-pack-.*|init|systemd.*|"
                       r"pulseaudio.*|pipewire.*|wireplumber|.*-desktop(-.*)?|gdm3|sddm|lightdm.*|plymouth.*|"
                       r"firmware-.*|.*-firmware|casper|initramfs-tools.*|base-files|libc6|cloud-init|snapd|"
                       r"network-manager.*|efibootmgr|login|passwd|dash|bash|sudo|apt|dpkg|"
                       r"cherry-.*|calamares.*)$")
UBUNTU_CODENAMES = {"bionic", "focal", "jammy", "kinetic", "lunar", "mantic", "noble", "oracular", "plucky",
                    "questing", "resolute"}
REINSTALL_LOG = ".local/share/cherry/reinstall.log"


# Ubuntu's names for packages Debian has under another name.
APT_RENAMES = {"docker-compose-v2": "docker-compose"}
# How root_reinstall() tells reinstall_apps(), through the log, what didn't work.
ROOT_PROBLEMS = "Didn't work: "


def reinstall_count(apps: dict) -> int:
    """How many things reinstall_apps() would put back, to show on its button."""
    n = sum(len(apps.get(k) or []) for k in ("flatpak", "cherry", "ollama", "venvs", "node", "rustup"))
    extras = apps.get("extras") or {}
    docker = extras.get("docker") or {}
    n += len([d for d in extras.get("debs") or [] if d.get("file")]) + len((extras.get("system") or {}).get("units") or [])
    n += len(docker.get("containers") or []) + len(docker.get("compose") or [])
    return n + len([p for p in apps.get("apt") or [] if not APT_NEVER.match(p)])


def _os_release() -> dict:
    out = {}
    try:
        for line in Path("/etc/os-release").read_text().splitlines():
            k, _, v = line.partition("=")
            out[k] = v.strip().strip('"')
    except OSError:
        pass
    return out


def _debian_based(rel: dict) -> bool:
    """Debian or built on it (Cherry OS says ID=cherry, ID_LIKE=debian), but not Ubuntu or built on Ubuntu."""
    like = rel.get("ID_LIKE", "").split()
    return rel.get("ID") == "debian" or ("debian" in like and "ubuntu" not in like and rel.get("ID") != "ubuntu")


def _distro_tags(rel: dict) -> set[str]:
    """How downloads name builds for this system: debian13, trixie, ubuntu24.04, noble…"""
    tags = {rel.get("VERSION_CODENAME", ""), rel.get("UBUNTU_CODENAME", "")}
    if _debian_based(rel):
        try:
            tags.add("debian" + Path("/etc/debian_version").read_text().strip().split(".")[0])
        except OSError:
            pass
    if rel.get("ID") == "ubuntu":
        tags.add("ubuntu" + rel.get("VERSION_ID", ""))
    return {t.lower() for t in tags if re.fullmatch(r"[\w.]+", t or "")}


def _upstream_deb(homepage: str, folder: Path, rel: dict) -> Path | None:
    """An app's own build for this system from its GitHub releases (app_1.2+debian13_amd64.deb), for when the
    copy rebuilt from the old computer can't install here (an Ubuntu build needing Ubuntu's libraries)."""
    m = re.fullmatch(r"https?://github\.com/([\w.-]+)/([\w.-]+?)(?:\.git)?/?", homepage or "")
    tags = _distro_tags(rel)
    if not m or not tags:
        return None
    arch = subprocess.run(["dpkg", "--print-architecture"], capture_output=True, text=True).stdout.strip()
    headers = {"User-Agent": "CherryOS/1.0", "Accept": "application/vnd.github+json"}
    try:
        req = urllib.request.Request(f"https://api.github.com/repos/{m[1]}/{m[2]}/releases/latest", headers=headers)
        with urllib.request.urlopen(req, timeout=60) as res:
            assets = json.load(res).get("assets") or []
        for a in assets:
            name = a.get("name", "")
            words = name.lower()
            if not (re.fullmatch(r"[\w.+~-]+\.deb", name) and re.search(rf"(?<![a-z0-9]){arch}(?![a-z0-9])", words)
                    and any(re.search(rf"(?<![a-z0-9]){re.escape(t)}(?![a-z0-9])", words) for t in tags)):
                continue
            want = (a.get("digest") or "").removeprefix("sha256:")
            path, digest = folder / name, hashlib.sha256()
            req = urllib.request.Request(a["browser_download_url"], headers={"User-Agent": "CherryOS/1.0"})
            with urllib.request.urlopen(req, timeout=600) as res, open(path, "wb") as out:
                while chunk := res.read(1 << 20):
                    digest.update(chunk)
                    out.write(chunk)
            if want and digest.hexdigest() != want:
                path.unlink()
                return None
            return path
    except (OSError, ValueError, KeyError):
        return None
    return None


def _retarget_source(text: str, os_id: str, codename: str) -> str:
    """A package source written for Ubuntu (…/ubuntu noble) pointed at the same place's Debian packages when
    this is Debian (Tailscale, Docker and most others publish both)."""
    if os_id != "debian" or not codename:
        return text
    pattern = r"(\S*/)ubuntu(/?)(\s+|\nSuites:\s*)(" + "|".join(sorted(UBUNTU_CODENAMES)) + r")\b"
    return re.sub(pattern, lambda m: f"{m.group(1)}debian{m.group(2)}{m.group(3)}{codename}", text)


UNIT_NAME = re.compile(r"[\w@.:-]+\.(service|timer|socket|path)")


def _mount_unit(where: str) -> str:
    """systemd's unit name for a mount point (systemd-escape --path --suffix=automount)."""
    path = where.strip("/")
    out = "".join(c if c.isascii() and (c.isalnum() or c in ":_.") else "-" if c == "/" else f"\\x{ord(c):02x}"
                  for c in path)
    return ("\\x2e" + out[1:] if out.startswith(".") else out or "-") + ".automount"


def _restore_mounts(mounts: list, moved: Callable[[str], str], home: str, root: Path,
                    ok: Callable[..., bool]) -> list[str]:
    """Adds the old computer's other drives and network shares to /etc/fstab: a drive only when it's connected
    here, each with nofail so the computer still starts without it. Returns what couldn't be added."""
    problems = []
    fstab = root / "etc/fstab"
    try:
        text = fstab.read_text()
    except OSError:
        text = ""
    have = {f[1] for f in (line.split() for line in text.splitlines()) if len(f) > 1 and not f[0].startswith("#")}
    added = []
    for m in mounts:
        spec, kind, options = m.get("spec", ""), m.get("type", ""), moved(m.get("options", "defaults"))
        where = moved(m.get("where", "") + "/").rstrip("/")
        if (not all(re.fullmatch(r"[^\s#]+", x) for x in (spec, where, kind, options)) or not where.startswith("/")
                or where in have or where in system_extras.SYSTEM_MOUNTS):
            continue
        if kind in system_extras.NETWORK_FS:
            helper = "sshfs" if kind == "fuse.sshfs" else f"mount.{kind}"
            if not shutil.which(helper) and not ok(["apt-get", "install", "-y", system_extras.NETWORK_FS[kind]]):
                problems.append(where)
                print(f"Couldn't install what {where} needs ({system_extras.NETWORK_FS[kind]})", flush=True)
                continue
        elif system_extras.DRIVE_SPEC.match(spec):
            key, _, value = spec.partition("=")
            device = spec if spec.startswith("/") else f"/dev/disk/by-{key.lower()}/{value}"
            if not os.path.lexists(root / device.lstrip("/")):
                problems.append(where)
                print(f"The drive at {where} on your old computer isn't connected to this one; plug it in and "
                      f"put it back with Disks", flush=True)
                continue
        else:
            continue
        opts = options.split(",")
        if "nofail" not in opts:
            opts.append("nofail")
        line = f"{spec} {where} {kind} {','.join(opts)} 0 {m.get('pass', '0') if m.get('pass') in ('0', '1', '2') else '0'}"
        text += ("" if not text or text.endswith("\n") else "\n") + line + "\n"
        added.append((where, opts))
        have.add(where)
    if not added:
        return problems
    fstab.parent.mkdir(parents=True, exist_ok=True)
    fstab.write_text(text)
    for where, _opts in added:
        target = root / where.lstrip("/")
        if not target.exists() and not target.is_symlink():
            target.mkdir(parents=True, exist_ok=True)
            if home and where.startswith(home + "/"):
                st = os.stat(home)
                os.chown(target, st.st_uid, st.st_gid)
    ok(["systemctl", "daemon-reload"])
    for where, opts in added:
        if "x-systemd.automount" in opts:
            ok(["systemctl", "start", _mount_unit(where)])
        elif "noauto" not in opts and not ok(["mount", where]):
            problems.append(where)
            print(f"{where} didn't mount here; see: sudo mount {where}", flush=True)
    return problems


def restore_extras(extras: dict, top: Path, old_home: str | None, home: str, root: Path = Path("/")) -> list[str]:
    """Puts back (as root) what system_extras gathered on the old computer, its files now under `top`: apps
    rebuilt from .deb files, your own system services with the /usr/local and /opt files they run, and Docker
    servers with their images and data. Nothing already on this computer is replaced. Returns what didn't work."""
    problems: list[str] = []

    def file(rel) -> Path | None:
        p = top / str(rel or "")
        return p if rel and p.is_file() and _inside(p, top) else None

    def moved(text: str) -> str:
        return text.replace(old_home + "/", home + "/") if old_home and home and old_home != home else text

    def ok(cmd, **kw) -> bool:
        try:
            return subprocess.run(cmd, **kw).returncode == 0
        except (OSError, subprocess.SubprocessError):
            return False

    def failed(what: str, msg: str) -> None:
        problems.append(what)
        print(msg, flush=True)
    release = _os_release()
    for d in extras.get("debs") or []:
        name, deb = d.get("name", ""), file(d.get("file"))
        if deb is not None and ok(["apt-get", "install", "-y", str(deb)]):
            continue
        upstream = _upstream_deb(d.get("homepage", ""), top, release)
        if upstream is not None and ok(["apt-get", "install", "-y", str(upstream)]):
            print(f"Installed {name} from {d['homepage']} (the old computer's copy doesn't work here)", flush=True)
            continue
        where = f" from {d['homepage']}" if d.get("homepage") else ""
        failed(name, f"Couldn't install {name}; get the version made for this computer{where}")

    system = extras.get("system") or {}
    for rel in system.get("files") or []:
        tar = file(rel)
        if tar is None:
            continue
        with tarfile.open(tar) as t:
            for m in t:
                if not m.name.startswith(("usr/local/", "opt/")) or os.path.lexists(root / m.name):
                    continue
                try:
                    t.extract(m, root, filter="tar")
                except (OSError, tarfile.TarError):
                    failed(f"/{m.name}", f"Couldn't put back /{m.name}")
    problems += _restore_mounts(system.get("mounts") or [], moved, home, root, ok)
    units = root / "etc/systemd/system"
    written = []
    for u in system.get("units") or []:
        name = u.get("name", "")
        if UNIT_NAME.fullmatch(name) and not (units / name).exists():
            units.mkdir(parents=True, exist_ok=True)
            (units / name).write_text(moved(u.get("text", "")))
            written.append(u)
    for d in system.get("dropins") or []:
        rel = d.get("path", "")
        if re.fullmatch(r"[\w@.:-]+\.d/[\w@.:-]+\.conf", rel) and not (units / rel).exists():
            (units / rel).parent.mkdir(exist_ok=True)
            (units / rel).write_text(moved(d.get("text", "")))
    ok(["systemctl", "daemon-reload"])
    for u in written:
        if u.get("enabled") and not ok(["systemctl", "enable", "--now", u["name"]]):
            failed(u["name"], f"Your service {u['name']} didn't start here; see: systemctl status {u['name']}")

    docker = extras.get("docker") or {}
    if not (docker.get("containers") or docker.get("compose")):
        return problems
    if not shutil.which("docker"):
        ok(["apt-get", "install", "-y", "docker.io", "docker-compose"])
    ok(["systemctl", "enable", "--now", "docker.service"])
    for img in docker.get("images") or []:
        tar = file(img.get("file"))
        if tar is not None and not ok(["docker", "image", "inspect", img.get("tag", "")], capture_output=True):
            ok(["docker", "load", "-i", str(tar)])

    def fill(name: str) -> None:
        for v in docker.get("volumes") or []:
            tar = file(v.get("file"))
            if v.get("container") == name and tar is not None:
                with open(tar, "rb") as f:
                    if not ok(["docker", "cp", "-a", "-", f"{name}:{os.path.dirname(v['path'].rstrip('/')) or '/'}"],
                              stdin=f):
                        failed(f"{name} {v['path']}", f"Couldn't put back {name}'s data in {v['path']}")

    def exists(name: str) -> bool:
        return ok(["docker", "container", "inspect", name], capture_output=True)
    for c in docker.get("containers") or []:
        if exists(c["name"]):  # from an earlier try: started, its data left as it is
            ok(["docker", "start", c["name"]], capture_output=True)
            continue
        c = dict(c, binds=[[moved(src + "/").rstrip("/") if src != "/" else src, dest, rw]
                           for src, dest, rw in c.get("binds") or []])
        if not ok(["docker", "create", *system_extras.docker_run_args(c)]):
            failed(c["name"], f"Couldn't make your Docker container {c['name']} again")
            continue
        fill(c["name"])
        if not ok(["docker", "start", c["name"]]):
            failed(c["name"], f"Your Docker container {c['name']} didn't start; see: docker logs {c['name']}")
    for p in docker.get("compose") or []:
        folder = moved(p.get("dir", "") + "/").rstrip("/")
        files = [moved(f) for f in p.get("files") or []]
        if not folder or not files or not all(os.path.isfile(f) for f in files):
            failed(p.get("name", ""), f"Docker Compose project {p.get('name')}: its files didn't come along")
            continue
        compose = ["docker", "compose", "-p", p["name"], "--project-directory", folder]
        for f in files:
            compose += ["-f", f]
        env = dict(os.environ, **{k: moved(v) for k, v in (p.get("env") or {}).items()})
        if not any(exists(n) for n in p.get("containers") or []):
            if not ok([*compose, "up", "--no-start"], cwd=folder, env=env):
                failed(p["name"], f"Couldn't make Docker Compose project {p['name']} again")
                continue
            for n in p.get("containers") or []:
                fill(n)
        if not ok([*compose, "up", "-d"], cwd=folder, env=env):
            failed(p["name"], f"Docker Compose project {p['name']} didn't start; see: docker compose -p {p['name']} logs")
    return problems


def root_reinstall(spec_path: str) -> list[str]:
    """The part of reinstall_apps() that runs as root (through pkexec): package sources and their keys, Debian
    packages, Cherry Store apps, groups, background services that run without being logged in, and the
    computer's name. Returns what didn't work."""
    spec = json.loads(Path(spec_path).read_text())
    problems: list[str] = []
    os.environ.update(DEBIAN_FRONTEND="noninteractive", PATH="/usr/sbin:/usr/bin:/sbin:/bin")
    rel = _os_release()
    user = spec["user"]
    folder = Path("/etc/apt/sources.list.d")
    added = []
    for src in spec.get("sources", []):
        name = src.get("name", "")
        if not re.fullmatch(r"[\w.+-]+\.(list|sources)", name) or (folder / name).exists():
            continue
        for key_path, data in src.get("keys", {}).items():
            if (key_path.startswith(KEY_DIRS) and re.fullmatch(r"[\w.+-]+", Path(key_path).name)
                    and not Path(key_path).exists()):
                Path(key_path).parent.mkdir(parents=True, exist_ok=True)
                Path(key_path).write_bytes(base64.b64decode(data))
                os.chmod(key_path, 0o644)
        (folder / name).write_text(_retarget_source(src["text"], "debian" if _debian_based(rel) else rel.get("ID", ""),
                                                    rel.get("VERSION_CODENAME", "")))
        added.append(folder / name)
    for f in added:
        ok = subprocess.run(["apt-get", "update", "-o", f"Dir::Etc::sourcelist={f}", "-o", "Dir::Etc::sourceparts=-",
                             "-o", "APT::Get::List-Cleanup=0"], capture_output=True).returncode == 0
        if not ok:
            f.rename(f.with_name(f.name + ".disabled-by-cherry-move"))
            print(f"Package source {f.name} doesn't work here; left switched off", flush=True)
    subprocess.run(["apt-get", "update"], capture_output=True)
    wanted = sorted({APT_RENAMES.get(p, p) for p in spec.get("apt", [])})
    have = set(subprocess.run(["dpkg-query", "-W", "-f=${db:Status-Abbrev} ${Package}\n"],
                              capture_output=True, text=True).stdout.split("\n"))
    have = {line.split()[-1] for line in have if line.startswith("ii")}
    missing = [p for p in wanted if p not in have]
    known = subprocess.run(["apt-cache", "show", "--no-all-versions", *missing], capture_output=True,
                           text=True).stdout if missing else ""
    available = sorted({ln.split(": ", 1)[1] for ln in known.splitlines() if ln.startswith("Package: ")})
    print(f"Installing {len(available)} packages", flush=True)

    def install(pkgs):
        return subprocess.run(["apt-get", "install", "-y", *pkgs]).returncode == 0
    if available and not install(available):
        for i in range(0, len(available), 15):
            batch = available[i:i + 15]
            if not install(batch):
                for p in batch:
                    if not install([p]):
                        problems.append(p)
                        print(f"Couldn't install {p}", flush=True)
    if spec.get("cherry") and shutil.which("cherry"):
        if subprocess.run(["cherry", "install", "-y", *spec["cherry"]]).returncode != 0:
            problems.append("Cherry Store apps")
    if spec.get("extras"):
        problems += restore_extras(spec["extras"], Path(spec["extras_dir"]), spec.get("old_home"),
                                   spec.get("home") or "")
    existing = subprocess.run(["getent", "group"], capture_output=True, text=True).stdout
    names = {line.split(":", 1)[0] for line in existing.splitlines()}
    for g in spec.get("groups", []):
        if g in names and re.fullmatch(r"[\w.-]+", g):
            subprocess.run(["usermod", "-aG", g, user])
    if spec.get("linger"):
        subprocess.run(["loginctl", "enable-linger", user])
    name = spec.get("hostname") or ""
    if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9-]{0,62}", name) and name != socket.gethostname():
        old = socket.gethostname()
        subprocess.run(["hostnamectl", "set-hostname", name])
        try:
            hosts = Path("/etc/hosts").read_text()
            Path("/etc/hosts").write_text(re.sub(rf"(?m)^(127\.0\.1\.1\s+){re.escape(old)}\b", rf"\g<1>{name}", hosts))
        except OSError:
            pass
    return problems


def reinstall_apps(apps: dict, log: Callable[[str], None] = print, home: Path | None = None,
                   hostname: str | None = None) -> list[str]:
    """Puts back what the old computer had but the transfer left out to be downloaded again: package sources,
    Debian packages, Flatpak and Cherry Store apps, Ollama models (Ollama keeps listening where it did), Rust
    toolchains, Python environments and node_modules; then starts your own background services. `hostname`
    gives this computer the old one's name, so things that reach it by name still find it. Returns what didn't
    work; everything is also written to ~/.local/share/cherry/reinstall.log."""
    home = home or Path.home()
    old_home = apps.get("home") if str(apps.get("home", "")).startswith("/") else None
    logfile = home / REINSTALL_LOG
    logfile.parent.mkdir(parents=True, exist_ok=True)
    problems: list[str] = []
    out = open(logfile, "a", encoding="utf-8")

    def step(msg: str) -> None:
        out.write(f"\n== {msg}\n")
        out.flush()
        log(msg)

    def run(cmd, **kw) -> bool:
        out.write("$ " + " ".join(map(str, cmd)) + "\n")
        out.flush()
        try:
            return subprocess.run(cmd, stdout=out, stderr=subprocess.STDOUT, **kw).returncode == 0
        except OSError as e:
            out.write(f"{e}\n")
            return False

    try:
        spec = {"user": os.environ.get("USER") or home.name, "sources": apps.get("apt_sources") or [],
                "apt": [p for p in apps.get("apt") or [] if not APT_NEVER.match(p)],
                "cherry": [a for a in apps.get("cherry") or [] if a], "groups": apps.get("groups") or [],
                "linger": bool(apps.get("linger")), "hostname": hostname, "extras": apps.get("extras") or {},
                "extras_dir": str(home / EXTRAS_HOME), "home": str(home), "old_home": old_home}
        if any(spec[k] for k in ("sources", "apt", "cherry", "linger", "groups", "hostname", "extras")):
            step("Installing your apps and system settings…")
            spec_file = home / ".cache/cherry-move/reinstall.json"
            spec_file.parent.mkdir(parents=True, exist_ok=True)
            spec_file.write_text(json.dumps(spec))
            lib = str(Path(__file__).resolve().parent.parent)
            code = f"import json, sys; sys.path.insert(0, {lib!r}); from cherryos.transfer import root_reinstall; " \
                   f"print({ROOT_PROBLEMS!r} + json.dumps(root_reinstall(sys.argv[1])), flush=True)"
            start = logfile.stat().st_size
            if not run(["pkexec", "/usr/bin/python3", "-c", code, str(spec_file)]):
                problems.append("system apps")
            with open(logfile, encoding="utf-8", errors="replace") as f:
                f.seek(start)
                for line in f:
                    if line.startswith(ROOT_PROBLEMS):
                        try:
                            problems += json.loads(line[len(ROOT_PROBLEMS):])
                        except ValueError:
                            pass
            spec_file.unlink(missing_ok=True)
        flatpaks = [a for a in apps.get("flatpak") or [] if a]
        if flatpaks and shutil.which("flatpak"):
            step(f"Installing {len(flatpaks)} Flatpak apps…")
            run(["flatpak", "remote-add", "--user", "--if-not-exists", "flathub",
                 "https://dl.flathub.org/repo/flathub.flatpakrepo"])
            if not run(["flatpak", "install", "--user", "-y", "--noninteractive", "flathub", *flatpaks]):
                for a in flatpaks:
                    if not run(["flatpak", "install", "--user", "-y", "--noninteractive", "flathub", a]):
                        problems.append(a)
        snaps = [s for s in apps.get("snap") or [] if s not in ("bare", "snapd") and not
                 re.match(r"(core\d*|gnome-\d.*|gtk-common-themes|mesa-.*|snapd-desktop-integration|snap-store|"
                          r"firmware-updater|canonical-livepatch)$", s)]
        if snaps:
            if shutil.which("snap"):
                step(f"Installing {len(snaps)} Snap apps…")
                for s in snaps:
                    if not run(["snap", "install", s]):
                        problems.append(s)
            else:
                out.write(f"\nSnap apps you had (no Snap here, install them another way): {' '.join(snaps)}\n")
        rustup = home / ".cargo/bin/rustup"
        if apps.get("rustup") and rustup.exists():
            step("Installing your Rust toolchains…")
            for t in apps["rustup"]:
                if not run([str(rustup), "toolchain", "install", t]):
                    problems.append(f"Rust {t}")
        envs = apps.get("venvs") or []
        if envs:
            step(f"Rebuilding {len(envs)} Python environments…")
        for env in envs:
            path = Path(_rehome(env["path"], old_home, home))
            if (path / "pyvenv.cfg").exists():
                continue
            if not path.parent.is_dir():
                continue
            python = shutil.which(f"python{env.get('python')}") if env.get("python") else None
            if not run([python or "python3", "-m", "venv", str(path)]):
                problems.append(str(path))
                continue
            pinned = [r for r in env.get("packages", []) if not r.startswith("-e ")]
            local = [_rehome(r[3:], old_home, home) for r in env.get("packages", []) if r.startswith("-e ")]
            pip = [str(path / "bin/python"), "-m", "pip", "install"]
            # PyTorch's CUDA, ROCm and CPU-only builds (torch==2.5.1+cu124) are only on PyTorch's own index.
            for tag in sorted({m.group(1) for r in pinned if (m := re.search(r"\+(cu\d+|rocm[\d.]+|cpu)$", r))}):
                pip += ["--extra-index-url", f"https://download.pytorch.org/whl/{tag}"]
            req_file = path / "requirements-from-old-computer.txt"
            req_file.write_text("\n".join(pinned) + "\n")
            if pinned and not run([*pip, "-r", str(req_file)]):
                for r in pinned:
                    if not run([*pip, r]):
                        problems.append(f"{path.name}: {r}")
            for target in local:
                if Path(target).is_dir():
                    run([*pip, "--no-deps", "-e", target])
        projects = [Path(_rehome(p, old_home, home)) for p in apps.get("node") or []]
        projects = [p for p in projects if (p / "package.json").exists() and not (p / "node_modules").exists()]
        if projects:
            step(f"Rebuilding node_modules in {len(projects)} projects…")
        for p in projects:
            if (p / "pnpm-lock.yaml").exists() and shutil.which("pnpm"):
                cmd = ["pnpm", "install", "--frozen-lockfile"]
            elif (p / "yarn.lock").exists() and shutil.which("yarn"):
                cmd = ["yarn", "install"]
            elif ((p / "bun.lockb").exists() or (p / "bun.lock").exists()) and shutil.which("bun"):
                cmd = ["bun", "install"]
            elif (p / "package-lock.json").exists():
                cmd = ["npm", "ci"]
            else:
                cmd = ["npm", "install"]
            if not shutil.which(cmd[0]) or not run(cmd, cwd=p):
                problems.append(f"node_modules in {p}")
        step("Starting your background services…")
        run(["systemctl", "--user", "daemon-reload"])
        run(["systemctl", "--user", "start", "default.target"])
        models = apps.get("ollama") or []
        if models:
            ollama = shutil.which("ollama") or str(home / ".local/bin/ollama")
            run(["systemctl", "--user", "start", "ollama.service"])
            for _ in range(30):
                if run([ollama, "list"]):
                    break
                time.sleep(1)
            for i, m in enumerate(models, 1):
                step(f"Downloading Ollama model {m} ({i} of {len(models)})…")
                if not run([ollama, "pull", m]):
                    problems.append(f"Ollama {m}")
        for kind, tool, cmd in (("arduino", "arduino-cli", ["core", "install"]),
                                ("platformio", "pio", ["pkg", "install", "-g", "--platform"])):
            items = apps.get(kind) or []
            if items and shutil.which(tool):
                step(f"Installing {tool} packages…")
                for it in items:
                    run([tool, *cmd, it])
            elif items:
                out.write(f"\n{tool} packages you had (installed again when {tool} needs them): {' '.join(items)}\n")
        out.write(f"\nDone. Problems: {', '.join(problems) or 'none'}\n")
    finally:
        out.close()
    return problems


# ---------------------------------------------------------------- USB sticks

@dataclass
class Drive:
    device: str
    label: str
    size: int
    free: int
    mount: Path | None
    marker: dict | None = field(default=None)

    @property
    def title(self) -> str:
        return f"{self.label or self.device} ({human(self.size)})"


def human(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1000 or unit == "TB":
            return f"{n:.0f} {unit}" if unit in ("B", "KB") else f"{n:.1f} {unit}"
        n /= 1000
    return str(n)


def removable_drives(mount_new: bool = True) -> list[Drive]:
    """Mounted (or mountable) partitions on USB sticks and other removable drives."""
    try:
        out = subprocess.run(["lsblk", "-J", "-b", "-o", "NAME,PATH,RM,HOTPLUG,TRAN,MOUNTPOINT,LABEL,SIZE,FSAVAIL,FSTYPE,TYPE"],
                             capture_output=True, text=True, timeout=10).stdout
        tree = json.loads(out or "{}").get("blockdevices", [])
    except (OSError, ValueError, subprocess.SubprocessError):
        return []
    drives = []

    def visit(dev, removable):
        rem = removable or str(dev.get("rm")) in ("1", "True", "true") or str(dev.get("hotplug")) in ("1", "True", "true") \
            or dev.get("tran") == "usb"
        if dev.get("type") == "loop" or dev.get("tran") in ("nvme", "sata"):
            rem = False if dev.get("tran") in ("nvme", "sata") else rem
        for child in dev.get("children") or []:
            visit(child, rem)
        fstype = dev.get("fstype")
        if not rem or not fstype or fstype in ("swap", "crypto_LUKS", "iso9660", "squashfs"):
            return
        mount = dev.get("mountpoint")
        if not mount and mount_new and fstype in ("vfat", "exfat", "ntfs", "ext4", "ext3", "ext2", "btrfs", "xfs"):
            mount = udisks_mount(dev["path"])
        if mount and (mount in ("/", "/boot", "/boot/efi") or mount.startswith("/run/live")):
            return
        free = 0
        if mount:
            try:
                free = shutil.disk_usage(mount).free
            except OSError:
                pass
        d = Drive(dev["path"], dev.get("label") or "", int(dev.get("size") or 0), free, Path(mount) if mount else None)
        if d.mount:
            d.marker = read_marker(d.mount)
        drives.append(d)

    for dev in tree:
        visit(dev, False)
    return drives


def udisks_mount(device: str) -> str | None:
    try:
        out = subprocess.run(["udisksctl", "mount", "-b", device, "--no-user-interaction"],
                             capture_output=True, text=True, timeout=20).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    if " at " in out:
        return out.rsplit(" at ", 1)[1].strip().rstrip(".")
    return None


def read_marker(mount: Path) -> dict | None:
    try:
        data = json.loads((mount / MARKER_DIR / MARKER).read_text())
        return data if data.get("format") == FORMAT and data.get("code") else None
    except (OSError, ValueError):
        return None


# ---------------------------------------------------------------- restore after installing

def user_pending_file() -> Path:
    return Path.home() / ".config/cherry/pending-transfer.json"


def save_pending(code: str, label: str, drive: Path | None = None, system: bool = False,
                 remote: dict | None = None) -> Path:
    """Remembers a transfer to restore after Cherry OS is installed (or at next login). The stick is only
    needed again if the files are stored on it; `remote` describes someone's own cloud or NAS (no secrets)."""
    marker = read_marker(drive) if drive else None
    on_stick = bool(marker and marker.get("storage") == "usb")
    data = json.dumps({"code": code, "label": label, "drive_label": _drive_label(drive) if on_stick else None,
                       "several_sticks": bool(on_stick and (not marker.get("final", True) or marker.get("parts", 1) > 1)),
                       "remote": remote or (marker or {}).get("remote")})
    if system:
        subprocess.run(["sudo", "-n", "install", "-d", "-m", "755", str(PENDING_SYSTEM.parent)], check=True)
        subprocess.run(["sudo", "-n", "tee", str(PENDING_SYSTEM)], input=data, text=True,
                       stdout=subprocess.DEVNULL, check=True)
        subprocess.run(["sudo", "-n", "chmod", "600", str(PENDING_SYSTEM)], check=True)
        return PENDING_SYSTEM
    path = user_pending_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(data)
    path.chmod(0o600)
    return path


def _drive_label(drive: Path) -> str:
    return drive.name


def load_pending() -> dict | None:
    try:
        return json.loads(user_pending_file().read_text())
    except (OSError, ValueError):
        return None


def clear_pending() -> None:
    try:
        user_pending_file().unlink()
    except OSError:
        pass


def adopt_pending(username: str, root: Path = Path("/")) -> bool:
    """Root, inside the installed system (Calamares): hand the live session's pending transfer to the new user."""
    src = root / str(PENDING_SYSTEM).lstrip("/")
    if not src.exists():
        return False
    import pwd
    user = pwd.getpwnam(username)
    dest = Path(user.pw_dir) / ".config/cherry/pending-transfer.json"
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy(src, dest)
    for p in (dest.parent.parent, dest.parent, dest):
        os.chown(p, user.pw_uid, user.pw_gid)
    dest.chmod(0o600)
    src.unlink()
    return True
