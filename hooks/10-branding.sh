#!/bin/sh
# Runs inside the chroot. Replaces Debian's identity with Cherry OS and registers the Cherry wallpaper
# theme with desktop-base, which every desktop reads its default background from.
set -eu
ART="/usr/share/$OS_ID/artwork"
WALL="/usr/share/backgrounds/$OS_ID"

# Divert so Debian's base-files upgrades don't put its os-release back.
dpkg-divert --local --rename --add /usr/lib/os-release
cat > /usr/lib/os-release <<EOF
PRETTY_NAME="$OS_NAME $OS_VERSION ($OS_CODENAME)"
NAME="$OS_NAME"
VERSION="$OS_VERSION ($OS_CODENAME)"
VERSION_ID="$OS_VERSION"
ID=$OS_ID
ID_LIKE=debian
VERSION_CODENAME=$DEBIAN_SUITE
HOME_URL="https://cherry-systems.github.io/os/"
SUPPORT_END=2030-06-30
LOGO=cherry-logo
EOF
ln -sf ../usr/lib/os-release /etc/os-release
printf '%s %s \\n \\l\n\n' "$OS_NAME" "$OS_VERSION" > /etc/issue
printf '%s %s\n' "$OS_NAME" "$OS_VERSION" > /etc/issue.net
cat > /etc/motd <<EOF

   Welcome to $OS_NAME $OS_VERSION "$OS_CODENAME" (built on Debian $DEBIAN_SUITE).
   Apps: cherry search / cherry install NAME     Files: cherry-move     Help: cherry-center

EOF

# Logo under every icon name menus, installers and "About" dialogs look up.
for size in 16 22 24 32 48 64 128 256; do
  dir="/usr/share/icons/hicolor/${size}x${size}/apps"
  mkdir -p "$dir"
  python3 -c "from PIL import Image; Image.open('$ART/logo.png').resize(($size,$size), Image.LANCZOS).save('$dir/cherry-logo.png')"
done
for name in distributor-logo start-here debian-logo debian-swirl org.xfce.panel.applicationsmenu xfce4-panel-menu \
            cinnamon-symbolic-menu budgie-menu-symbolic kde-start-here start-here-kde; do
  ln -sf cherry-logo.svg "/usr/share/icons/hicolor/scalable/apps/$name.svg"
  for size in 16 22 24 32 48 64 128 256; do
    ln -sf cherry-logo.png "/usr/share/icons/hicolor/${size}x${size}/apps/$name.png"
  done
done
cp "$ART/logo.png" /usr/share/pixmaps/cherry-logo.png
gtk-update-icon-cache -f -q /usr/share/icons/hicolor || true

# ---- desktop-base theme: wallpaper, lock screen, login background, GRUB
THEME=/usr/share/desktop-base/cherry-theme
svg_wrap() {  # PNG -> SVG that embeds it (desktop-base and GNOME expect .svg names)
  python3 - "$1" "$2" <<'PY'
import base64, sys
from PIL import Image
w, h = Image.open(sys.argv[1]).size
data = base64.b64encode(open(sys.argv[1], "rb").read()).decode()
open(sys.argv[2], "w").write(f'<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" '
    f'width="{w}" height="{h}" viewBox="0 0 {w} {h}"><image width="{w}" height="{h}" '
    f'xlink:href="data:image/png;base64,{data}"/></svg>')
PY
}
for part in wallpaper lockscreen; do
  img="$WALL/cherry-dusk.png"
  [ "$part" = lockscreen ] && img="$WALL/cherry-night.png"
  mkdir -p "$THEME/$part/contents/images"
  cp "$img" "$THEME/$part/contents/images/2560x1440.png"
  for res in 1920x1080 1920x1200 1280x1024 1280x800 2520x1080 1080x2160; do
    svg_wrap "$img" "$THEME/$part/contents/images/$res.svg"
  done
  cat > "$THEME/$part/metadata.json" <<EOF
{ "KPlugin": { "Authors": [{ "Name": "Cherry OS" }], "Id": "Cherry$part", "Name": "Cherry", "License": "CC-BY-SA-4.0" } }
EOF
  cat > "$THEME/$part/gnome-background.xml" <<EOF
<background><static><duration>8640000.0</duration><file>$img</file></static></background>
EOF
done
mkdir -p "$THEME/login" "$THEME/grub"
svg_wrap "$WALL/cherry-night.png" "$THEME/login/background.svg"
cp "$THEME/login/background.svg" "$THEME/login/background-nologo.svg"
cp "$ART/grub.png" "$THEME/grub/grub-4x3.png"
cp "$ART/grub.png" "$THEME/grub/grub-16x9.png"
update-alternatives --install /usr/share/desktop-base/active-theme desktop-theme "$THEME" 100
update-alternatives --set desktop-theme "$THEME"
update-alternatives --install /usr/share/images/desktop-base/desktop-background desktop-background \
  "$THEME/wallpaper/contents/images/1920x1080.svg" 100
update-alternatives --set desktop-background "$THEME/wallpaper/contents/images/1920x1080.svg"

# Every Cherry wallpaper listed in each desktop's own wallpaper picker.
mkdir -p /usr/share/gnome-background-properties /usr/share/mate-background-properties
{
  echo '<?xml version="1.0"?><!DOCTYPE wallpapers SYSTEM "gnome-wp-list.dtd"><wallpapers>'
  for f in "$WALL"/*.png; do
    n=$(basename "$f" .png | sed 's/cherry-//; s/.*/Cherry \u&/')
    echo "<wallpaper deleted=\"false\"><name>$n</name><filename>$f</filename><options>zoom</options></wallpaper>"
  done
  echo '</wallpapers>'
} > /usr/share/gnome-background-properties/cherry.xml
cp /usr/share/gnome-background-properties/cherry.xml /usr/share/mate-background-properties/cherry.xml
for f in "$WALL"/*.png; do
  id="Cherry$(basename "$f" .png | sed 's/cherry-//; s/.*/\u&/')"
  mkdir -p "/usr/share/wallpapers/$id/contents/images"
  ln -sf "$f" "/usr/share/wallpapers/$id/contents/images/2560x1440.png"
  printf '{ "KPlugin": { "Id": "%s", "Name": "%s", "Authors": [{ "Name": "Cherry OS" }] } }\n' "$id" "$id" \
    > "/usr/share/wallpapers/$id/metadata.json"
done

# Installed systems' boot menu.
mkdir -p /etc/default/grub.d
cat > /etc/default/grub.d/cherry.cfg <<EOF
GRUB_DISTRIBUTOR="$OS_NAME"
GRUB_BACKGROUND="$ART/grub.png"
GRUB_GFXMODE=1024x768,auto
# Start whatever was picked last, so a Windows update that restarts the computer goes back to Windows.
GRUB_DEFAULT=saved
GRUB_SAVEDEFAULT=true
EOF
