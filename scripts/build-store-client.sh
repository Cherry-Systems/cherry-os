#!/usr/bin/env bash
# Packages just the Cherry Store client, for other Debian-based systems:
#   cherry-store-client_<v>_all.deb   the cherry command, the Cherry Store app, its polkit rule and a
#                                     daily timer that updates the app itself from the store
# Cherry OS itself gets the same files from cherry-desktop (scripts/build-debs.sh).
# Usage: scripts/build-store-client.sh OUT_DIR
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
OUT="$(realpath -m "${1:-build/debs}")"
VERSION="$(sed -n 's/^__version__ = "\(.*\)"/\1/p' desktop/lib/cherryos/__init__.py)"
S="$(mktemp -d)"
trap 'rm -rf "$S"' EXIT
chmod 755 "$S"  # it becomes "/" when installed
mkdir -p "$OUT"
rm -f "$OUT"/cherry-store-client_*.deb

for f in __init__.py pkg.py cloud.py ui/__init__.py ui/store.py; do
  install -D -m 644 "desktop/lib/cherryos/$f" "$S/usr/lib/cherry/cherryos/$f"
done
install -D -m 755 desktop/bin/cherry "$S/usr/bin/cherry"
install -D -m 755 desktop/bin/cherry-store "$S/usr/bin/cherry-store"
install -D -m 644 desktop/applications/cherry-store.desktop "$S/usr/share/applications/cherry-store.desktop"
install -D -m 644 desktop/icons/cherry-store.svg "$S/usr/share/icons/hicolor/scalable/apps/cherry-store.svg"
install -D -m 644 desktop/polkit/org.cherryos.cherry.policy "$S/usr/share/polkit-1/actions/org.cherryos.cherry.policy"
install -D -m 644 desktop/store-client/cherry-store-self-update.service desktop/store-client/cherry-store-self-update.timer \
  -t "$S/usr/lib/systemd/system/"
mkdir -p "$S/DEBIAN"
cat > "$S/DEBIAN/control" <<EOF
Package: cherry-store-client
Version: $VERSION
Architecture: all
Maintainer: Cherry OS <hello@cherryos.invalid>
Depends: python3 (>= 3.10), python3-cryptography, python3-gi, gir1.2-gtk-4.0, gir1.2-adw-1, dpkg, apt, ca-certificates, pkexec | policykit-1
Recommends: sudo
Suggests: gh
Conflicts: cherry-desktop
Section: admin
Priority: optional
Homepage: https://cherry-systems.github.io/store/
Description: Cherry Store: apps published by anyone, for Debian-based Linux
 Browse, install, update and remove apps from the Cherry Store. Every app is an
 ordinary Debian package, signed by its publisher and virus-scanned, and installed
 with apt. Includes the Cherry Store app and the cherry command, and keeps itself
 up to date.
EOF
cat > "$S/DEBIAN/postinst" <<'EOF'
#!/bin/sh
set -e
if [ "$1" = configure ]; then
  update-desktop-database -q /usr/share/applications 2>/dev/null || true
  gtk-update-icon-cache -q /usr/share/icons/hicolor 2>/dev/null || true
  systemctl enable cherry-store-self-update.timer >/dev/null 2>&1 || true
  if [ -d /run/systemd/system ]; then
    systemctl daemon-reload >/dev/null 2>&1 || true
    systemctl start cherry-store-self-update.timer >/dev/null 2>&1 || true
  fi
fi
EOF
cat > "$S/DEBIAN/prerm" <<'EOF'
#!/bin/sh
set -e
if [ "$1" = remove ]; then
  systemctl disable --now cherry-store-self-update.timer >/dev/null 2>&1 || true
fi
EOF
chmod 755 "$S/DEBIAN/postinst" "$S/DEBIAN/prerm"
dpkg-deb --root-owner-group --build "$S" "$OUT/cherry-store-client_${VERSION}_all.deb" >/dev/null
ls -1 "$OUT"/cherry-store-client_*.deb
