#!/bin/sh
# Runs inside the chroot. Windows app support out of the box: ships Wine Mono (.NET) and Wine Gecko
# (web views) so the first Windows app needs no download or questions, and opens .exe/.msi with
# cherry-windows-run (desktop/mimeapps.list).
set -eu
command -v wine >/dev/null || exit 0

# The versions this Wine release asks for (see `strings` of its appwiz.cpl).
WINE_VER=$(dpkg-query -W -f='${Version}' wine | sed 's/~.*//; s/-.*//')
case "$WINE_VER" in
  10.*) MONO=9.4.0 GECKO=2.47.4 ;;
  9.*)  MONO=8.1.0 GECKO=2.47.4 ;;
  *)    MONO=9.4.0 GECKO=2.47.4 ;;
esac
fetch() {  # url dest-dir
  mkdir -p "$2"
  curl -fsSL --retry 3 "$1" | tar -xJ -C "$2" || echo "warning: couldn't download $1 (apps will offer to download it)" >&2
}
fetch "https://dl.winehq.org/wine/wine-mono/$MONO/wine-mono-$MONO-x86.tar.xz" /usr/share/wine/mono
fetch "https://dl.winehq.org/wine/wine-gecko/$GECKO/wine-gecko-$GECKO-x86.tar.xz" /usr/share/wine/gecko
fetch "https://dl.winehq.org/wine/wine-gecko/$GECKO/wine-gecko-$GECKO-x86_64.tar.xz" /usr/share/wine/gecko

update-desktop-database -q /usr/share/applications || true
update-mime-database /usr/share/mime || true
