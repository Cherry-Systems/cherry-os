#!/usr/bin/env bash
# Remakes scripts/os-kit/test-transfer-stick.img.gz: a 64 MB FAT USB stick image holding a real Cherry Move transfer
# of four sample files, sent from a computer called "sams-old-pc". It has a new code each time, so update the code in
# docs/ADDING-CHERRY-APPS.md afterwards. Needs Docker and a built kit (scripts/make-os-kit.sh).
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/../.."
[[ -x build/cherry-kit/install-cherry-apps.sh ]] || { echo "Run scripts/make-os-kit.sh first" >&2; exit 1; }
OUT="$(mktemp -d)"
trap 'rm -rf "$OUT"' EXIT
docker run --rm --network host --hostname sams-old-pc -v "$PWD/build/cherry-kit:/kit:ro" -v "$OUT:/out" debian:trixie bash -c '
set -e
apt-get update -qq >/dev/null
/kit/install-cherry-apps.sh / >/dev/null 2>&1
apt-get install -y -qq mtools dosfstools >/dev/null 2>&1
export HOME=/home/sam
mkdir -p $HOME/Documents/School $HOME/Pictures $HOME/Desktop /usb
printf "Cherry Move test file.\nIf you can read this on the new computer, the transfer worked.\n" > $HOME/Documents/cherry-move-test.txt
printf "Science homework: due Friday.\n" > $HOME/Documents/School/homework.txt
printf "Shopping list: milk, bread, cherries.\n" > $HOME/Desktop/shopping-list.txt
python3 -c "from PIL import Image; Image.new(\"RGB\", (320, 200), (200, 16, 64)).save(\"$HOME/Pictures/red-square.png\")"
cherry-move send --to usb --usb /usb --only documents,pictures,desktop
mkfs.vfat -C /out/stick.img 65536 -n OLDPC >/dev/null
mcopy -s -i /out/stick.img /usb/* ::
chown -R '"$(id -u):$(id -g)"' /out'
gzip -9c "$OUT/stick.img" > scripts/os-kit/test-transfer-stick.img.gz
echo "Wrote scripts/os-kit/test-transfer-stick.img.gz. Put the code printed above into docs/ADDING-CHERRY-APPS.md."
