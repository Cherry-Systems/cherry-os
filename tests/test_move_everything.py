"""Cherry Move brings a whole Linux home folder: app settings, sign-ins, game saves, servers, editor chats and
source code; leaves behind only caches and what's downloaded again (from the list it keeps), and rebuilds that.
Run: make test
"""
import hashlib
import io
import json
import os
import sqlite3
import stat
import sys
import tarfile
import threading
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "desktop/lib"))

from cherryos import transfer  # noqa: E402
from test_transfer import cats_for  # noqa: E402

# Left behind on purpose (relative to the home folder); everything else must arrive.
LEFT = (".cache/", ".local/share/Trash/", ".ollama/models/blobs/", ".var/app/com.mojang.Minecraft/cache/",
        "Projects/demo/.venv/", "Projects/tool/env/", "Projects/demo/node_modules/", ".rustup/toolchains/",
        ".local/share/flatpak/", ".config/Cursor/User/globalStorage/state.vscdb-wal",
        ".config/Cursor/User/globalStorage/state.vscdb-shm", ".ollama/models/manifests/")


def write(path: Path, data="x") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(data, bytes):
        path.write_bytes(data)
    else:
        path.write_text(data)
    return path


def workspace_id(folder: Path) -> str:
    return hashlib.md5(f"{folder}{os.stat(folder).st_ino}".encode()).hexdigest()


def make_linux_home(root: Path):
    """A home folder like a real one, and an open database with changes still only in its write-ahead log
    (as Cursor's chats are while Cursor runs). Returns (home, the open connection)."""
    home = root / "home"
    write(home / "Documents/plan.txt", "plan")
    write(home / os.fsdecode(b"Downloads/\x01\x90F@@\xe8 not UTF-8"), b"a name that isn't text")
    write(home / "notes.txt", "a loose file in the home folder")
    write(home / ".config/SomeApp/settings.json", '{"theme": "dark"}')
    write(home / ".config/google-chrome/Default/Bookmarks", "{}")
    write(home / "snap/foo/common/data.txt", "snap app data")
    write(home / ".var/app/com.mojang.Minecraft/data/minecraft/saves/World/level.dat", b"\x00save")
    write(home / ".var/app/com.mojang.Minecraft/cache/blob", "cache")
    write(home / ".cache/thumbnails/a.png", "cache")
    write(home / ".local/share/Trash/files/old.txt", "deleted")
    write(home / ".cursor/projects/home-proj/agent-transcripts/chat.jsonl", '{"role": "user"}\n')
    write(home / ".cursor/mcp.json", "{}")
    # Apps' own node_modules come as they are (an MCP server, npm -g running a background service).
    write(home / ".cursor/mcp-servers/minecraft/package.json", "{}")
    write(home / ".cursor/mcp-servers/minecraft/node_modules/mineflayer/index.js", "bot")
    write(home / ".npm-global/lib/node_modules/openclaw/dist/index.js", "gateway")
    # Apps' own node_modules come as they are: an MCP server, and a server installed with npm -g.
    write(home / ".cursor/mcp-servers/minecraft-mcp/package.json", '{"name": "minecraft-mcp"}')
    write(home / ".cursor/mcp-servers/minecraft-mcp/node_modules/mineflayer/index.js", "module.exports = {}")
    write(home / ".npm-global/lib/node_modules/openclaw/dist/index.js", "// gateway")
    write(home / ".bash_history", "ls\n")
    write(home / ".ssh/id_ed25519", "key")
    # Ollama: the program, its service (listening on the network) and models downloaded again by name.
    write(home / ".local/ollama/bin/ollama", "#!/bin/sh\n").chmod(0o755)
    (home / ".local/bin").mkdir(parents=True)
    os.symlink(home / ".local/ollama/bin/ollama", home / ".local/bin/ollama")
    unit = write(home / ".config/systemd/user/ollama.service",
                 f"[Service]\nExecStart={home}/.local/bin/ollama serve\nEnvironment=OLLAMA_HOST=0.0.0.0:11434\n")
    (unit.parent / "default.target.wants").mkdir()
    os.symlink(unit, unit.parent / "default.target.wants/ollama.service")
    write(home / ".ollama/id_ed25519", "ollama key")
    write(home / ".ollama/models/manifests/registry.ollama.ai/library/llama3.1/8b", "{}")
    write(home / ".ollama/models/manifests/registry.ollama.ai/someone/tiny/latest", "{}")
    write(home / ".ollama/models/blobs/sha256-abc", b"\x00" * 1000)
    write(home / ".rustup/toolchains/stable-x86_64-unknown-linux-gnu/bin/rustc", "rustc")
    write(home / ".rustup/settings.toml", 'default_toolchain = "stable"\n')
    write(home / ".local/share/flatpak/app/org.example.App/files/bin", "app")
    # Source code, with a Python environment and node_modules rebuilt on the new computer.
    write(home / "Projects/demo/main.py", "print('hi')\n")
    write(home / "Projects/demo/package.json", '{"name": "demo"}')
    write(home / "Projects/demo/node_modules/left-pad/index.js", "module.exports = 1")
    write(home / "Projects/demo/.venv/pyvenv.cfg", "home = /usr/bin\nversion = 3.12.3\n")
    write(home / "Projects/demo/.venv/lib/python3.12/site-packages/requests-2.31.0.dist-info/METADATA", "")
    write(home / "Projects/demo/.venv/lib/python3.12/site-packages/pip-24.0.dist-info/METADATA", "")
    write(home / "Projects/demo/.venv/lib/python3.12/site-packages/demo-0.1.dist-info/direct_url.json",
          json.dumps({"url": f"file://{home}/Projects/demo", "dir_info": {"editable": True}}))
    write(home / "Projects/tool/env/pyvenv.cfg", "version_info = 3.11.9\n")
    write(home / "Projects/tool/tool.py", "")
    # Cursor: chats in a live database, and a project's history under its workspace id.
    db = home / ".config/Cursor/User/globalStorage/state.vscdb"
    db.parent.mkdir(parents=True)
    conn = sqlite3.connect(db)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA wal_autocheckpoint=0")
    conn.execute("CREATE TABLE cursorDiskKV (key TEXT PRIMARY KEY, value BLOB)")
    conn.executemany("INSERT INTO cursorDiskKV VALUES (?, ?)",
                     [(f"composerData:{i}", os.urandom(200)) for i in range(500)])
    conn.commit()
    assert (db.parent / "state.vscdb-wal").stat().st_size > 0
    project = home / "Projects/demo"
    ws = home / ".config/Cursor/User/workspaceStorage/0123456789abcdef0123456789abcdef"
    write(ws / "workspace.json", json.dumps({"folder": f"file://{project}"}))
    write(ws / "state.vscdb", b"workspace state")
    return home, conn


def relative_files(home: Path) -> dict:
    out = {}
    for p in home.rglob("*"):
        if p.is_symlink():
            out[str(p.relative_to(home))] = ("link", os.readlink(p))
        elif p.is_file():
            out[str(p.relative_to(home))] = ("file", p.read_bytes())
    return out


def is_left(rel: str) -> bool:
    return any(rel == pre.rstrip("/") or rel.startswith(pre) for pre in LEFT)


def check_everything_arrived(old: Path, new: Path, manifest: dict):
    before, after = relative_files(old), relative_files(new)
    for rel, (kind, data) in before.items():
        if is_left(rel):
            assert rel not in after, f"{rel} should stay behind"
            continue
        if rel.startswith(".config/Cursor/User/workspaceStorage/"):
            continue  # renamed to the id the folder has here, checked below
        assert rel in after, f"{rel} didn't arrive"
        if rel.endswith("state.vscdb") and "globalStorage" in rel:
            continue
        if kind == "link":
            assert after[rel] == ("link", data.replace(str(old), str(new))), rel
        elif rel.endswith(".service"):
            assert after[rel][1] == data.replace(str(old).encode(), str(new).encode()), rel
        else:
            assert after[rel] == (kind, data), rel
    # The chats: every row, though half of them were only in the write-ahead log.
    db = new / ".config/Cursor/User/globalStorage/state.vscdb"
    conn = sqlite3.connect(db)
    assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    assert conn.execute("SELECT count(*) FROM cursorDiskKV WHERE key LIKE 'composerData:%'").fetchone()[0] == 1000
    conn.close()
    # The project's history found under the id its folder has here.
    ws = new / ".config/Cursor/User/workspaceStorage" / workspace_id(new / "Projects/demo")
    assert json.loads((ws / "workspace.json").read_text())["folder"] == f"file://{new}/Projects/demo"
    assert (ws / "state.vscdb").read_bytes() == b"workspace state"
    apps = manifest["apps"]
    assert apps["home"] == str(old)
    assert sorted(apps["ollama"]) == ["llama3.1:8b", "someone/tiny:latest"]
    assert apps["rustup"] == ["stable-x86_64-unknown-linux-gnu"]
    envs = {e["path"]: e for e in apps["venvs"]}
    assert set(envs) == {str(old / "Projects/demo/.venv"), str(old / "Projects/tool/env")}
    assert envs[str(old / "Projects/demo/.venv")]["python"] == "3.12"
    assert sorted(envs[str(old / "Projects/demo/.venv")]["packages"]) == [f"-e {old}/Projects/demo",
                                                                          "requests==2.31.0"]
    assert envs[str(old / "Projects/tool/env")]["python"] == "3.11"
    assert apps["node"] == [str(old / "Projects/demo")]


def test_whole_home_folder_moves(tmp_path, monkeypatch):
    home, conn = make_linux_home(tmp_path)
    cats = cats_for(home, monkeypatch)
    ids = {c.id for c in cats}
    assert {"apps", "other", "browsers", "personal"} <= ids
    arcs = [arc for c in cats for prefix, base in c.paths for _p, arc in transfer._walk(base, prefix, home, c.skip)]
    assert len(arcs) == len(set(arcs)), "something is brought twice"
    for c in cats:
        assert c.size == sum(transfer.measure(p, home, c.skip)[0] for _, p in c.paths)
    stick = tmp_path / "usb"
    stick.mkdir()
    conn.executemany("INSERT INTO cursorDiskKV VALUES (?, ?)",
                     [(f"composerData:{i}", os.urandom(200)) for i in range(500, 1000)])
    conn.commit()
    result = transfer.send(cats, "usb", drive=stick)
    conn.close()
    new = tmp_path / "new"
    new.mkdir()
    manifest = transfer.restore(result.code, drive=stick, home=new)
    check_everything_arrived(home, new, manifest)
    assert not list((home / ".cache/cherry-move").glob("snapshot-*"))


def test_audit_finds_nothing_missing(tmp_path, monkeypatch):
    home, conn = make_linux_home(tmp_path)
    conn.close()
    cats = cats_for(home, monkeypatch)
    packed = {str(p) for c in cats for prefix, base in c.paths for p, _arc in transfer._walk(base, prefix, home, c.skip)}
    why = transfer.left_behind_rules(home)
    for p in home.rglob("*"):
        if (p.is_file() or p.is_symlink()) and str(p) not in packed:
            rel = str(p.relative_to(home))
            assert why(rel) or rel.endswith(("-wal", "-shm")), f"{rel} left behind for no reason"


def test_new_computers_app_settings_give_way(tmp_path, monkeypatch):
    home, conn = make_linux_home(tmp_path)
    conn.close()
    stick = tmp_path / "usb"
    stick.mkdir()
    new = tmp_path / "new"
    write(new / ".config/SomeApp/settings.json", '{"theme": "light", "fresh": true}')
    write(new / ".config/dconf/user", b"new desktop settings")
    write(home / ".config/dconf/user", b"the old computer's desktop settings")
    # Cursor was opened here first: an empty database with its own write-ahead log.
    fresh = new / ".config/Cursor/User/globalStorage/state.vscdb"
    write(fresh, b"SQLite format 3\x00 new and empty")
    write(Path(f"{fresh}-wal"), b"new log")
    result = transfer.send(cats_for(home, monkeypatch), "usb", drive=stick)
    got = transfer.restore(result.code, drive=stick, home=new)
    assert (new / ".config/SomeApp/settings.json").read_text() == '{"theme": "dark"}'
    assert '"fresh"' in (new / ".config/SomeApp/settings (before Cherry Move).json").read_text()
    assert (new / ".config/dconf/user").read_bytes() == b"new desktop settings"
    assert (new / ".config/dconf/user (from old computer)").read_bytes() == b"the old computer's desktop settings"
    assert not Path(f"{fresh}-wal").exists()
    assert (fresh.parent / "state (before Cherry Move).vscdb-wal").read_bytes() == b"new log"
    conn = sqlite3.connect(fresh)
    assert conn.execute("SELECT count(*) FROM cursorDiskKV").fetchone()[0] == 500
    conn.close()
    assert got["replaced_files"] >= 2


def test_folder_permissions_come_back(tmp_path, monkeypatch):
    """Go's module cache is read-only folders; private folders stay private even where the new computer made
    them already (less strictly)."""
    home = tmp_path / "home"
    cache = home / "go/pkg/mod/example.com/lib@v1.0.0"
    write(cache / "lib.go", "package lib")
    write(home / ".config/app/settings.json", "{}")
    for d in (cache, cache.parent):
        d.chmod(0o555)
    (home / ".config").chmod(0o700)
    stick = tmp_path / "usb"
    stick.mkdir()
    try:
        result = transfer.send(cats_for(home, monkeypatch), "usb", drive=stick)
        new = tmp_path / "new"
        (new / ".config").mkdir(parents=True, mode=0o775)
        (new / ".config").chmod(0o775)
        for _ in range(2):  # and again, into the read-only folders the first restore left
            transfer.restore(result.code, drive=stick, home=new)
        got = new / "go/pkg/mod/example.com/lib@v1.0.0"
        assert (got / "lib.go").read_text() == "package lib"
        assert stat.S_IMODE(got.stat().st_mode) == 0o555 and stat.S_IMODE(got.parent.stat().st_mode) == 0o555
        assert stat.S_IMODE((new / ".config").stat().st_mode) == 0o700
    finally:
        for root in (home, tmp_path / "new"):
            for d in root.rglob("*"):
                if d.is_dir() and not d.is_symlink():
                    d.chmod(0o755)


def test_restore_never_writes_outside_home(tmp_path, monkeypatch):
    outside = tmp_path / "outside"
    outside.mkdir()
    new = tmp_path / "new"
    new.mkdir()
    os.symlink(outside, new / "Escape")
    tar_path = tmp_path / "evil.tar"
    with tarfile.open(tar_path, "w") as tar:
        info = tarfile.TarInfo("home/Escape/pwned.txt")
        data = b"nope"
        info.size = len(data)
        tar.addfile(info, io.BytesIO(data))
        link = tarfile.TarInfo("home/.config/abs-link")
        link.type, link.linkname = tarfile.SYMTYPE, "/etc/passwd"
        tar.addfile(link)

    class Reader:
        def __init__(self, *_a, **_k):
            self.f = open(tar_path, "rb")

        def __enter__(self):
            return self.f

        def __exit__(self, *_a):
            self.f.close()

    monkeypatch.setattr(transfer, "source_for", lambda *a, **k: (transfer.new_code(), None))
    monkeypatch.setattr(transfer, "read_manifest", lambda *_a: {"home": "/home/old"})
    monkeypatch.setattr(transfer, "ChunkReader", Reader)
    monkeypatch.setattr(transfer, "remember_restored", lambda *_a: None)
    transfer.restore("x", home=new)
    assert not (outside / "pwned.txt").exists()
    assert os.readlink(new / ".config/abs-link") == "/etc/passwd"  # a link may point anywhere; nothing written through it


def test_ubuntu_package_sources_point_at_debian():
    tailscale = ("deb [signed-by=/usr/share/keyrings/tailscale-archive-keyring.gpg] "
                 "https://pkgs.tailscale.com/stable/ubuntu noble main\n")
    assert transfer._retarget_source(tailscale, "debian", "trixie") == tailscale.replace("ubuntu noble", "debian trixie")
    deb822 = "Types: deb\nURIs: https://download.docker.com/linux/ubuntu\nSuites: noble\nComponents: stable\n"
    assert "linux/debian\nSuites: trixie" in transfer._retarget_source(deb822, "debian", "trixie")
    cursor = "Types: deb\nURIs: https://downloads.cursor.com/aptrepo\nSuites: stable\n"
    assert transfer._retarget_source(cursor, "debian", "trixie") == cursor
    assert transfer._retarget_source(tailscale, "ubuntu", "noble") == tailscale
    assert transfer.DISTRO_SOURCES.search("http://au.archive.ubuntu.com/ubuntu/")
    assert transfer.DISTRO_SOURCES.search("https://esm.ubuntu.com/apps/ubuntu")
    assert not transfer.DISTRO_SOURCES.search("https://pkgs.tailscale.com/stable/ubuntu")


def test_system_packages_from_another_distro_are_not_reinstalled():
    never = ["linux-generic-hwe-24.04", "grub-efi-amd64-signed", "shim-signed", "nvidia-driver-580-open", "init",
             "ubuntu-desktop-minimal", "language-pack-en", "pulseaudio-dlna", "systemd", "gdm3"]
    keep = ["cursor", "docker.io", "nodejs", "tailscale", "vlc", "git", "build-essential", "ffmpeg", "golang-go"]
    assert all(transfer.APT_NEVER.match(p) for p in never)
    assert not any(transfer.APT_NEVER.match(p) for p in keep)
    assert transfer.reinstall_count({"apt": never + keep, "flatpak": ["a"], "ollama": ["m:1"]}) == len(keep) + 2


def test_reinstall_rebuilds_environments_and_starts_services(tmp_path, monkeypatch):
    fake = tmp_path / "bin"
    fake.mkdir()
    calls = tmp_path / "calls"
    for tool in ("systemctl", "npm", "ollama", "flatpak", "pkexec"):
        write(fake / tool, f'#!/bin/sh\necho "{tool} $*" >> {calls}\n'
                           + ('[ "$1" = ci ] && mkdir node_modules\n' if tool == "npm" else "")).chmod(0o755)
    monkeypatch.setenv("PATH", f"{fake}:{os.environ['PATH']}")
    old, new = "/home/old", tmp_path / "home"
    write(new / "Projects/web/package.json", "{}")
    write(new / "Projects/web/package-lock.json", "{}")
    write(new / "Projects/py/app.py", "")
    apps = {"home": old, "node": [f"{old}/Projects/web"], "ollama": ["llama3.1:8b"],
            "venvs": [{"path": f"{old}/Projects/py/.venv", "python": "3.99", "packages": []}]}
    problems = transfer.reinstall_apps(apps, log=lambda _m: None, home=new)
    assert problems == []
    assert (new / "Projects/py/.venv/pyvenv.cfg").exists()
    assert (new / "Projects/web/node_modules").is_dir()
    log = calls.read_text()
    assert "systemctl --user daemon-reload" in log and "systemctl --user start default.target" in log
    assert "ollama pull llama3.1:8b" in log
    assert "pkexec" not in log  # nothing needed root
    assert "Done. Problems: none" in (new / transfer.REINSTALL_LOG).read_text()


def test_python_env_and_ollama_lists(tmp_path):
    home, conn = make_linux_home(tmp_path)
    conn.close()
    assert transfer.ollama_models(home) == ["llama3.1:8b", "someone/tiny:latest"]
    env = transfer.python_env(str(home / "Projects/demo/.venv"))
    assert env["python"] == "3.12" and "requests==2.31.0" in env["packages"]
    assert not any(p.startswith("pip==") for p in env["packages"])


@pytest.mark.parametrize("rel,reason", [
    (".ollama/models", "reinstall"), (".ollama/id_ed25519", None), (".var/app/org.x.Y/cache", "cache"),
    (".var/app/org.x.Y/data", None), (".local/share/flatpak", "reinstall"), (".rustup/settings.toml", None),
    (".local/share/Steam/steamapps/common", "reinstall"), (".local/share/Steam/steamapps/compatdata", None),
])
def test_leave_rules(rel, reason):
    assert transfer.leave_reason(rel) == reason


def lock_like_chromium(db: Path) -> sqlite3.Connection:
    """A sign-in database the way a running Chromium-based browser holds it: write-ahead log, locked for good."""
    db.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db)
    conn.execute("PRAGMA locking_mode=EXCLUSIVE")
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("CREATE TABLE cookies (host TEXT, value TEXT)")
    conn.execute("INSERT INTO cookies VALUES ('example.com', 'signed in')")
    conn.commit()
    probe = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=0.1)
    with pytest.raises(sqlite3.OperationalError):
        probe.execute("SELECT count(*) FROM sqlite_master").fetchone()
    probe.close()
    return conn


def check_signed_in(db: Path):
    conn = sqlite3.connect(db)
    assert conn.execute("SELECT value FROM cookies").fetchall() == [("signed in",)]
    conn.close()


def test_running_browser_doesnt_stop_the_move(tmp_path, monkeypatch):
    home = tmp_path / "home"
    db = home / ".config/chromium/Default/Cookies"
    browser = lock_like_chromium(db)
    write(home / "Documents/notes.txt", "notes")
    monkeypatch.setattr(transfer, "SNAPSHOT_PATIENCE", 1)
    stick = tmp_path / "usb"
    stick.mkdir()
    result = transfer.send(cats_for(home, monkeypatch), "usb", drive=stick)
    browser.close()
    assert not result.skipped
    new = tmp_path / "new"
    new.mkdir()
    transfer.restore(result.code, drive=stick, home=new)
    check_signed_in(new / ".config/chromium/Default/Cookies")
    assert (new / "Documents/notes.txt").read_text() == "notes"


def test_open_database_in_a_folder_whose_name_isnt_text(tmp_path, monkeypatch):
    home = tmp_path / "home"
    folder = home / os.fsdecode(b"Projects/old \xff\xfe stuff?#%")
    folder.mkdir(parents=True)
    conn = sqlite3.connect(folder / "app.db")
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA wal_autocheckpoint=0")
    conn.execute("CREATE TABLE t (v TEXT)")
    conn.execute("INSERT INTO t VALUES ('only in the log')")
    conn.commit()
    stick = tmp_path / "usb"
    stick.mkdir()
    result = transfer.send(cats_for(home, monkeypatch), "usb", drive=stick)
    conn.close()
    new = tmp_path / "new"
    new.mkdir()
    transfer.restore(result.code, drive=stick, home=new)
    got = sqlite3.connect(new / folder.relative_to(home) / "app.db")
    assert got.execute("SELECT v FROM t").fetchall() == [("only in the log",)]
    got.close()


def test_copy_of_a_file_written_to_while_packed_keeps_its_own_contents(tmp_path, monkeypatch):
    home = tmp_path / "home"
    (home / "Documents").mkdir(parents=True)
    (home / "Videos").mkdir()
    disk = os.urandom(2 * 1024 * 1024)
    for p in (home / "Documents" / "disk.img", home / "Videos" / "disk backup.img"):
        p.write_bytes(disk)
        os.utime(p, (1_500_000_000, 1_500_000_000))

    class WrittenTo(transfer._Exactly):  # a running VM writes to its disk while it's packed
        def read(self, n=-1):
            if self.f.name.endswith("disk.img") and not getattr(self.f, "written", False):
                self.f.written = True
                with open(self.f.name, "r+b") as w:
                    w.seek(1024 * 1024)
                    w.write(b"\xee" * 4096)
            return super().read(n)
    monkeypatch.setattr(transfer, "_Exactly", WrittenTo)
    stick = tmp_path / "usb"
    stick.mkdir()
    result = transfer.send(cats_for(home, monkeypatch), "usb", drive=stick)
    new = tmp_path / "new"
    new.mkdir()
    transfer.restore(result.code, drive=stick, home=new)
    assert (new / "Videos" / "disk backup.img").read_bytes() == disk


@pytest.mark.parametrize("stop_after", [None, 1, 2])
def test_file_that_cant_be_read_to_the_end_stays_behind(tmp_path, monkeypatch, stop_after):
    home = tmp_path / "home"
    (home / "Documents").mkdir(parents=True)
    fine, bad = os.urandom(300_000), os.urandom(300_000)
    write(home / "Documents" / "a fine.bin", fine)
    write(home / "Documents" / "bad sector.bin", bad)
    write(home / "Documents" / "copy of bad sector.bin", bad)
    write(home / "Documents" / "z after.txt", "after")

    class BadSector:  # the old computer's disk can't read the second half
        def __init__(self, f):
            self.f, self.name = f, f.name

        def read(self, n):
            if self.f.tell() >= 150_000:
                raise OSError(5, "Input/output error")
            return self.f.read(min(n, 150_000 - self.f.tell()))

    class Failing(transfer._Exactly):
        def __init__(self, f, size):
            super().__init__(BadSector(f) if f.name.endswith(os.sep + "bad sector.bin") else f, size)
    monkeypatch.setattr(transfer, "_Exactly", Failing)
    monkeypatch.setattr(transfer, "CHECKPOINT_EVERY", 0)
    stick = tmp_path / "usb"
    stick.mkdir()
    result = transfer.send(cats_for(home, monkeypatch), "usb", drive=stick)
    assert result.skipped == [str(home / "Documents" / "bad sector.bin")]
    new = tmp_path / "new"
    new.mkdir()
    if stop_after:
        stop, calls = threading.Event(), []

        def progress(done, total, name):
            calls.append(name)
            if len(calls) == stop_after:
                stop.set()
        with pytest.raises(InterruptedError):
            transfer.restore(result.code, drive=stick, home=new, progress=progress, cancel=stop)
    transfer.restore(result.code, drive=stick, home=new)
    docs = new / "Documents"
    assert sorted(p.name for p in docs.iterdir()) == ["a fine.bin", "copy of bad sector.bin", "z after.txt"]
    assert (docs / "a fine.bin").read_bytes() == fine
    assert (docs / "copy of bad sector.bin").read_bytes() == bad
    assert (docs / "z after.txt").read_text() == "after"


def test_file_that_shrinks_while_packed_keeps_the_stream_valid(tmp_path):
    f = tmp_path / "log"
    f.write_bytes(b"a" * 100)
    with open(f, "rb") as fh:
        reader = transfer._Exactly(fh, 150)
        data = reader.read(1000)
    assert data == b"a" * 100 + bytes(50) and reader.read(10) == b""
    assert stat.S_ISREG(os.stat(f).st_mode)
