#!/usr/bin/env python3
"""Joins Mac programs for different processors into one universal program, like Apple's lipo.

  lipo.py OUTPUT INPUT..."""
import os
import struct
import sys

FAT_MAGIC = 0xCAFEBABE
CPU_ARM64 = 0x0100000C
out, inputs = sys.argv[1], sys.argv[2:]
slices = []
for path in inputs:
    data = open(path, "rb").read()
    magic, cputype, subtype = struct.unpack("<III", data[:12])
    if magic != 0xFEEDFACF:
        sys.exit(f"{path}: not a 64-bit Mac program")
    # Each processor's part starts on a page boundary: 16 KB on Apple silicon, 4 KB on Intel.
    slices.append((cputype, subtype, 14 if cputype == CPU_ARM64 else 12, data))

header = struct.pack(">II", FAT_MAGIC, len(slices))
offset = len(header) + 20 * len(slices)
entries, body = b"", b""
for cputype, subtype, align, data in slices:
    start = -(-(offset + len(body)) // (1 << align)) * (1 << align)
    body += b"\0" * (start - offset - len(body))
    entries += struct.pack(">iiIII", cputype, subtype, start, len(data), align)
    body += data
with open(out, "wb") as f:
    f.write(header + entries + body)
os.chmod(out, 0o755)
