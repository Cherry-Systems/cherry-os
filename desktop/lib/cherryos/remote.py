"""Your own storage for Cherry Move: Google Drive, OneDrive, MEGA, or a NAS at home (SMB, SFTP or WebDAV).

rclone does the signing in and the talking to each service. Sign-ins live in ~/.config/cherry/rclone.conf
(readable only by you) and are forgotten once a move is done. While a move runs, `rclone serve webdav`
listens on 127.0.0.1 with a random password, and chunks go to it over plain HTTP, several at once.

Google Drive uses the "app folder" permission: Cherry Move can only see the files it made itself, hidden from
the normal Drive view (they still count towards your storage).
"""
from __future__ import annotations

import base64
import collections
import http.client
import json
import os
import re
import secrets
import shutil
import signal
import socket
import subprocess
import threading
import time
import urllib.parse
from dataclasses import dataclass
from pathlib import Path

from .transfer import REMOTE_ROOT, TransferError

CONFIG = Path.home() / ".config/cherry/rclone.conf"
OAUTH_FILE = Path("/etc/cherry/oauth.json")  # optional {"gdrive": {"client_id": …, "client_secret": …}, …}
# For checks someone is waiting on (signing in, free space, listing): rclone otherwise tries a wrong password
# 10 times with growing pauses (11 s), and a NAS that doesn't answer for a minute at a time each try.
QUICK = ("--low-level-retries", "1", "--retries", "1", "--contimeout", "15s", "--timeout", "60s")


@dataclass(frozen=True)
class Kind:
    id: str
    title: str
    backend: str
    free: str
    signs_in_with_browser: bool
    note: str = ""


KINDS = {
    "gdrive": Kind("gdrive", "Google Drive", "drive", "15 GB free", True,
                   "Free accounts have 15 GB, shared with Gmail and Photos."),
    "onedrive": Kind("onedrive", "OneDrive", "onedrive", "5 GB free", True,
                     "Free accounts have 5 GB."),
    "mega": Kind("mega", "MEGA", "mega", "20 GB free", False,
                 "Free accounts have 20 GB, but MEGA limits how much you can download each day, so getting a "
                 "big move back can pause for a few hours. Accounts with two-step sign-in don't work yet."),
    "nas": Kind("nas", "A NAS or another computer at home", "", "", False,
                "Any network drive: a Synology/QNAP NAS, a Windows shared folder, or a computer running SSH."),
}
NAS_TYPES = {
    "smb": "Windows / Samba shared folder (SMB)",
    "sftp": "SSH / SFTP",
    "webdav": "WebDAV",
}


_version: tuple | None = None


def rclone_version() -> tuple:
    global _version
    if _version is None:
        try:
            out = subprocess.run(["rclone", "version"], capture_output=True, text=True, timeout=20).stdout
            m = re.search(r"v(\d+)\.(\d+)", out)
            _version = (int(m.group(1)), int(m.group(2))) if m else (0, 0)
        except (OSError, subprocess.SubprocessError):
            _version = (0, 0)
    return _version


def available() -> bool:
    return shutil.which("rclone") is not None and rclone_version() >= (1, 60)


class UntrustedCertificate(TransferError):
    """A NAS on https with a certificate it made itself (Synology and QNAP do, out of the box)."""

    def __init__(self):
        super().__init__("This NAS uses a security certificate it made itself, so Cherry Move can't check it's "
                         "really your NAS. That's normal for a NAS at home. Connect anyway if it's yours.")


def _rclone(*args: str, timeout: float = 120, check: bool = True) -> subprocess.CompletedProcess:
    if not available():
        if shutil.which("rclone"):
            have = ".".join(map(str, rclone_version()))
            raise TransferError(f"Cherry Move needs rclone 1.60 or newer, and this computer has {have}. "
                                "Get a newer one from https://rclone.org/downloads/")
        raise TransferError("rclone isn't installed. Install it (sudo apt install rclone) and try again.")
    CONFIG.parent.mkdir(parents=True, exist_ok=True)
    try:
        res = subprocess.run(["rclone", "--config", str(CONFIG), *args], capture_output=True, text=True,
                             timeout=timeout, stdin=subprocess.DEVNULL)
    except subprocess.TimeoutExpired:
        raise TransferError("The storage took too long to answer. Check the connection and try again.") from None
    _private()
    if check and res.returncode != 0:
        if _bad_certificate(res.stderr):
            raise UntrustedCertificate()
        raise TransferError(_reason(res.stderr))
    return res


def _bad_certificate(stderr: str) -> bool:
    lower = stderr.lower()
    return "x509:" in lower or "tls: failed to verify certificate" in lower


def _private() -> None:
    try:
        CONFIG.chmod(0o600)
    except OSError:
        pass


def _reason(stderr: str) -> str:
    lines = [ln for ln in stderr.strip().splitlines() if ln.strip()]
    last = lines[-1] if lines else "unknown error"
    last = re.sub(r"^\d{4}/\d\d/\d\d \d\d:\d\d:\d\d (ERROR|CRITICAL|NOTICE)\s*:\s*", "", last)
    lower = last.lower()
    if "subsystem request failed" in lower:
        return ("The NAS lets you in over SSH but has SFTP switched off. Connect with \"Windows / Samba shared "
                "folder (SMB)\" instead, or switch SFTP on (on a Synology: Control Panel › File Services › FTP › "
                "Enable SFTP service).")
    if "account is currently disabled" in lower:
        return "This NAS doesn't let guests in. Type your username and password for the NAS."
    if "login" in lower and "fail" in lower or "invalid" in lower and ("password" in lower or "credentials" in lower) \
            or "logon failure" in lower or "authentication" in lower or "unauthorized" in lower or "401" in lower \
            or "unable to authenticate" in lower:
        return "The username or password wasn't accepted."
    if "no such host" in lower or "connection refused" in lower or "i/o timeout" in lower or "no route" in lower:
        return "Couldn't reach it. Check the address, and that it's switched on and on the same network."
    return last


def _resolvable(host: str) -> str:
    """The address as typed, or with .local added when only that can be found ("nas" → "nas.local"), the
    way the file manager finds it."""
    if "." in host or ":" in host or host.startswith("["):
        return host
    for name in (host, f"{host}.local"):
        try:
            socket.getaddrinfo(name, None)
            return name
        except OSError:
            continue
    return host


def _oauth_client(kind: str) -> list[str]:
    try:
        client = json.loads(OAUTH_FILE.read_text()).get(kind) or {}
    except (OSError, ValueError):
        client = {}
    client_id = os.environ.get(f"CHERRY_{kind.upper()}_CLIENT_ID") or client.get("client_id")
    client_secret = os.environ.get(f"CHERRY_{kind.upper()}_CLIENT_SECRET") or client.get("client_secret")
    return ["client_id", client_id, "client_secret", client_secret or ""] if client_id else []


def split_address(settings: dict) -> dict:
    """Takes a NAS address the way people copy it (nas.local:2222, smb://nas/Backups/Move, \\\\NAS\\Backups,
    sftp://me@nas/volume1/Backups) apart into host, port, folder and username. An SFTP folder starting with /
    stays absolute; anything else is relative (to the share for SMB, to your home folder for SFTP)."""
    s = {k: v for k, v in settings.items() if v not in (None, "")}
    kind = s.get("type")
    if kind not in ("smb", "sftp"):
        return s
    address = str(s.get("host", "")).strip().replace("\\", "/")
    m = re.match(r"^(?:[a-z]+:(?=//))?/*(?:([^@/]+)@)?([^/]*)(/.*)?$", address, re.I)
    if m:
        user, host, path = m.groups()
        if user and not s.get("user"):
            s["user"] = user
        port = re.match(r"^([^:\[\]]+|\[[^\]]+\]):(\d+)$", host)
        if port:
            host = port.group(1)
            s.setdefault("port", port.group(2))
        s["host"] = host
        if path and path.strip("/") and not s.get("path"):
            s["path"] = path
    folder = str(s.get("path", "")).strip().replace("\\", "/")
    absolute = kind == "sftp" and folder.startswith("/")
    folder = folder.strip("/")
    s["path"] = "/" + folder if absolute else folder
    if not s["path"]:
        del s["path"]
    if "port" in s:
        s["port"] = str(s["port"])
    return s


class Remote:
    """One place to keep a move: a signed-in cloud drive or a NAS folder."""

    def __init__(self, kind: str, settings: dict | None = None):
        if kind not in KINDS:
            raise TransferError(f"Unknown kind of storage: {kind}")
        self.kind = KINDS[kind]
        self.settings = split_address({k: v for k, v in (settings or {}).items() if k not in ("kind", "title")})
        self.name = f"cherry-{kind}"
        self._server: subprocess.Popen | None = None
        self._port = 0
        self._auth = ""
        self._local = threading.local()
        self._lock = threading.Lock()

    # ---------------------------------------------------------------- describing

    @property
    def title(self) -> str:
        if self.kind.id == "nas":
            return f"your NAS ({self.settings.get('host') or self.settings.get('url', '')})"
        return self.kind.title

    def descriptor(self) -> dict:
        """What's saved on the stick and in the pending file: enough to find the files again, never a secret."""
        return {"kind": self.kind.id, "title": self.title, **self.settings}

    @classmethod
    def from_descriptor(cls, d: dict) -> "Remote":
        return cls(d.get("kind", ""), d)

    @property
    def root(self) -> str:
        return f"{self.name}:{self.settings.get('path', '')}"

    def at(self, rel: str) -> str:
        """rel inside the chosen folder. "name:/x" would be the top of the whole disk over SFTP."""
        rel = rel.strip("/")
        return f"{self.root}{rel}" if self.root.endswith(":") else f"{self.root}/{rel}"

    def _rc(self, *args: str, **kw) -> subprocess.CompletedProcess:
        """rclone, trusting the NAS's own certificate if the person said to."""
        return _rclone(*(["--no-check-certificate"] if self.settings.get("insecure") else []), *args, **kw)

    # ---------------------------------------------------------------- signing in

    def sign_in(self, password: str = "", on_url=None, cancel: threading.Event | None = None) -> None:
        """Signs in (opening the browser for Google and Microsoft), then checks the storage can be written to.
        `on_url(url)` gets the sign-in link in case the browser doesn't open by itself."""
        _rclone("config", "delete", self.name, check=False)
        k, s = self.kind.id, self.settings
        if s.get("host"):
            s["host"] = _resolvable(s["host"])
        create = ("config", "create", "--obscure", self.name)
        port = ["port", str(s["port"])] if s.get("port") else []
        if k == "gdrive":
            self._browser_sign_in(["drive", "scope", "drive.appfolder", "root_folder_id", "appDataFolder",
                                   *_oauth_client(k)], on_url, cancel)
        elif k == "onedrive":
            self._browser_sign_in(["onedrive", *_oauth_client(k)], on_url, cancel)
        elif k == "mega":
            _rclone(*create, "mega", "user", s.get("user", ""), "pass", password)
        elif s.get("type") == "smb":
            _rclone(*create, "smb", "host", s.get("host", ""), "user", s.get("user") or "guest", "pass", password,
                    *port, *(["domain", s["domain"]] if s.get("domain") else []))
        elif s.get("type") == "sftp":
            _rclone(*create, "sftp", "host", s.get("host", ""), "user", s.get("user", ""), "pass", password, *port)
        elif s.get("type") == "webdav":
            _rclone(*create, "webdav", "url", s.get("url", ""), "vendor", "other", "user", s.get("user", ""),
                    "pass", password)
        else:
            raise TransferError("Choose how to connect to the NAS.")
        try:
            self._rc(*QUICK, "mkdir", self.at(REMOTE_ROOT), timeout=90)
        except TransferError as err:
            if s.get("type") == "smb" and "share name cannot be found" in str(err).lower():
                err = TransferError(self._no_such_share())
            self.forget()
            raise err from None

    def _no_such_share(self) -> str:
        share = self.settings.get("path", "").split("/")[0]
        res = self._rc(*QUICK, "lsf", "--dirs-only", f"{self.name}:", timeout=60, check=False)
        shares = sorted({ln.strip().rstrip("/") for ln in res.stdout.splitlines() if ln.strip()}
                        - {"IPC$", "print$"}, key=str.lower)
        msg = f"There's no shared folder called \"{share}\" on the NAS."
        return f"{msg} Its shared folders are: {', '.join(shares)}." if shares else msg

    def _browser_sign_in(self, args: list[str], on_url, cancel) -> None:
        _rclone("version")
        proc = subprocess.Popen(["rclone", "--config", str(CONFIG), "config", "create", self.name, *args],
                                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                text=True)
        output = []

        def read():
            for line in proc.stdout:
                output.append(line)
                m = re.search(r"(http://127\.0\.0\.1:\d+/auth\?state=\S+)", line)
                if m and on_url:
                    on_url(m.group(1))
        reader = threading.Thread(target=read, daemon=True)
        reader.start()
        deadline = time.time() + 600
        while proc.poll() is None:
            if (cancel is not None and cancel.is_set()) or time.time() > deadline:
                proc.terminate()
                proc.wait()
                raise InterruptedError("cancelled")
            time.sleep(0.2)
        reader.join(2)
        _private()
        if proc.returncode != 0:
            raise TransferError(_reason("".join(output)))

    def signed_in(self) -> bool:
        if not CONFIG.exists():
            return False
        return f"[{self.name}]" in CONFIG.read_text()

    def forget(self) -> None:
        """Signs out: removes the saved sign-in from this computer (the files stay where they are)."""
        self.close()
        if CONFIG.exists():
            _rclone("config", "delete", self.name, check=False)

    def free_space(self) -> int | None:
        """Bytes free, or None if the storage doesn't say (some NAS types)."""
        res = self._rc(*QUICK, "about", f"{self.name}:", "--json", timeout=60, check=False)
        try:
            info = json.loads(res.stdout)
        except ValueError:
            return None
        if "free" in info:
            return int(info["free"])
        if "total" in info and "used" in info:
            return max(0, int(info["total"]) - int(info["used"]) - int(info.get("trashed", 0)))
        return None

    # ---------------------------------------------------------------- moving chunks

    def _start(self) -> None:
        with self._lock:
            if self._server and self._server.poll() is None:
                return
            user, password = "cherry", secrets.token_urlsafe(24)
            with socket.socket() as s:
                s.bind(("127.0.0.1", 0))
                port = s.getsockname()[1]
            env = {**os.environ, "RCLONE_USER": user, "RCLONE_PASS": password}
            self._server = subprocess.Popen(
                ["rclone", "--config", str(CONFIG), "serve", "webdav", self.root, "--addr", f"127.0.0.1:{port}",
                 "--dir-cache-time", "5s", "--low-level-retries", "10", "--retries", "5",
                 *(["--no-check-certificate"] if self.settings.get("insecure") else [])],
                env=env, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                preexec_fn=_die_with_parent)
            # rclone writes a line for every failed try (a NAS asleep, Wi-Fi dropping out); unread, the pipe
            # fills after a few hundred and rclone stops answering for good.
            said: collections.deque = collections.deque(maxlen=40)
            reader = threading.Thread(target=_keep_reading, args=(self._server.stderr, said), daemon=True)
            reader.start()
            self._port = port
            self._auth = "Basic " + base64.b64encode(f"{user}:{password}".encode()).decode()
            deadline = time.time() + 30
            while time.time() < deadline:
                if self._server.poll() is not None:
                    reader.join(5)
                    raise TransferError(_reason("".join(said)))
                try:
                    socket.create_connection(("127.0.0.1", port), timeout=1).close()
                    return
                except OSError:
                    time.sleep(0.2)
            raise TransferError("rclone didn't start in time.")

    def _request(self, method: str, path: str, body: bytes | None = None) -> tuple[int, bytes]:
        while True:
            self._start()
            conn = getattr(self._local, "conn", None)
            reused = conn is not None and getattr(self._local, "port", None) == self._port
            if not reused:
                conn = http.client.HTTPConnection("127.0.0.1", self._port, timeout=900)
                self._local.conn, self._local.port = conn, self._port
            try:
                conn.request(method, "/" + urllib.parse.quote(path.strip("/")), body=body,
                             headers={"Authorization": self._auth})
                res = conn.getresponse()
                return res.status, res.read()
            except (http.client.RemoteDisconnected, BrokenPipeError, ConnectionResetError):
                conn.close()
                self._local.conn = None
                if not reused:
                    raise
                # A connection kept open from before had been closed at the other end: try a new one.
            except OSError:
                conn.close()
                self._local.conn = None
                raise
            except http.client.HTTPException as e:
                conn.close()
                self._local.conn = None
                raise TransferError(f"The connection to {self.title} broke off ({e!r}).") from e

    def mkdirs(self, path: str) -> None:
        made = ""
        for part in path.strip("/").split("/"):
            made = f"{made}/{part}" if made else part
            status, body = self._request("MKCOL", made + "/")
            if status not in (200, 201, 405):
                raise TransferError(f"Couldn't make a folder in {self.title} ({status} {body[:200]!r})")

    def put(self, path: str, data: bytes) -> None:
        status, body = self._request("PUT", path, data)
        if status not in (200, 201, 204):
            raise TransferError(f"Couldn't save to {self.title} ({status} {body[:200]!r})")

    def get(self, path: str) -> bytes | None:
        status, body = self._request("GET", path)
        if status == 404:
            return None
        if status != 200:
            raise TransferError(f"Couldn't read from {self.title} ({status})")
        return body

    def list_dirs(self, path: str) -> list[str]:
        """Names of the folders in path (empty if it doesn't exist)."""
        res = self._rc(*QUICK, "lsf", "--dirs-only", self.at(path), timeout=120, check=False)
        if res.returncode != 0:
            if "not found" in res.stderr.lower() or "doesn't exist" in res.stderr.lower():
                return []
            raise TransferError(f"Couldn't look in {self.title}: {_reason(res.stderr)}")
        return [line.rstrip("/") for line in res.stdout.splitlines() if line.strip()]

    def delete(self, path: str) -> None:
        self.close()
        self._rc("purge", self.at(path), timeout=600, check=False)

    def close(self) -> None:
        with self._lock:
            if self._server and self._server.poll() is None:
                self._server.terminate()
                try:
                    self._server.wait(10)
                except subprocess.TimeoutExpired:
                    self._server.kill()
            self._server = None

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        self.close()


def _keep_reading(pipe, lines: collections.deque) -> None:
    for line in pipe:
        lines.append(line.decode(errors="replace"))
    pipe.close()


def _die_with_parent() -> None:
    """The rclone server stops if Cherry Move does (even if it crashes)."""
    try:
        import ctypes
        ctypes.CDLL("libc.so.6").prctl(1, signal.SIGTERM)  # PR_SET_PDEATHSIG
    except (OSError, AttributeError):
        pass


# ---------------------------------------------------------------- finding a NAS

@dataclass
class FoundNas:
    name: str
    type: str
    host: str
    port: int

    @property
    def label(self) -> str:
        return f"{self.name} ({NAS_TYPES[self.type].split(' (')[0]})"


def discover_nas(timeout: float = 5) -> list[FoundNas]:
    """NAS boxes and shared folders announced on the home network (needs avahi-browse)."""
    if not shutil.which("avahi-browse"):
        return []
    services = {"_smb._tcp": "smb", "_sftp-ssh._tcp": "sftp", "_webdav._tcp": "webdav", "_webdavs._tcp": "webdav"}
    found: dict[tuple, FoundNas] = {}
    procs = {}
    for service in services:
        try:
            procs[service] = subprocess.Popen(["avahi-browse", "-tprk", service], stdout=subprocess.PIPE,
                                              stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL, text=True)
        except OSError:
            pass
    deadline = time.monotonic() + timeout
    for service, proc in procs.items():
        try:
            out, _ = proc.communicate(timeout=max(0.1, deadline - time.monotonic()))
        except subprocess.TimeoutExpired:
            proc.kill()
            out, _ = proc.communicate()
        kind = services[service]
        for line in out.splitlines():
            f = line.split(";")
            if len(f) >= 9 and f[0] == "=" and f[2] == "IPv4":
                name = re.sub(r"\\(\d{3})", lambda m: chr(int(m.group(1))), f[3])
                host = f[6].rstrip(".") if f[6].endswith(".local") else f[7]
                found.setdefault((kind, host), FoundNas(name, kind, host, int(f[8])))
    return sorted(found.values(), key=lambda n: (n.name.lower(), n.type))
