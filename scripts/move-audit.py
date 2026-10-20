#!/usr/bin/env python3
"""Checks what Cherry Move would leave behind from a home folder, without packing anything.
  move-audit.py [HOME] [--list FILE]
Walks every file and symlink in HOME (default: yours) and compares it with what `cherry-move send` with every
category ticked packs (the same walk send() does). Prints what wouldn't come, grouped by folder and largest
first, with the reason: a left-behind rule (cache, reinstalled from the list…) or NOT BROUGHT, which is a bug.
--list writes every path left behind, with its reason, to FILE.
"""
import os
import sys
import time
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "desktop/lib"))
from cherryos import transfer  # noqa: E402


def main(argv):
    args = list(argv)
    out = None
    if "--list" in args:
        i = args.index("--list")
        out = Path(args[i + 1])
        del args[i:i + 2]
    home = Path(args[0]) if args else Path.home()
    started = time.time()
    rules = transfer.left_behind_rules(home)
    packed: set = set()
    left: dict = {}
    for c in transfer.categories(home, measure_sizes=False):
        for prefix, base in c.paths:
            for path, _arc in transfer._walk(base, prefix, home, c.skip, left.__setitem__):
                packed.add(str(path))

    def why(path: str) -> str:
        stem, dash, end = path.rpartition("-")
        if dash and end in ("wal", "shm", "journal") and stem in packed and transfer._live_sqlite(stem):
            return "sqlite"
        p = path
        while p != str(home) and p != "/":
            if p in left:
                return left[p]
            p = os.path.dirname(p)
        return rules(os.path.relpath(path, home)) or "NOT BROUGHT"

    groups: dict = defaultdict(lambda: [0, 0])
    total_files = total_size = miss_files = miss_size = bugs = 0
    closed: list = []
    listing = open(out, "w") if out else None
    for root, dirs, names in os.walk(home, onerror=lambda e: closed.append(e.filename)):
        for n in names + [d for d in dirs if os.path.islink(os.path.join(root, d))]:
            path = os.path.join(root, n)
            try:
                st = os.lstat(path)
            except OSError:
                continue
            total_files += 1
            total_size += st.st_size
            if path in packed:
                if os.path.isfile(path) and not os.path.islink(path) and not os.access(path, os.R_OK):
                    reason = f"unreadable (owned by uid {st.st_uid}), listed as stayed behind when sending"
                else:
                    continue
            elif max(st.st_mtime, st.st_ctime) >= started:
                reason = "new since the check started"
            else:
                reason = why(path)
            bugs += reason == "NOT BROUGHT"
            rel = os.path.relpath(path, home)
            miss_files += 1
            miss_size += st.st_size
            key = "/".join(rel.split(os.sep)[:3 if rel.startswith(".") else 2])
            g = groups[(key, reason)]
            g[0] += 1
            g[1] += st.st_size
            if listing:
                listing.write(f"{reason}\t{rel}\n")
    if listing:
        listing.close()
    print(f"{total_files} files, {transfer.human(total_size)} in {home}")
    print(f"Left behind: {miss_files} files, {transfer.human(miss_size)}; {bugs} not brought by mistake")
    if closed:
        print(f"{len(closed)} folders couldn't be opened (another user's): " + ", ".join(closed[:10]))
    print()
    for (key, reason), (files, size) in sorted(groups.items(), key=lambda kv: (kv[0][1] != "NOT BROUGHT", -kv[1][1]))[:200]:
        print(f"{transfer.human(size):>10} {files:>9} files  {key}  [{reason}]")
    return 1 if bugs else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
