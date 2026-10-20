#!/bin/sh
# Installs the Linux app from its package (.deb on Debian/Ubuntu, .rpm on Fedora) in a container, checks the
# menu entry and icons, then tests its own window (WebKitGTK) on a virtual screen.
#   scripts/move-test/linux-window-test.sh [IMAGE]      default debian:trixie; screenshots in build/window-test
set -e
cd "$(dirname "$0")/../.."
ls build/move/cherry-move-app_*_amd64.deb build/move/cherry-move-app-*.x86_64.rpm >/dev/null ||
  { echo "run make move first" >&2; exit 1; }
rm -rf build/window-test && mkdir -p build/window-test
exec docker run --rm -v "$PWD:$PWD:ro" -v "$PWD/build/window-test:/out" -w "$PWD" "${1:-debian:trixie}" sh -c '
  set -e
  if command -v apt-get >/dev/null; then
    export DEBIAN_FRONTEND=noninteractive
    apt-get update -qq
    apt-get install -y -qq --no-install-recommends xvfb openbox wmctrl xdotool imagemagick python3 \
      ca-certificates fonts-dejavu-core xdg-user-dirs desktop-file-utils >/dev/null
    apt-get install -y -qq ./build/move/cherry-move-app_*_amd64.deb >/dev/null
  else
    dnf install -y -q xorg-x11-server-Xvfb openbox wmctrl xdotool ImageMagick python3 dejavu-sans-fonts \
      xdg-user-dirs desktop-file-utils shadow-utils >/dev/null
    dnf install -y -q ./build/move/cherry-move-app-*.x86_64.rpm >/dev/null
  fi
  ok() { echo "PASS $1"; }
  bad() { echo "FAIL $1"; status=1; }
  status=0
  cherry-move-app --version | grep -q "Cherry Move" && ok "installed as cherry-move-app" || bad "cherry-move-app runs"
  desktop-file-validate /usr/share/applications/cherry-move-app.desktop && ok "menu entry is valid" ||
    bad "menu entry is valid"
  for s in scalable/apps/cherry-move-app.svg 48x48/apps/cherry-move-app.png 256x256/apps/cherry-move-app.png; do
    [ -f /usr/share/icons/hicolor/$s ] && ok "icon $s" || bad "icon $s"
  done
  useradd -m alex
  su alex -c "
    export HOME=/home/alex DISPLAY=:99
    Xvfb :99 -screen 0 1280x900x24 >/dev/null 2>&1 &
    sleep 1
    openbox >/dev/null 2>&1 &
    python3 scripts/move-test/make_files.py /home/alex --small >/dev/null
    python3 scripts/move-test/linux_window_test.py /usr/bin/cherry-move-app /tmp/out
  " || status=$?
  cp -r /tmp/out/. /out/
  exit $status
'
