#!/bin/sh
# Restores a Cherry Move transfer from USB stick images (in any order) with Cherry OS's own restore code, in a
# throwaway Debian trixie container, then checks the files against the hashes from the old computer.
#   scripts/move-test/restore_sticks.sh HASHES STICK.img [STICK2.img …]
# The images are mounted read-only. Needs Docker.
set -e
cd "$(dirname "$0")/../.."
[ $# -ge 2 ] || { echo "usage: $0 HASHES STICK.img [STICK2.img …]" >&2; exit 2; }
hashes=$(realpath "$1"); shift
mounts="" imgs=""
i=0
for img in "$@"; do
  i=$((i + 1))
  mounts="$mounts -v $(realpath "$img"):/sticks/$i.img:ro"
  imgs="$imgs /sticks/$i.img"
done
exec docker run --rm --privileged -v "$PWD:/src:ro" -v "$hashes:/hashes.txt:ro" $mounts debian:trixie sh -c '
set -e
apt-get update -qq
DEBIAN_FRONTEND=noninteractive apt-get install -y -qq --no-install-recommends python3-cryptography python3-zstandard \
  python3-qrcode >/dev/null
for img in '"$imgs"'; do
  n=$(basename "$img" .img)
  mkdir -p /mnt/img /copy/$n
  # Sticks may be a bare filesystem or have a partition table (first partition).
  off=$(python3 -c "
import struct, sys
b = open(sys.argv[1], \"rb\").read(512)
p = b[446:462]
fat = b[54:59] == b\"FAT12\" or b[54:59] == b\"FAT16\" or b[82:87] == b\"FAT32\" or b[3:11] == b\"EXFAT   \" or b[3:7] == b\"NTFS\"
print(0 if fat or b[510:512] != b\"\x55\xaa\" or p[4] == 0 else struct.unpack(\"<I\", p[8:12])[0] * 512)" "$img")
  mount -o loop,ro,offset=$off "$img" /mnt/img
  cp -a /mnt/img/. /copy/$n/
  umount /mnt/img
done
cd /src
PYTHONPATH=desktop/lib python3 - <<EOF
import json
from pathlib import Path
from cherryos import transfer
sticks = {}
for d in sorted(Path("/copy").iterdir()):
    m = json.loads((d / "CHERRY-TRANSFER/cherry-transfer.json").read_text())
    sticks[m.get("part", 1)] = (d, m)
first = sticks[min(sticks)]
print("sticks:", {p: m.get("label") for p, (d, m) in sticks.items()}, "from", first[1].get("source_os"))
code = first[1]["code"]
got = transfer.restore(code, drive=first[0], home=Path("/restored"),
                       next_stick=lambda part, problem: sticks[part][0])
print({k: v for k, v in got.items() if k not in ("apps", "same")})
EOF
python3 scripts/move-test/check_files.py /hashes.txt /restored
stat -c "%a %n" /restored/.ssh /restored/.ssh/id_ed25519
if [ -f /restored/.mozilla/firefox/profiles.ini ]; then
  echo "== Firefox profiles.ini after restore:"
  cat /restored/.mozilla/firefox/profiles.ini
  ls /restored/.mozilla/firefox
fi
'
