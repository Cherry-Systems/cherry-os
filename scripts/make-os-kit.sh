#!/usr/bin/env bash
# Packs what another OS builder needs to include the Cherry Store and Cherry Move, built from this tree, so they're
# the same version and the same files as Cherry OS's own:
#   build/cherry-kit/
#     START-HERE.md                docs/ADDING-CHERRY-APPS.md
#     install-cherry-apps.sh       installs the two packages into the builder's image
#     check-cherry-apps.sh         checks the image (or a VM of it) has exactly Cherry OS's files
#     add-cherrymove-to-iso.sh     puts the old-computer app on the ISO, as files
#     install-into-image/          cherry-move_<v>_all.deb, cherry-store-client_<v>_all.deb, SHA256SUMS
#     for-the-usb-stick/CherryMove.tar   the Cherry Move app for old computers (from `make move`)
#     testing/test-transfer-stick.img    a USB stick image with a sample transfer (scripts/os-kit/make-test-stick.sh)
#     docs/                        CHERRY-STORE-SETUP.md, CHERRY-MOVE-SETUP.md
#   build/cherry-kit.tar.gz        the same, in one file
# Usage: scripts/make-os-kit.sh
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
VERSION="$(sed -n 's/^__version__ = "\(.*\)"/\1/p' desktop/lib/cherryos/__init__.py)"
KIT=build/cherry-kit
STAGE="$(mktemp -d)"
trap 'rm -rf "$STAGE"' EXIT
[[ -f build/move/CherryMove-Setup.exe ]] || { echo "No build/move/CherryMove-Setup.exe: run make move first" >&2; exit 1; }
rm -rf "$KIT" build/cherry-kit.tar.gz
mkdir -p "$KIT/install-into-image" "$KIT/for-the-usb-stick" "$KIT/docs"

scripts/build-debs.sh "$STAGE/debs" >/dev/null
scripts/build-store-client.sh "$STAGE/debs" >/dev/null
cp "$STAGE/debs/cherry-move_${VERSION}_all.deb" "$STAGE/debs/cherry-store-client_${VERSION}_all.deb" "$KIT/install-into-image/"
(cd "$KIT/install-into-image" && sha256sum ./*.deb | sed 's| \./| |' > SHA256SUMS)

for d in "$KIT"/install-into-image/*.deb; do
  pkg="$(dpkg-deb -f "$d" Package)"
  mkdir "$STAGE/$pkg"
  dpkg-deb -x "$d" "$STAGE/$pkg"
  (cd "$STAGE/$pkg" && find . -type f | sort | sed 's|^\./||' | xargs sha256sum | sed "s|^|$pkg |") >> "$STAGE/sums"
done

for s in install-cherry-apps.sh check-cherry-apps.sh add-cherrymove-to-iso.sh; do
  python3 - "scripts/os-kit/$s" "$KIT/$s" "$VERSION" "$STAGE/sums" <<'PY'
import sys
src, dst, version, sums = sys.argv[1:]
text = open(src).read().replace("@VERSION@", version).replace("@FILE_SUMS@", open(sums).read().rstrip("\n"))
open(dst, "w").write(text)
PY
  chmod 755 "$KIT/$s"
done

mkdir "$STAGE/CherryMove"
cp build/move/CherryMove-Setup.exe build/move/CherryMove.dmg build/move/*.deb build/move/*.rpm \
  build/move/cherry-move-linux-* move/README.txt "$STAGE/CherryMove/"
tar -C "$STAGE" -cf "$KIT/for-the-usb-stick/CherryMove.tar" CherryMove

mkdir -p "$KIT/testing"
gunzip -c scripts/os-kit/test-transfer-stick.img.gz > "$KIT/testing/test-transfer-stick.img"

cp docs/ADDING-CHERRY-APPS.md "$KIT/START-HERE.md"
cp docs/CHERRY-STORE-SETUP.md docs/CHERRY-MOVE-SETUP.md "$KIT/docs/"
sed -i 's#](CHERRY-#](docs/CHERRY-#g' "$KIT/START-HERE.md"
tar -C build -czf build/cherry-kit.tar.gz cherry-kit
echo "$KIT/ and build/cherry-kit.tar.gz (Cherry OS $VERSION):"
(cd "$KIT" && find . -type f | sort)
