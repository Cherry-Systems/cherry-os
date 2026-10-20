"""Cherry Move through rclone to a pretend NAS (rclone serving a local folder over WebDAV or SFTP).
Needs rclone; run inside trixie with tests/in-trixie.sh.
"""
import os
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "desktop/lib"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from cherryos import remote, transfer  # noqa: E402
from test_transfer import cats_for, make_home, same_tree  # noqa: E402

pytestmark = pytest.mark.skipif(not shutil.which("rclone"), reason="needs rclone")


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def nas(tmp_path, monkeypatch, request):
    """A pretend NAS: rclone serving tmp_path/nas over request.param (webdav or sftp) as nasuser/naspass."""
    monkeypatch.setattr(remote, "CONFIG", tmp_path / "rclone.conf")
    folder = tmp_path / "nas"
    folder.mkdir()
    port = free_port()
    proc = subprocess.Popen(["rclone", "serve", request.param, str(folder), "--addr", f"127.0.0.1:{port}",
                             "--user", "nasuser", "--pass", "naspass", "--config", "/dev/null"],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(100):
        try:
            socket.create_connection(("127.0.0.1", port), timeout=1).close()
            break
        except OSError:
            time.sleep(0.1)
    settings = {"type": "webdav", "url": f"http://127.0.0.1:{port}/", "user": "nasuser"} \
        if request.param == "webdav" else {"type": "sftp", "host": "127.0.0.1", "port": port, "user": "nasuser"}
    yield folder, settings
    proc.terminate()
    proc.wait()


@pytest.mark.parametrize("nas", ["webdav", "sftp"], indirect=True)
def test_move_through_nas(tmp_path, monkeypatch, nas):
    folder, settings = nas
    home = make_home(tmp_path, big=12 * 1024 * 1024)
    cats = cats_for(home, monkeypatch)

    wrong = remote.Remote("nas", settings)
    with pytest.raises(transfer.TransferError):
        wrong.sign_in("not the password")
    assert not wrong.signed_in()

    old = remote.Remote("nas", settings)
    old.sign_in("naspass")
    assert old.signed_in() and "naspass" not in remote.CONFIG.read_text()
    assert oct(remote.CONFIG.stat().st_mode & 0o777) == "0o600"
    stick = tmp_path / "usb"
    stick.mkdir()
    with old:
        result = transfer.send(cats, "remote", drive=stick, remote=old)
    old.forget()
    assert not old.signed_in()
    assert any((folder / "CherryMove").rglob("*.bin"))

    marker = transfer.read_marker(stick)
    assert marker["storage"] == "remote" and "pass" not in str(marker["remote"])
    new_side = remote.Remote.from_descriptor(marker["remote"])
    assert new_side.descriptor() == marker["remote"]
    new_side.sign_in("naspass")
    new = tmp_path / "new"
    new.mkdir()
    with new_side:
        transfer.restore(result.code, drive=stick, home=new, remote=new_side)
        same_tree(home, new)
        keys, source = transfer.source_for(result.code, remote=new_side)
        source.delete()
    assert not list((folder / "CherryMove").iterdir())
    new_side.forget()


def test_reason_messages():
    assert "password" in remote._reason("2026/10/04 10:00:00 ERROR : 401 Unauthorized")
    assert "reach" in remote._reason("dial tcp: lookup nas.local: no such host")
    assert "password" in remote._reason("NewFs: couldn't connect SSH: ssh: handshake failed: ssh: unable to "
                                        "authenticate, attempted methods [none password], no supported methods remain")
    assert "SFTP switched off" in remote._reason("NewFs: couldn't initialise SFTP: ssh: subsystem request failed")
    assert "guests" in remote._reason("couldn't connect SMB: response error: The referenced account is currently "
                                      "disabled and may not be logged on to.")


def test_short_nas_names_get_local_added(monkeypatch):
    def lookup(name, _port):
        if name != "mynas.local":
            raise socket.gaierror("no such host")
    monkeypatch.setattr(remote.socket, "getaddrinfo", lookup)
    assert remote._resolvable("mynas") == "mynas.local"
    assert remote._resolvable("othernas") == "othernas"
    assert remote._resolvable("192.168.1.20") == "192.168.1.20"


@pytest.mark.parametrize("typed, want", [
    ({"type": "smb", "host": "nas.local", "path": "/Backups/"}, {"host": "nas.local", "path": "Backups"}),
    ({"type": "smb", "host": r"\\NAS\Backups\Move"}, {"host": "NAS", "path": "Backups/Move"}),
    ({"type": "smb", "host": "smb://nas.local/Backups"}, {"host": "nas.local", "path": "Backups"}),
    ({"type": "smb", "host": "192.168.1.20:4445", "path": "Backups"}, {"host": "192.168.1.20", "port": "4445"}),
    ({"type": "smb", "host": "mynas:4445", "path": "Backups"}, {"host": "mynas", "port": "4445"}),
    ({"type": "sftp", "host": "nas.local:2222", "path": "/volume1/Backups/"},
     {"host": "nas.local", "port": "2222", "path": "/volume1/Backups"}),
    ({"type": "sftp", "host": "sftp://alex@nas.local:22/srv/backups"},
     {"host": "nas.local", "port": "22", "user": "alex", "path": "/srv/backups"}),
    ({"type": "sftp", "host": "nas.local", "path": "Backups", "port": 2222}, {"path": "Backups", "port": "2222"}),
    ({"type": "sftp", "host": "[fe80::1]:2222"}, {"host": "[fe80::1]", "port": "2222"}),
    ({"type": "webdav", "url": "https://nas.local:5006/"}, {"url": "https://nas.local:5006/"}),
])
def test_nas_addresses_typed_any_way(typed, want):
    got = remote.Remote("nas", typed).settings
    assert {k: got.get(k) for k in want} == want


def test_folders_inside_the_nas():
    assert remote.Remote("nas", {"type": "sftp", "host": "h"}).at("CherryMove") == "cherry-nas:CherryMove"
    assert remote.Remote("nas", {"type": "sftp", "host": "h", "path": "/volume1/B"}).at("CherryMove/x") == \
        "cherry-nas:/volume1/B/CherryMove/x"
    assert remote.Remote("nas", {"type": "sftp", "host": "h", "path": "Backups"}).at("/CherryMove/") == \
        "cherry-nas:Backups/CherryMove"
    assert remote.Remote("nas", {"type": "smb", "host": "h", "path": "/Backups"}).at("CherryMove") == \
        "cherry-nas:Backups/CherryMove"


def self_signed(tmp_path) -> tuple[Path, Path]:
    import datetime as dt

    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "nas.local")])
    now = dt.datetime.now(dt.timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
            .serial_number(x509.random_serial_number()).not_valid_before(now - dt.timedelta(days=1))
            .not_valid_after(now + dt.timedelta(days=30))
            .add_extension(x509.SubjectAlternativeName([x509.DNSName("localhost")]), critical=False)
            .sign(key, hashes.SHA256()))
    crt, pem = tmp_path / "nas.crt", tmp_path / "nas.key"
    crt.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    pem.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                      serialization.NoEncryption()))
    return crt, pem


def test_nas_with_its_own_certificate(tmp_path, monkeypatch):
    """Synology and QNAP serve WebDAV over https with a certificate they made themselves."""
    monkeypatch.setattr(remote, "CONFIG", tmp_path / "rclone.conf")
    crt, pem = self_signed(tmp_path)
    folder = tmp_path / "nas"
    folder.mkdir()
    port = free_port()
    proc = subprocess.Popen(["rclone", "serve", "webdav", str(folder), "--addr", f"127.0.0.1:{port}", "--cert",
                             str(crt), "--key", str(pem), "--user", "u", "--pass", "p", "--config", "/dev/null"],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(100):
            try:
                socket.create_connection(("127.0.0.1", port), timeout=1).close()
                break
            except OSError:
                time.sleep(0.1)
        settings = {"type": "webdav", "url": f"https://localhost:{port}/", "user": "u"}
        with pytest.raises(remote.UntrustedCertificate):
            remote.Remote("nas", settings).sign_in("p")
        home = make_home(tmp_path, big=9 * 1024 * 1024)
        cats = cats_for(home, monkeypatch)
        nas = remote.Remote("nas", {**settings, "insecure": True})
        nas.sign_in("p")
        with nas:
            result = transfer.send(cats, "remote", remote=nas)
            assert [m.id for m in transfer.list_remote_moves(nas)] == [result.keys.id]
            new = tmp_path / "new"
            new.mkdir()
            transfer.restore(result.code, home=new, remote=nas)
        same_tree(home, new)
        nas.forget()
    finally:
        proc.terminate()
        proc.wait()


# Stands in for `rclone serve webdav`: complains a lot (as rclone does each time a NAS doesn't answer), and closes
# each connection after answering without saying so (as a server does with one left idle too long).
CHATTY_RCLONE = """#!{python}
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

host, port = sys.argv[sys.argv.index("--addr") + 1].rsplit(":", 1)
sys.stderr.write("ERROR : /: Dir.Stat error: couldn't list files: connection refused\\n" * 20000)
sys.stderr.flush()


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_GET(self):
        body = self.path.encode()
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
        self.close_connection = True

    def log_message(self, *args):
        pass


ThreadingHTTPServer((host, int(port)), Handler).serve_forever()
"""


def test_nas_keeps_answering_through_rclone_complaints_and_dropped_connections(tmp_path, monkeypatch):
    fake = tmp_path / "bin/rclone"
    fake.parent.mkdir()
    fake.write_text(CHATTY_RCLONE.format(python=sys.executable))
    fake.chmod(0o755)
    monkeypatch.setenv("PATH", f"{fake.parent}:{os.environ['PATH']}")
    monkeypatch.setattr(remote, "CONFIG", tmp_path / "rclone.conf")
    with remote.Remote("nas", {"type": "webdav", "url": "http://nas.local/", "user": "u"}) as nas:
        for i in range(3):
            assert nas.get(f"CherryMove/x/{i}.bin") == f"/CherryMove/x/{i}.bin".encode()
