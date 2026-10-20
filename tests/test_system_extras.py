"""What lives outside the home folder but is still yours (system_extras.py): apps from downloaded .deb files,
your own system services, Docker servers. Gathered on the old computer, carried, and put back by Cherry OS.
Run: make test
"""
import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import tarfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "desktop/lib"))

from cherryos import system_extras, transfer  # noqa: E402
from test_move_everything import make_linux_home, write  # noqa: E402
from test_transfer import cats_for  # noqa: E402

REAL_COLLECT = system_extras.collect  # tests/conftest.py swaps it out for every test


@pytest.mark.skipif(shutil.which("dpkg-deb") is None, reason="needs dpkg-deb")
def test_repack_on_a_merged_usr_system(tmp_path, monkeypatch):
    """/lib is a link to usr/lib on today's Debian and Ubuntu, but packages still list it as a folder."""
    r = tmp_path / "sys"
    write(r / "usr/lib/systemd/system/thing.service", "[Service]\nExecStart=/usr/bin/thing\n")
    write(r / "usr/bin/thing", "#!/bin/sh\n").chmod(0o755)
    write(r / "etc/thing.conf", "setting=1\n")
    os.symlink("usr/lib", r / "lib")
    os.symlink("thing", r / "usr/bin/thing-alias")
    info = tmp_path / "info"
    listed = ["/.", r / "lib", r / "lib/systemd", r / "lib/systemd/system", r / "lib/systemd/system/thing.service",
              r / "usr", r / "usr/bin", r / "usr/bin/thing", r / "usr/bin/thing-alias", r / "etc", r / "etc/thing.conf"]
    write(info / "thing.list", "".join(f"{p}\n" for p in listed))
    write(info / "thing.conffiles", f"{r}/etc/thing.conf\n{r}/etc/gone.conf\n")
    write(info / "thing.postinst", "#!/bin/sh\nexit 0\n")
    monkeypatch.setattr(system_extras, "DPKG_INFO", str(info))
    real_run = system_extras.run
    status = ("Package: thing\nStatus: install ok installed\nVersion: 1.2\nArchitecture: all\n"
              "Maintainer: Someone <a@b.c>\nConffiles:\n /etc/thing.conf abc\nDescription: a thing\n more about it\n")
    monkeypatch.setattr(system_extras, "run", lambda cmd, timeout=120, **kw:
                        status if cmd[:2] == ["dpkg-query", "-s"] else real_run(cmd, timeout, **kw))
    deb = system_extras.repack("thing", str(tmp_path / "out"))
    assert deb and Path(deb).is_file()
    out = subprocess.run(["dpkg-deb", "-c", deb], capture_output=True, text=True, check=True).stdout
    assert f".{r}/lib/systemd/system/thing.service" in out and f".{r}/usr/bin/thing-alias -> thing" in out
    control = subprocess.run(["dpkg-deb", "-f", deb], capture_output=True, text=True, check=True).stdout
    assert "Version: 1.2" in control and "Status" not in control and "Conffiles" not in control
    conffiles = subprocess.run(["dpkg-deb", "-I", deb, "conffiles"], capture_output=True, text=True).stdout
    assert conffiles.split() == [f"{r}/etc/thing.conf"]
    assert not (tmp_path / "out/build").exists()


def test_collect_gives_paths_inside_its_folder(tmp_path, monkeypatch):
    out = tmp_path / "extras"

    def repack(pkg, out_dir):
        return str(write(Path(out_dir) / "packages" / f"{pkg}.deb", "deb"))
    monkeypatch.setattr(system_extras, "unavailable_packages", lambda: ["anydesk", "broken"])
    monkeypatch.setattr(system_extras, "repack", lambda pkg, d: None if pkg == "broken" else repack(pkg, d))
    monkeypatch.setattr(system_extras, "system_services", lambda d: {
        "units": [{"name": "backup-job.service", "text": "ExecStart=/usr/local/bin/backup-job\n", "enabled": True}],
        "dropins": [], "files": [str(write(Path(d) / "system/usr-local.tar", "tar"))]})
    monkeypatch.setattr(system_extras, "docker_servers", lambda d: {})
    monkeypatch.setattr(system_extras, "run", lambda cmd, **kw: "https://anydesk.com/\n" if "anydesk" in cmd else "")
    found = REAL_COLLECT(str(out))
    assert found["debs"] == [{"name": "anydesk", "file": "packages/anydesk.deb", "homepage": "https://anydesk.com/"},
                             {"name": "broken", "file": None, "homepage": ""}]
    assert found["system"]["files"] == ["system/usr-local.tar"]
    assert found["system"]["units"][0]["text"] == "ExecStart=/usr/local/bin/backup-job\n"


def test_docker_container_made_again_as_it_was():
    c = {"name": "vpn", "image": "vpn:local", "restart": "unless-stopped", "network": "host",
         "ports": [["127.0.0.1", "8080", "80/tcp"]], "binds": [["/home/old/.config/vpn/server", "/etc/openvpn", False]],
         "volumes": ["/data"], "env": ["MODE=server"], "cap_add": ["NET_ADMIN"], "privileged": False,
         "devices": ["/dev/net/tun:/dev/net/tun"], "extra_hosts": [], "user": "", "workdir": "",
         "entrypoint": ["/bin/sh", "-c"], "cmd": ["openvpn --config /etc/openvpn/server.conf"]}
    assert system_extras.docker_run_args(c) == [
        "--name", "vpn", "--restart", "unless-stopped", "--network", "host", "-p", "127.0.0.1:8080:80/tcp",
        "-v", "/home/old/.config/vpn/server:/etc/openvpn:ro", "-v", "/data", "-e", "MODE=server",
        "--cap-add", "NET_ADMIN", "--device", "/dev/net/tun:/dev/net/tun", "--entrypoint", "/bin/sh",
        "vpn:local", "-c", "openvpn --config /etc/openvpn/server.conf"]


def fake_tools(tmp_path, monkeypatch, *tools) -> Path:
    fake, calls = tmp_path / "bin", tmp_path / "calls"
    fake.mkdir()
    for tool in tools:
        body = f'#!/bin/sh\necho "{tool} $*" >> {calls}\n'
        if tool == "docker":
            body += ('case "$1 $2" in "container inspect"|"image inspect") exit 1;; esac\n'
                     f'if [ "$1" = compose ]; then echo "compose sees STORIES_PATH=$STORIES_PATH" >> {calls}; fi\n'
                     'if [ "$1" = cp ]; then cat > /dev/null; fi\n')
        write(fake / tool, body).chmod(0o755)
    monkeypatch.setenv("PATH", f"{fake}:{os.environ['PATH']}")
    return calls


def tar_of(path: Path, files: dict) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(path, "w") as t:
        for name, data in files.items():
            info = tarfile.TarInfo(name)
            info.size, info.mode = len(data), 0o755
            t.addfile(info, io.BytesIO(data))
    return path


def test_restore_puts_back_services_apps_and_servers(tmp_path, monkeypatch):
    calls = fake_tools(tmp_path, monkeypatch, "apt-get", "systemctl", "docker")
    root, home = tmp_path / "root", tmp_path / "home/isaiah"
    top = home / transfer.EXTRAS_HOME
    write(top / "packages/anydesk.deb", "deb")
    tar_of(top / "system/usr-local.tar", {"usr/local/bin/backup-job": b"new", "usr/local/bin/mine": b"old computer's",
                                          "etc/shadow": b"never"})
    write(root / "usr/local/bin/mine", "this computer's")
    write(root / "etc/systemd/system/anydesk.service", "from the anydesk package")
    write(top / "docker/image-0.tar", "image")
    write(top / "docker/volume-0.tar", "volume")
    write(top / "docker/volume-1.tar", "volume")
    write(home / "Projects/notes/docker-compose.yml", "services: {}\n")
    extras = {
        "debs": [{"name": "anydesk", "file": "packages/anydesk.deb"}, {"name": "waydroid", "file": None}],
        "system": {"units": [
            {"name": "backup-job.service", "text": "ExecStart=/home/old/bin/backup-job\n", "enabled": True},
            {"name": "check.service", "text": "[Service]\n", "enabled": False},
            {"name": "anydesk.service", "text": "old", "enabled": True},
            {"name": "../../evil.service", "text": "x", "enabled": True}],
            "dropins": [{"path": "dnsmasq.service.d/override.conf", "text": "[Unit]\nAfter=network-online.target\n"}],
            "files": ["system/usr-local.tar"]},
        "docker": {
            "compose": [{"name": "notes", "dir": "/home/old/Projects/notes",
                         "files": ["/home/old/Projects/notes/docker-compose.yml"],
                         "containers": ["notes-office"], "env": {"STORIES_PATH": "/home/old/Stories"}}],
            "containers": [{"name": "vpn", "image": "vpn:local", "restart": "unless-stopped", "network": "host",
                            "binds": [["/home/old/.config/vpn/server", "/etc/openvpn", False]]}],
            "images": [{"tag": "vpn:local", "file": "docker/image-0.tar"}],
            "volumes": [{"container": "vpn", "path": "/data", "file": "docker/volume-0.tar"},
                        {"container": "notes-office", "path": "/var/lib/office",
                         "file": "docker/volume-1.tar"},
                        {"container": "vpn", "path": "/escape", "file": "../../../etc/passwd"}]}}
    assert transfer.restore_extras(extras, top, "/home/old", str(home), root=root) == ["waydroid"]
    units = root / "etc/systemd/system"
    assert (units / "backup-job.service").read_text() == f"ExecStart={home}/bin/backup-job\n"
    assert (units / "anydesk.service").read_text() == "from the anydesk package"
    assert not (tmp_path / "evil.service").exists() and not (root / "etc/evil.service").exists()
    assert (units / "dnsmasq.service.d/override.conf").exists()
    assert (root / "usr/local/bin/backup-job").read_bytes() == b"new"
    assert (root / "usr/local/bin/mine").read_text() == "this computer's"
    assert not (root / "etc/shadow").exists()
    log = calls.read_text().splitlines()
    assert f"apt-get install -y {top}/packages/anydesk.deb" in log
    assert "systemctl enable --now backup-job.service" in log
    assert not any("check.service" in line or "enable --now anydesk" in line for line in log)
    assert f"docker load -i {top}/docker/image-0.tar" in log
    assert (f"docker create --name vpn --restart unless-stopped --network host "
            f"-v {home}/.config/vpn/server:/etc/openvpn:ro vpn:local") in log
    assert log.index("docker cp -a - vpn:/") < log.index("docker start vpn")
    assert not any("/escape" in line for line in log)
    compose = f"docker compose -p notes --project-directory {home}/Projects/notes " \
              f"-f {home}/Projects/notes/docker-compose.yml"
    assert log.index(f"{compose} up --no-start") < log.index("docker cp -a - notes-office:/var/lib") \
        < log.index(f"{compose} up -d")
    assert f"compose sees STORIES_PATH={home}/Stories" in log


def test_servers_from_an_earlier_try_are_started_and_their_data_left_alone(tmp_path, monkeypatch):
    calls = fake_tools(tmp_path, monkeypatch, "systemctl")
    write(tmp_path / "bin/docker", f'#!/bin/sh\necho "docker $*" >> {calls}\nif [ "$1" = cp ]; then cat > /dev/null; fi\n'
          ).chmod(0o755)  # every container and image already exists
    home = tmp_path / "home/isaiah"
    top = home / transfer.EXTRAS_HOME
    write(top / "docker/volume-0.tar", "volume")
    write(home / "Projects/web/compose.yml", "services: {}\n")
    extras = {"docker": {
        "compose": [{"name": "web", "dir": f"{home}/Projects/web", "files": [f"{home}/Projects/web/compose.yml"],
                     "containers": ["web-app"]}],
        "containers": [{"name": "vpn", "image": "vpn:local", "restart": "always", "network": "host"}],
        "volumes": [{"container": "web-app", "path": "/data", "file": "docker/volume-0.tar"}]}}
    assert transfer.restore_extras(extras, top, None, str(home), root=tmp_path / "root") == []
    log = calls.read_text().splitlines()
    assert "docker start vpn" in log and not any(line.startswith(("docker create", "docker cp")) for line in log)
    compose = f"docker compose -p web --project-directory {home}/Projects/web -f {home}/Projects/web/compose.yml"
    assert f"{compose} up -d" in log and f"{compose} up --no-start" not in log


def test_other_drives_and_network_shares_are_mounted_again(tmp_path, monkeypatch):
    fstab = write(tmp_path / "old-fstab", "# comment\nUUID=sys / ext4 defaults 0 1\nUUID=efi /boot/efi vfat umask=0077 0 1\n"
                  "/swap.img none swap sw 0 0\nUUID=big /media/old/big ext4 defaults,x-systemd.automount 0 2\n"
                  "LABEL=gone /mnt/gone ext4 defaults 0 2\n/dev/sdb1 /mnt/raw ext4 defaults 0 2\n"
                  "//nas.local/me /home/old/nas cifs credentials=/home/old/.config/nas,noauto,user 0 0\n"
                  "tmpfs /tmp tmpfs defaults 0 0\n")
    mounts = system_extras.drive_mounts(str(fstab))
    assert [m["where"] for m in mounts] == ["/media/old/big", "/mnt/gone", "/home/old/nas"]
    calls = fake_tools(tmp_path, monkeypatch, "apt-get", "systemctl", "mount", "mount.cifs")
    root, home = tmp_path / "root", tmp_path / "home/isaiah"
    home.mkdir(parents=True)
    write(root / "etc/fstab", "UUID=new / ext4 defaults 0 1\nUUID=had /media/old/big ext4 defaults 0 2")
    write(root / "dev/disk/by-uuid/big", "")
    mounts += [{"spec": "UUID=big", "where": "/media/old/big", "type": "ext4", "options": "defaults", "pass": "2"},
               {"spec": "UUID=big", "where": "/mnt/x\nUUID=big /etc", "type": "ext4", "options": "defaults"}]
    mounts[0]["where"] = "/media/old/big2"

    def ok(cmd, **kw):
        return subprocess.run(cmd, **kw).returncode == 0
    problems = transfer._restore_mounts(mounts, lambda t: t.replace("/home/old/", f"{home}/"), str(home), root, ok)
    assert problems == ["/mnt/gone"]
    assert (root / "etc/fstab").read_text() == (
        "UUID=new / ext4 defaults 0 1\nUUID=had /media/old/big ext4 defaults 0 2\n"
        "UUID=big /media/old/big2 ext4 defaults,x-systemd.automount,nofail 0 2\n"
        f"//nas.local/me {home}/nas cifs credentials={home}/.config/nas,noauto,user,nofail 0 0\n")
    assert (root / "media/old/big2").is_dir() and (root / str(home).lstrip("/") / "nas").is_dir()
    log = calls.read_text().splitlines()
    assert "systemctl start media-old-big2.automount" in log
    assert not any(line.startswith(("mount ", "apt-get")) for line in log)


def test_compose_settings_that_came_from_outside_the_project(tmp_path):
    """Compose reads .env by itself, but not a config.env a script loads first, or what the shell had set."""
    folder = tmp_path / "notes"
    write(folder / "docker-compose.yml", "services:\n  connector:\n    ports:\n      - \"${PORT:-8095}:8095\"\n"
          "    volumes:\n      - ${STORIES_PATH}:/stories:rw\n      - ${CACHE}:/cache\n"
          "    environment:\n      JWT_SECRET: ${JWT_SECRET}\n      URL: $PUBLIC_URL\n      LITERAL: $$HOME\n"
          "      FROM_DOTENV: ${IN_DOTENV}\n")
    write(folder / "config.env", "# settings\nexport JWT_SECRET='s3'\nPORT=9000\nUNUSED=1\n")
    write(folder / ".env", "IN_DOTENV=yes\n")
    running = [{"Config": {"Env": ["PUBLIC_URL=http://pc:8095", "JWT_SECRET=other", "PATH=/bin"]},
                "Mounts": [{"Type": "bind", "Source": "/home/old/Stories", "Destination": "/stories"},
                           {"Type": "volume", "Source": "/var/lib/docker/x", "Destination": "/cache"}]}]
    env = system_extras.compose_env(str(folder), [str(folder / "docker-compose.yml")], running)
    assert env == {"JWT_SECRET": "s3", "PORT": "9000", "PUBLIC_URL": "http://pc:8095", "STORIES_PATH": "/home/old/Stories"}


@pytest.mark.skipif(shutil.which("dpkg") is None, reason="needs dpkg")
def test_app_built_for_another_system_comes_from_its_own_releases(tmp_path, monkeypatch):
    arch = subprocess.run(["dpkg", "--print-architecture"], capture_output=True, text=True).stdout.strip()
    deb = b"the debian 13 build"
    assets = [{"name": f"app_1.2+ubuntu24.04_{arch}.deb", "browser_download_url": "https://x/u"},
              {"name": f"app_1.2+debian13_{arch}.deb.sig", "browser_download_url": "https://x/sig"},
              {"name": f"app_1.2+debian1_{arch}.deb", "browser_download_url": "https://x/d1"},
              {"name": f"app_1.2+debian13_{arch}.deb", "browser_download_url": "https://x/d13",
               "digest": "sha256:" + hashlib.sha256(deb).hexdigest()}]
    asked = []

    def urlopen(req, timeout=None):
        asked.append(req.full_url)
        if req.full_url == "https://api.github.com/repos/example/fan-control/releases/latest":
            return io.BytesIO(json.dumps({"assets": assets}).encode())
        return io.BytesIO(deb if req.full_url == "https://x/d13" else b"wrong")
    monkeypatch.setattr(transfer.urllib.request, "urlopen", urlopen)
    monkeypatch.setattr(transfer, "_distro_tags", lambda rel: {"debian13", "trixie"})
    got = transfer._upstream_deb("https://github.com/example/fan-control", tmp_path, {})
    assert got == tmp_path / f"app_1.2+debian13_{arch}.deb" and got.read_bytes() == deb
    assets[-1]["digest"] = "sha256:" + "0" * 64
    assert transfer._upstream_deb("https://github.com/example/fan-control", tmp_path, {}) is None
    assert not got.exists()
    assert transfer._upstream_deb("https://anydesk.com/", tmp_path, {}) is None
    assert transfer._upstream_deb("", tmp_path, {}) is None
    assert all(u.startswith(("https://api.github.com/", "https://x/d13")) for u in asked)


def test_debian_based_systems():
    assert transfer._debian_based({"ID": "cherry", "ID_LIKE": "debian"})
    assert transfer._debian_based({"ID": "debian"})
    assert not transfer._debian_based({"ID": "ubuntu", "ID_LIKE": "debian"})
    assert not transfer._debian_based({"ID": "linuxmint", "ID_LIKE": "ubuntu debian"})
    assert "trixie" in transfer._distro_tags({"ID": "cherry", "ID_LIKE": "debian", "VERSION_CODENAME": "trixie"})
    assert {"ubuntu24.04", "noble"} <= transfer._distro_tags({"ID": "ubuntu", "VERSION_ID": "24.04",
                                                            "VERSION_CODENAME": "noble"})


def test_moved_with_the_files_and_handed_to_the_reinstall(tmp_path, monkeypatch):
    home, conn = make_linux_home(tmp_path)
    conn.close()

    def collect(out_dir):
        write(Path(out_dir) / "packages/anydesk.deb", "the rebuilt app")
        return {"debs": [{"name": "anydesk", "file": "packages/anydesk.deb"}], "system": {}, "docker": {}}
    monkeypatch.setattr(system_extras, "collect", collect)
    stick = tmp_path / "usb"
    stick.mkdir()
    result = transfer.send(cats_for(home, monkeypatch), "usb", drive=stick)
    assert not (home / ".cache/cherry-move/extras").exists()
    new = tmp_path / "new"
    new.mkdir()
    manifest = transfer.restore(result.code, drive=stick, home=new)
    extras = manifest["apps"]["extras"]
    assert extras["debs"] == [{"name": "anydesk", "file": "packages/anydesk.deb"}]
    assert (new / transfer.EXTRAS_HOME / "packages/anydesk.deb").read_text() == "the rebuilt app"
    apps = {"home": manifest["apps"]["home"], "extras": extras}
    assert transfer.reinstall_count(apps) == 1

    spec = tmp_path / "spec.json"
    fake_tools(tmp_path, monkeypatch, "systemctl")
    said = write(tmp_path / "said", transfer.ROOT_PROBLEMS + '["fan-control", "wake-lan.service"]\n')
    write(tmp_path / "bin/pkexec", f'#!/bin/sh\nfor a; do last="$a"; done\ncp "$last" {spec}\ncat {said}\n').chmod(0o755)
    assert transfer.reinstall_apps(apps, log=lambda _m: None, home=new) == ["fan-control", "wake-lan.service"]
    got = json.loads(spec.read_text())
    assert got["extras"] == extras and got["extras_dir"] == str(new / transfer.EXTRAS_HOME)
    assert got["old_home"] == str(home) and got["home"] == str(new)
