"""Offline tests for Cherry's own code (no cloud or root needed).
Run: make test
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "desktop/lib"))

from cherryos import codes, desktops, pkg  # noqa: E402


# ---------------------------------------------------------------- transfer codes

def test_code_round_trip():
    keys = codes.new_code()
    pretty = codes.pretty(keys.code)
    assert len(codes.normalise(pretty)) == 34
    again = codes.keys_from_code(pretty.lower().replace("-", " "))
    assert (again.id, again.key) == (keys.id, keys.key)
    sealed = keys.seal(3, b"hello")
    assert again.open(3, sealed) == b"hello"
    with pytest.raises(Exception):
        again.open(4, sealed)  # chunks can't be swapped around


def test_code_typo_is_caught():
    code = codes.normalise(codes.new_code().code)
    swapped = code[:5] + ("A" if code[5] != "A" else "B") + code[6:]
    with pytest.raises(codes.BadCode):
        codes.keys_from_code(swapped)
    with pytest.raises(codes.BadCode):
        codes.keys_from_code(code[:-3])


# ---------------------------------------------------------------- desktops

def make_sessions(root: Path):
    for folder, name in [("wayland-sessions", "plasma"), ("xsessions", "plasmax11"), ("wayland-sessions", "gnome"),
                         ("xsessions", "gnome-xorg"), ("xsessions", "cinnamon"), ("xsessions", "xfce"),
                         ("xsessions", "budgie-desktop")]:
        p = root / "usr/share" / folder / f"{name}.desktop"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("[Desktop Entry]\n")


def test_every_desktop_has_a_session(tmp_path):
    make_sessions(tmp_path)
    for d in desktops.DESKTOPS.values():
        assert bool(d.session(tmp_path)) != bool(d.on_demand), d.id
    assert desktops.DESKTOPS["kde"].session(tmp_path)[1] == "plasma"


def test_hyprland_comes_from_backports_without_touching_other_desktops(tmp_path):
    (tmp_path / "etc").mkdir()
    (tmp_path / "etc/os-release").write_text('NAME="Cherry OS"\nVERSION_CODENAME=trixie\n')
    cmds = desktops.prepare_install("hyprland", tmp_path)
    pin = (tmp_path / "etc/apt/preferences.d/cherry-hyprland").read_text()
    assert "Pin: release n=trixie-backports" in pin and "Package: *" not in pin and "mesa" not in pin
    assert "src:libxkbcommon:any" in pin
    assert "codename=trixie-backports" in (tmp_path / "etc/apt/apt.conf.d/53cherry-hyprland").read_text()
    assert cmds[-1][:4] == ["apt-get", "install", "-y", "--no-install-recommends"] and "hyprland" in cmds[-1]
    assert desktops.finish_install("hyprland", tmp_path) == []
    units = tmp_path / "usr/lib/systemd/user"
    units.mkdir(parents=True)
    (units / "waybar.service").write_text("")
    assert desktops.finish_install("hyprland", tmp_path) == [["systemctl", "--global", "disable", "waybar.service"]]
    with pytest.raises(ValueError):
        desktops.prepare_install("kde", tmp_path)


def test_hyprland_files_are_packaged():
    root = Path(__file__).resolve().parent.parent
    script = (root / "scripts/build-debs.sh").read_text()
    for f in (root / "desktop/hyprland").iterdir():
        assert f.name in script, f.name
    lua = (root / "desktop/hyprland/hyprland.lua").read_text()
    helper = (root / "desktop/hyprland/cherry-hyprland").read_text()
    for sub in ("start", "apps", "power", "shortcuts", "browser", "screenshot"):
        assert f'" {sub}' in lua and f"\n{sub})" in helper, sub


def test_set_default_overrides_installer_choice(tmp_path):
    make_sessions(tmp_path)
    conf = tmp_path / "etc/gdm3/daemon.conf"
    conf.parent.mkdir(parents=True)
    conf.write_text("# GDM configuration\n[daemon]\nAutomaticLoginEnable=true\nAutomaticLogin=amy\n"
                    "#WaylandEnable=false\n\n[security]\n")
    user = tmp_path / "var/lib/AccountsService/users/amy"
    user.parent.mkdir(parents=True)
    user.write_text("[User]\nLanguage=\nXSession=budgie-desktop\nIcon=\nSystemAccount=false\n")
    desktops.write_system_default("kde", tmp_path, user="amy")
    text = conf.read_text()
    assert "DefaultSession=plasma.desktop" in text and "AutomaticLogin=amy" in text and "#WaylandEnable=false" in text
    assert text.index("DefaultSession") < text.index("[security]")
    acct = user.read_text()
    assert "Session=plasma\n" in acct and "SessionType=wayland" in acct and "XSession=\n" in acct
    assert "SystemAccount=false" in acct
    assert (tmp_path / "etc/cherry/default-desktop").read_text().strip() == "kde"


def test_x11_desktop_session(tmp_path):
    make_sessions(tmp_path)
    desktops.write_user_session("bob", "xfce", tmp_path)
    acct = (tmp_path / "var/lib/AccountsService/users/bob").read_text()
    assert "XSession=xfce" in acct and "SessionType=x11" in acct


def test_set_ini_key():
    assert desktops.set_ini_key("", "daemon", "A", "1") == "[daemon]\nA=1\n"
    text = "[a]\nx=1\n\n[b]\ny=2\n"
    assert desktops.set_ini_key(text, "a", "x", "3") == "[a]\nx=3\n\n[b]\ny=2\n"
    assert desktops.set_ini_key(text, "b", "z", "4") == "[a]\nx=1\n\n[b]\ny=2\nz=4\n"
    assert desktops.set_ini_key(text, "a", "x", None) == "[a]\n\n[b]\ny=2\n"


def test_grub_entries_cover_every_desktop():
    out = desktops.grub_entries(list(desktops.DESKTOPS), "kde")
    for d in desktops.DESKTOPS.values():
        assert f"cherry.desktop={d.id}" in out


def test_installer_starts_on_the_desktop_being_tried():
    text = "mode: required\ndefault: kde\nitems:\n  - id: kde\n"
    assert desktops.installer_default_text(text, "budgie") == "mode: required\ndefault: budgie\nitems:\n  - id: kde\n"


def test_debians_nvidia_driver_is_on_the_iso():
    root = Path(__file__).resolve().parent.parent
    hook = (root / "hooks/35-nvidia.sh").read_text()
    line = next(x for x in hook.splitlines() if x.endswith("> /usr/share/cherry/nvidia-packages-550"))
    wanted = line.split('"')[1].split()
    pool = [line.split() for line in (root / "config/installer-pool.list").read_text().splitlines()]
    assert wanted in pool


def test_install_buttons_start_the_launcher_the_hooks_change():
    root = Path(__file__).resolve().parent.parent
    for hook in ("hooks/30-installer.sh", "hooks/35-nvidia.sh"):
        assert "/usr/bin/calamares-install-debian" in (root / hook).read_text()
    move = (root / "desktop/lib/cherryos/ui/move.py").read_text()
    assert "exec calamares-install-debian" in move
    assert "DEFAULT_INSTALLER)" in (root / "desktop/lib/cherryos/ui/center.py").read_text()


@pytest.mark.parametrize("app", ["store", "center", "move"])
def test_app_windows_match_their_menu_entries(app):
    import re
    root = Path(__file__).resolve().parent.parent / "desktop"
    source = (root / f"lib/cherryos/ui/{app}.py").read_text()
    app_id = re.search(r'^APP_ID = "(.+)"', source, re.M).group(1)
    entry = (root / f"applications/cherry-{app}.desktop").read_text()
    assert f"StartupWMClass={app_id}\n" in entry
    icon = re.search(r"^Icon=(.+)$", entry, re.M).group(1)
    assert f'identify(APP_ID, "' in source and f'"{icon}")' in source
    assert (root / f"icons/{icon}.svg").exists()


def test_store_website_uses_the_app_icon():
    root = Path(__file__).resolve().parent.parent
    assert (root / "cloud/public/cherry-store.svg").read_bytes() == (root / "desktop/icons/cherry-store.svg").read_bytes()


def test_packages_install_side_by_side(tmp_path):
    """Other systems install cherry-move next to cherry-store-client; Cherry OS has cherry-move next to cherry-desktop."""
    import shutil
    import subprocess
    if not shutil.which("dpkg-deb"):
        pytest.skip("needs dpkg-deb")
    root = Path(__file__).resolve().parent.parent
    subprocess.run(["bash", root / "scripts/build-debs.sh", tmp_path], check=True, capture_output=True)
    subprocess.run(["bash", root / "scripts/build-store-client.sh", tmp_path], check=True, capture_output=True)

    def files(name):
        deb, = tmp_path.glob(f"{name}_*.deb")
        out = subprocess.run(["dpkg-deb", "-c", deb], capture_output=True, text=True, check=True).stdout
        return {line.split(None, 5)[5] for line in out.splitlines() if not line.startswith("d")}

    move, store, desktop = files("cherry-move"), files("cherry-store-client"), files("cherry-desktop")
    assert not move & store and not move & desktop
    assert {"./etc/xdg/autostart/cherry-move-watch.desktop", "./etc/xdg/autostart/cherry-move-pending.desktop",
            "./usr/lib/cherry-move/cherryos/ui/move.py", "./usr/bin/cherry-move",
            "./usr/lib/cherry-move/cherryos/system_extras.py"} <= move
    assert "./usr/lib/cherry/cherryos/transfer.py" in desktop  # Cherry Center reads pending transfers
    for timer in ("./usr/lib/systemd/system/cherry-store-self-update.timer",
                  "./usr/lib/systemd/system/cherry-store-self-update.service"):
        assert timer in desktop and timer in store
    unit = (root / "desktop/store-client/cherry-store-self-update.service").read_text()
    assert "cherry update -y cherry-store-client cherry-desktop cherry-move" in unit


def test_os_name_and_live_detection(tmp_path, monkeypatch):
    real = Path.read_text
    fake = {"/etc/os-release": 'PRETTY_NAME="Example 2"\nNAME="Example OS"\n', "/proc/cmdline": "quiet boot=casper"}
    monkeypatch.setattr(Path, "read_text", lambda self, *a, **k: fake[str(self)] if str(self) in fake else real(self, *a, **k))
    assert desktops.os_name() == "Example OS" and desktops.is_live()
    fake["/proc/cmdline"] = "root=/dev/sda1 quiet"
    assert not desktops.is_live()


def test_live_system_never_locks(tmp_path):
    rc = tmp_path / "etc/xdg/kscreenlockerrc"
    rc.parent.mkdir(parents=True)
    rc.write_text("[Greeter][Wallpaper][org.kde.image][General]\nImage=file:///night.png\n")
    desktops.live_no_lock(tmp_path)
    text = rc.read_text()
    assert "Image=file:///night.png" in text and "[Daemon]\nAutolock=false\nLockOnResume=false\n" in text
    xfce = tmp_path / "etc/xdg/xfce4/xfconf/xfce-perchannel-xml/xfce4-screensaver.xml"
    assert 'name="enabled" type="bool" value="false"' in xfce.read_text()


def test_watcher_stays_quiet_for_files_already_back(tmp_path, monkeypatch):
    from cherryos import transfer
    try:
        from cherryos.ui import move
    except (ImportError, ValueError):
        pytest.skip("needs GTK 4 and libadwaita")
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    a, b = codes.new_code(), codes.new_code()
    assert not move.restoring_or_restored(a.code) and not move.restoring_or_restored(None)
    transfer.save_pending(codes.pretty(a.code), "old laptop")
    assert move.restoring_or_restored(a.code) and not move.restoring_or_restored(b.code)
    transfer.clear_pending()
    transfer.remember_restored(b)
    assert move.restoring_or_restored(b.code) and not move.restoring_or_restored(a.code)


def test_errors_reach_the_window_however_late_it_looks(monkeypatch):
    try:
        from cherryos import ui
    except (ImportError, ValueError):
        pytest.skip("needs GTK 4 and libadwaita")
    later = []
    monkeypatch.setattr(ui.GLib, "idle_add", lambda fn, *args: later.append((fn, args)))
    heard = []

    def work():
        raise InterruptedError("cancelled")
    ui.run_async(work, None, heard.append).join()
    for fn, args in later:
        fn(*args)
    assert len(heard) == 1 and isinstance(heard[0], InterruptedError)


def test_tar_stream_does_not_keep_every_member():
    import io
    import tarfile
    from cherryos import transfer
    buf = io.BytesIO()
    with transfer._StreamTar.open(fileobj=buf, mode="w|", format=tarfile.PAX_FORMAT) as tar:
        for i in range(200):
            info = tarfile.TarInfo(f"home/f{i}")
            info.size = 3
            tar.addfile(info, io.BytesIO(b"abc"))
            assert not tar.members
    buf.seek(0)
    names = []
    with transfer._StreamTar.open(fileobj=buf, mode="r|") as tar:
        for member in tar:
            names.append(member.name)
            assert tar.extractfile(member).read() == b"abc"
            assert len(tar.members) <= 1
    assert names == [f"home/f{i}" for i in range(200)]


def test_pending_transfer_remembers_the_nas(tmp_path, monkeypatch):
    from cherryos import transfer
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    nas = {"kind": "nas", "title": "your NAS (nas.local)", "type": "smb", "host": "nas.local", "path": "Backups"}
    transfer.save_pending("CODE", "old laptop", None, remote=nas)
    assert transfer.load_pending()["remote"] == nas and transfer.load_pending()["drive_label"] is None


# ---------------------------------------------------------------- packages (more in test_store.py)

def test_bad_manifests():
    for m in [{"name": "X", "version": "1", "summary": "s"}, {"name": "ok-name", "version": "1"},
              {"name": "ok-name", "version": "1", "summary": "s", "kind": "windows"},
              {"name": "ok-name", "version": "1 2", "summary": "s"}]:
        with pytest.raises(pkg.PkgError):
            pkg.validate_manifest(dict(m))
