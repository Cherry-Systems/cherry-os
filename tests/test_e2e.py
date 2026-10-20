"""End-to-end tests against a running Cherry Cloud (cd cloud && npx wrangler dev --port 8790).

  CHERRY_CLOUD_URL=http://localhost:8790 python3 -m pytest tests/test_e2e.py -v
cloud/.dev.vars must hold STORE_TOKEN=dev-store, GITHUB_TOKEN=dev-github and GITHUB_API=http://localhost:8797:
this file stands in for GitHub on port 8797 and plays the store's workflow (store/tools) itself.
"""
import atexit
import json
import os
import secrets
import shutil
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
TMP = Path(tempfile.mkdtemp(prefix="cherry-e2e-"))
atexit.register(shutil.rmtree, TMP, ignore_errors=True)
os.environ.setdefault("CHERRY_CLOUD_URL", "http://localhost:8790")
os.environ["CHERRY_ROOT"] = str(TMP / "root")
os.environ["STORE_TOKEN"] = "dev-store"
sys.path.insert(0, str(REPO / "desktop/lib"))
sys.path.insert(0, str(REPO / "store/tools"))

from cherryos import pkg, transfer  # noqa: E402
from cherryos.cloud import Cloud, CloudError  # noqa: E402
import pipeline  # noqa: E402
import store  # noqa: E402

CLOUD = Cloud()
RUN = secrets.token_hex(3)
NAME = f"hello-{RUN}"
GITHUB = []  # (path, body) of every call Cherry Cloud makes to "GitHub"


class FakeGitHub(BaseHTTPRequestHandler):
    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["content-length"])))
        assert self.headers["authorization"] == "Bearer dev-github"
        GITHUB.append((self.path, body))
        self.send_response(204 if self.path.endswith("/dispatches") else 201)
        self.end_headers()

    def log_message(self, *_):
        pass


@pytest.fixture(scope="module", autouse=True)
def fake_github():
    server = ThreadingHTTPServer(("127.0.0.1", 8797), FakeGitHub)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield
    server.shutdown()


def identity(user):
    os.environ["CHERRY_IDENTITY"] = str(TMP / f"{user}.json")


def make_project(version="0.1.0"):
    d = TMP / f"proj-{version}"
    (d / "bin").mkdir(parents=True, exist_ok=True)
    (d / "bin" / NAME).write_text("#!/bin/sh\necho hello from cherry\n")
    (d / "bin" / NAME).chmod(0o755)
    (d / "cherry.toml").write_text(f'''name = "{NAME}"
version = "{version}"
summary = "Test app {RUN}"

[[apps]]
name = "Hello"
exec = "bin/{NAME}"

[bin]
{NAME} = "bin/{NAME}"
''')
    return d


def test_signup():
    identity("alice")
    ident = pkg.signup(f"alice{RUN}", cloud=CLOUD)
    assert CLOUD.post("/api/accounts/whoami", {}, signer=ident.signer)["username"] == f"alice{RUN}"
    with pytest.raises(CloudError) as e:
        identity("alice2")
        pkg.signup(f"alice{RUN}", cloud=CLOUD)
    assert e.value.status == 409


def test_publish_with_a_cherry_account_reaches_the_store(monkeypatch):
    identity("alice")
    monkeypatch.setattr(store, "INDEX", TMP / "index.json")
    monkeypatch.setattr(store, "debian_names", lambda: set())
    res = pkg.publish_cherry(make_project("0.1.0"), cloud=CLOUD, wait=0, log=lambda *_: None)
    path, body = GITHUB[-1]
    assert path == "/repos/Cherry-Systems/cherry-store/dispatches" and body["event_type"] == "cherry-upload"
    payload = body["client_payload"]
    assert payload["username"] == f"alice{RUN}" and payload["request"]["sha256"] == res["sha256"]

    # What the store's workflow does: collect the file, check it, report back.
    upload = payload["upload"]
    assert CLOUD.get(f"/api/uploads/{upload}")["state"] == "waiting"
    with pytest.raises(CloudError) as e:
        CLOUD.get(f"/api/handoff/{upload}/chunks/0", raw=True)
    assert e.value.status == 401
    deb = TMP / "collected.deb"
    with deb.open("wb") as out:  # pipeline.download does the same over https
        for i in range(payload["chunks"]):
            out.write(CLOUD.get(f"/api/handoff/{upload}/chunks/{i}", raw=True, headers={"authorization": "Bearer dev-store"}))
    result = store.check(deb, payload["request"], f"cherry:{payload['username']}", no_scan=True)
    assert result["name"] == NAME and result["version"] == "0.1.0"
    pipeline.CLOUD, pipeline.STORE_TOKEN = CLOUD.url, "dev-store"
    pipeline.cloud_done(upload, True, f"Published {NAME} 0.1.0.")
    status = CLOUD.get(f"/api/uploads/{upload}")
    assert status["state"] == "done" and status["message"] == f"Published {NAME} 0.1.0."
    with pytest.raises(CloudError):
        CLOUD.get(f"/api/handoff/{upload}/chunks/0", raw=True, headers={"authorization": "Bearer dev-store"})


def test_upload_must_be_signed_by_the_account(monkeypatch):
    identity("alice")
    deb, request = pkg.prepare(make_project("0.2.0"))
    ident = pkg.load_identity()
    bad = {**request, "signature": request["signature"][::-1]}
    with pytest.raises(CloudError, match="signature"):
        CLOUD.post("/api/uploads", {"request": bad, "size": deb.stat().st_size, "chunks": 1}, signer=ident.signer)
    identity("mallory")
    _, other = pkg.prepare(make_project("0.3.0"))
    with pytest.raises(CloudError, match="account's keys"):
        CLOUD.post("/api/uploads", {"request": other, "size": 100, "chunks": 1}, signer=ident.signer)


def test_yank_and_report_go_to_github():
    identity("alice")
    assert "withdraw" in pkg.yank(NAME, "0.1.0", "cherry", cloud=CLOUD)
    path, body = GITHUB[-1]
    assert body["event_type"] == "cherry-yank" and body["client_payload"]["request"]["name"] == NAME
    msg = pkg.report(NAME, "malware", "steals @everyone's passwords", cloud=CLOUD)
    assert "Thanks" in msg
    path, body = GITHUB[-1]
    assert path.endswith("/issues") and body["labels"] == ["report"] and "@\u200beveryone" in body["body"]


def make_home(name):
    home = TMP / name
    docs = home / "Documents" / "School"
    docs.mkdir(parents=True)
    (docs / "essay.txt").write_text("my essay " * 1000)
    (home / "Pictures").mkdir()
    (home / "Pictures" / "cat.bin").write_bytes(os.urandom(9 * 1024 * 1024))  # spans two chunks, incompressible
    (home / ".ssh").mkdir()
    (home / ".ssh" / "id_test").write_text("secret key")
    (home / ".bashrc").write_text("alias ll='ls -l'\n")
    (home / "Projects" / "game").mkdir(parents=True)
    (home / "Projects" / "game" / "main.py").write_text("print('hi')\n")
    return home


def compare(old, new):
    for rel in ["Documents/School/essay.txt", "Pictures/cat.bin", ".ssh/id_test", "Projects/game/main.py"]:
        assert (old / rel).read_bytes() == (new / rel).read_bytes(), rel
    assert (new / ".bashrc.from-old-computer").read_text() == "alias ll='ls -l'\n"


def test_transfer_via_usb(monkeypatch):
    old = make_home("old-home-usb")
    monkeypatch.setenv("HOME", str(old))
    drive = TMP / "usb"
    drive.mkdir()
    result = transfer.send(transfer.categories(old), "usb", drive=drive)
    marker = transfer.read_marker(drive)
    assert marker["code"] == result.code and marker["storage"] == "usb"
    new = TMP / "new-home-usb"
    new.mkdir()
    (new / ".bashrc").write_text("# cherry default\n")
    monkeypatch.setenv("HOME", str(new))
    transfer.restore(marker["code"], drive=drive, home=new)
    compare(old, new)
    assert (new / ".bashrc").read_text() == "# cherry default\n"
