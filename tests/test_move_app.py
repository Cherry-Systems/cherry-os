"""The Windows/macOS/Linux Cherry Move app (move/, in Go) must make transfers that Cherry OS's Python restore reads.
Run: make test (skipped when Go isn't installed)
"""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "desktop/lib"))

from cherryos import transfer  # noqa: E402
from test_transfer import MB, make_home, same_tree  # noqa: E402

MOVE = Path(__file__).resolve().parent.parent / "move"
BIN = os.environ.get("CHERRY_MOVE_BIN")  # a prebuilt app, for running these where Go isn't installed
pytestmark = pytest.mark.skipif(not BIN and shutil.which("go") is None, reason="Go isn't installed")


@pytest.fixture(scope="module")
def app(tmp_path_factory):
    if BIN:
        return Path(BIN).resolve()
    exe = tmp_path_factory.mktemp("bin") / "cherry-move"
    subprocess.run(["go", "build", "-o", str(exe), "."], cwd=MOVE, check=True)
    return exe


NO_EXTRAS = 'import json; print(json.dumps({"debs": [], "system": {}, "docker": {}}))\n'


def run_send(app, home: Path, *args, extras: str = NO_EXTRAS) -> dict:
    env = {k: v for k, v in os.environ.items() if not k.startswith("XDG_")}
    env["HOME"] = str(home)
    env["XDG_DATA_HOME"] = str(home.parent / "app-state")  # keeps the app's log out of the files being moved
    stand_in = home.parent / "system_extras.py"
    stand_in.write_text(extras)
    env["CHERRY_MOVE_EXTRAS"] = str(stand_in)
    out = subprocess.run([str(app), "send", *args], env=env, check=True, capture_output=True, text=True)
    return json.loads(out.stdout)


def test_one_stick(app, tmp_path):
    home = make_home(tmp_path)
    (home / ".ssh").mkdir(mode=0o700)
    (home / ".ssh" / "id_ed25519").write_text("key\n")
    (home / ".ssh" / "id_ed25519").chmod(0o600)
    stick = tmp_path / "usb"
    stick.mkdir()
    res = run_send(app, home, "--to", "usb", "--usb", str(stick))
    assert res["sticks"] == 1 and res["files"] == 8 and not res["skipped"]
    marker = transfer.read_marker(stick)
    assert marker["format"] == "cherry-move/2" and marker["final"] and marker["code"] == res["code"]
    stored = sum(f.stat().st_size for f in (stick / "CHERRY-TRANSFER").rglob("*.bin"))
    assert stored < 3 * MB + len(b"x" * 300_000)  # the photo is stored once and the text compressed
    new = tmp_path / "new"
    new.mkdir()
    got = transfer.restore(res["code"], drive=stick, home=new)
    assert got["restored_files"] == 8
    same_tree(home, new)
    assert (new / ".ssh").stat().st_mode & 0o777 == 0o700
    assert (new / ".ssh" / "id_ed25519").stat().st_mode & 0o777 == 0o600


def test_several_sticks(app, tmp_path):
    home = make_home(tmp_path, big=50 * MB)
    sticks = [tmp_path / f"stick{n}" for n in (1, 2, 3)]
    for s in sticks:
        s.mkdir()
    res = run_send(app, home, "--usb", str(sticks[0]), "--next-sticks", f"{sticks[1]},{sticks[2]}",
                   "--stick-capacity", str(30 * MB))
    assert res["sticks"] == 3
    markers = [transfer.read_marker(s) for s in sticks]
    assert [m["part"] for m in markers] == [1, 2, 3] and [m["final"] for m in markers] == [False, False, True]
    new = tmp_path / "new"
    new.mkdir()
    transfer.restore(res["code"], drive=sticks[2], home=new, next_stick=lambda part, _problem: sticks[part - 1])
    same_tree(home, new)


def test_copies_in_a_folder_whose_name_isnt_text(app, tmp_path):
    home = tmp_path / "home"
    odd = home / "Videos" / os.fsdecode(b"bad\xff\xfe name")
    odd.mkdir(parents=True)
    photo = os.urandom(200_000)
    (odd / "photo.jpg").write_bytes(photo)
    (home / ".myapp").mkdir()
    (home / ".myapp" / "photo copy.jpg").write_bytes(photo)  # after Videos in the transfer: stored once
    stick = tmp_path / "usb"
    stick.mkdir()
    res = run_send(app, home, "--to", "usb", "--usb", str(stick))
    new = tmp_path / "new"
    new.mkdir()
    transfer.restore(res["code"], drive=stick, home=new)
    assert (new / "Videos" / odd.name / "photo.jpg").read_bytes() == photo
    assert (new / ".myapp" / "photo copy.jpg").read_bytes() == photo


def test_open_database_in_a_folder_whose_name_isnt_text(app, tmp_path):
    import sqlite3
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
    res = run_send(app, home, "--to", "usb", "--usb", str(stick))
    conn.close()
    new = tmp_path / "new"
    new.mkdir()
    transfer.restore(res["code"], drive=stick, home=new)
    there = new / folder.relative_to(home)
    assert sorted(p.name for p in there.iterdir()) == ["app.db"]  # copied through SQLite, not file by file
    got = sqlite3.connect(there / "app.db")
    assert got.execute("SELECT v FROM t").fetchall() == [("only in the log",)]
    got.close()


def test_copy_of_a_file_written_to_while_packed_keeps_its_own_contents(app, tmp_path):
    import time
    home = tmp_path / "home"
    (home / "VMs").mkdir(parents=True)
    disk = os.urandom(200 * MB)
    for p in (home / "VMs" / "a disk.img", home / "VMs" / "b disk backup.img"):  # packed in this order
        p.write_bytes(disk)
        os.utime(p, (1_500_000_000, 1_500_000_000))
    stick = tmp_path / "usb"
    stick.mkdir()
    env = {k: v for k, v in os.environ.items() if not k.startswith("XDG_")}
    env.update(HOME=str(home), XDG_DATA_HOME=str(tmp_path / "app-state"))
    (tmp_path / "system_extras.py").write_text(NO_EXTRAS)
    env["CHERRY_MOVE_EXTRAS"] = str(tmp_path / "system_extras.py")
    proc = subprocess.Popen([str(app), "send", "--to", "usb", "--usb", str(stick)], env=env,
                            stdout=subprocess.PIPE, text=True)
    while not (stick / "CHERRY-TRANSFER/data/00000/00000001.bin").exists() and proc.poll() is None:
        time.sleep(0.005)
    with open(home / "VMs" / "a disk.img", "r+b") as w:  # a running VM writes to its disk while it's packed
        w.seek(199 * MB)
        w.write(b"\xee" * MB)
    out, _ = proc.communicate()
    assert proc.returncode == 0
    new = tmp_path / "new"
    new.mkdir()
    transfer.restore(json.loads(out)["code"], drive=stick, home=new)
    assert (new / "VMs" / "b disk backup.img").read_bytes() == disk


def test_file_cut_short_while_packed_stays_behind(app, tmp_path):
    import time
    home = tmp_path / "home"
    (home / "Logs").mkdir(parents=True)
    log = os.urandom(200 * MB)
    for p in (home / "Logs" / "a big.log", home / "Logs" / "b big copy.log"):  # packed in this order
        p.write_bytes(log)
        os.utime(p, (1_500_000_000, 1_500_000_000))
    (home / "Logs" / "c after.txt").write_text("after")
    stick = tmp_path / "usb"
    stick.mkdir()
    env = {k: v for k, v in os.environ.items() if not k.startswith("XDG_")}
    env.update(HOME=str(home), XDG_DATA_HOME=str(tmp_path / "app-state"))
    (tmp_path / "system_extras.py").write_text(NO_EXTRAS)
    env["CHERRY_MOVE_EXTRAS"] = str(tmp_path / "system_extras.py")
    proc = subprocess.Popen([str(app), "send", "--to", "usb", "--usb", str(stick)], env=env,
                            stdout=subprocess.PIPE, text=True)
    while not (stick / "CHERRY-TRANSFER/data/00000/00000001.bin").exists() and proc.poll() is None:
        time.sleep(0.005)
    os.truncate(home / "Logs" / "a big.log", 150 * MB)  # rotated while it's packed
    out, _ = proc.communicate()
    assert proc.returncode == 0
    res = json.loads(out)
    assert res["skipped"] == [str(home / "Logs" / "a big.log")]
    new = tmp_path / "new"
    new.mkdir()
    transfer.restore(res["code"], drive=stick, home=new)
    assert sorted(p.name for p in (new / "Logs").iterdir()) == ["b big copy.log", "c after.txt"]
    assert (new / "Logs" / "b big copy.log").read_bytes() == log


def test_whole_linux_home_folder(app, tmp_path):
    """From another Linux (Ubuntu, Mint…) the app brings everything Cherry OS's own Cherry Move does: app
    settings, chats in live databases, servers and source code, with the list of what's downloaded again."""
    from test_move_everything import check_everything_arrived, make_linux_home
    home, conn = make_linux_home(tmp_path)
    conn.executemany("INSERT INTO cursorDiskKV VALUES (?, ?)",
                     [(f"composerData:{i}", os.urandom(200)) for i in range(500, 1000)])
    conn.commit()
    stick = tmp_path / "usb"
    stick.mkdir()
    res = run_send(app, home, "--to", "usb", "--usb", str(stick))
    conn.close()
    assert not res["skipped"]
    new = tmp_path / "new"
    new.mkdir()
    manifest = transfer.restore(res["code"], drive=stick, home=new)
    check_everything_arrived(home, new, manifest)


def test_running_browser_doesnt_stop_the_move(app, tmp_path):
    from test_move_everything import check_signed_in, lock_like_chromium
    home = make_home(tmp_path)
    browser = lock_like_chromium(home / ".config/chromium/Default/Cookies")
    stick = tmp_path / "usb"
    stick.mkdir()
    res = run_send(app, home, "--to", "usb", "--usb", str(stick))
    browser.close()
    assert not res["skipped"]
    new = tmp_path / "new"
    new.mkdir()
    transfer.restore(res["code"], drive=stick, home=new)
    check_signed_in(new / ".config/chromium/Default/Cookies")


def test_apps_services_and_servers_outside_home_come_along(app, tmp_path):
    from test_move_everything import make_linux_home
    home, conn = make_linux_home(tmp_path)
    conn.close()
    stand_in = ("import json, os, sys\n"
                "os.makedirs(sys.argv[1] + '/docker')\n"
                "open(sys.argv[1] + '/docker/volume-0.tar', 'w').write('server data')\n"
                "print(json.dumps({'debs': [], 'system': {}, 'docker': {'containers': [{'name': 'web'}],"
                " 'volumes': [{'container': 'web', 'path': '/data', 'file': 'docker/volume-0.tar'}]}}))\n")
    stick = tmp_path / "usb"
    stick.mkdir()
    res = run_send(app, home, "--to", "usb", "--usb", str(stick), extras=stand_in)
    assert not (home / ".cache/cherry-move/extras").exists()
    new = tmp_path / "new"
    new.mkdir()
    manifest = transfer.restore(res["code"], drive=stick, home=new)
    assert manifest["apps"]["extras"]["docker"]["volumes"][0]["file"] == "docker/volume-0.tar"
    assert (new / transfer.EXTRAS_HOME / "docker/volume-0.tar").read_text() == "server data"


class FolderNas:
    """Reads a folder the app wrote to as if it were the NAS share."""

    def __init__(self, root: Path):
        self.root = root

    def get(self, path):
        p = self.root / path
        return p.read_bytes() if p.is_file() else None


def test_nas_folder(app, tmp_path):
    home = make_home(tmp_path)
    share = tmp_path / "share"
    share.mkdir()
    res = run_send(app, home, "--to", "folder", "--folder", str(share), "--strong")
    info = share / "CherryMove" / transfer.keys_from_code(res["code"]).id / "info.json"
    assert json.loads(info.read_text())["chunks"] == res["chunks"] + 1
    new = tmp_path / "new"
    new.mkdir()
    transfer.restore(res["code"], home=new, remote=FolderNas(share))
    same_tree(home, new)
