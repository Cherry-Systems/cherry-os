#!/usr/bin/env python3
"""Host side of the Windows test VM helper (agent.ps1 runs inside the VM).
  winvm.py serve [DIR]        serve jobs on 127.0.0.1:8120 (the VM sees it as 10.0.2.2) and files from DIR at /files/
  winvm.py run 'PowerShell'   run a snippet in the VM and print its output (needs `serve` running)
The VM can PUT a file to /up/NAME; it lands in DIR/build/win-up/NAME.
"""
import json
import sys
import threading
import time
import urllib.request
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

PORT = 8120
jobs: list = []
results: dict = {}
lock = threading.Condition()
files_dir = Path(".")


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def _json(self, obj, status=200):
        body = json.dumps(obj).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _body(self):
        return json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")

    def do_GET(self):
        if self.path == "/next":
            with lock:
                lock.wait_for(lambda: jobs, timeout=20)
                job = jobs.pop(0) if jobs else {}
            return self._json(job)
        if self.path.startswith("/result/"):
            jid = self.path.rsplit("/", 1)[1]
            with lock:
                lock.wait_for(lambda: jid in results, timeout=600)
                return self._json({"output": results.pop(jid, None)})
        if self.path.startswith("/files/"):
            path = (files_dir / self.path[len("/files/"):]).resolve()
            if files_dir.resolve() in path.parents and path.is_file():
                data = path.read_bytes()
                self.send_response(200)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
                return
        self._json({"error": "not found"}, 404)

    def do_PUT(self):
        if not self.path.startswith("/up/") or "/" in self.path[len("/up/"):]:
            return self._json({"error": "not found"}, 404)
        dest = files_dir / "build" / "win-up" / self.path[len("/up/"):]
        dest.parent.mkdir(parents=True, exist_ok=True)
        left = int(self.headers.get("Content-Length", 0))
        with open(dest, "wb") as f:
            while left:
                block = self.rfile.read(min(left, 1 << 20))
                if not block:
                    break
                f.write(block)
                left -= len(block)
        self._json({"ok": left == 0}, 200 if left == 0 else 400)

    def do_POST(self):
        data = self._body()
        with lock:
            if self.path == "/enqueue":
                jid = uuid.uuid4().hex
                jobs.append({"id": jid, "script": data["script"]})
                lock.notify_all()
                return self._json({"id": jid})
            if self.path == "/done":
                results[data["id"]] = data.get("output", "")
                lock.notify_all()
                return self._json({})
        self._json({"error": "not found"}, 404)


def main(argv):
    global files_dir
    if argv[:1] == ["serve"]:
        files_dir = Path(argv[1] if len(argv) > 1 else ".")
        ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
    elif argv[:1] == ["run"]:
        req = urllib.request.Request(f"http://127.0.0.1:{PORT}/enqueue", json.dumps({"script": " ".join(argv[1:])}).encode(),
                                     {"Content-Type": "application/json"})
        jid = json.load(urllib.request.urlopen(req))["id"]
        start = time.time()
        while time.time() - start < 1800:
            out = json.load(urllib.request.urlopen(f"http://127.0.0.1:{PORT}/result/{jid}", timeout=700))["output"]
            if out is not None:
                sys.stdout.write(out)
                return
        sys.exit("timed out waiting for the VM")
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main(sys.argv[1:])
