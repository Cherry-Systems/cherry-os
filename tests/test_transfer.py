"""Offline tests for Cherry Move's packing, sticks and remote storage (no cloud needed).
Run: make test
"""
import datetime as dt
import os
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "desktop/lib"))

from cherryos import transfer  # noqa: E402

MB = 1024 * 1024
TEXT = "".join(f"line {i}: the quick brown fox jumps over the lazy dog\n" for i in range(4000)).encode()


def make_home(root: Path, big: int = 0) -> Path:
    home = root / "home"
    (home / "Documents").mkdir(parents=True)
    (home / "Documents" / "essay.txt").write_bytes(TEXT)
    (home / "Documents" / "essay copy.txt").write_bytes(TEXT)
    (home / "Pictures").mkdir()
    photo = os.urandom(3 * MB)
    (home / "Pictures" / "cat.jpg").write_bytes(photo)
    (home / "Pictures" / "Holiday").mkdir()
    (home / "Pictures" / "Holiday" / "cat again.jpg").write_bytes(photo)
    os.link(home / "Pictures" / "cat.jpg", home / "Pictures" / "cat-link.jpg")
    (home / "Pictures" / "different.jpg").write_bytes(photo[:-1] + b"x")
    (home / "Videos").mkdir()
    if big:
        (home / "Videos" / "film.mp4").write_bytes(os.urandom(big))
    (home / ".bashrc").write_text("alias ll='ls -l'\n")
    return home


def cats_for(home: Path, monkeypatch):
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(transfer, "xdg_dir", lambda _x, fallback, h: h / fallback)
    monkeypatch.setattr(transfer, "installed_apps", lambda: {})
    return transfer.categories(home)


def same_tree(old: Path, new: Path):
    for p in old.rglob("*"):
        if p.is_file():
            rel = p.relative_to(old)
            if rel.name == ".bashrc":
                rel = rel.with_name(".bashrc.from-old-computer")
            assert (new / rel).read_bytes() == p.read_bytes(), rel


# ---------------------------------------------------------------- compression

def test_pack_round_trip_and_raw_fallback():
    noise = os.urandom(MB)
    assert transfer.pack(TEXT)[:1] == transfer.ZSTD
    assert transfer.pack(noise)[:1] == transfer.RAW
    for data in (TEXT, noise, b""):
        assert transfer.unpack(transfer.pack(data)) == data
        assert transfer.unpack(transfer.pack(data, transfer.STRONG)) == data


def test_zlib_when_zstandard_missing(monkeypatch):
    monkeypatch.setattr(transfer, "zstandard", None)
    body = transfer.pack(TEXT)
    assert body[:1] == transfer.ZLIB and transfer.unpack(body) == TEXT
    monkeypatch.undo()
    with pytest.raises(transfer.TransferError):
        monkeypatch.setattr(transfer, "zstandard", None)
        transfer.unpack(transfer.ZSTD + b"whatever")


def test_chunk_folders_stay_small():
    assert transfer.chunk_name(0) == "data/00000/00000000.bin"
    assert transfer.chunk_name(1234567) == "data/01234/01234567.bin"


# ---------------------------------------------------------------- plan, duplicates, estimate

def test_plan_finds_duplicates(tmp_path, monkeypatch):
    home = make_home(tmp_path)
    plan = transfer.make_plan(cats_for(home, monkeypatch))
    assert plan.files == 7
    assert plan.duplicates == 3  # essay copy, cat again, cat-link (hard link); different.jpg isn't one
    assert plan.saved == len(TEXT) + 2 * 3 * MB
    assert set(plan.first) == {"xdg/DOCUMENTS/essay copy.txt", "xdg/PICTURES/cat-link.jpg"}  # first in walk order


def test_working_out_the_size_again_reads_nothing_again(tmp_path, monkeypatch):
    home = make_home(tmp_path)
    cats = cats_for(home, monkeypatch)  # counting the files walks them once
    monkeypatch.setattr(transfer, "_digests", {})

    def no_walking(*_a, **_k):
        raise AssertionError("walked the files again")
    monkeypatch.setattr(transfer, "_walk", no_walking)
    opened = []
    real_open = open
    monkeypatch.setattr("builtins.open", lambda f, *a, **k: (opened.append(str(f)), real_open(f, *a, **k))[1])
    first = transfer.make_plan(cats)
    assert first.duplicates == 3 and opened
    opened.clear()
    again = transfer.make_plan(cats)  # after going back to pick other things
    assert (again.duplicates, again.saved, again.same) == (first.duplicates, first.saved, first.same)
    assert not opened


def test_working_out_the_size_stops_when_the_page_is_left(tmp_path, monkeypatch):
    home = make_home(tmp_path)
    cats = cats_for(home, monkeypatch)
    left = transfer.threading.Event()
    left.set()
    with pytest.raises(InterruptedError):
        transfer.make_plan(cats, cancel=left)
    with pytest.raises(InterruptedError):
        transfer.scan_category(cats[0], home, cancel=left)


def test_desktop_made_files_stay_behind(tmp_path, monkeypatch):
    home = make_home(tmp_path)
    flatpak_ff = home / ".var/app/org.mozilla.firefox"
    (flatpak_ff / ".mozilla/native-messaging-hosts").mkdir(parents=True)
    (flatpak_ff / ".mozilla/native-messaging-hosts/org.kde.plasma.browser_integration.json").write_text("{}")
    (flatpak_ff / "plasma-browser-integration-host").write_text("#!/bin/sh\n")
    (flatpak_ff / ".mozilla/firefox").mkdir()
    (flatpak_ff / ".mozilla/firefox/profiles.ini").write_text("[General]\n")
    (home / "Pictures" / "Thumbs.db").write_bytes(b"x")
    cats = cats_for(home, monkeypatch)
    names = {Path(arc).name for c in cats for prefix, base in c.paths for _p, arc in transfer._walk(base, prefix)}
    assert "profiles.ini" in names and "cat.jpg" in names
    assert not names & {"org.kde.plasma.browser_integration.json", "plasma-browser-integration-host", "Thumbs.db"}


def test_estimate_and_fit(tmp_path, monkeypatch):
    home = tmp_path / "h"
    (home / "Documents").mkdir(parents=True)
    for i in range(20):
        (home / "Documents" / f"notes{i}.txt").write_bytes(TEXT * 5 + str(i).encode())
    (home / "Videos").mkdir()
    (home / "Videos" / "film.mp4").write_bytes(os.urandom(20 * MB))
    plan = transfer.make_plan(cats_for(home, monkeypatch))
    sizes = transfer.estimate(plan)
    text_bytes = 20 * len(TEXT) * 5
    assert 20 * MB < sizes[transfer.NORMAL] < 20 * MB + text_bytes * 0.3 + 20 * MB  # film barely shrinks
    assert sizes[transfer.STRONG] <= sizes[transfer.NORMAL]
    fits = transfer.fit(sizes, 10 * 1024 * MB)
    assert fits.sticks == 1 and fits.level == transfer.NORMAL
    small = transfer.fit(sizes, 30 * MB)
    assert small.sticks == 2
    tight = {transfer.NORMAL: 101 * MB, transfer.STRONG: 95 * MB}
    assert transfer.fit(tight, 100 * MB + transfer.STICK_RESERVE).level == transfer.STRONG
    assert transfer.fit(tight, 100 * MB + transfer.STICK_RESERVE).sticks == 1


# ---------------------------------------------------------------- USB sticks

class FakeSticks:
    """Folders that act like sticks of `capacity` bytes."""

    def __init__(self, root: Path, capacity: int, monkeypatch):
        self.root, self.capacity = root, capacity
        self.asked = []
        monkeypatch.setattr(transfer, "free_space", self.free)

    def stick(self, n: int) -> Path:
        p = self.root / f"stick{n}"
        p.mkdir(exist_ok=True)
        return p

    def free(self, mount: Path) -> int:
        used = sum(f.stat().st_size for f in Path(mount).rglob("*") if f.is_file())
        return self.capacity - used


def test_one_stick_round_trip(tmp_path, monkeypatch):
    home = make_home(tmp_path)
    cats = cats_for(home, monkeypatch)
    stick = tmp_path / "usb"
    stick.mkdir()
    result = transfer.send(cats, "usb", drive=stick)
    marker = transfer.read_marker(stick)
    assert marker["code"] == result.code and marker["final"] and marker["parts"] == 1
    stored = sum(f.stat().st_size for f in (stick / "CHERRY-TRANSFER").rglob("*.bin"))
    assert stored < 3 * MB + len(TEXT)  # photo stored once, text compressed
    new = tmp_path / "new"
    new.mkdir()
    assert not transfer.was_restored(result.code, new)
    got = transfer.restore(result.code, drive=stick, home=new)
    same_tree(home, new)
    assert got["restored_files"] == 7
    assert transfer.was_restored(result.code, new) and not transfer.was_restored(result.code, home)
    assert result.code not in (new / ".config/cherry/restored-transfers").read_text()
    again = transfer.restore(result.code, drive=stick, home=new)
    assert again["restored_files"] == 0 and again["skipped_files"] == 7
    assert len((new / ".config/cherry/restored-transfers").read_text().split()) == 1


def test_files_already_here_are_kept(tmp_path, monkeypatch):
    home = make_home(tmp_path)
    stick = tmp_path / "usb"
    stick.mkdir()
    result = transfer.send(cats_for(home, monkeypatch), "usb", drive=stick)
    new = tmp_path / "new"
    (new / "Documents").mkdir(parents=True)
    edited = TEXT[:-1] + b"!"
    (new / "Documents" / "essay.txt").write_bytes(edited)  # same size, different words
    (new / "Documents" / "essay copy.txt").write_bytes(TEXT)  # same words, saved at another time
    for p in (new / "Documents").iterdir():
        os.utime(p, (1e9, 1e9))
    got = transfer.restore(result.code, drive=stick, home=new)
    assert (new / "Documents" / "essay.txt").read_bytes() == edited
    assert (new / "Documents" / "essay (from old computer).txt").read_bytes() == TEXT
    assert not (new / "Documents" / "essay copy (from old computer).txt").exists()
    assert got["skipped_files"] == 1 and got["restored_files"] == 6
    assert not list(new.rglob("*.cherry-part"))


def test_network_mounts_are_read_from_mountinfo():
    info = "\n".join([
        "36 25 0:32 / / rw,relatime shared:1 - ext4 /dev/nvme0n1p2 rw",
        "90 36 0:51 / /home/me/thehole rw,relatime shared:50 - autofs systemd-1 rw,fd=62",
        "91 90 0:52 / /home/me/thehole rw,relatime shared:51 - cifs //nas.local/me rw,vers=3.0",
        "92 36 0:53 / /home/me/My\\040Phone rw,nosuid - fuse.jmtpfs jmtpfs rw",
        "93 36 8:17 / /home/me/Games rw - ext4 /dev/sdb1 rw",
        "94 36 8:33 / /home/me/Old\\040Disk rw - fuseblk /dev/sdc1 rw",
    ])
    assert transfer.network_mounts(info) == {"/home/me/thehole", "/home/me/My Phone"}


def test_network_drives_in_the_home_folder_stay_behind(tmp_path, monkeypatch):
    home = make_home(tmp_path)
    (home / "thehole" / "CherryMove").mkdir(parents=True)
    (home / "thehole" / "CherryMove" / "00000001.bin").write_bytes(b"x" * 1000)
    (home / "Projects" / "share").mkdir(parents=True)
    (home / "Projects" / "share" / "big.iso").write_bytes(b"y" * 1000)
    (home / "Projects" / "mine.py").write_text("print('hi')\n")
    monkeypatch.setattr(transfer, "network_mounts",
                        lambda *_: frozenset({str(home / "thehole"), str(home / "Projects" / "share")}))
    stick = tmp_path / "usb"
    stick.mkdir()
    result = transfer.send(cats_for(home, monkeypatch), "usb", drive=stick)
    new = tmp_path / "new"
    new.mkdir()
    transfer.restore(result.code, drive=stick, home=new)
    assert (new / "Projects" / "mine.py").exists() and (new / "Documents" / "essay.txt").exists()
    assert not (new / "thehole").exists() and not (new / "Projects" / "share").exists()
    assert transfer.left_behind_rules(home)("Projects/share/big.iso") == "network"


def test_restore_writes_nothing_into_network_drives_or_transfer_stores(tmp_path, monkeypatch):
    home = make_home(tmp_path)
    store = home / "Stuff" / "CherryMove" / ("ab" * 16) / "data" / "00000"
    store.mkdir(parents=True)
    (store / "00000001.bin").write_bytes(os.urandom(1000))
    (home / "Stuff" / "notes.txt").write_text("keep me\n")
    (home / "nas").mkdir()
    (home / "nas" / "film.mkv").write_bytes(b"z" * 1000)
    stick = tmp_path / "usb"
    stick.mkdir()
    result = transfer.send(cats_for(home, monkeypatch), "usb", drive=stick)
    new = tmp_path / "new"
    (new / "nas").mkdir(parents=True)  # the NAS is mounted here on the new computer
    monkeypatch.setattr(transfer, "network_mounts", lambda *_: frozenset({str(new / "nas")}))
    transfer.restore(result.code, drive=stick, home=new)
    assert (new / "Stuff" / "notes.txt").read_text() == "keep me\n"
    assert not (new / "Stuff" / "CherryMove" / ("ab" * 16)).exists()
    assert not list((new / "nas").iterdir())


def test_restoring_again_keeps_one_copy_of_each_clash(tmp_path, monkeypatch):
    home = make_home(tmp_path)
    stick = tmp_path / "usb"
    stick.mkdir()
    result = transfer.send(cats_for(home, monkeypatch), "usb", drive=stick)
    new = tmp_path / "new"
    (new / "Documents").mkdir(parents=True)
    (new / "Documents" / "essay.txt").write_bytes(b"written on the new computer")
    transfer.restore(result.code, drive=stick, home=new)
    again = transfer.restore(result.code, drive=stick, home=new)
    assert (new / "Documents" / "essay (from old computer).txt").read_bytes() == TEXT
    assert not (new / "Documents" / "essay (from old computer 2).txt").exists()
    assert again["restored_files"] == 0


def test_links_already_here_stay_and_the_old_files_go_beside_them(tmp_path, monkeypatch):
    home = make_home(tmp_path)
    stick = tmp_path / "usb"
    stick.mkdir()
    result = transfer.send(cats_for(home, monkeypatch), "usb", drive=stick)
    new = tmp_path / "new"
    dotfiles = new / "dotfiles"
    dotfiles.mkdir(parents=True)
    (dotfiles / "essay.txt").write_bytes(TEXT)
    (dotfiles / "other.jpg").write_bytes(b"the new computer's picture")
    (new / "Documents").mkdir()
    (new / "Pictures").mkdir()
    os.symlink(dotfiles / "essay.txt", new / "Documents" / "essay.txt")  # the same words: nothing to add
    os.symlink(dotfiles / "other.jpg", new / "Pictures" / "cat-link.jpg")  # the name the photo is stored under
    os.symlink("/usr/share/doc", new / "Pictures" / "different.jpg")
    transfer.restore(result.code, drive=stick, home=new)
    photo = (home / "Pictures" / "cat.jpg").read_bytes()
    assert os.readlink(new / "Pictures" / "cat-link.jpg") == str(dotfiles / "other.jpg")
    assert (dotfiles / "other.jpg").read_bytes() == b"the new computer's picture"
    assert (new / "Pictures" / "cat-link (from old computer).jpg").read_bytes() == photo
    assert (new / "Pictures" / "cat.jpg").read_bytes() == photo
    assert (new / "Pictures" / "Holiday" / "cat again.jpg").read_bytes() == photo
    assert (new / "Pictures" / "different.jpg (from old computer)").read_bytes() == \
        (home / "Pictures" / "different.jpg").read_bytes()
    assert not (new / "Documents" / "essay (from old computer).txt").exists()
    assert transfer.restore(result.code, drive=stick, home=new)["restored_files"] == 0


def test_names_as_long_as_a_name_can_be(tmp_path, monkeypatch):
    home = tmp_path / "home"
    docs = home / "Documents"
    docs.mkdir(parents=True)
    longest = "L" * 251 + ".txt"
    accented = "é" * 120 + ".txt"  # 244 bytes
    for name in (longest, accented):
        (docs / name).write_text(f"old {name[:3]}\n")
    stick = tmp_path / "usb"
    stick.mkdir()
    result = transfer.send(cats_for(home, monkeypatch), "usb", drive=stick)
    new = tmp_path / "new"
    (new / "Documents").mkdir(parents=True)
    (new / "Documents" / accented).write_text("the new computer's\n")
    for _ in range(2):
        transfer.restore(result.code, drive=stick, home=new)
    assert (new / "Documents" / longest).read_text() == "old LLL\n"
    assert (new / "Documents" / accented).read_text() == "the new computer's\n"
    beside = [p for p in (new / "Documents").iterdir() if p.name.endswith(" (from old computer).txt")]
    assert len(beside) == 1 and beside[0].read_text() == "old ééé\n"
    assert len(os.fsencode(beside[0].name)) <= 255 and beside[0].name.startswith("éééé")


def test_folders_whose_name_is_taken_here_still_arrive(tmp_path, monkeypatch):
    home = tmp_path / "home"
    for rel in ("Projects/game/main.py", "Projects/site/index.html", "Projects/notes/todo.md",
                ".config/someapp/settings.json", ".myapp/data/save.dat"):
        (home / rel).parent.mkdir(parents=True, exist_ok=True)
        (home / rel).write_text(f"old {rel}\n")
    stick = tmp_path / "usb"
    stick.mkdir()
    result = transfer.send(cats_for(home, monkeypatch), "usb", drive=stick)
    new = tmp_path / "new"
    outside = tmp_path / "another disk"
    outside.mkdir()
    (new / "Projects").mkdir(parents=True)
    (new / ".config").mkdir()
    (new / "Projects" / "game").write_text("a file here, where the old computer had a folder\n")
    os.symlink("nowhere", new / "Projects" / "site")
    os.symlink(outside, new / "Projects" / "notes")
    (new / ".config" / "someapp").write_text("the new computer's settings\n")
    for _ in range(2):
        transfer.restore(result.code, drive=stick, home=new)
        assert (new / "Projects" / "game").read_text() == "a file here, where the old computer had a folder\n"
        assert (new / "Projects" / "game (from old computer)" / "main.py").read_text() == "old Projects/game/main.py\n"
        assert os.readlink(new / "Projects" / "site") == "nowhere"
        assert (new / "Projects" / "site (from old computer)" / "index.html").exists()
        assert not list(outside.iterdir())
        assert (new / "Projects" / "notes (from old computer)" / "todo.md").exists()
        # Settings: the old computer's win, the new computer's are kept beside them.
        assert (new / ".config" / "someapp" / "settings.json").read_text() == "old .config/someapp/settings.json\n"
        assert (new / ".config" / "someapp (before Cherry Move)").read_text() == "the new computer's settings\n"
        assert (new / ".myapp" / "data" / "save.dat").exists()
    assert not (new / "Projects" / "game (from old computer 2)").exists()


def test_stopped_restore_finds_copies_in_folders_restored_under_another_name(tmp_path, monkeypatch):
    monkeypatch.setattr(transfer, "CHUNK", 64 * 1024)
    monkeypatch.setattr(transfer, "CHECKPOINT_EVERY", 0)
    home = tmp_path / "home"
    photo = os.urandom(300_000)
    (home / "Pictures" / "Holiday").mkdir(parents=True)
    (home / "Pictures" / "Holiday" / "beach.jpg").write_bytes(photo)
    (home / "Music").mkdir()
    for i in range(60):
        (home / "Music" / f"song {i:02}.mp3").write_bytes(os.urandom(20_000))
    (home / "Videos").mkdir()
    (home / "Videos" / "beach again.jpg").write_bytes(photo)  # stored once, with the first
    stick = tmp_path / "usb"
    stick.mkdir()
    result = transfer.send(cats_for(home, monkeypatch), "usb", drive=stick)
    new = tmp_path / "new"
    (new / "Pictures").mkdir(parents=True)
    (new / "Pictures" / "Holiday").write_text("a file with the folder's name\n")
    stop = transfer.threading.Event()
    seen = []

    def progress(done, total, name):
        seen.append(name)
        if len(seen) == 20:
            stop.set()
    with pytest.raises(InterruptedError):
        transfer.restore(result.code, drive=stick, home=new, progress=progress, cancel=stop)
    transfer.restore(result.code, drive=stick, home=new)
    assert (new / "Pictures" / "Holiday (from old computer)" / "beach.jpg").read_bytes() == photo
    assert (new / "Videos" / "beach again.jpg").read_bytes() == photo


def test_a_file_stored_once_arrives_when_its_other_name_cant(tmp_path, monkeypatch):
    home = tmp_path / "home"
    for folder in ("Music", "Projects"):
        (home / folder).mkdir(parents=True)
    (home / "Music" / "song.mp3").write_bytes(os.urandom(50_000))
    os.link(home / "Music" / "song.mp3", home / "Projects" / "song.mp3")
    stick = tmp_path / "usb"
    stick.mkdir()
    result = transfer.send(cats_for(home, monkeypatch), "usb", drive=stick)
    new = tmp_path / "new"
    outside = tmp_path / "another disk"
    outside.mkdir()
    new.mkdir()
    # This computer's Music folder is on another disk, which restore() never writes to.
    monkeypatch.setattr(transfer, "xdg_dir", lambda x, fallback, h: outside if x == "MUSIC" else h / fallback)
    transfer.restore(result.code, drive=stick, home=new)
    assert (new / "Projects" / "song.mp3").read_bytes() == (home / "Projects" / "song.mp3").read_bytes()
    assert not list(outside.iterdir())
    assert not list((new / ".cache").rglob("*-links"))


def test_stopped_restore_carries_on_where_it_got_to(tmp_path, monkeypatch):
    monkeypatch.setattr(transfer, "CHUNK", 64 * 1024)
    monkeypatch.setattr(transfer, "CHECKPOINT_EVERY", 0)
    home = make_home(tmp_path)
    (home / "Music").mkdir()
    for i in range(60):  # after Pictures, so the stop comes between the photo and its copies
        (home / "Music" / f"song {i:02}.mp3").write_bytes(os.urandom(20_000))
    (home / "Videos" / "later").mkdir()
    for name in ("cat 2.jpg", "cat 3.jpg"):  # copies of a photo sent early, stored once
        (home / "Videos" / "later" / name).write_bytes((home / "Pictures" / "cat.jpg").read_bytes())
    stick = tmp_path / "usb"
    stick.mkdir()
    result = transfer.send(cats_for(home, monkeypatch), "usb", drive=stick)
    new = tmp_path / "new"
    new.mkdir()
    stop = transfer.threading.Event()
    seen = []

    def progress(done, total, name):
        seen.append(name)
        if len(seen) == 40:
            stop.set()
    with pytest.raises(InterruptedError):
        transfer.restore(result.code, drive=stick, home=new, progress=progress, cancel=stop)
    checkpoint = new / ".cache/cherry-move" / f"restore-{transfer.keys_from_code(result.code).id}.json"
    assert checkpoint.exists()
    read = []
    get = transfer.UsbSource.get
    monkeypatch.setattr(transfer.UsbSource, "get", lambda self, i: (read.append(i), get(self, i))[1])
    got = transfer.restore(result.code, drive=stick, home=new)
    assert min(i for i in read if i) > 1  # carried on, didn't start again
    same_tree(home, new)
    assert not checkpoint.exists() and not list(new.rglob("*.cherry-part"))
    assert got["restored_files"] + got["skipped_files"] >= 60


KEYRING = [
    {"label": "Chrome Safe Storage", "attributes": {"application": "chrome", "xdg:schema": "chrome_libsecret_os_crypt_password_v2"},
     "secret": "Y2hyb21lLWtleQ==", "type": "text/plain"},
    {"label": "Chromium Safe Storage", "attributes": {"application": "Cursor", "xdg:schema": "chrome_libsecret_os_crypt_password_v2"},
     "secret": "Y3Vyc29yLWtleQ==", "type": "text/plain"},
    {"label": "Password for 'me' on 'gh:github.com'", "type": "text/plain", "secret": "dGVzdC10b2tlbg==",
     "attributes": {"service": "gh:github.com", "username": "me", "xdg:schema": "org.freedesktop.Secret.Generic"}},
]


def test_saved_passwords_travel_and_browsers_are_pointed_at_them(tmp_path, monkeypatch):
    from cherryos import secret_store
    home = make_home(tmp_path)
    (home / ".var/app/com.google.Chrome/config/google-chrome/Default").mkdir(parents=True)
    (home / ".var/app/com.google.Chrome/config/google-chrome/Default/Login Data").write_bytes(b"v11...")
    (home / ".cursor").mkdir()
    (home / ".cursor/argv.json").write_text('// Runtime arguments\n{\n\t// crash reports\n'
                                            '\t"enable-crash-reporter": true\n}\n')
    added = []
    monkeypatch.setattr(secret_store, "read_all", lambda: KEYRING)
    monkeypatch.setattr(secret_store, "add_all", lambda items: (added.extend(items), items)[1])
    stick = tmp_path / "usb"
    stick.mkdir()
    result = transfer.send(cats_for(home, monkeypatch), "usb", drive=stick)
    new = tmp_path / "new"
    new.mkdir()
    got = transfer.restore(result.code, drive=stick, home=new)
    assert added == KEYRING and got["passwords_added"] == 3 and "keyring" not in got
    flags = new / ".var/app/com.google.Chrome/config/chrome-flags.conf"
    assert flags.read_text() == "--password-store=gnome-libsecret\n"
    argv = (new / ".cursor/argv.json").read_text()
    import json, re
    assert json.loads(re.sub(r"//[^\n]*", "", argv)) == {"password-store": "gnome-libsecret",
                                                          "enable-crash-reporter": True}
    assert "// crash reports" in argv
    transfer.use_keyring_for_browsers(new, KEYRING)  # again: nothing doubled
    assert flags.read_text().count("password-store") == 1 and argv == (new / ".cursor/argv.json").read_text()
    assert not (new / ".vscode").exists()  # VS Code's key wasn't among them


def test_no_saved_passwords_without_browsers_apps_or_keys(tmp_path, monkeypatch):
    from cherryos import secret_store
    home = make_home(tmp_path)
    monkeypatch.setattr(secret_store, "read_all", lambda: KEYRING)
    added = []
    monkeypatch.setattr(secret_store, "add_all", lambda items: (added.extend(items), items)[1])
    stick = tmp_path / "usb"
    stick.mkdir()
    cats = [c for c in cats_for(home, monkeypatch) if c.id in ("documents", "pictures")]
    result = transfer.send(cats, "usb", drive=stick)
    new = tmp_path / "new"
    new.mkdir()
    assert transfer.restore(result.code, drive=stick, home=new)["passwords_added"] == 0 and not added


@pytest.mark.real_keyring
def test_keyring_read_and_add_need_a_keyring(monkeypatch):
    from cherryos import secret_store
    monkeypatch.setenv("DBUS_SESSION_BUS_ADDRESS", "unix:path=/nonexistent/cherry-test-bus")
    assert secret_store.read_all() == [] and secret_store.add_all(KEYRING) == []


@pytest.mark.real_keyring
def test_keyring_items_round_trip_through_gnome_keyring(tmp_path):
    import json
    import shutil
    import subprocess
    if not (shutil.which("gnome-keyring-daemon") and shutil.which("dbus-run-session")):
        pytest.skip("needs gnome-keyring-daemon and dbus-run-session")
    run = tmp_path / "run"
    run.mkdir(mode=0o700)
    env = {"PATH": os.environ["PATH"], "HOME": str(tmp_path), "XDG_DATA_HOME": str(tmp_path / "data"),
           "XDG_CONFIG_HOME": str(tmp_path / "config"), "XDG_CACHE_HOME": str(tmp_path / "cache"),
           "XDG_RUNTIME_DIR": str(run), "PYTHONPATH": str(Path(__file__).resolve().parent.parent / "desktop/lib")}
    script = (
        "printf test | gnome-keyring-daemon --unlock --components=secrets >/dev/null && "
        "python3 -c 'import json, sys; from cherryos import secret_store as s; "
        "items = json.load(sys.stdin); added = s.add_all(items); again = s.add_all(items[:1]); "
        "print(json.dumps([len(added), len(again), s.read_all()]))'")
    out = subprocess.run(["dbus-run-session", "--", "sh", "-c", script], env=env, input=json.dumps(KEYRING),
                         capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    added, again, items = json.loads(out.stdout.strip().splitlines()[-1])
    assert added == 3 and again == 1
    key = lambda i: i["label"]  # noqa: E731
    assert sorted(items, key=key) == sorted(KEYRING, key=key)  # replaced, not added twice


def test_send_time_shown_in_this_computers_zone(monkeypatch):
    monkeypatch.setenv("TZ", "Australia/Brisbane")
    time.tzset()
    try:
        assert f"{transfer.sent_at({'created': '2026-10-04T06:16:00+00:00'}):%H:%M}" == "16:16"
        assert f"{transfer.sent_at({'created': '2026-10-04T06:16:00'}):%H:%M}" == "06:16"  # older apps: no zone
        assert transfer.sent_at({"created": transfer.now_iso()}).utcoffset() == dt.timedelta(hours=10)
        assert transfer.sent_at({}) is None and transfer.sent_at({"created": "soon"}) is None
    finally:
        monkeypatch.undo()
        time.tzset()


def test_several_sticks(tmp_path, monkeypatch):
    home = make_home(tmp_path, big=50 * MB)
    cats = cats_for(home, monkeypatch)
    sticks = FakeSticks(tmp_path, 30 * MB, monkeypatch)  # three 8 MB chunks each; eight chunks in all
    offered = iter([sticks.stick(1), sticks.stick(2), sticks.stick(3), sticks.stick(4)])
    problems = []

    def next_stick(part, problem):
        problems.append((part, problem))
        if part == 2 and problem is None:
            return sticks.stick(1)  # the full one again
        return next(offered)
    next(offered)
    result = transfer.send(cats, "usb", drive=sticks.stick(1), next_stick=next_stick)
    assert result.sticks == 3
    assert "again" in problems[1][1]
    markers = [transfer.read_marker(sticks.stick(n)) for n in (1, 2, 3)]
    assert [m["part"] for m in markers] == [1, 2, 3]
    assert [m["final"] for m in markers] == [False, False, True]
    assert markers[1]["first"] > 0 and markers[2]["chunks"] == result.chunks + 1

    with pytest.raises(transfer.NeedStick) as need:
        transfer.restore(result.code, drive=sticks.stick(1), home=tmp_path / "nowhere")
    assert need.value.part == 2

    asked = []

    def give(part, problem):
        asked.append((part, problem))
        if len(asked) == 2:
            return sticks.stick(3)  # wrong one first
        return sticks.stick(part)
    new = tmp_path / "new"
    new.mkdir()
    transfer.restore(result.code, drive=sticks.stick(2), home=new, next_stick=give)
    assert asked[0] == (1, None) and asked[1] == (2, None) and "stick 3" in asked[2][1]
    same_tree(home, new)


def test_marker_only_stick_points_at_remote(tmp_path, monkeypatch):
    home = make_home(tmp_path)
    cats = cats_for(home, monkeypatch)
    nas = FakeRemote()
    stick = tmp_path / "usb"
    (stick / "CHERRY-TRANSFER" / "data").mkdir(parents=True)  # an old transfer's leftovers
    result = transfer.send(cats, "remote", drive=stick, remote=nas)
    assert not (stick / "CHERRY-TRANSFER" / "data").exists()
    with pytest.raises(transfer.NeedRemote) as need:
        transfer.restore(result.code, drive=stick, home=tmp_path / "x")
    assert need.value.descriptor == nas.descriptor()
    new = tmp_path / "new"
    new.mkdir()
    transfer.restore(result.code, drive=stick, home=new, remote=nas)
    same_tree(home, new)
    keys, source = transfer.source_for(result.code, remote=nas)
    source.delete()
    assert not any(k.startswith(f"CherryMove/{keys.id}") for k in nas.files)


class FakeRemote:
    title = "Test NAS"

    def __init__(self):
        self.files, self.dirs = {}, set()

    def descriptor(self):
        return {"kind": "nas", "title": self.title, "type": "webdav", "url": "http://nas.local/"}

    def mkdirs(self, path):
        self.dirs.add(path)

    def put(self, path, data):
        assert path.rsplit("/", 1)[0] in self.dirs or path.endswith("info.json")
        self.files[path] = bytes(data)

    def get(self, path):
        return self.files.get(path)

    def delete(self, path):
        for k in [k for k in self.files if k.startswith(path + "/")]:
            del self.files[k]

    def list_dirs(self, path):
        names = {k[len(path) + 1:].split("/", 1)[0] for k in self.files if k.startswith(path + "/")}
        return sorted(n for n in names if any(k.startswith(f"{path}/{n}/") for k in self.files))


def test_moves_in_storage_are_listed_without_the_code(tmp_path, monkeypatch):
    home = make_home(tmp_path)
    cats = cats_for(home, monkeypatch)
    nas = FakeRemote()
    result = transfer.send(cats, "remote", remote=nas, label="Old laptop")
    nas.files[f"CherryMove/{'0' * 32}/data/00000/00000001.bin"] = b"unfinished: no info.json"
    broken = f"CherryMove/{'1' * 32}/info.json"
    nas.files[broken] = b"{}"
    real_get = nas.get

    def get(path):
        if path == broken:
            raise transfer.TransferError("The connection broke off")
        return real_get(path)
    monkeypatch.setattr(transfer, "_retry", lambda fn, attempts=4: fn())
    nas.get = get
    moves = transfer.list_remote_moves(nas)
    assert [m.label for m in moves] == ["Old laptop"]
    keys, _ = transfer.source_for(result.code, remote=nas)
    assert moves[0].id == keys.id and moves[0].info["files"] == result.files
    info = nas.files[f"CherryMove/{keys.id}/info.json"].decode()
    assert result.code not in info and keys_raw(result.code) not in info


def keys_raw(code):
    return code.replace("-", "")


def test_cancel_cleans_up_remote(tmp_path, monkeypatch):
    home = make_home(tmp_path, big=20 * MB)
    cats = cats_for(home, monkeypatch)
    nas = FakeRemote()
    import threading
    stop = threading.Event()

    def progress(done, total, name):
        if done > 5 * MB:
            stop.set()
    with pytest.raises(InterruptedError):
        transfer.send(cats, "remote", remote=nas, cancel=stop, progress=progress)
    assert not nas.files


WINDOWS_PROFILES = """[Install308046B0AF4A39CB]
Default=Profiles/abcd.default-release
Locked=1

[Profile1]
Name=default
IsRelative=1
Path=Profiles/efgh.default
Default=1

[Profile0]
Name=default-release
IsRelative=1
Path=Profiles/abcd.default-release

[General]
StartWithLastProfile=1
Version=2
"""


def firefox_home(root: Path) -> Path:
    ff = root / ".mozilla" / "firefox"
    for p in ("abcd.default-release", "efgh.default"):
        (ff / "Profiles" / p).mkdir(parents=True)
    (ff / "Profiles" / "abcd.default-release" / "compatibility.ini").write_text(
        "[Compatibility]\nLastVersion=160.0_20261001000000/20261001000000\nLastOSABI=WINNT_x86_64-msvc\n")
    (ff / "installs.ini").write_text("[308046B0AF4A39CB]\nDefault=Profiles/abcd.default-release\nLocked=1\n")
    return ff


def test_firefox_profile_from_windows_is_adopted(tmp_path):
    ff = firefox_home(tmp_path)
    (ff / "profiles.ini").write_text(WINDOWS_PROFILES)
    transfer.fix_firefox(tmp_path)
    ini = (ff / "profiles.ini").read_text()
    assert "[Install" not in ini and not (ff / "installs.ini").exists()
    assert "Path=Profiles/abcd.default-release\nDefault=1" in ini
    assert ini.count("Default=1") == 1
    compat = (ff / "Profiles" / "abcd.default-release" / "compatibility.ini").read_text()
    assert "LastVersion=1.0_" in compat and "WINNT" not in compat


def test_firefox_profile_added_when_firefox_already_used_here(tmp_path):
    ff = firefox_home(tmp_path)
    (ff / "xyz.default-esr").mkdir()
    (ff / "profiles.ini").write_text("[Profile0]\nName=default-esr\nIsRelative=1\nPath=xyz.default-esr\nDefault=1\n\n"
                                     "[General]\nStartWithLastProfile=1\nVersion=2\n\n"
                                     "[Install3B6073811A6ABF12]\nDefault=xyz.default-esr\nLocked=1\n")
    (ff / "installs.ini").write_text("[3B6073811A6ABF12]\nDefault=xyz.default-esr\nLocked=1\n")
    (ff / "profiles (from old computer).ini").write_text(WINDOWS_PROFILES)
    transfer.fix_firefox(tmp_path)
    ini = (ff / "profiles.ini").read_text()
    assert "Path=xyz.default-esr" in ini and "Path=Profiles/efgh.default" in ini
    assert "[Install3B6073811A6ABF12]\nDefault=Profiles/abcd.default-release" in ini
    assert "Default=Profiles/abcd.default-release" in (ff / "installs.ini").read_text()
    assert not (ff / "profiles (from old computer).ini").exists()


@pytest.mark.parametrize("where", [".config/mozilla/firefox", "snap/firefox/common/.mozilla/firefox"])
def test_firefox_profile_is_found(tmp_path, monkeypatch, where):
    home = make_home(tmp_path)
    (home / where).mkdir(parents=True)
    (home / where / "profiles.ini").write_text(WINDOWS_PROFILES)
    paths = dict(next(c for c in cats_for(home, monkeypatch) if c.id == "browsers").paths)
    assert paths["home/.mozilla/firefox"] == home / where
