#!/usr/bin/env bash
# Packages Cherry's own apps:
#   cherry-move_<v>_all.deb     Cherry Move alone, for Cherry OS and any Debian/Ubuntu/Mint
#   cherry-desktop_<v>_all.deb  everything else on Cherry OS (store, center, desktops, Windows runner)
# Other distros, Windows and macOS use the Cherry Move app in move/ (make move).
# Usage: scripts/build-debs.sh OUT_DIR
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
OUT="$(realpath -m "${1:-build/debs}")"
VERSION="$(sed -n 's/^__version__ = "\(.*\)"/\1/p' desktop/lib/cherryos/__init__.py)"
STAGE="$(mktemp -d)"
trap 'rm -rf "$STAGE"' EXIT
mkdir -p "$OUT"
rm -f "$OUT"/cherry-*.deb

MOVE_LIB=(__init__.py codes.py transfer.py secret_store.py system_extras.py remote.py desktops.py ui/__init__.py ui/move.py)
# Before 1.0.4, cherry-move's library lived in /usr/lib/cherry and cherry-desktop had the autostart entries.
SPLIT=1.0.4

# ---- cherry-move: its own copy of the library, so it installs next to cherry-store-client on other systems
M="$STAGE/cherry-move"
for f in "${MOVE_LIB[@]}"; do install -D -m 644 "desktop/lib/cherryos/$f" "$M/usr/lib/cherry-move/cherryos/$f"; done
install -D -m 755 desktop/bin/cherry-move "$M/usr/bin/cherry-move"
install -D -m 644 desktop/applications/cherry-move.desktop "$M/usr/share/applications/cherry-move.desktop"
install -D -m 644 desktop/autostart/cherry-move-*.desktop -t "$M/etc/xdg/autostart/"
install -D -m 644 desktop/icons/cherry-move.svg "$M/usr/share/icons/hicolor/scalable/apps/cherry-move.svg"
install -D -m 644 desktop/icons/cherry-logo.svg "$M/usr/share/icons/hicolor/scalable/apps/cherry-logo.svg"
mkdir -p "$M/DEBIAN"
cat > "$M/DEBIAN/control" <<EOF
Package: cherry-move
Version: $VERSION
Architecture: all
Maintainer: Cherry OS <hello@cherryos.invalid>
Depends: python3 (>= 3.9), python3-gi, gir1.2-gtk-4.0, gir1.2-adw-1 (>= 1.2), python3-cryptography, python3-qrcode, python3-pil, python3-zstandard, udisks2, xdg-user-dirs, ca-certificates
Recommends: rclone (>= 1.60), avahi-utils, sudo
Breaks: cherry-desktop (<< $SPLIT)
Replaces: cherry-desktop (<< $SPLIT)
Section: utils
Priority: optional
Description: Move your files to a new computer or Linux distro
 Cherry Move packs your documents, photos, browser data and more, encrypts them on your
 computer, and keeps them on USB sticks, your own cloud drive or a NAS until the new one
 brings them back.
EOF
dpkg-deb --root-owner-group --build "$M" "$OUT/cherry-move_${VERSION}_all.deb" >/dev/null

# ---- cherry-desktop
D="$STAGE/cherry-desktop"
(cd desktop/lib/cherryos && find . -name '*.py' -not -path '*/__pycache__/*') | while read -r f; do
  install -D -m 644 "desktop/lib/cherryos/${f#./}" "$D/usr/lib/cherry/cherryos/${f#./}"
done
for b in desktop/bin/*; do
  [[ -f $b && $(basename "$b") != cherry-move ]] || continue
  install -D -m 755 "$b" "$D/usr/bin/$(basename "$b")"
done
for a in desktop/applications/*.desktop; do
  [[ $(basename "$a") == cherry-move.desktop ]] && continue
  install -D -m 644 "$a" "$D/usr/share/applications/$(basename "$a")"
done
for a in desktop/autostart/*.desktop; do
  [[ $(basename "$a") == cherry-move-* ]] || install -D -m 644 "$a" "$D/etc/xdg/autostart/$(basename "$a")"
done
install -D -m 644 desktop/mimeapps.list "$D/etc/xdg/mimeapps.list"
install -D -m 644 desktop/polkit/*.policy -t "$D/usr/share/polkit-1/actions/"
install -D -m 644 desktop/systemd/*.service desktop/store-client/cherry-store-self-update.* -t "$D/usr/lib/systemd/system/"
install -D -m 644 desktop/icons/cherry-store.svg "$D/usr/share/icons/hicolor/scalable/apps/cherry-store.svg"
# Hyprland, which Cherry Center downloads on request: Cherry's settings, used once it's installed.
install -D -m 644 desktop/hyprland/hyprland.lua desktop/hyprland/hypridle.conf desktop/hyprland/hyprlock.conf \
  -t "$D/etc/xdg/hypr/"
install -D -m 755 desktop/hyprland/cherry-hyprland "$D/usr/lib/cherry/hyprland/cherry-hyprland"
install -D -m 644 desktop/hyprland/{waybar.jsonc,waybar.css,mako.conf,fuzzel.ini,shortcuts.txt} \
  -t "$D/usr/share/cherry/hyprland/"
# Its card in Cherry Center (the ISO's own desktops get their pictures from make-artwork.py).
install -D -m 644 artwork/screens/hyprland.png "$D/usr/share/cherry/screens/hyprland.png"
mkdir -p "$D/DEBIAN"
cat > "$D/DEBIAN/control" <<EOF
Package: cherry-desktop
Version: $VERSION
Architecture: all
Maintainer: Cherry OS <hello@cherryos.invalid>
Depends: cherry-move (>= $VERSION), python3 (>= 3.11), libnotify-bin, policykit-1 | pkexec, desktop-file-utils, dpkg, apt, ca-certificates
Recommends: wine, winetricks, timeshift, flatpak, gh
Breaks: cherry-move (<< $SPLIT)
Replaces: cherry-move (<< $SPLIT)
Section: admin
Priority: optional
Description: Cherry OS apps: Cherry Store, Cherry Center, desktop switcher, Windows app runner
 The cherry package manager and store client, the Cherry Center welcome and settings app,
 desktop previews and switching, and double-click support for Windows programs.
EOF
cat > "$D/DEBIAN/postinst" <<'EOF'
#!/bin/sh
set -e
if [ "$1" = configure ]; then
  systemctl enable cherry-live-desktop.service cherry-store-self-update.timer >/dev/null 2>&1 || true
  if [ -d /run/systemd/system ]; then
    systemctl daemon-reload >/dev/null 2>&1 || true
    systemctl start cherry-store-self-update.timer >/dev/null 2>&1 || true
  fi
  update-desktop-database -q /usr/share/applications 2>/dev/null || true
  gtk-update-icon-cache -q /usr/share/icons/hicolor 2>/dev/null || true
fi
EOF
cat > "$D/DEBIAN/prerm" <<'EOF'
#!/bin/sh
set -e
if [ "$1" = remove ]; then
  systemctl disable --now cherry-store-self-update.timer >/dev/null 2>&1 || true
fi
EOF
chmod 755 "$D/DEBIAN/postinst" "$D/DEBIAN/prerm"
dpkg-deb --root-owner-group --build "$D" "$OUT/cherry-desktop_${VERSION}_all.deb" >/dev/null

ls -1 "$OUT"
