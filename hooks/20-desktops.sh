#!/bin/sh
# Runs inside the chroot. One login screen (GDM) listing one session per desktop, Cherry colours and
# wallpaper on every desktop, and the live boot-menu desktop picker.
set -eu
WALL="/usr/share/backgrounds/$OS_ID"
ART="/usr/share/$OS_ID/artwork"

# ---- Sessions: hide the variants nobody needs so the login screen's list has one entry per desktop
# (plus X11 fallbacks for Plasma and GNOME). Diverted, so package upgrades don't bring them back.
for f in wayland-sessions/cinnamon-wayland wayland-sessions/gnome-classic wayland-sessions/gnome-classic-wayland \
         wayland-sessions/gnome-wayland wayland-sessions/xfce-wayland xsessions/cinnamon2d xsessions/gnome-classic \
         xsessions/gnome-classic-xorg xsessions/gnome; do
  path="/usr/share/$f.desktop"
  [ -e "$path" ] || continue
  dpkg-divert --local --rename --divert "$path.cherry-hidden" --add "$path"
done
python3 - <<'PY'
import sys
sys.path.insert(0, "/usr/lib/cherry")
from cherryos.desktops import DESKTOPS
for d in (d for d in DESKTOPS.values() if not d.on_demand):
    found = d.session()
    print(f"{d.label}: {found[0]} session '{found[1]}'" if found else f"warning: no session file for {d.label}",
          file=sys.stdout if found else sys.stderr)
PY
python3 -c "import sys; sys.path.insert(0,'/usr/lib/cherry'); from cherryos.desktops import write_system_default; write_system_default('$DEFAULT_DESKTOP')"

# ---- GDM look: cherry logo, accent and dark style. Debian's GDM ignores /etc/dconf/db/gdm; it
# compiles /usr/share/gdm/dconf/* into its own database every time it starts.
python3 -c "from PIL import Image; im = Image.open('$ART/logo.png'); im.thumbnail((96, 96)); im.save('$ART/gdm-logo.png')"
cat > /usr/share/gdm/dconf/95-cherry-settings <<EOF
[org/gnome/login-screen]
logo='$ART/gdm-logo.png'

[org/gnome/desktop/interface]
accent-color='red'
color-scheme='prefer-dark'

[org/gnome/desktop/peripherals/touchpad]
tap-to-click=true
EOF
# gdm3's postinst only links display-manager.service when debconf runs with a live system bus.
echo /usr/sbin/gdm3 > /etc/X11/default-display-manager
ln -sf /usr/lib/systemd/system/gdm.service /etc/systemd/system/display-manager.service
systemctl enable cherry-live-desktop.service

# ---- GNOME, Budgie and Cinnamon defaults (GSettings; users can still change everything).
cat > /usr/share/glib-2.0/schemas/90_cherry.gschema.override <<EOF
[org.gnome.desktop.background]
picture-uri='file://$WALL/cherry-dusk.png'
picture-uri-dark='file://$WALL/cherry-dusk.png'
picture-options='zoom'
primary-color='#26081e'

[org.gnome.desktop.screensaver]
picture-uri='file://$WALL/cherry-night.png'
picture-options='zoom'

[org.gnome.desktop.interface]
accent-color='red'
color-scheme='prefer-dark'
gtk-theme='Adwaita-dark'
font-name='Noto Sans 11'

[org.gnome.desktop.wm.preferences]
button-layout='appmenu:minimize,maximize,close'

[org.gnome.shell]
enabled-extensions=['dash-to-dock@micxgx.gmail.com', 'ubuntu-appindicators@ubuntu.com']
favorite-apps=['firefox-esr.desktop', 'org.gnome.Nautilus.desktop', 'cherry-store.desktop', 'cherry-center.desktop', 'org.gnome.Console.desktop', 'libreoffice-writer.desktop', 'org.gnome.TextEditor.desktop']

[org.gnome.shell.extensions.dash-to-dock]
dock-position='BOTTOM'
dash-max-icon-size=44
show-trash=false
click-action='minimize-or-previews'

[org.gnome.desktop.peripherals.touchpad]
tap-to-click=true

[org.cinnamon.desktop.background]
picture-uri='file://$WALL/cherry-dusk.png'
picture-options='zoom'

[org.cinnamon.desktop.interface]
gtk-theme='Adwaita-dark'

[org.cinnamon]
favorite-apps=['firefox-esr.desktop', 'cherry-store.desktop', 'cherry-center.desktop', 'nemo.desktop', 'org.gnome.Terminal.desktop']

[com.solus-project.budgie-panel]
dark-theme=true

[org.gnome.desktop.background:Budgie]
picture-uri='file://$WALL/cherry-dusk.png'
picture-uri-dark='file://$WALL/cherry-dusk.png'
picture-options='zoom'

[org.gnome.desktop.screensaver:Budgie]
picture-uri='file://$WALL/cherry-night.png'
EOF
# Budgie ships [schema:Budgie] overrides at priority 20, which beat plain ones regardless of file order.
if [ -f /usr/share/glib-2.0/schemas/org.x.apps.gschema.xml ]; then
  # Cinnamon, Xfce and Budgie get dark mode for modern (libadwaita) apps from the XApp portal.
  cat > /usr/share/glib-2.0/schemas/91_cherry-xapp.gschema.override <<'EOF'
[org.x.apps.portal]
color-scheme='prefer-dark'
EOF
  mkdir -p /etc/xdg/xdg-desktop-portal
  src=/usr/share/xdg-desktop-portal/xfce-portals.conf
  [ -f "$src" ] && { cat "$src"; echo 'org.freedesktop.impl.portal.Settings=xapp;gtk;'; } \
    > /etc/xdg/xdg-desktop-portal/xfce-portals.conf
  # Budgie calls itself "Budgie:GNOME", so any interface not listed here falls back to the GNOME
  # backend, which hangs outside GNOME Shell and takes the whole portal (and dark mode) down with it.
  cat > /etc/xdg/xdg-desktop-portal/budgie-portals.conf <<'EOF'
[preferred]
default=gtk;
org.freedesktop.impl.portal.Settings=xapp;gtk;
org.freedesktop.impl.portal.Wallpaper=xapp;gtk;
org.freedesktop.impl.portal.Screenshot=xapp;gtk;
org.freedesktop.impl.portal.Background=xapp;gtk;
org.freedesktop.impl.portal.Inhibit=xapp;gtk;
org.freedesktop.impl.portal.Lockdown=xapp;gtk;
org.freedesktop.impl.portal.Secret=gnome-keyring;
org.freedesktop.impl.portal.GlobalShortcuts=none;
org.freedesktop.impl.portal.Clipboard=none;
org.freedesktop.impl.portal.InputCapture=none;
org.freedesktop.impl.portal.RemoteDesktop=none;
org.freedesktop.impl.portal.ScreenCast=none;
org.freedesktop.impl.portal.Usb=none;
EOF
fi
glib-compile-schemas /usr/share/glib-2.0/schemas

# ---- Account picture for new users (the live user and the one the installer creates): a cherry, not
# desktop-base's Debian swirl. Diverted, so desktop-base upgrades don't bring the swirl back.
dpkg-divert --local --rename --divert /etc/skel/.face.debian --add /etc/skel/.face
python3 - "$ART/logo.png" /etc/skel/.face <<'PY'
import sys
from PIL import Image
face = Image.new("RGBA", (256, 256), (38, 8, 30, 255))
logo = Image.open(sys.argv[1]).convert("RGBA")
logo.thumbnail((190, 190), Image.LANCZOS)
face.alpha_composite(logo, ((256 - logo.width) // 2, (256 - logo.height) // 2))
face.convert("RGB").save(sys.argv[2], "PNG")
PY

# ---- KDE Plasma: Debian's look-and-feel with the Cherry wallpaper, dark Breeze and a cherry accent.
LNF=/usr/share/plasma/look-and-feel
if [ -d "$LNF/org.debian.desktop" ]; then
  rm -rf "$LNF/org.cherryos.desktop"
  cp -a "$LNF/org.debian.desktop" "$LNF/org.cherryos.desktop"
  python3 - "$LNF/org.cherryos.desktop" <<'PY'
import json, re, sys
from pathlib import Path
d = Path(sys.argv[1])
meta = json.loads((d / "metadata.json").read_text())
meta["KPlugin"]["Id"] = "org.cherryos.desktop"
meta["KPlugin"]["Name"] = "Cherry"
meta["KPlugin"]["Description"] = "Cherry OS"
(d / "metadata.json").write_text(json.dumps(meta, indent=2))
defaults = (d / "contents/defaults").read_text()
defaults = re.sub(r"(?m)^Image=.*$", "Image=CherryDusk", defaults)
defaults = re.sub(r"(?m)^ColorScheme=.*$", "ColorScheme=BreezeDark", defaults)
(d / "contents/defaults").write_text(defaults)
PY
  # Plasma's default panel, with the cherry as the menu button and Cherry's apps pinned.
  cat > "$LNF/org.cherryos.desktop/contents/layouts/org.kde.plasma.desktop-layout.js" <<'EOF'
var panel = new Panel
panel.height = 2 * Math.floor(gridUnit * 2.5 / 2)
const maximumAspectRatio = 21/9;
if (panel.formFactor === "horizontal") {
    const geo = screenGeometry(panel.screen);
    const maximumWidth = Math.ceil(geo.height * maximumAspectRatio);
    if (geo.width > maximumWidth) {
        panel.alignment = "center";
        panel.minimumLength = maximumWidth;
        panel.maximumLength = maximumWidth;
    }
}
var kickoff = panel.addWidget("org.kde.plasma.kickoff")
kickoff.currentConfigGroup = ["General"]
kickoff.writeConfig("icon", "cherry-logo")
panel.addWidget("org.kde.plasma.pager")
var tasks = panel.addWidget("org.kde.plasma.icontasks")
tasks.currentConfigGroup = ["General"]
tasks.writeConfig("launchers", ["applications:firefox-esr.desktop", "applications:org.kde.dolphin.desktop",
    "applications:cherry-store.desktop", "applications:cherry-center.desktop", "applications:org.kde.konsole.desktop",
    "applications:systemsettings.desktop"])
panel.addWidget("org.kde.plasma.marginsseparator")
panel.addWidget("org.kde.plasma.systemtray")
panel.addWidget("org.kde.plasma.digitalclock")
panel.addWidget("org.kde.plasma.showdesktop")

var desktopsArray = desktopsForActivity(currentActivity());
for (var j = 0; j < desktopsArray.length; j++) {
    desktopsArray[j].wallpaperPlugin = 'org.kde.image';
}
EOF
fi
# On Wayland KWin starts the input method itself; without this IBus pops up a warning at every login.
cat > /etc/xdg/kwinrc <<EOF
[Wayland]
InputMethod[\$e]=/usr/share/applications/org.freedesktop.IBus.Panel.Wayland.Gtk3.desktop
EOF
# ...and im-config must leave QT_IM_MODULE/GTK_IM_MODULE unset there, or IBus warns anyway.
sed -i 's/^DESKTOP_SETUP_IBUS=.*/DESKTOP_SETUP_IBUS="GNOME:KDE"/' /etc/default/im-config

# ---- Budgie: its icon task list keeps its pins under an ID made when the panel first starts, so a
# GSettings override can't reach it. Pin Cherry's apps from inside the first session instead.
cat > /usr/libexec/cherry-budgie-pins <<'EOF'
#!/bin/sh
done="${XDG_CONFIG_HOME:-$HOME/.config}/cherry/budgie-pinned"
[ -e "$done" ] && exit 0
p=/com/solus-project/budgie-panel
for _ in $(seq 60); do
  for id in $(dconf list "$p/applets/"); do
    [ "$(dconf read "$p/applets/${id}name")" = "'Icon Task List'" ] || continue
    schema="com.solus-project.icon-tasklist:$p/instance/icon-tasklist/$id"
    if [ "$(gsettings get "$schema" pinned-launchers)" = "@as []" ]; then
      gsettings set "$schema" pinned-launchers \
        "['firefox-esr.desktop', 'nemo.desktop', 'cherry-store.desktop', 'cherry-center.desktop', 'org.gnome.Terminal.desktop']"
      # The running panel only reads its pins when it starts.
      setsid budgie-panel --replace >/dev/null 2>&1 &
    fi
    mkdir -p "${done%/*}" && touch "$done"
    exit 0
  done
  sleep 1
done
EOF
chmod 755 /usr/libexec/cherry-budgie-pins
cat > /etc/xdg/autostart/cherry-budgie-pins.desktop <<'EOF'
[Desktop Entry]
Type=Application
Name=Cherry apps on the Budgie panel
Exec=/usr/libexec/cherry-budgie-pins
NoDisplay=true
OnlyShowIn=Budgie;
X-GNOME-Autostart-Delay=4
EOF

# Tray helpers that only Xfce needs (the other desktops have their own screen locker and Bluetooth).
for f in light-locker blueman; do
  file=/etc/xdg/autostart/$f.desktop
  [ -f "$file" ] || continue
  sed -i '/^OnlyShowIn=/d; /^NotShowIn=/d' "$file"
  echo 'OnlyShowIn=XFCE;' >> "$file"
done
# Cherry Center is the welcome app; plasma-desktop depends on Plasma Welcome, so turn off its launcher.
for rc in kded5rc kded6rc; do
  printf '[Module-kded_plasma-welcome]\nautoload=false\n' > "/etc/xdg/$rc"
done
cat > /etc/xdg/kdeglobals <<EOF
[General]
ColorScheme=BreezeDark
AccentColor=200,16,64
BrowserApplication=firefox-esr.desktop

[KDE]
LookAndFeelPackage=org.cherryos.desktop

[Icons]
Theme=breeze-dark
EOF
cat > /etc/xdg/plasmarc <<EOF
[Theme]
name=breeze-dark
EOF
cat > /etc/xdg/kscreenlockerrc <<EOF
[Greeter][Wallpaper][org.kde.image][General]
Image=file://$WALL/cherry-night.png
EOF
# DrKonqi's crash socket is ConditionUser=!@system but also UpheldBy=sockets.target, so in the login
# screen's own session systemd retries it every 10 seconds forever, flooding the journal. WantedBy still
# starts it for people's sessions.
rm -f /etc/systemd/user/sockets.target.upholds/drkonqi-coredump-launcher.socket

# ---- XFCE: wallpaper for whichever monitor and workspace names xfdesktop 4.20 uses (see reference notes).
XFD=/etc/xdg/xfce4/xfconf/xfce-perchannel-xml
mkdir -p "$XFD"
{
  echo '<?xml version="1.0" encoding="UTF-8"?>'
  echo '<channel name="xfce4-desktop" version="1.0"><property name="backdrop" type="empty"><property name="screen0" type="empty">'
  for mon in monitor0 monitor1 monitorVirtual-1 monitorVirtual1 monitorHDMI-1 monitorHDMI-A-1 monitoreDP-1 monitorDP-1 monitorVGA-1; do
    echo "<property name=\"$mon\" type=\"empty\">"
    for ws in 0 1 2 3; do
      echo "<property name=\"workspace$ws\" type=\"empty\"><property name=\"last-image\" type=\"string\" value=\"$WALL/cherry-dusk.png\"/><property name=\"image-style\" type=\"int\" value=\"5\"/><property name=\"color-style\" type=\"int\" value=\"0\"/></property>"
    done
    echo '</property>'
  done
  echo '</property></property></channel>'
} > "$XFD/xfce4-desktop.xml"
# Pin Cherry Store and Cherry Center on the dock, after the web browser.
python3 - /etc/xdg/xfce4/panel/default.xml <<'PY'
import re, sys
from pathlib import Path
p = Path(sys.argv[1])
xml = p.read_text()
browser = re.search(r'<property name="plugin-(\d+)" type="string" value="launcher">\s*<property name="items" type="array">\s*'
                    r'<value type="string" value="xfce4-web-browser.desktop"/>\s*</property>\s*</property>', xml)
if "cherry-store.desktop" in xml:
    pass
elif browser:
    used = [int(n) for n in re.findall(r'name="plugin-(\d+)"', xml)]
    ids = [max(used) + 1, max(used) + 2]
    launchers = "".join(f'\n    <property name="plugin-{i}" type="string" value="launcher">\n      <property name="items" type="array">\n'
                        f'        <value type="string" value="{app}"/>\n      </property>\n    </property>'
                        for i, app in zip(ids, ("cherry-store.desktop", "cherry-center.desktop")))
    xml = xml[:browser.end()] + launchers + xml[browser.end():]
    anchor = f'<value type="int" value="{browser.group(1)}"/>'
    xml = xml.replace(anchor, anchor + "".join(f'\n        <value type="int" value="{i}"/>' for i in ids), 1)
    p.write_text(xml)
else:
    print("warning: Xfce's default panel has no web browser launcher; Cherry's apps aren't pinned", file=sys.stderr)
PY
if [ -f "$XFD/xsettings.xml" ]; then
  sed -i -e 's|name="ThemeName" type="string" value="[^"]*"|name="ThemeName" type="string" value="Adwaita-dark"|' \
         -e 's|name="IconThemeName" type="string" value="[^"]*"|name="IconThemeName" type="string" value="Adwaita"|' \
         "$XFD/xsettings.xml"
fi
