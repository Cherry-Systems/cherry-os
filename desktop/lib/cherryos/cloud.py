"""Talks to Cherry Cloud (cloud/ in the Cherry OS source): the package store."""
from __future__ import annotations

import base64
import hashlib
import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path

DEFAULT_URL = "https://cherry-cloud.isaiahcannings.workers.dev"
USER_AGENT = "CherryOS/1.0"


def cloud_url() -> str:
    if os.environ.get("CHERRY_CLOUD_URL"):
        return os.environ["CHERRY_CLOUD_URL"].rstrip("/")
    for conf in (Path.home() / ".config/cherry/cloud.conf", Path("/etc/cherry/cloud.conf")):
        try:
            for line in conf.read_text().splitlines():
                if line.startswith("CLOUD_URL="):
                    return line.split("=", 1)[1].strip().rstrip("/")
        except OSError:
            continue
    return DEFAULT_URL


class CloudError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


class Signer:
    """Signs store requests with the publisher's Ed25519 key (see cloud/src/auth.ts)."""

    def __init__(self, private_key, key_id: str):
        self.private_key = private_key
        self.key_id = key_id

    def header(self, method: str, path: str, body: bytes | None) -> str:
        ts = int(time.time())
        body_hash = "UNSIGNED-PAYLOAD" if body is None else hashlib.sha256(body).hexdigest()
        sig = self.private_key.sign(f"{method}\n{path}\n{ts}\n{body_hash}".encode())
        return f"Cherry key={self.key_id},ts={ts},sig={base64.b64encode(sig).decode()}"


class Cloud:
    def __init__(self, url: str | None = None, timeout: float = 120):
        self.url = (url or cloud_url()).rstrip("/")
        self.timeout = timeout

    def request(self, method: str, path: str, *, json_body=None, data: bytes | None = None,
                headers: dict | None = None, signer: Signer | None = None, raw: bool = False):
        hdrs = {"user-agent": USER_AGENT, **(headers or {})}
        body = None
        if json_body is not None:
            body = json.dumps(json_body).encode()
            hdrs["content-type"] = "application/json"
        elif data is not None:
            body = data
            hdrs["content-type"] = "application/octet-stream"
        if signer:
            signed_body = body if json_body is not None else (None if data is not None else b"")
            hdrs["authorization"] = signer.header(method, path.split("?")[0], signed_body)
        req = urllib.request.Request(self.url + path, data=body, method=method, headers=hdrs)
        for attempt in range(4):
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as res:
                    payload = res.read()
                    return payload if raw else (json.loads(payload) if payload else {})
            except urllib.error.HTTPError as err:
                text = err.read().decode(errors="replace")
                try:
                    message = json.loads(text).get("error", text)
                except ValueError:
                    # Not Cherry Cloud's own JSON: a proxy or hosting error page, which means nothing to users.
                    message = f"Cherry Cloud at {self.url} isn't answering properly (HTTP {err.code})"
                if err.code >= 500 and err.code != 507 and attempt < 3 and not signer:
                    time.sleep(2 ** attempt)
                    continue
                raise CloudError(err.code, message) from None
            except (urllib.error.URLError, TimeoutError, ConnectionError) as err:
                if attempt < 3:
                    time.sleep(2 ** attempt)
                    continue
                raise CloudError(0, f"can't reach Cherry Cloud ({self.url}): {getattr(err, 'reason', err)}") from None
        raise CloudError(0, "unreachable")

    def get(self, path: str, **kw):
        return self.request("GET", path, **kw)

    def post(self, path: str, body=None, **kw):
        return self.request("POST", path, json_body=body if body is not None else {}, **kw)
