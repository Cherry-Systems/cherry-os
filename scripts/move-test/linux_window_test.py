#!/usr/bin/env python3
"""Tests the Linux app's own window, on a virtual screen (run by linux-window-test.sh in a container).
  linux_window_test.py APP OUT_DIR
Opens the app, checks a second launch brings the first to the front instead of opening another, that closing
the window while files are being moved asks first, that "Save code as a file…" saves through the system's
save box, that printing opens the print box, and that closing the window ends the app. Screenshots go in
OUT_DIR."""
import json
import os
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path

APP, OUT = Path(sys.argv[1]).resolve(), Path(sys.argv[2]).resolve()
OUT.mkdir(parents=True, exist_ok=True)
HOME = Path(os.environ["HOME"])
failures = []


def check(ok, what):
    print(("PASS " if ok else "FAIL ") + what, flush=True)
    if not ok:
        failures.append(what)


def sh(*cmd):
    return subprocess.run(cmd, capture_output=True, text=True).stdout.strip()


def shot(name):
    sh("import", "-window", "root", str(OUT / f"{name}.png"))


def windows():
    return [l for l in sh("wmctrl", "-l").splitlines() if "Cherry Move" in l]


def wait(cond, what, timeout=60):
    end = time.time() + timeout
    while time.time() < end:
        v = cond()
        if v:
            return v
        time.sleep(0.5)
    check(False, f"timed out: {what}")
    return None


def running():
    f = HOME / ".local/share/cherry-move/running.json"
    return json.loads(f.read_text()) if f.exists() else None


def call(path, body=None):
    info = running()
    base, token = info["url"].split("/?t=")
    req = urllib.request.Request(base + path, data=json.dumps(body).encode() if body is not None else None,
                                 headers={"X-Cherry": token, "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.loads(r.read())


def start_app():
    return subprocess.Popen([str(APP)], stdout=open(OUT / "app.log", "a"), stderr=subprocess.STDOUT)


def send_to(stick):
    s = wait(lambda: (lambda s: s if s["catsReady"] else None)(call("/api/state")), "categories")
    call("/api/plan", {"ids": [c["id"] for c in s["cats"] if c["default"]]})
    wait(lambda: call("/api/state").get("sizes"), "plan")
    stick.mkdir(exist_ok=True)
    call("/api/send", {"storage": "usb", "drive": str(stick), "level": 0})


# 1. It opens in its own window.
app = start_app()
check(bool(wait(windows, "the window")), "opens in its own window")
time.sleep(4)
shot("1-window")
state = call("/api/state")
check(state.get("window") is True, "the page knows it's in the app's window")

# 2. Opening it again brings this one to the front instead of opening a second.
second = subprocess.run([str(APP)], capture_output=True, timeout=20)
check(second.returncode == 0, "a second launch ends straight away")
time.sleep(1)
check(len(windows()) == 1 and app.poll() is None, "still exactly one window")

# 3. Closing it while files are being moved asks first.
send_to(Path("/tmp/stick1"))
wait(lambda: call("/api/state")["sending"], "sending")
sh("wmctrl", "-c", "Cherry Move")
time.sleep(1.5)
shot("2-close-while-sending")
check(app.poll() is None and len(windows()) == 1, "closing while sending keeps the window open")
call("/api/quit", {})  # what the dialog's "Stop and close" does
check(wait(lambda: app.poll() is not None, "app ends after Stop and close", 40) is not None, "Stop and close ends the app")
check(running() is None, "running.json is gone afterwards")

# 4. A whole move, then "Save code as a file…" through the system's save box.
app = start_app()
wait(windows, "the window again")
send_to(Path("/tmp/stick2"))
s = wait(lambda: (lambda s: s if not s["sending"] and (s.get("result") or s.get("sendErr")) else None)(
    call("/api/state")), "the move", 600)
check(bool(s and s.get("result")), "the move finished: " + str(s and (s.get("sendErr") or s["result"]["files"])))
saved = {}
threading.Thread(target=lambda: saved.update(call("/api/save-code", {})), daemon=True).start()
time.sleep(3)
shot("3-save-box")
target = HOME / "my code.txt"
sh("xdotool", "key", "ctrl+a")
sh("xdotool", "type", "--delay", "30", str(target))
sh("xdotool", "key", "Return")
wait(lambda: saved, "the save box to close", 20)
check(saved.get("saved") == str(target) and target.exists() and s["code"] in target.read_text(),
      f"the code was saved where chosen ({saved})")
shot("4-after-save")

# 5. "Print code" opens the system's print box.
threading.Thread(target=lambda: call("/api/print", {}), daemon=True).start()
box = wait(lambda: [l for l in sh("wmctrl", "-l").splitlines() if l.endswith(" Print")], "the print box", 20)
shot("5-print-box")
check(bool(box), "Print opens the system's print box")
sh("xdotool", "key", "Escape")
check(bool(wait(lambda: not [l for l in sh("wmctrl", "-l").splitlines() if l.endswith(" Print")],
                "the print box to close", 10)), "the print box can be cancelled")

# 6. Closing the window when nothing is happening ends the app.
sh("wmctrl", "-c", "Cherry Move")
check(wait(lambda: app.poll() is not None, "app ends when its window is closed", 20) is not None,
      "closing the window ends the app")
print("RESULT:", "all passed" if not failures else f"{len(failures)} failed: {failures}")
sys.exit(1 if failures else 0)
