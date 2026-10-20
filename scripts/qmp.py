#!/usr/bin/env python3
"""Drives a running test VM (scripts/run-qemu.sh) through its QMP socket.
  qmp.py shot OUT.png            screenshot
  qmp.py type 'text'             type text (US layout)
  qmp.py key ctrl-alt-t ret      press keys / combos in order
  qmp.py click X Y [right]       click at screen pixel X,Y (screen size from the last screenshot, default 1600x900)
  qmp.py move X Y
  qmp.py drag X1 Y1 X2 Y2        press at X1,Y1, move to X2,Y2 and let go
  qmp.py scroll X Y N           mouse wheel N clicks at X,Y (negative scrolls up)
  qmp.py usb-add FILE NAME       plug in a disk image as a USB stick
  qmp.py usb-remove NAME         unplug it (sticks from run-qemu.sh --usb are stick0, stick1, …)
  qmp.py link on|off             plug the network cable in or out
  qmp.py powerdown               press the power button (clean shutdown)
  qmp.py quit                    pull the plug
Socket: $VM_DIR/qmp.sock (default build/vm).
"""
import json
import os
import socket
import sys
import time
from pathlib import Path

SOCK = Path(os.environ.get("VM_DIR", Path(__file__).resolve().parent.parent / "build/vm")) / "qmp.sock"
SCREEN = (1600, 900)

SHIFTED = {'~': 'grave_accent', '!': '1', '@': '2', '#': '3', '$': '4', '%': '5', '^': '6', '&': '7', '*': '8', '(': '9',
           ')': '0', '_': 'minus', '+': 'equal', '{': 'bracket_left', '}': 'bracket_right', '|': 'backslash',
           ':': 'semicolon', '"': 'apostrophe', '<': 'comma', '>': 'dot', '?': 'slash'}
PLAIN = {' ': 'spc', '-': 'minus', '=': 'equal', '[': 'bracket_left', ']': 'bracket_right', '\\': 'backslash',
         ';': 'semicolon', "'": 'apostrophe', ',': 'comma', '.': 'dot', '/': 'slash', '`': 'grave_accent',
         '\n': 'ret', '\t': 'tab'}


class Qmp:
    def __init__(self):
        self.s = socket.socket(socket.AF_UNIX)
        self.s.connect(str(SOCK))
        self.f = self.s.makefile("rw")
        self.f.readline()
        self.cmd("qmp_capabilities")

    def cmd(self, execute, **args):
        self.f.write(json.dumps({"execute": execute, "arguments": args}) + "\n")
        self.f.flush()
        while True:
            reply = json.loads(self.f.readline())
            if "return" in reply or "error" in reply:
                if "error" in reply:
                    raise RuntimeError(reply["error"])
                return reply["return"]

    def keys(self, names):
        self.cmd("send-key", keys=[{"type": "qcode", "data": n} for n in names])
        time.sleep(0.04)

    def mouse(self, x, y, button=None):
        sx, sy = screen_size()
        events = [{"type": "abs", "data": {"axis": "x", "value": int(x * 32767 / (sx - 1))}},
                  {"type": "abs", "data": {"axis": "y", "value": int(y * 32767 / (sy - 1))}}]
        self.cmd("input-send-event", events=events)
        if button:
            time.sleep(0.05)
            for down in (True, False):
                self.cmd("input-send-event", events=[{"type": "btn", "data": {"down": down, "button": button}}])
                time.sleep(0.05)


def screen_size():
    size = SOCK.parent / "screen-size"
    if size.exists():
        w, h = size.read_text().split()
        return int(w), int(h)
    return SCREEN


def char_keys(c):
    if c.isalpha():
        return (["shift", c.lower()] if c.isupper() else [c])
    if c.isdigit():
        return [c]
    if c in SHIFTED:
        return ["shift", SHIFTED[c]]
    return [PLAIN[c]]


def main(argv):
    q = Qmp()
    op = argv[0]
    if op == "shot":
        out = Path(argv[1]).resolve()
        out.unlink(missing_ok=True)
        q.cmd("screendump", filename=str(out), format="png")
        for _ in range(50):  # QEMU finishes writing after it replies
            if out.exists() and out.stat().st_size > 0:
                time.sleep(0.2)
                break
            time.sleep(0.1)
        try:
            from PIL import Image
            with Image.open(out) as im:
                (SOCK.parent / "screen-size").write_text(f"{im.width} {im.height}")
        except Exception:
            pass
    elif op == "type":
        for c in " ".join(argv[1:]):
            q.keys(char_keys(c))
    elif op == "key":
        for combo in argv[1:]:
            q.keys(combo.split("-") if combo != "minus" else ["minus"])
            time.sleep(0.1)
    elif op in ("click", "move"):
        button = None if op == "move" else (argv[3] if len(argv) > 3 else "left")
        q.mouse(float(argv[1]), float(argv[2]), button)
    elif op == "drag":
        x1, y1, x2, y2 = map(float, argv[1:5])
        q.mouse(x1, y1)
        time.sleep(0.2)
        q.cmd("input-send-event", events=[{"type": "btn", "data": {"down": True, "button": "left"}}])
        for i in range(1, 21):
            time.sleep(0.03)
            q.mouse(x1 + (x2 - x1) * i / 20, y1 + (y2 - y1) * i / 20)
        time.sleep(0.5)
        q.cmd("input-send-event", events=[{"type": "btn", "data": {"down": False, "button": "left"}}])
    elif op == "scroll":  # scroll X Y CLICKS (negative = up)
        q.mouse(float(argv[1]), float(argv[2]))
        clicks = int(argv[3])
        button = "wheel-down" if clicks > 0 else "wheel-up"
        for _ in range(abs(clicks)):
            for down in (True, False):
                q.cmd("input-send-event", events=[{"type": "btn", "data": {"down": down, "button": button}}])
            time.sleep(0.05)
    elif op == "usb-add":
        name = argv[2]
        q.cmd("blockdev-add", driver="raw", **{"node-name": name},
              file={"driver": "file", "filename": str(Path(argv[1]).resolve())})
        q.cmd("device_add", driver="usb-storage", bus="xhci.0", drive=name, id=f"dev-{name}", removable=True)
    elif op == "usb-remove":
        name = argv[1]
        q.cmd("device_del", id=f"dev-{name}")
        for _ in range(50):
            time.sleep(0.2)
            try:
                q.cmd("blockdev-del", **{"node-name": name})
                break
            except RuntimeError as e:
                if "find node" in str(e):
                    break  # a run-qemu.sh --usb stick: its -drive backend just stays behind
                # otherwise still in use until the guest lets go
    elif op == "link":  # link on|off: plug the network cable in or out (run-qemu.sh's nic0)
        q.cmd("set_link", name="nic0", up=argv[1] == "on")
    elif op == "powerdown":
        q.cmd("system_powerdown")
    elif op == "quit":
        q.cmd("quit")
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main(sys.argv[1:])
