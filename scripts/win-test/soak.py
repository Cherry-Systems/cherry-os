#!/usr/bin/env python3
"""Windows soak test for the Cherry Move app, round after round.

In the Windows test VM each round builds a new random home folder with what Windows has and Linux doesn't (paths
longer than 260 characters, read-only, hidden and system files, junctions, hard links, alternate data streams,
names ending in a dot or a space, files another program holds locked, timestamps from 1601 to 2200…), sends it
with CherryMove.exe to one stick, several small sticks or a NAS folder (sometimes switching the app off part way,
or changing files while it packs them), then restores it here with Cherry OS's restore and checks every file.

Needs the VM (scripts/win-test/run-windows-vm.sh) and `python3 scripts/win-test/winvm.py serve .` in the
repository's root. Run: PYTHONPATH=desktop/lib python3 scripts/win-test/soak.py [--minutes N | --rounds N] [--seed S]
A failing round's files are kept in build/win-soak/round-SEED."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import shutil
import sys
import tarfile
import threading
import time
import traceback
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "desktop/lib"), str(ROOT / "tests")]

from cherryos import secret_store, transfer  # noqa: E402
from soak import FolderNas, Patch, digest, failed_report, wipe  # noqa: E402

MB = 1024 * 1024
SERVER = "http://127.0.0.1:8120"
BASE = ROOT / "build" / "win-soak"
UP = ROOT / "build" / "win-up" / "round.tar"
APP = ROOT / "build" / "move" / "parts" / "CherryMove.exe"
EPOCH = 116444736000000000  # 1970-01-01 as a FILETIME
WORDS = ["notes", "photo", "Report", "Résumé", "日本語のファイル", "emoji 🍒", "with space", " leading", "-dash",
         "#hash%20", "a&b", "brackets [1]", "(copy)", "semi;colon", "quote'single", "UPPER", "dots...x", "x" * 90,
         "café", "ß", "Ω", "~tilde", "$dollar", "@at", "=eq", "+plus", ",comma", "Ünïcödé", "ελληνικά", "עברית",
         "한국어", "IMG_0042", "budget 2025", "essay (from old computer)", "file (before Cherry Move)", "desktop.ini2"]
EXTS = ["", ".txt", ".jpg", ".docx", ".pdf", ".mp3", ".PST", ".db", ".lnk2", ".tar.gz", ".JPG"]
ODD_ENDS = [".", " ", "..", ". "]  # Windows itself can't make these; WSL and Cygwin can


def vm(script: str, timeout: float = 4 * 3600) -> str:
    req = urllib.request.Request(f"{SERVER}/enqueue", json.dumps({"script": script}).encode(),
                                 {"Content-Type": "application/json"})
    jid = json.load(urllib.request.urlopen(req))["id"]
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        out = json.load(urllib.request.urlopen(f"{SERVER}/result/{jid}", timeout=700))["output"]
        if out is not None:
            return out
    raise TimeoutError("the VM didn't answer")


def filetime(rng: random.Random) -> int:
    r = rng.random()
    if r < 0.03:
        secs = rng.randint(-11644473599, -1)  # 1601 to 1969
    elif r < 0.05:
        secs = rng.randint(4102444800, 7258118400)  # 2100 to 2200
    else:
        secs = rng.randint(315532800, 1900000000)
    return EPOCH + secs * 10**7 + rng.randrange(10**7)


def make_spec(rng: random.Random, app_sha: str) -> tuple[dict, list[str]]:
    notes = []
    profile = rng.choices(["small", "many", "big"], [0.65, 0.2, 0.15])[0]
    n_dirs, n_files = {"small": (rng.randint(3, 40), rng.randint(20, 300)),
                       "many": (rng.randint(50, 300), rng.randint(1500, 4000)),
                       "big": (rng.randint(2, 10), rng.randint(5, 30))}[profile]
    items, dirs, files, taken = [], [""], [], set()
    odd = rng.random() < 0.2

    def name(parent: str, ext: str = "", long: bool = False) -> str:
        for _ in range(100):
            n = ("y" * rng.randint(60, 200)) if long else rng.choice(WORDS)
            if rng.random() < 0.5:
                n += f" {rng.randint(1, 999)}"
            n += ext
            if odd and not long and rng.random() < 0.03:
                n += rng.choice(ODD_ENDS)
            key = (parent, n.casefold().rstrip(". "))
            if key not in taken and n.strip(". ") and len(n.encode("utf-16-le")) <= 510:
                taken.add(key)
                return n
        raise RuntimeError("no free name")

    def join(parent: str, n: str) -> str:
        return f"{parent}\\{n}" if parent else n

    for _ in range(n_dirs):
        parent = rng.choice(dirs)
        d = join(parent, name(parent))
        dirs.append(d)
        items.append({"t": "d", "p": d})
    if rng.random() < 0.3:  # deep folders: paths far past Windows' old 260-character limit
        parent = rng.choice(dirs)
        for _ in range(rng.randint(2, 7)):
            parent = join(parent, name(parent, long=True))
            dirs.append(parent)
            items.append({"t": "d", "p": parent})
        notes.append("long paths")
    contents = []
    for _ in range(n_files):
        parent = rng.choice(dirs)
        p = join(parent, name(parent, rng.choice(EXTS)))
        if contents and rng.random() < 0.1:
            size, seed, kind = rng.choice(contents)  # a copy
        else:
            r = rng.random()
            size = (0 if r < 0.08 else rng.randint(1, 64 * 1024) if r < 0.7 else
                    rng.randint(64 * 1024, 4 * MB) if r < 0.96 else rng.randint(4 * MB, 40 * MB))
            if profile == "big" and rng.random() < 0.3:
                size = rng.randint(60, 300) * MB
            seed, kind = rng.randrange(2**31), rng.choices([0, 1, 2], [0.6, 0.3, 0.1])[0]
            contents.append((size, seed, kind))
        f = {"t": "f", "p": p, "size": size, "seed": seed, "kind": kind, "mtime": filetime(rng)}
        r = rng.random()
        if r < 0.1:
            f["attrs"] = 0x21  # read-only
        elif r < 0.16:
            f["attrs"] = 0x22  # hidden
        elif r < 0.19:
            f["attrs"] = 0x26  # hidden and system: Windows' own, left behind
        items.append(f)
        files.append(p)
    if len(dirs) > 3 and rng.random() < 0.15:
        d = rng.choice(dirs[1:])
        items.append({"t": "d", "p": d, "attrs": 0x16})  # a hidden system folder: left behind with all inside
        notes.append("system folder")
    for _ in range(rng.choice([0, 0, 1, 3])):
        parent = rng.choice(dirs)
        items.append({"t": "h", "p": join(parent, name(parent, ".txt")), "target": rng.choice(files)})
    for i in range(rng.choice([0, 0, 1, 2])):
        target = rng.choice([r"C:\Windows\Fonts", r"C:\Users\alex\SoakHome", r"C:\Users\alex\Documents"])
        items.append({"t": "j", "p": join(rng.choice(dirs[:3]), f"junction{i}"), "target": target})
    for i in range(rng.choice([0, 0, 1])):
        target = rng.choice(files)
        items.append({"t": "s", "p": join(rng.choice(dirs), f"symlink{i}"),
                      "target": "C:\\Users\\alex\\SoakHome\\" + target, "dir": False})
    for p in rng.sample(files, min(len(files), rng.choice([0, 0, 1, 3]))):
        items.append({"t": "a", "p": p, "stream": "Zone.Identifier", "data": "[ZoneTransfer]\r\nZoneId=3\r\n"})
    if odd:
        notes.append("names ending in a dot or space")
    locked = rng.sample(files, min(len(files), rng.randint(1, 2))) if rng.random() < 0.25 else []
    if locked:
        notes.append(f"{len(locked)} locked")
    busy = None
    if rng.random() < 0.25:
        ops = []
        free = [p for p in files if p not in locked]
        for _ in range(rng.randint(3, 15)):
            op = rng.choice(["append", "rewrite", "shrink", "delete", "rename", "create"])
            p = rng.choice(free)
            o = {"op": op, "p": p, "gap": rng.randint(0, 300)}
            if op == "rename":
                o["q"] = p + " renamed"
            elif op == "create":
                o["p"] = p + " new"
            ops.append(o)
        busy = {"delay": rng.randint(0, 3000), "ops": ops}
        notes.append("busy while sending")
    where = rng.choices(["stick", "sticks", "folder"], [0.4, 0.35, 0.25])[0]
    raw = sum(i["size"] for i in items if i["t"] == "f")
    sticks = 1
    if where == "folder":
        args = ["--to", "folder", "--folder", r"C:\soak\share"]
    else:
        args = ["--to", "usb", "--usb", r"C:\soak\sticks\1"]
        if where == "sticks":
            sticks = raw // (8 * MB) + 12
            cap = 4 * MB + 8 * MB * rng.randint(2, 5) + rng.randint(0, 8 * MB)
            args += ["--next-sticks", ",".join(rf"C:\soak\sticks\{n}" for n in range(2, sticks + 1)),
                     "--stick-capacity", str(cap)]
    if rng.random() < 0.33:
        args.append("--strong")
    kill_after = round(rng.uniform(0.2, 5), 2) if rng.random() < 0.25 else 0
    notes.insert(0, f"{profile} {where}{' strong' if '--strong' in args else ''}")
    spec = {"items": items, "locked": locked, "busy": busy, "args": args, "sticks": sticks,
            "kill_after": kill_after, "app_sha": app_sha}
    return spec, notes


def unix_ns(ft: int) -> int:
    """A FILETIME as the new computer keeps it: ext4 holds no time before 13 December 1901 (Windows goes to 1601)."""
    return max((ft - EPOCH) * 100, -2**31 * 10**9)


def run_round(seed: int, app_sha: str) -> tuple[bool, str, list]:
    rng = random.Random(seed)
    work = BASE / f"round-{seed}"
    wipe(work)
    work.mkdir(parents=True)
    problems: list[str] = []
    patch = Patch()
    spec, notes = make_spec(rng, app_sha)
    try:
        (BASE / "spec.json").write_text(json.dumps(spec))
        UP.unlink(missing_ok=True)
        out = vm("New-Item -ItemType Directory -Force C:\\soak | Out-Null; "
                 "Invoke-WebRequest -UseBasicParsing http://10.0.2.2:8120/files/scripts/win-test/soak-round.ps1 "
                 "-OutFile $env:TEMP\\soak-round.ps1; & $env:TEMP\\soak-round.ps1")
        try:
            summary = json.loads(out.strip().splitlines()[-1])
        except (ValueError, IndexError):
            raise RuntimeError(f"the round in the VM failed: {out[-3000:]}") from None
        if spec["kill_after"]:
            notes.append("app switched off part way, sent again" if summary["killed"] else "switch-off came too late")
        with tarfile.open(UP) as t:
            t.extractall(work, filter="fully_trusted")
        up = work / "up"
        failed = (up / "failed.txt").read_text(encoding="utf-8").strip()
        made_links = {i["p"] for i in spec["items"] if i["t"] in "hjs"}
        for line in failed.splitlines():
            kind, _, rest = line.partition(" ")
            if kind in ("s",):
                continue  # symlinks need developer mode
            problems.append(f"couldn't make in the VM: {line}")
        if summary["exit"] != 0:
            raise RuntimeError(f"the app failed ({summary['exit']}): {(up / 'err.txt').read_text(errors='replace')[-3000:]}")
        res = json.loads((up / "out.json").read_text(encoding="utf-8"))
        touched = set((up / "touched.txt").read_text(encoding="utf-8-sig").split("\n")) - {""}
        pairs = [(i["p"], i["target"]) for i in spec["items"] if i["t"] == "h"]
        while extra := ({b for a, b in pairs if a in touched} | {a for a, b in pairs if b in touched}) - touched:
            touched |= extra  # editing one name of a hard link changes them all

        entries = {}
        for line in (up / "manifest.tsv").read_text(encoding="utf-8").splitlines():
            rel, kind, attrs, size, ft, sha = line.split("\t")
            entries[rel] = (kind, int(attrs), int(size), int(ft), sha)
        left = []  # folders Windows' own (hidden and system) or links: nothing in them comes
        for rel, (kind, attrs, *_rest) in entries.items():
            if attrs & 0x400 or (attrs & 0x6) == 0x6:
                left.append(rel + "\\")

        def stays(rel: str) -> bool:
            return any(rel.startswith(x) for x in left)

        skipped = set()
        for p in res.get("skipped") or []:
            i = p.find("\\SoakHome\\")
            if i < 0:
                problems.append(f"skipped outside the soak folder: {p!r}")
            else:
                skipped.add(p[i + len("\\SoakHome\\"):])

        restore_patch(patch)
        new = work / "new" / "someone"
        new.mkdir(parents=True)
        os.environ["HOME"] = str(new)
        if spec["args"][1] == "folder":
            kwargs = {"remote": FolderNas(work / "share")}
        else:
            used = [work / "sticks" / str(n) for n in range(1, max(1, res.get("sticks") or 1) + 1)]
            kwargs = {"drive": used[0], "next_stick": lambda part, problem: used[part - 1]}
        for _ in range(rng.choice([0, 0, 1, 2])):
            stop, at = threading.Event(), rng.random()

            def progress(done, total, name, stop=stop, at=at):
                if done >= at * total:
                    stop.set()
            try:
                transfer.restore(res["code"], home=new, progress=progress, cancel=stop, **kwargs)
                break
            except InterruptedError:
                notes.append("restore stopped")
        transfer.restore(res["code"], home=new, **kwargs)

        got = new / "SoakHome"
        for rel, (kind, attrs, size, ft, sha) in sorted(entries.items()):
            here = got / rel.replace("\\", "/")
            if rel in touched or stays(rel) or attrs & 0x400 or (attrs & 0x6) == 0x6:
                if (attrs & 0x400 or (attrs & 0x6) == 0x6 or stays(rel)) and (here.exists() or here.is_symlink()):
                    problems.append(f"came through Windows' own or a link: {rel!r}")
                continue
            if kind == "d":
                if not here.is_dir():
                    problems.append(f"folder missing: {rel!r}")
                continue
            if sha in ("locked", "denied"):
                if rel not in skipped:
                    problems.append(f"locked file not listed as left behind: {rel!r}")
                if here.exists():
                    problems.append(f"locked file came anyway: {rel!r}")
                continue
            if rel in skipped:
                problems.append(f"left behind for no reason: {rel!r}")
                continue
            if not here.is_file():
                problems.append(f"missing: {rel!r} ({size} bytes)")
                continue
            st = here.stat()
            if st.st_size != size or digest(here) != sha:
                problems.append(f"different: {rel!r} ({size} -> {st.st_size} bytes)")
            if abs(st.st_mtime_ns - unix_ns(ft)) > 10_000:
                problems.append(f"time {unix_ns(ft)} -> {st.st_mtime_ns}: {rel!r}")
        for rel in skipped - set(entries) - touched:
            problems.append(f"left behind, not in the folder: {rel!r}")
        if got.exists():
            for dirpath, dirs, names in os.walk(got):
                for n in dirs + names:
                    rel = str((Path(dirpath) / n).relative_to(got)).replace("/", "\\")
                    if rel not in entries and rel not in touched:
                        problems.append(f"extra: {rel!r}")
        elif any(k for k in entries):
            problems.append("nothing restored")
        if not problems and rng.random() < 0.3:
            before = snapshot(got)
            again = transfer.restore(res["code"], home=new, **kwargs)
            notes.append("restored twice")
            if snapshot(got) != before or again["restored_files"]:
                problems.append("restoring a second time changed the files")
        notes.append(f"{sum(1 for v in entries.values() if v[0] == 'f')} files")
        if made_links:
            notes.append(f"{len(made_links)} links")
    except Exception:
        problems.append("CRASH " + traceback.format_exc())
    finally:
        patch.undo()
    ok = not problems
    if ok:
        wipe(work)
    return ok, ", ".join(notes), problems


def snapshot(root: Path) -> dict:
    return {str(p.relative_to(root)): (p.stat().st_size, p.stat().st_mtime_ns) for p in root.rglob("*") if p.is_file()}


def restore_patch(patch: Patch) -> None:
    patch.set(transfer, "network_mounts", lambda *_: frozenset())
    patch.set(secret_store, "add_all", lambda items: list(items))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--minutes", type=float, default=0)
    ap.add_argument("--rounds", type=int, default=0)
    ap.add_argument("--seed", type=int, default=None)
    args = ap.parse_args()
    BASE.mkdir(parents=True, exist_ok=True)
    app_sha = hashlib.sha256(APP.read_bytes()).hexdigest().upper()
    seed = args.seed if args.seed is not None else random.SystemRandom().randrange(10**9)
    end = time.monotonic() + args.minutes * 60 if args.minutes else None
    rounds = passed = 0
    real_home = os.environ.get("HOME", "")
    while not (args.rounds and rounds >= args.rounds or end and time.monotonic() > end
               or not args.rounds and not end and rounds):
        t = time.monotonic()
        ok, notes, problems = run_round(seed, app_sha)
        os.environ["HOME"] = real_home
        rounds += 1
        passed += ok
        print(f"{time.strftime('%H:%M:%S')} round {rounds} seed {seed}: {'ok' if ok else 'FAILED'} "
              f"({time.monotonic() - t:.0f}s; {notes})", flush=True)
        for p in problems[:30]:
            print("    " + p.replace("\n", "\n    "), flush=True)
        if len(problems) > 30:
            print(f"    … and {len(problems) - 30} more", flush=True)
        if not ok:
            failed_report(BASE / f"round-{seed}", seed, notes, problems,
                          f"PYTHONPATH=desktop/lib python3 scripts/win-test/soak.py --seed {seed} --rounds 1")
        seed = random.Random(seed).randrange(10**9)
    print(f"{passed}/{rounds} rounds passed", flush=True)
    sys.exit(0 if passed == rounds else 1)


if __name__ == "__main__":
    main()
