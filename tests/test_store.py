"""The Cherry Store: building .debs, the store's intake checks (store/tools) and Cherry's installer.

Needs dpkg-deb. The real apt install/update/remove test only runs as root with CHERRY_TEST_APT=1
(tests/in-trixie.sh sets it inside the Debian container).
"""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "desktop/lib"))
sys.path.insert(0, str(REPO / "store/tools"))

from cherryos import pkg  # noqa: E402
import pipeline  # noqa: E402
import store  # noqa: E402

QUIET = dict(log=lambda *_: None)


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("CHERRY_IDENTITY", str(tmp_path / "alice.json"))
    monkeypatch.setattr(store, "INDEX", tmp_path / "index.json")
    monkeypatch.setattr(store, "debian_names", lambda: {"sudo", "coreutils", "bash"})
    monkeypatch.setenv("CHERRY_STORE_INDEX_URL", f"file://{tmp_path / 'index.json'}")
    monkeypatch.setattr(pkg, "TRUST_FILE", tmp_path / "trusted-keys.json")
    monkeypatch.setattr(pkg, "DB_DIR", tmp_path / "installed")


def make_project(tmp_path: Path, name="hello-cherry", version="1.2", extra="") -> Path:
    proj = tmp_path / f"{name}-{version}"
    (proj / "bin").mkdir(parents=True)
    (proj / "bin" / name).write_text(f"#!/bin/sh\necho hi from {version}\n")
    (proj / "icon.png").write_bytes(b"\x89PNG")
    (proj / "cherry.toml").write_text(f'''name = "{name}"
version = "{version}"
summary = "Says hi"
description = """
Line one.

Line two.
"""
license = "MIT"
[[apps]]
name = "Hello"
exec = "bin/{name}"
icon = "icon.png"
[bin]
{name} = "bin/{name}"
{extra}''')
    return proj


def listing_of(deb: Path) -> dict:
    out = subprocess.run(["dpkg-deb", "-c", str(deb)], capture_output=True, text=True, check=True).stdout
    return {line.split(None, 5)[5].split(" -> ")[0]: line for line in out.splitlines()}


def publish_locally(tmp_path: Path, proj: Path, via="github:alice") -> dict:
    deb, request = pkg.prepare(proj)
    result = store.check(deb, request, via, no_scan=True)
    store.add({**result, "scan": {"status": "clean", "detail": ""}}, deb.resolve().as_uri())
    return result


# ---------------------------------------------------------------- building

def test_build_makes_a_normal_deb(tmp_path):
    old_umask = os.umask(0o002)
    try:
        deb = pkg.build(make_project(tmp_path))
    finally:
        os.umask(old_umask)
    assert deb.name == "hello-cherry_1.2_all.deb"
    files = listing_of(deb)
    assert files["./opt/hello-cherry/bin/hello-cherry"].startswith("-rwxr-xr-x root/root")
    assert files["./opt/hello-cherry/icon.png"].startswith("-rw-r--r--")
    assert files["./usr/share/applications/cherry-hello-cherry-0.desktop"].startswith("-rw-r--r--")
    assert "-> /opt/hello-cherry/bin/hello-cherry" in files["./usr/bin/hello-cherry"]
    assert "./opt/hello-cherry/cherry.toml" not in files
    desktop = subprocess.run(["dpkg-deb", "--fsys-tarfile", str(deb)], capture_output=True, check=True).stdout
    assert b"Exec=\"/opt/hello-cherry/bin/hello-cherry\"" in desktop and b"Icon=/opt/hello-cherry/icon.png" in desktop
    fields = store.control_fields(deb)
    assert fields["Package"] == "hello-cherry" and fields["Version"] == "1.2"
    assert fields["Description"] == "Says hi\nLine one.\n.\nLine two."
    assert pkg.deb_manifest(deb)["license"] == "MIT"
    assert subprocess.run(["dpkg-deb", "-I", str(deb)], capture_output=True).returncode == 0


def test_windows_app_runs_through_cherry_runner(tmp_path):
    proj = tmp_path / "notepadish"
    proj.mkdir()
    (proj / "app.exe").write_bytes(b"MZ")
    (proj / "cherry.toml").write_text('name = "notepadish"\nversion = "1"\nsummary = "x"\nkind = "windows"\n'
                                      '[windows]\nexe = "app.exe"\n[[apps]]\nname = "Notepadish"\n')
    deb = pkg.build(proj)
    tar = subprocess.run(["dpkg-deb", "--fsys-tarfile", str(deb)], capture_output=True, check=True).stdout
    assert b"Exec=cherry-windows-run --package notepadish 0" in tar
    assert "wine" in store.control_fields(deb)["Depends"]
    assert pkg.deb_manifest(deb)["windows"]["exe"] == "app.exe"


def test_bad_bin_entry(tmp_path):
    proj = make_project(tmp_path)
    (proj / "bin/hello-cherry").unlink()
    with pytest.raises(pkg.PkgError, match="isn't in the folder"):
        pkg.build(proj)


# ---------------------------------------------------------------- the store's checks

def test_store_accepts_and_lists_a_signed_package(tmp_path):
    result = publish_locally(tmp_path, make_project(tmp_path))
    assert result["warnings"] == [] and result["license"] == "MIT" and result["tag"] == "hello-cherry_1.2"
    found = pkg.search(["hi"])
    assert [p["name"] for p in found] == ["hello-cherry"] and found[0]["publisher"] == "github:alice"
    entry = pkg.listing("hello-cherry")
    assert entry["latest"] == "1.2" and entry["description"] == "Line one.\n\nLine two."
    path = pkg.fetch(entry, **QUIET)
    assert pkg.deb_fields(path)["Version"] == "1.2"


def test_store_refuses_bad_uploads(tmp_path):
    proj = make_project(tmp_path)
    deb, request = pkg.prepare(proj)
    for change, why in [({"sha256": "0" * 64}, "sha256"), ({"signature": request["signature"][::-1]}, "signature"),
                        ({"name": "other-name"}, "signature"), ({"version": "1:2"}, "versions")]:
        with pytest.raises(store.Rejected, match=why):
            store.check(deb, {**request, **change}, "github:alice", no_scan=True)
    publish_locally(tmp_path, proj)
    deb, request = pkg.prepare(proj)  # a rebuild in a later second has new timestamps, so a new sha256
    with pytest.raises(store.Rejected, match="already published"):
        store.check(deb, request, "github:alice", no_scan=True)

    os.environ["CHERRY_IDENTITY"] = str(tmp_path / "mallory.json")
    deb2, request2 = pkg.prepare(make_project(tmp_path, version="9.9"))
    with pytest.raises(store.Rejected, match="another publisher"):
        store.check(deb2, request2, "cherry:mallory", no_scan=True)
    deb3, request3 = pkg.prepare(make_project(tmp_path, name="cherry-desktop"))
    with pytest.raises(store.Rejected, match="reserved"):
        store.check(deb3, request3, "cherry:mallory", no_scan=True)


def test_cherry_names_need_a_cherry_os_release_key(tmp_path, monkeypatch):
    os.environ["CHERRY_IDENTITY"] = str(tmp_path / "release.json")
    release = pkg.ensure_identity()
    (tmp_path / "official-keys.txt").write_text(f"# release keys\n{release.public_key_b64}  # test\n")
    monkeypatch.setattr(store, "OFFICIAL_KEYS", tmp_path / "official-keys.txt")
    monkeypatch.setattr(pkg, "OFFICIAL_KEYS", {release.public_key_b64})
    publish_locally(tmp_path, make_project(tmp_path, name=pkg.SELF_PACKAGE), via="github:maintainer")
    entry = pkg.load_index()["packages"][pkg.SELF_PACKAGE]
    assert entry["publisher"] == "Cherry OS"
    pkg.check_trust(entry)
    assert pkg.search(["hi"]) == []  # the store app doesn't list itself

    os.environ["CHERRY_IDENTITY"] = str(tmp_path / "mallory.json")
    deb, request = pkg.prepare(make_project(tmp_path, name="cherry-tools"))
    with pytest.raises(store.Rejected, match="reserved"):
        store.check(deb, request, "github:mallory", no_scan=True)
    with pytest.raises(pkg.PkgError, match="isn't signed by Cherry OS"):
        pkg.check_trust({**entry, "name": "cherry-tools", "public_key": request["public_key"]}, force=True)


def test_store_records_warnings_for_unusual_packages(tmp_path):
    stage = tmp_path / "stage"
    (stage / "DEBIAN").mkdir(parents=True)
    (stage / "DEBIAN/control").write_text("Package: sudo\nVersion: 99\nArchitecture: all\nMaintainer: x <x@y>\n"
                                          "Replaces: coreutils\nDescription: not really sudo\n")
    (stage / "DEBIAN/postinst").write_text("#!/bin/sh\necho hi\n")
    (stage / "DEBIAN/postinst").chmod(0o755)
    (stage / "etc").mkdir()
    (stage / "etc/evil.conf").write_text("x")
    (stage / "usr/bin").mkdir(parents=True)
    (stage / "usr/bin/rootme").write_text("x")
    (stage / "usr/bin/rootme").chmod(0o4755)
    for d in (stage, stage / "DEBIAN", stage / "etc", stage / "usr", stage / "usr/bin"):
        d.chmod(0o755)
    deb = tmp_path / "sudo.deb"
    subprocess.run(["dpkg-deb", "--root-owner-group", "--build", str(stage), str(deb)], check=True, capture_output=True)
    ident = pkg.ensure_identity()
    sha = __import__("hashlib").sha256(deb.read_bytes()).hexdigest()
    request = {"name": "sudo", "version": "99", "sha256": sha, "public_key": ident.public_key_b64,
               "signature": ident.sign(pkg.signing_message("sudo", "99", sha))}
    warnings = " ".join(store.check(deb, request, "github:alice", no_scan=True)["warnings"])
    for bit in ("install scripts as administrator (postinst)", "Debian's 'sudo'", "replace Debian packages: coreutils",
                "/etc/evil.conf", "setuid", "/usr/bin/rootme"):
        assert bit in warnings, bit


def test_client_shows_store_warnings(tmp_path):
    publish_locally(tmp_path, make_project(tmp_path))
    entry = pkg.listing("hello-cherry")
    version = entry["versions"]["1.2"]
    assert pkg.warnings_for(entry) == []
    version["scan"] = {"status": "flagged", "detail": "Eicar-Test-Signature"}
    version["warnings"] = ["Runs its own install scripts as administrator (postinst)."]
    warns = pkg.warnings_for(entry)
    assert "Eicar-Test-Signature" in warns[0] and "postinst" in warns[1]
    version["scan"] = {"status": "pending"}
    assert "hasn't been virus-scanned" in pkg.warnings_for(entry)[0]


def test_tampered_download_is_refused(tmp_path):
    publish_locally(tmp_path, make_project(tmp_path))
    entry = pkg.listing("hello-cherry")
    path = Path(entry["versions"]["1.2"]["url"][7:])
    data = bytearray(path.read_bytes())
    data[-1] ^= 1
    path.write_bytes(bytes(data))
    with pytest.raises(pkg.PkgError, match="corrupt"):
        pkg.fetch(entry, **QUIET)


def test_new_key_for_a_known_package_is_refused(tmp_path):
    publish_locally(tmp_path, make_project(tmp_path))
    entry = pkg.listing("hello-cherry")
    pkg.remember_trust(entry)
    pkg.check_trust(entry)
    with pytest.raises(pkg.PkgError, match="signed by someone else"):
        pkg.check_trust({**entry, "key_id": "0123456789abcdef", "publisher": "cherry:mallory"})


def test_newer_version_and_withdrawing(tmp_path):
    publish_locally(tmp_path, make_project(tmp_path, version="1.2"))
    publish_locally(tmp_path, make_project(tmp_path, version="1.10"), via="cherry:alice")
    entry = pkg.listing("hello-cherry")
    assert entry["latest"] == "1.10" and entry["versions"]["1.10"]["via"] == "cherry:alice"
    ident = pkg.load_identity()
    with pytest.raises(store.Rejected, match="only the publisher"):
        store.yank({"name": "hello-cherry", "version": "1.10", "signature": ident.sign(b"something else")})
    store.yank({"name": "hello-cherry", "version": "1.10",
                "signature": ident.sign(pkg.yank_message("hello-cherry", "1.10"))})
    assert pkg.listing("hello-cherry")["latest"] == "1.2"
    store.takedown("hello-cherry", "malware")
    with pytest.raises(pkg.PkgError, match="taken down: malware"):
        pkg.listing("hello-cherry")
    assert pkg.search([]) == []


def test_publish_issue_is_parsed_safely():
    request = {"name": "hello-cherry", "version": "1.2", "url": "https://example.com/x.deb"}
    body = f"Published with cherry.\n\n```json\n{json.dumps(request)}\n```\n"
    assert pipeline.request_from_issue({"title": "Publish: hello-cherry 1.2", "body": body}) == ("publish", request)
    assert pipeline.request_from_issue({"title": "Withdraw: hello-cherry 1.2", "body": body})[0] == "withdraw"
    for title, text, why in [("Publish: hello-cherry 1.3", body, "disagree"), ("Please add my app", body, "title"),
                             ("Publish: hello-cherry 1.2", "no json here", "json block")]:
        with pytest.raises(store.Rejected, match=why):
            pipeline.request_from_issue({"title": title, "body": text})
    with pytest.raises(store.Rejected, match="https"):
        pipeline.download("file:///etc/passwd", Path("/nonexistent"))


# ---------------------------------------------------------------- installing for real

@pytest.mark.skipif(os.geteuid() != 0 or not os.environ.get("CHERRY_TEST_APT"), reason="needs root and apt")
def test_install_update_remove_with_apt(tmp_path):
    publish_locally(tmp_path, make_project(tmp_path, version="1.2"))
    asked = []
    recs = pkg.install(["hello-cherry"], confirm=lambda n, w: asked.append(w) or True, **QUIET)
    assert recs[0]["version"] == "1.2" and not asked
    assert subprocess.run(["hello-cherry"], capture_output=True, text=True).stdout.strip() == "hi from 1.2"
    assert Path("/usr/share/applications/cherry-hello-cherry-0.desktop").exists()
    assert pkg.installed()["hello-cherry"]["publisher"] == "github:alice"

    publish_locally(tmp_path, make_project(tmp_path, version="1.3"))
    assert pkg.updates() == [("hello-cherry", "1.2", "1.3")]
    pkg.install(["hello-cherry"], **QUIET)
    assert subprocess.run(["hello-cherry"], capture_output=True, text=True).stdout.strip() == "hi from 1.3"
    assert pkg.updates() == []

    pkg.uninstall("hello-cherry", **QUIET)
    assert not Path("/opt/hello-cherry").exists() and "hello-cherry" not in pkg.installed()


def system_deb(tmp_path: Path, name: str, version: str, depends: str = "") -> Path:
    stage = tmp_path / f"stage-{name}-{version}"
    (stage / "DEBIAN").mkdir(parents=True)
    (stage / "DEBIAN/control").write_text(f"Package: {name}\nVersion: {version}\nArchitecture: all\n"
                                          f"Maintainer: x <x@y>\n{f'Depends: {depends}' + chr(10) if depends else ''}"
                                          f"Description: test {name}\n")
    (stage / f"usr/share/{name}").mkdir(parents=True)
    (stage / f"usr/share/{name}/version").write_text(version)
    deb = tmp_path / f"{name}_{version}_all.deb"
    subprocess.run(["dpkg-deb", "--root-owner-group", "--build", str(stage), str(deb)], check=True, capture_output=True)
    return deb


@pytest.mark.skipif(os.geteuid() != 0 or not os.environ.get("CHERRY_TEST_APT"), reason="needs root and apt")
def test_cherry_os_packages_update_through_the_store(tmp_path, monkeypatch):
    """Cherry OS ships cherry-desktop + cherry-move outside the store; release-key-signed updates reach them."""
    os.environ["CHERRY_IDENTITY"] = str(tmp_path / "release.json")
    release = pkg.ensure_identity()
    (tmp_path / "official-keys.txt").write_text(f"{release.public_key_b64}\n")
    monkeypatch.setattr(store, "OFFICIAL_KEYS", tmp_path / "official-keys.txt")
    monkeypatch.setattr(pkg, "OFFICIAL_KEYS", {release.public_key_b64})
    store.INDEX.write_text(json.dumps({"format": 1, "packages": {}}))

    def release_version(v):
        for deb in (system_deb(tmp_path, "cherry-move", v), system_deb(tmp_path, "cherry-desktop", v, f"cherry-move (>= {v})")):
            _, request = pkg.prepare(deb)
            result = store.check(deb, request, "github:maintainer", no_scan=True)
            store.add({**result, "scan": {"status": "clean", "detail": ""}}, deb.resolve().as_uri())

    # As installed from the ISO: plain apt, nothing recorded by the store.
    env = dict(os.environ, DEBIAN_FRONTEND="noninteractive")
    subprocess.run(["apt-get", "install", "-y", str(system_deb(tmp_path, "cherry-move", "1.0")),
                    str(system_deb(tmp_path, "cherry-desktop", "1.0", "cherry-move (>= 1.0)"))],
                   check=True, capture_output=True, env=env)
    try:
        assert {n: r["version"] for n, r in pkg.installed().items()} == {"cherry-desktop": "1.0", "cherry-move": "1.0"}
        assert pkg.updates() == []
        release_version("1.1")
        assert pkg.search(["test"]) == []  # never offered in Explore
        assert sorted(pkg.updates()) == [("cherry-desktop", "1.0", "1.1"), ("cherry-move", "1.0", "1.1")]
        pkg.install(["cherry-desktop"], **QUIET)  # brings cherry-move along
        assert Path("/usr/share/cherry-desktop/version").read_text() == "1.1"
        assert Path("/usr/share/cherry-move/version").read_text() == "1.1"
        assert pkg.updates() == []
        with pytest.raises(pkg.PkgError, match="part of your system"):
            pkg.uninstall("cherry-desktop", **QUIET)
        # A package that would need something removed fails instead of removing it.
        clash = system_deb(tmp_path, "clashy", "1", "")
        control = subprocess.run(["dpkg-deb", "-f", str(clash)], capture_output=True, text=True).stdout
        assert "Conflicts" not in control
        stage = tmp_path / "stage-clashy-1"
        (stage / "DEBIAN/control").write_text(control.rstrip("\n") + "\nConflicts: cherry-move\n")
        subprocess.run(["dpkg-deb", "--root-owner-group", "--build", str(stage), str(clash)], check=True, capture_output=True)
        with pytest.raises(pkg.PkgError):
            pkg._apt_install([clash])
        assert pkg._dpkg_version("cherry-move") == "1.1"
    finally:
        subprocess.run(["apt-get", "remove", "-y", "--purge", "cherry-desktop", "cherry-move"], capture_output=True, env=env)
