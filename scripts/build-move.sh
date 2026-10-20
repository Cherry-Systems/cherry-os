#!/bin/sh
# Builds the Cherry Move app for the old computer into build/move/:
#   CherryMove-Setup.exe                Windows 10 and 11 installer (Intel/AMD and ARM PCs)
#   CherryMove.dmg                      macOS 11 or newer (Apple silicon and Intel)
#   cherry-move-app_<v>_<arch>.deb      Debian, Ubuntu, Mint and friends
#   cherry-move-app-<v>-1.<arch>.rpm    Fedora, openSUSE and friends
#   cherry-move-linux-x86_64, -arm64    any other Linux: runs as it is
# Needs Go and Docker (the installers are made in a container, see scripts/move-packaging).
set -e
cd "$(dirname "$0")/.."
ROOT="$PWD"
OUT="$ROOT/build/move"
PARTS="$OUT/parts"
TOOLS="$ROOT/build/tools"
VERSION="${VERSION:-1.0}"
rm -rf "$OUT"
mkdir -p "$PARTS" "$TOOLS"

cp desktop/lib/cherryos/system_extras.py move/system_extras.py
cd move
# The Windows icon, version details and manifest (DPI-aware, modern controls).
go run github.com/tc-hib/go-winres@v0.3.3 make --in winres/winres.json --arch amd64,arm64 \
  --product-version "$VERSION" --file-version "$VERSION"
build() { # GOOS GOARCH OUTPUT [extra ldflags]
  CGO_ENABLED=0 GOOS=$1 GOARCH=$2 go build -trimpath -ldflags "-s -w -X main.version=$VERSION $4" -o "$3" .
}
build windows amd64 "$PARTS/CherryMove.exe" -H=windowsgui
build windows arm64 "$PARTS/CherryMove-arm64.exe" -H=windowsgui
build darwin amd64 "$PARTS/CherryMove-darwin-amd64"
build darwin arm64 "$PARTS/CherryMove-darwin-arm64"
build linux amd64 "$OUT/cherry-move-linux-x86_64"
build linux arm64 "$OUT/cherry-move-linux-arm64"
cd "$ROOT"

# rcodesign signs the Mac app from Linux.
RCODESIGN_VERSION=0.29.0
rcodesign="$TOOLS/rcodesign-$RCODESIGN_VERSION"
if [ ! -x "$rcodesign" ]; then
  tarball="apple-codesign-$RCODESIGN_VERSION-$(uname -m)-unknown-linux-musl"
  url="https://github.com/indygreg/apple-platform-rs/releases/download/apple-codesign%2F$RCODESIGN_VERSION/$tarball.tar.gz"
  tmp=$(mktemp -d)
  curl -fsSL -o "$tmp/r.tar.gz" "$url"
  echo "$(curl -fsSL "$url.sha256" | cut -d' ' -f1)  $tmp/r.tar.gz" | sha256sum -c --quiet
  tar -xzf "$tmp/r.tar.gz" -C "$tmp"
  mv "$tmp/$tarball/rcodesign" "$rcodesign"
  rm -rf "$tmp"
fi
# Microsoft's small WebView2 installer, run by Setup on the few PCs that don't have WebView2 yet.
webview2="$TOOLS/MicrosoftEdgeWebview2Setup.exe"
if [ ! -s "$webview2" ]; then
  curl -fsSL -o "$webview2.tmp" "https://go.microsoft.com/fwlink/p/?LinkId=2124703"
  mv "$webview2.tmp" "$webview2"
fi
cp "$webview2" "$PARTS/"

docker build -q -t cherry-move-packager scripts/move-packaging >/dev/null
docker run --rm -u "$(id -u):$(id -g)" -e VERSION="$VERSION" -v "$ROOT:/src:ro" -v "$OUT:/out" -v "$PARTS:/parts" \
  -v "$rcodesign:/rcodesign:ro" cherry-move-packager sh /src/scripts/move-packaging/package.sh

echo "Linux packages"
for arch in amd64 arm64; do
  bin=$([ $arch = amd64 ] && echo x86_64 || echo arm64)
  cat > "$PARTS/nfpm-$arch.yaml" <<EOF
name: cherry-move-app
arch: $arch
platform: linux
version: $VERSION
release: 1
section: utils
priority: optional
maintainer: Cherry OS <hello@cherryos.invalid>
vendor: Cherry OS
description: |-
  Bring your files to Cherry OS
  Cherry Move packs up your documents, photos, music, videos, desktop, downloads and browser
  bookmarks, compressed and encrypted, onto USB sticks, your own cloud drive or a NAS, so
  Cherry OS can bring them back on your new computer. Nothing on this computer is changed.
contents:
  - src: $OUT/cherry-move-linux-$bin
    dst: /usr/bin/cherry-move-app
    file_info: {mode: 0755}
  - src: $ROOT/scripts/move-packaging/cherry-move-app.desktop
    dst: /usr/share/applications/cherry-move-app.desktop
    file_info: {mode: 0644}
  - src: $ROOT/desktop/icons/cherry-move.svg
    dst: /usr/share/icons/hicolor/scalable/apps/cherry-move-app.svg
    file_info: {mode: 0644}
EOF
  for size in 32 48 64 128 256 512; do
    printf '  - src: %s\n    dst: %s\n' "$PARTS/art/icon-$size.png" \
      "/usr/share/icons/hicolor/${size}x$size/apps/cherry-move-app.png" >> "$PARTS/nfpm-$arch.yaml"
  done
  cat >> "$PARTS/nfpm-$arch.yaml" <<EOF
overrides:
  deb:
    recommends: [libwebkit2gtk-4.1-0, udisks2, xdg-utils]
  rpm:
    recommends: [webkit2gtk4.1, libwebkit2gtk-4_1-0, udisks2, xdg-utils]
EOF
  for format in deb rpm; do
    go run github.com/goreleaser/nfpm/v2/cmd/nfpm@v2.47.0 package -f "$PARTS/nfpm-$arch.yaml" -p $format -t "$OUT/"
  done
done
ls -l "$OUT"
