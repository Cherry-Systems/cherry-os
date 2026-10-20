"""cherry — the Cherry OS package manager.

Apps in the Cherry Store are ordinary Debian packages (.deb) kept in the Cherry-Systems/cherry-store
repository on GitHub; its index.json lists them. Cherry downloads a package, checks its hash and its
publisher's Ed25519 signature, shows the store's warnings (virus scan, install scripts and so on) and then
hands it to apt, so dependencies, upgrades and removal work like any other Debian package.

The first key seen for a package name is remembered, and a different key for the same name is refused.
Anyone can publish: with a GitHub account (through the gh command) or a Cherry account (through Cherry Cloud).
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

from .cloud import Cloud, CloudError, Signer

try:
    import tomllib
except ImportError:  # Python < 3.11 (only needed for building packages)
    tomllib = None

ROOT = Path(os.environ.get("CHERRY_ROOT", "/"))
DB_DIR = ROOT / "var/lib/cherry/installed"
TRUST_FILE = ROOT / "var/lib/cherry/trusted-keys.json"
STORE_REPO = "Cherry-Systems/cherry-store"
INDEX_URL = f"https://raw.githubusercontent.com/{STORE_REPO}/main/index.json"
INDEX_MAX_AGE = 300
CHUNK = 16 * 1024 * 1024
KINDS = ("native", "windows", "appimage")
ARCHES = ("all", "amd64", "arm64")
EXCLUDE = {".git", "dist", "__pycache__", ".venv", "node_modules", "cherry.toml"}
NAME_RE = r"[a-z0-9][a-z0-9.+-]{1,62}"
VERSION_RE = r"[0-9][0-9A-Za-z.+~-]{0,63}"
# Names like cherry-* belong to Cherry OS and must be signed with one of its release keys.
OFFICIAL_KEYS = {"xkgIvrCrIG4pSUJDNjOYqhHSnVw3Mfn1Dz9hn6cvSr0="}
SELF_PACKAGE = "cherry-store-client"  # the Cherry Store app for other Debian-based systems
# Cherry's own packages. They come with Cherry OS (or as a download) rather than from Explore, update through
# the store signed with a Cherry OS release key, and the store never removes them.
SYSTEM_PACKAGES = {SELF_PACKAGE: "Cherry Store (this app)", "cherry-desktop": "Cherry OS apps",
                   "cherry-move": "Cherry Move"}


def display_name(name: str) -> str:
    return SYSTEM_PACKAGES.get(name, name)


def reserved(name: str) -> bool:
    return name == "cherry" or name.startswith("cherry-")


class PkgError(Exception):
    pass


def human_size(n: int) -> str:
    return f"{n / 1e6:.1f} MB" if n >= 1e6 else f"{max(1, round(n / 1e3))} KB"


def signing_message(name: str, version: str, sha256: str) -> bytes:
    return f"cherry-package\n{name}\n{version}\n{sha256}".encode()


def yank_message(name: str, version: str) -> bytes:
    return f"cherry-yank\n{name}\n{version}".encode()


def tag_for(name: str, version: str) -> str:
    return f"{name}_{version}".replace("~", "-").replace(":", "-")


def store_setting(key: str, default: str) -> str:
    env = os.environ.get(f"CHERRY_STORE_{key}")
    if env:
        return env
    for conf in (Path.home() / ".config/cherry/store.conf", ROOT / "etc/cherry/store.conf"):
        try:
            for line in conf.read_text().splitlines():
                if line.startswith(f"{key}="):
                    return line.split("=", 1)[1].strip()
        except OSError:
            continue
    return default


# ---------------------------------------------------------------- publisher identity

def identity_file() -> Path:
    return Path(os.environ.get("CHERRY_IDENTITY", Path.home() / ".config/cherry/publisher.json"))


@dataclass
class Identity:
    username: str
    key_id: str
    private_key: Ed25519PrivateKey

    @property
    def public_key_b64(self) -> str:
        raw = self.private_key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
        return base64.b64encode(raw).decode()

    @property
    def signer(self) -> Signer:
        return Signer(self.private_key, self.key_id)

    def sign(self, message: bytes) -> str:
        return base64.b64encode(self.private_key.sign(message)).decode()

    def save(self) -> Path:
        raw = self.private_key.private_bytes(serialization.Encoding.Raw, serialization.PrivateFormat.Raw,
                                             serialization.NoEncryption())
        path = identity_file()
        path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as fh:
            json.dump({"username": self.username, "key_id": self.key_id,
                       "private_key": base64.b64encode(raw).decode()}, fh, indent=2)
        return path


def key_id_for(public_b64: str) -> str:
    return hashlib.sha256(base64.b64decode(public_b64)).hexdigest()[:16]


def load_identity(path: Path | None = None) -> Identity:
    path = path or identity_file()
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        raise PkgError("you don't have a publisher key yet — run 'cherry signup NAME' or publish with --github") from None
    key = Ed25519PrivateKey.from_private_bytes(base64.b64decode(data["private_key"]))
    return Identity(data.get("username", ""), data["key_id"], key)


def ensure_identity() -> Identity:
    """The signing key for packages, made the first time someone publishes."""
    if identity_file().exists():
        return load_identity()
    ident = Identity("", "", Ed25519PrivateKey.generate())
    ident.key_id = key_id_for(ident.public_key_b64)
    ident.save()
    return ident


def signup(username: str, email: str = "", cloud: Cloud | None = None) -> Identity:
    cloud = cloud or Cloud()
    ident = ensure_identity()
    if ident.username:
        raise PkgError(f"already signed in as {ident.username} ({identity_file()})")
    cloud.post("/api/accounts", {"username": username.lower(), "email": email, "public_key": ident.public_key_b64},
               signer=ident.signer)
    ident.username = username.lower()
    ident.save()
    return ident


def login(key_file: Path, cloud: Cloud | None = None) -> Identity:
    ident = load_identity(key_file)
    try:
        ident.username = (cloud or Cloud()).post("/api/accounts/whoami", {}, signer=ident.signer)["username"]
    except CloudError as err:
        if err.status not in (401, 403, 404):
            raise
        ident.username = ""
    ident.save()
    return ident


# ---------------------------------------------------------------- cherry.toml and building .debs

def read_manifest_file(path: Path) -> dict:
    if tomllib is None:
        raise PkgError("building packages needs Python 3.11 or newer")
    try:
        manifest = tomllib.loads(path.read_text())
    except (OSError, tomllib.TOMLDecodeError) as err:
        raise PkgError(f"can't read {path}: {err}") from None
    return validate_manifest(manifest)


def validate_manifest(m: dict) -> dict:
    for key in ("name", "version", "summary"):
        if not m.get(key):
            raise PkgError(f"cherry.toml needs '{key}'")
    if not re.fullmatch(NAME_RE, m["name"]):
        raise PkgError("name must be 2-63 lowercase letters, numbers, '.', '+' or '-'")
    m["version"] = str(m["version"])
    if not re.fullmatch(VERSION_RE, m["version"]):
        raise PkgError("version must start with a number and use only letters, numbers, '.', '+', '~' and '-'")
    m.setdefault("kind", "native")
    if m["kind"] not in KINDS:
        raise PkgError(f"kind must be one of: {', '.join(KINDS)}")
    m.setdefault("architecture", "all")
    if m["architecture"] not in ARCHES:
        raise PkgError(f"architecture must be one of: {', '.join(ARCHES)}")
    if m["kind"] == "windows" and not (m.get("windows") or {}).get("exe"):
        raise PkgError("Windows packages need [windows] exe = \"...\"")
    for app in m.get("apps", []):
        if not app.get("name") or not app.get("exec", m.get("windows", {}).get("exe")):
            raise PkgError("each [[apps]] entry needs a name and exec")
    for cmd in m.get("bin") or {}:
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._+-]*", cmd):
            raise PkgError(f"bad command name: {cmd}")
    return m


TEMPLATE = '''# Cherry Store package. Docs: cherry help
name = "{name}"
version = "0.1.0"
summary = "One line about what it does"
description = """
A longer description shown in the Cherry Store.
"""
license = "MIT"
homepage = ""
kind = "native"          # native (Linux program) | windows (.exe run with Wine) | appimage
architecture = "all"     # all (scripts) | amd64 | arm64 (compiled programs)
apt_depends = []         # Debian packages your app needs, e.g. ["python3-gi"]
depends = []             # other Cherry Store packages

[[apps]]                 # menu entries
name = "{title}"
exec = "bin/{name}"      # path inside this folder
icon = "icon.png"
categories = "Utility;"
terminal = false

[bin]                    # commands added to everyone's PATH
{name} = "bin/{name}"

# For a Windows app, use kind = "windows" and:
# [windows]
# exe = "MyApp.exe"                 # or "C:/Program Files/MyApp/MyApp.exe" after an installer
# installer = "setup.exe"           # optional: run once per user before the first launch
# installer_args = "/S"
'''


def init(directory: Path) -> Path:
    path = directory / "cherry.toml"
    if path.exists():
        raise PkgError(f"{path} already exists")
    name = "".join(c if c.isalnum() else "-" for c in directory.resolve().name.lower()).strip("-") or "my-app"
    path.write_text(TEMPLATE.format(name=name, title=name.replace("-", " ").title()))
    return path


def _desktop_entry(name: str, idx: int, app: dict, manifest: dict, appdir: Path, stage_appdir: Path) -> str:
    kind = manifest["kind"]
    exec_line = f"cherry-windows-run --package {name} {idx}" if kind == "windows" else f'"{appdir / app["exec"]}"'
    icon = app.get("icon")
    icon_path = str(appdir / icon) if icon and (stage_appdir / icon).exists() else (
        "wine" if kind == "windows" else "application-x-executable")
    lines = ["[Desktop Entry]", "Type=Application", f"Name={app['name']}", f"Comment={manifest.get('summary', '')}",
             f"Exec={exec_line}", f"Icon={icon_path}", f"Categories={app.get('categories', 'Utility;')}",
             f"Terminal={'true' if app.get('terminal') else 'false'}", f"X-Cherry-Package={name}"]
    return "\n".join(lines) + "\n"


def _control(m: dict, maintainer: str, size_kb: int) -> str:
    depends = list(m.get("apt_depends", [])) + list(m.get("depends", []))
    if m["kind"] == "windows":
        depends += ["wine", "cherry-desktop"]
    desc = [m["summary"].strip()]
    for line in (m.get("description") or "").strip().splitlines():
        desc.append(f" {line}" if line.strip() else " .")
    fields = {"Package": m["name"], "Version": m["version"], "Architecture": m["architecture"],
              "Maintainer": maintainer, "Installed-Size": str(size_kb), "Depends": ", ".join(dict.fromkeys(depends)),
              "Section": "misc", "Priority": "optional", "Homepage": m.get("homepage", "")}
    return "".join(f"{k}: {v}\n" for k, v in fields.items() if v) + "Description: " + "\n".join(desc) + "\n"


def build(directory: Path, maintainer: str | None = None) -> Path:
    """Turns a folder with cherry.toml into dist/NAME_VERSION_ARCH.deb. Files go to /opt/NAME."""
    if not shutil.which("dpkg-deb"):
        raise PkgError("building packages needs dpkg-deb (sudo apt install dpkg)")
    manifest = read_manifest_file(directory / "cherry.toml")
    name = manifest["name"]
    appdir = Path("/opt") / name
    dist = directory / "dist"
    dist.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        stage = Path(tmp) / "root"
        opt = stage / "opt" / name
        shutil.copytree(directory, opt, symlinks=True,
                        ignore=lambda d, names: [n for n in names if n in EXCLUDE and Path(d) == directory])
        execs = {app["exec"] for app in manifest.get("apps", []) if manifest["kind"] == "appimage"}
        execs |= set((manifest.get("bin") or {}).values())
        for path in [opt, *opt.rglob("*")]:
            if path.is_symlink():
                continue
            rel = str(path.relative_to(opt))
            path.chmod(0o755 if path.is_dir() or path.stat().st_mode & 0o111 or rel in execs else 0o644)
        (opt / "cherry.json").write_text(json.dumps(manifest, indent=2))
        (opt / "cherry.json").chmod(0o644)
        for cmd, rel in (manifest.get("bin") or {}).items():
            if not (opt / rel).exists():
                raise PkgError(f"[bin] {cmd} points at {rel}, which isn't in the folder")
            link = stage / "usr/bin" / cmd
            link.parent.mkdir(parents=True, exist_ok=True)
            link.symlink_to(appdir / rel)
        for idx, app in enumerate(manifest.get("apps", [])):
            entry = stage / "usr/share/applications" / f"cherry-{name}-{idx}.desktop"
            entry.parent.mkdir(parents=True, exist_ok=True)
            entry.write_text(_desktop_entry(name, idx, app, manifest, appdir, opt))
            entry.chmod(0o644)
        for d in [stage, *stage.rglob("*")]:
            if d.is_dir() and not d.is_symlink():
                d.chmod(0o755)
        size_kb = sum(p.lstat().st_size for p in stage.rglob("*")) // 1024 + 1
        if not maintainer:
            ident = ensure_identity()
            maintainer = f"{ident.username or os.environ.get('USER', 'publisher')} <{ident.key_id}@cherry-store.invalid>"
        (stage / "DEBIAN").mkdir(mode=0o755)
        (stage / "DEBIAN/control").write_text(_control(manifest, maintainer, size_kb))
        (stage / "DEBIAN/control").chmod(0o644)
        out = dist / f"{name}_{manifest['version']}_{manifest['architecture']}.deb"
        res = subprocess.run(["dpkg-deb", "--root-owner-group", "-Zxz", "--build", str(stage), str(out)],
                             capture_output=True, text=True)
        if res.returncode:
            raise PkgError(f"dpkg-deb failed: {res.stderr.strip()}")
    return out


def deb_fields(path: Path) -> dict:
    res = subprocess.run(["dpkg-deb", "-f", str(path), "Package", "Version", "Architecture", "Homepage"],
                         capture_output=True, text=True)
    if res.returncode:
        raise PkgError(f"{path} isn't a valid .deb")
    return dict(line.split(": ", 1) for line in res.stdout.splitlines() if ": " in line)


def deb_manifest(path: Path) -> dict:
    """cherry.json from a package Cherry built, or {} for any other .deb."""
    fields = deb_fields(path)
    res = subprocess.run(["dpkg-deb", "--fsys-tarfile", str(path)], capture_output=True)
    if res.returncode == 0:
        import io
        import tarfile
        with tarfile.open(fileobj=io.BytesIO(res.stdout)) as tar:
            for name in (f"./opt/{fields['Package']}/cherry.json", f"opt/{fields['Package']}/cherry.json"):
                try:
                    return json.load(tar.extractfile(name))
                except (KeyError, ValueError, AttributeError):
                    continue
    return {}


# ---------------------------------------------------------------- the store's index

def _index_cache() -> Path:
    if os.geteuid() == 0 or ROOT != Path("/"):
        return ROOT / "var/cache/cherry/index.json"
    return Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "cherry/index.json"


def load_index(refresh: bool = False) -> dict:
    """The store's index.json, re-downloaded when it's more than a few minutes old."""
    cache = _index_cache()
    url = store_setting("INDEX_URL", INDEX_URL)
    if url.startswith("file://"):
        return json.loads(Path(url[7:]).read_text())
    fresh = cache.exists() and time.time() - cache.stat().st_mtime < INDEX_MAX_AGE
    if refresh or not fresh:
        try:
            req = urllib.request.Request(f"{url}?t={int(time.time()) // 60}", headers={"User-Agent": "CherryOS/1.0"})
            with urllib.request.urlopen(req, timeout=60) as res:
                data = res.read()
            index = json.loads(data)
            cache.parent.mkdir(parents=True, exist_ok=True)
            cache.write_bytes(data)
            return index
        except (OSError, ValueError) as err:
            if not cache.exists():
                raise PkgError(f"can't reach the Cherry Store ({url}): {getattr(err, 'reason', err)}") from None
    return json.loads(cache.read_text())


def listing(name: str, index: dict | None = None) -> dict:
    pkg = (index or load_index()).get("packages", {}).get(name)
    if not pkg:
        raise PkgError(f"there's no '{name}' in the Cherry Store (try 'cherry search')")
    if pkg.get("taken_down"):
        raise PkgError(f"'{name}' was taken down: {pkg['taken_down'].get('reason') or 'reported'}")
    if not pkg.get("latest"):
        raise PkgError(f"every version of '{name}' was withdrawn by its publisher")
    return pkg


def search(words: list[str], index: dict | None = None) -> list[dict]:
    words = [w.lower() for w in words]
    out = []
    for pkg in (index or load_index()).get("packages", {}).values():
        if pkg.get("taken_down") or not pkg.get("latest") or pkg["name"] in SYSTEM_PACKAGES:
            continue
        text = f"{pkg['name']} {pkg.get('summary', '')} {pkg.get('description', '')}".lower()
        if all(w in text for w in words):
            out.append(pkg)
    return sorted(out, key=lambda p: (not any(w in p["name"] for w in words), p["name"]))


def warnings_for(pkg: dict, version: dict | None = None) -> list[str]:
    version = version or pkg["versions"][pkg["latest"]]
    scan = version.get("scan", {})
    out = []
    if scan.get("status") == "flagged":
        out.append(f"The virus scanner flagged this package: {scan.get('detail') or 'unknown threat'}.")
    elif scan.get("status") != "clean":
        out.append("This version hasn't been virus-scanned yet.")
    out += version.get("warnings", [])
    if version.get("yanked"):
        out.append("The publisher withdrew this version.")
    if pkg.get("kind") == "windows":
        out.append("This is a Windows app; it runs with Wine.")
    return out


# ---------------------------------------------------------------- installing

def _dpkg_version(name: str) -> str | None:
    res = subprocess.run(["dpkg-query", "-W", "-f=${db:Status-Status} ${Version}", name], capture_output=True, text=True)
    status, _, version = res.stdout.partition(" ")
    return version if res.returncode == 0 and status == "installed" else None


def newer(a: str, b: str) -> bool:
    return subprocess.run(["dpkg", "--compare-versions", a, "gt", b]).returncode == 0


def installed() -> dict[str, dict]:
    """Apps installed from the Cherry Store (that apt hasn't since removed), and Cherry's own packages."""
    out = {}
    if DB_DIR.is_dir():
        for p in sorted(DB_DIR.glob("*.json")):
            try:
                rec = json.loads(p.read_text())
            except (OSError, ValueError):
                continue
            version = _dpkg_version(p.stem) if ROOT == Path("/") else rec.get("version")
            if version:
                rec["version"] = version
                out[p.stem] = rec
    for name in SYSTEM_PACKAGES:
        if name not in out and ROOT == Path("/") and (version := _dpkg_version(name)):
            out[name] = {"name": name, "version": version, "publisher": "Cherry OS"}
    return out


def _trust() -> dict:
    try:
        return json.loads(TRUST_FILE.read_text())
    except (OSError, ValueError):
        return {}


def check_trust(pkg: dict, force: bool = False) -> None:
    if reserved(pkg["name"]) and pkg.get("public_key") not in OFFICIAL_KEYS:
        raise PkgError(f"'{pkg['name']}' claims to be part of Cherry OS but isn't signed by Cherry OS. Refusing.")
    if pkg["name"] == SELF_PACKAGE and _dpkg_version("cherry-desktop"):
        raise PkgError("Cherry OS already has the Cherry Store built in")
    trust = _trust().get(pkg["name"])
    if trust and pkg["key_id"] not in trust["keys"] and not force:
        raise PkgError(f"'{pkg['name']}' used to come from {trust['publisher']} but is now signed by someone else "
                       f"({pkg['publisher']}). Refusing (use --force if you're sure).")


def remember_trust(pkg: dict) -> None:
    trust = _trust()
    entry = trust.setdefault(pkg["name"], {"publisher": pkg["publisher"], "keys": []})
    entry["publisher"] = pkg["publisher"]
    if pkg["key_id"] not in entry["keys"]:
        entry["keys"].append(pkg["key_id"])
    TRUST_FILE.parent.mkdir(parents=True, exist_ok=True)
    TRUST_FILE.write_text(json.dumps(trust, indent=2))


def fetch(pkg: dict, log=print) -> Path:
    """Downloads a package's newest version and checks it's exactly what its publisher signed."""
    name, version = pkg["name"], pkg["latest"]
    info = pkg["versions"][version]
    tmp = Path(tempfile.mkdtemp(prefix="cherry-"))
    tmp.chmod(0o755)
    path = tmp / f"{name}_{version}_{info['architecture']}.deb"
    digest, got = hashlib.sha256(), 0
    log(f"Downloading {name} {version} ({human_size(info['size'])})")
    try:
        req = urllib.request.Request(info["url"], headers={"User-Agent": "CherryOS/1.0"})
        with urllib.request.urlopen(req, timeout=300) as res, open(path, "wb") as out:
            while chunk := res.read(1 << 20):
                got += len(chunk)
                if got > info["size"]:
                    break
                digest.update(chunk)
                out.write(chunk)
    except OSError as err:
        shutil.rmtree(tmp, ignore_errors=True)
        raise PkgError(f"couldn't download {name}: {getattr(err, 'reason', err)}") from None
    path.chmod(0o644)
    if got != info["size"] or digest.hexdigest() != info["sha256"]:
        shutil.rmtree(tmp, ignore_errors=True)
        raise PkgError(f"{name}: the download is corrupt (it doesn't match the store's hash)")
    try:
        Ed25519PublicKey.from_public_bytes(base64.b64decode(pkg["public_key"])).verify(
            base64.b64decode(info["signature"]), signing_message(name, version, info["sha256"]))
    except (InvalidSignature, ValueError):
        shutil.rmtree(tmp, ignore_errors=True)
        raise PkgError(f"{name}: the publisher's signature doesn't match — not installing") from None
    if key_id_for(pkg["public_key"]) != pkg["key_id"]:
        raise PkgError(f"{name}: key id mismatch")
    fields = deb_fields(path)
    if (fields.get("Package"), fields.get("Version")) != (name, version):
        raise PkgError(f"{name}: the package's contents don't match the store listing")
    return path


def _apt_install(paths: list[Path], log=print) -> None:
    env = dict(os.environ, DEBIAN_FRONTEND="noninteractive")
    # --no-remove: an app (or an update) that clashes with something installed fails instead of removing it.
    res = subprocess.run(["apt-get", "install", "-y", "--no-install-recommends", "--no-remove",
                          *[str(p.resolve()) for p in paths]], env=env)
    if res.returncode:
        raise PkgError("apt couldn't install it (see the messages above)")


def _record(name: str, version: str, pkg: dict | None) -> dict:
    rec = {"name": name, "version": version, "publisher": (pkg or {}).get("publisher", "local file"),
           "key_id": (pkg or {}).get("key_id"), "installed": int(time.time())}
    DB_DIR.mkdir(parents=True, exist_ok=True)
    (DB_DIR / f"{name}.json").write_text(json.dumps(rec, indent=2))
    return rec


def install_file(path: Path, log=print) -> dict:
    """Installs a local .deb (no store listing, so no signature to check). Needs root."""
    fields = deb_fields(path)
    _apt_install([path], log)
    return _record(fields["Package"], fields["Version"], None)


def _store_depends(path: Path, index: dict) -> list[str]:
    res = subprocess.run(["dpkg-deb", "-f", str(path), "Depends", "Pre-Depends"], capture_output=True, text=True)
    names = {re.split(r"[\s(:]", p.strip())[0] for p in res.stdout.replace("\n", ",").replace("|", ",").split(",")
             if p.strip() and ":" not in p.split("(")[0]}
    return [n for n in sorted(names) if n in index.get("packages", {}) and _needs(n, index)]


def _needs(name: str, index: dict) -> bool:
    """A store dependency to fetch: not installed, or one of Cherry's own packages with a newer version out."""
    have = _dpkg_version(name)
    if not have:
        return True
    latest = index["packages"][name].get("latest")
    return name in SYSTEM_PACKAGES and bool(latest) and newer(latest, have)


def _companions(name: str, index: dict) -> list[str]:
    """Cherry OS's own packages are released together: updating one brings the others along."""
    if name not in SYSTEM_PACKAGES:
        return []
    return [n for n in SYSTEM_PACKAGES if n != name and n in index.get("packages", {}) and _dpkg_version(n)
            and _needs(n, index)]


def install(names: list[str], confirm=None, force: bool = False, log=print) -> list[dict]:
    """Downloads, checks and installs store apps, and any store apps they depend on."""
    index = load_index(refresh=True)
    todo, seen, ready = list(names), set(), []
    try:
        while todo:
            name = todo.pop(0)
            if name in seen:
                continue
            seen.add(name)
            pkg = listing(name, index)
            check_trust(pkg, force)
            warns = warnings_for(pkg)
            if warns and confirm and not confirm(name, warns):
                log(f"Skipped {name}")
                continue
            path = fetch(pkg, log=log)
            ready.append((pkg, path))
            todo += _store_depends(path, index) + _companions(name, index)
        if not ready:
            return []
        log(f"Installing {', '.join(p['name'] for p, _ in ready)}")
        _apt_install([path for _, path in reversed(ready)], log)
        done = []
        for pkg, _ in ready:
            remember_trust(pkg)
            done.append(_record(pkg["name"], pkg["latest"], pkg))
            log(f"Installed {pkg['name']} {pkg['latest']} (by {pkg['publisher']})")
        return done
    finally:
        for _, path in ready:
            shutil.rmtree(path.parent, ignore_errors=True)


def uninstall(name: str, log=print) -> None:
    if name in SYSTEM_PACKAGES:
        raise PkgError(f"{display_name(name)} ({name}) is part of your system, so the Cherry Store won't remove it")
    if name not in installed():
        raise PkgError(f"{name} wasn't installed from the Cherry Store")
    env = dict(os.environ, DEBIAN_FRONTEND="noninteractive")
    if subprocess.run(["apt-get", "remove", "-y", name], env=env).returncode:
        raise PkgError(f"apt couldn't remove {name}")
    (DB_DIR / f"{name}.json").unlink(missing_ok=True)
    log(f"Removed {name} (your own files and Windows app data in your home folder are kept)")


def updates(index: dict | None = None, only: list[str] | None = None) -> list[tuple[str, str, str]]:
    index = index or load_index(refresh=True)
    out = []
    for name, rec in installed().items():
        if only and name not in only:
            continue
        pkg = index.get("packages", {}).get(name)
        if name in SYSTEM_PACKAGES and pkg and pkg.get("public_key") not in OFFICIAL_KEYS:
            continue
        if pkg and not pkg.get("taken_down") and pkg.get("latest") and newer(pkg["latest"], rec["version"]):
            out.append((name, rec["version"], pkg["latest"]))
    return out


def ensure_root(argv: list[str]) -> None:
    """Re-runs the command with sudo (terminal) or pkexec (no terminal) when root is needed."""
    if os.geteuid() == 0 or ROOT != Path("/"):
        return
    env = [f"{k}={os.environ[k]}" for k in ("CHERRY_CLOUD_URL", "CHERRY_STORE_INDEX_URL") if os.environ.get(k)]
    prefix = ["env", *env] if env else []
    # Running /usr/bin/cherry itself (not python3) is what lets polkit show "Install or remove Cherry Store apps".
    cmd = [os.path.realpath(argv[0]), *argv[1:]] if os.access(argv[0], os.X_OK) else [sys.executable, *argv]
    tool = "sudo" if sys.stdin.isatty() else "pkexec"
    os.execvp(tool, [tool, *prefix, *cmd])


# ---------------------------------------------------------------- publishing

def _gh(*args: str, input: str | None = None) -> str:
    if not shutil.which("gh"):
        raise PkgError("publishing with GitHub needs the gh command (sudo apt install gh)")
    res = subprocess.run(["gh", *args], capture_output=True, text=True, input=input)
    if res.returncode:
        raise PkgError(f"gh {args[0]} failed: {res.stderr.strip() or res.stdout.strip()}")
    return res.stdout.strip()


def prepare(path: Path) -> tuple[Path, dict]:
    """Builds the folder if needed and returns the .deb and the signed request the store checks."""
    if path.is_dir():
        path = build(path)
    if path.suffix != ".deb":
        raise PkgError(f"{path} isn't a .deb or a folder with cherry.toml")
    ident = ensure_identity()
    fields, manifest = deb_fields(path), deb_manifest(path)
    name, version = fields["Package"], fields["Version"]
    sha = hashlib.sha256(path.read_bytes()).hexdigest()
    request = {"name": name, "version": version, "sha256": sha, "public_key": ident.public_key_b64,
               "signature": ident.sign(signing_message(name, version, sha)),
               "kind": manifest.get("kind", "native"), "license": manifest.get("license", ""),
               "homepage": manifest.get("homepage", fields.get("Homepage", ""))}
    return path, request


def _wait_for_issue(repo: str, number: str, log, timeout: float) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        time.sleep(15)
        issue = json.loads(_gh("issue", "view", number, "-R", repo, "--json", "state,stateReason,comments"))
        if issue["state"] == "CLOSED":
            reply = issue["comments"][-1]["body"] if issue["comments"] else ""
            return {"ok": issue.get("stateReason") == "COMPLETED", "message": reply}
        log("Waiting for the store to check and scan it…")
    return {"ok": None, "message": "The store is still working on it; you'll see the result on the issue."}


def publish_github(path: Path, log=print, wait: float = 900) -> dict:
    deb, request = prepare(path)
    repo = store_setting("REPO", STORE_REPO)
    user = _gh("api", "user", "-q", ".login")
    mine = f"{user}/cherry-packages"
    if subprocess.run(["gh", "repo", "view", mine], capture_output=True).returncode:
        log(f"Creating {mine} on GitHub for your packages")
        _gh("repo", "create", mine, "--public", "--add-readme", "--description", "My apps for the Cherry Store")
    tag = tag_for(request["name"], request["version"])
    log(f"Uploading {deb.name} to {mine}")
    if subprocess.run(["gh", "release", "view", tag, "-R", mine], capture_output=True).returncode == 0:
        _gh("release", "upload", tag, str(deb), "-R", mine, "--clobber")
    else:
        _gh("release", "create", tag, str(deb), "-R", mine, "--title", f"{request['name']} {request['version']}",
            "--notes", "Published to the Cherry Store.")
    assets = json.loads(_gh("release", "view", tag, "-R", mine, "--json", "assets"))["assets"]
    request["url"] = next(a["url"] for a in assets if a["name"] == deb.name)
    body = (f"Published with `cherry publish --github`.\n\n```json\n{json.dumps(request, indent=1)}\n```\n")
    url = _gh("issue", "create", "-R", repo, "--title", f"Publish: {request['name']} {request['version']}",
              "--body-file", "-", input=body)
    log(f"Sent to the store: {url}")
    result = _wait_for_issue(repo, url.rstrip("/").rsplit("/", 1)[-1], log, wait) if wait else {"ok": None, "message": ""}
    return {**request, **result, "issue": url}


def _wait_for_cloud(cloud: Cloud, upload: str, log, timeout: float) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        time.sleep(10)
        status = cloud.get(f"/api/uploads/{upload}")
        if status["state"] in ("done", "failed"):
            return {"ok": status["state"] == "done", "message": status.get("message", "")}
        log("Waiting for the store to check and scan it…")
    return {"ok": None, "message": "The store is still working on it; run 'cherry info NAME' later."}


def publish_cherry(path: Path, cloud: Cloud | None = None, log=print, wait: float = 900) -> dict:
    cloud = cloud or Cloud()
    ident = load_identity()
    if not ident.username:
        raise PkgError("you don't have a Cherry account yet — run 'cherry signup NAME' (or use --github)")
    deb, request = prepare(path)
    data = deb.read_bytes()
    chunks = max(1, -(-len(data) // CHUNK))
    upload = cloud.post("/api/uploads", {"request": request, "size": len(data), "chunks": chunks},
                        signer=ident.signer)["upload"]
    for i in range(chunks):
        log(f"Uploading {request['name']} {request['version']}: part {i + 1} of {chunks}")
        cloud.request("PUT", f"/api/uploads/{upload}/chunks/{i}", data=data[i * CHUNK:(i + 1) * CHUNK],
                      signer=ident.signer)
    cloud.post(f"/api/uploads/{upload}/finish", {}, signer=ident.signer)
    log("Sent to the store")
    result = _wait_for_cloud(cloud, upload, log, wait) if wait else {"ok": None, "message": ""}
    return {**request, **result}


def yank(name: str, version: str, route: str, cloud: Cloud | None = None) -> str:
    ident = load_identity()
    request = {"name": name, "version": version, "signature": ident.sign(yank_message(name, version))}
    if route == "github":
        body = f"Withdrawn with `cherry yank`.\n\n```json\n{json.dumps(request, indent=1)}\n```\n"
        return _gh("issue", "create", "-R", store_setting("REPO", STORE_REPO), "--title",
                   f"Withdraw: {name} {version}", "--body-file", "-", input=body)
    return (cloud or Cloud()).post("/api/yank", {"request": request}, signer=ident.signer)["message"]


def report_url(name: str) -> str:
    from urllib.parse import quote
    return (f"https://github.com/{store_setting('REPO', STORE_REPO)}/issues/new?template=report.yml"
            f"&title={quote('Report: ' + name)}&package={quote(name)}")


def report(name: str, reason: str, details: str, cloud: Cloud | None = None) -> str:
    """Files a report as an issue on the store's repository (through Cherry Cloud, so no GitHub account is needed)."""
    return (cloud or Cloud()).post("/api/reports", {"name": name, "reason": reason, "details": details})["message"]
