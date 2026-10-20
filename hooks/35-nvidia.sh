#!/bin/sh
# Runs inside the chroot. Nvidia graphics work from the first boot: the live system has Nvidia's current driver
# (from Nvidia's own Debian repository: GTX 16 and RTX 20 cards onwards, including ones newer than Debian's
# driver), built for the live kernel. Older cards get the free driver (nouveau) live; when installing, GTX 900 and
# 1000 cards are switched to Debian's 550 driver (kept on the ISO), cards no Nvidia driver supports keep nouveau,
# and computers without an Nvidia card lose the driver.
# Secure Boot only loads drivers signed by a key the firmware trusts, so with it on the live system uses nouveau,
# and the installed driver is signed with a key made for that computer, approved once on the blue MOK screen at the
# first restart. Whenever Nvidia's driver can't take a card, nouveau does, so the screen never stays black.
set -eu

CODE=12345678  # typed on the MOK screen, whose keyboard is always US: digits only
NV_REPO=https://developer.download.nvidia.com/compute/cuda/repos/debian13/x86_64

# ---- Nvidia's repository, for Nvidia's driver packages only (everything else stays Debian's).
curl -fsSL -o /tmp/cuda-keyring.deb "$NV_REPO/cuda-keyring_1.1-1_all.deb"
dpkg -i /tmp/cuda-keyring.deb
rm /tmp/cuda-keyring.deb
cat > /etc/apt/preferences.d/cherry-nvidia <<'EOF'
# Cherry OS: only Nvidia's driver comes from Nvidia's repository, never its other packages.
Package: *
Pin: origin developer.download.nvidia.com
Pin-Priority: 100

Package: nvidia-* libnvidia-* libcuda* libnvcuvid* libnvoptix* libxnvctrl* xserver-xorg-video-nvidia* firmware-nvidia-gsp* libegl-nvidia* libgles-nvidia* libglx-nvidia*
Pin: origin developer.download.nvidia.com
Pin-Priority: 600
EOF
apt-get update
mkdir -p /usr/share/cherry/nvidia-ids
# Nvidia's packages conflict with nvidia-detect, so keep its lists of which card each driver supports.
cp /usr/share/nvidia/*.ids /usr/share/cherry/nvidia-ids/
test -s /usr/share/cherry/nvidia-ids/nvidia-open.ids
dpkg-query -W -f='${Package}\n' | sort > /tmp/before
apt-get install -y nvidia-open linux-headers-amd64
dpkg-query -W -f='${Package}\n' | sort | comm -13 /tmp/before - > /usr/share/cherry/nvidia-live-packages
rm /tmp/before
: > /usr/share/cherry/nvidia-live-nvidia-packages
for p in $(cat /usr/share/cherry/nvidia-live-packages); do
  apt-cache policy "$p" | grep -A1 '^ \*\*\*' | grep -q developer.download.nvidia.com &&
    echo "$p" >> /usr/share/cherry/nvidia-live-nvidia-packages
done
grep -qx nvidia-open /usr/share/cherry/nvidia-live-nvidia-packages
# Build it for the live kernel (dkms otherwise builds for the kernel the ISO was built on).
for k in /lib/modules/*; do
  if [ -d "$k/build" ]; then dkms autoinstall -k "${k##*/}"; fi
done
ls /lib/modules/*/updates/dkms/nvidia.ko* >/dev/null
# The cards Nvidia's current driver supports: those its list doesn't send to an older (legacy) driver.
# (nvidia-detect's nvidia-open.ids only has the data centre cards that need the open driver.)
python3 - <<'EOF'
import gzip, json
chips = json.load(gzip.open("/usr/share/doc/nvidia-driver/supported-gpus.json.gz"))["chips"]
ids = sorted({"10DE" + c["devid"][2:].upper() for c in chips if "legacybranch" not in c})
assert "10DE2487" in ids and "10DE1B80" not in ids  # RTX 3060 yes, GTX 1080 no
open("/usr/share/cherry/nvidia-ids/nvidia-615.ids", "w").write("\n".join(ids) + "\n")
EOF
# With DKMS drivers and Secure Boot on, shim-signed (added by the installer, then updated automatically) asks
# whether to turn Secure Boot checking off, and fails when nobody can answer. The MOK screen approves the key instead.
echo "shim-signed-common shim/disable_secureboot boolean false" | debconf-set-selections
# dkms signed it with a key it made just now. That key must never ship: anyone with it could sign drivers every
# computer that approved it would load. The installer builds and signs it again with a key of the computer's own.
rm -f /var/lib/dkms/mok.key /var/lib/dkms/mok.pub

# The persistence daemon and Dynamic Boost need a working Nvidia card; don't start them (and fail) without one.
for unit in nvidia-persistenced nvidia-powerd; do
  mkdir -p /etc/systemd/system/$unit.service.d
  printf '[Unit]\nAfter=cherry-nvidia-fallback.service\nConditionPathExists=/dev/nvidiactl\n' \
    > /etc/systemd/system/$unit.service.d/cherry.conf
done

# ---- At every start, before the login screen: the free driver for any Nvidia card Nvidia's didn't take.
cat > /usr/libexec/cherry-nvidia-fallback <<'EOF'
#!/bin/sh
# Nvidia's driver turns the free one (nouveau) off. For each Nvidia card left without a driver (Secure Boot refused
# Nvidia's, the card is too old or too new for it, or a kernel update left it unbuilt), load the free one.
udevadm settle -t 15
cards=
for d in /sys/bus/pci/devices/*; do
  [ "$(cat $d/vendor 2>/dev/null)" = 0x10de ] || continue
  case $(cat $d/class 2>/dev/null) in 0x03*) ;; *) continue ;; esac
  [ -e $d/driver ] || cards="$cards $d"
done
[ -n "$cards" ] || exit 0
err=$(modprobe nvidia-drm 2>&1)
udevadm settle -t 15
for d in $cards; do
  [ -e $d/driver ] && continue
  modprobe nouveau
  mkdir -p /run/cherry
  case $err in
    *"Key was rejected"*) echo key ;;
    *"No such device"*|"") echo unsupported ;;
    *) echo other ;;
  esac > /run/cherry/nvidia-fallback
  break
done
udevadm settle -t 15
EOF
chmod 755 /usr/libexec/cherry-nvidia-fallback
cat > /etc/systemd/system/cherry-nvidia-fallback.service <<'EOF'
[Unit]
Description=Use the free graphics driver for Nvidia cards Nvidia's driver can't take
After=systemd-modules-load.service systemd-udev-trigger.service
Before=display-manager.service

[Service]
Type=oneshot
ExecStart=/usr/libexec/cherry-nvidia-fallback

[Install]
WantedBy=graphical.target
EOF
systemctl enable cherry-nvidia-fallback.service

# ---- While installing (Calamares, in the new system, offline: Debian's 550 driver is on the ISO).
cat > /usr/libexec/cherry-nvidia-install <<'EOF'
#!/bin/sh
# Keeps Nvidia's driver, switches to Debian's 550, or removes it, for the Nvidia cards in this computer.
# "cherry.nvidia=615" or "=550" on the live session's kernel command line pretends to have such a card (tests).
set -u
export DEBIAN_FRONTEND=noninteractive
IDS=/usr/share/cherry/nvidia-ids
pick=none
for d in /sys/bus/pci/devices/*; do
  [ "$(cat $d/vendor 2>/dev/null)" = 0x10de ] || continue
  case $(cat $d/class 2>/dev/null) in 0x03*) ;; *) continue ;; esac
  id=10DE$(sed 's/^0x//' $d/device | tr a-f A-F)
  if grep -qx "$id" $IDS/nvidia-615.ids; then pick=615           # GTX 16, RTX 20 and later
  elif grep -qx "$id" $IDS/nvidia.ids; then [ $pick = 615 ] || pick=550   # GTX 900, 1000, Titan V
  elif cat $IDS/nvidia-legacy-*.ids $IDS/nvidia-tesla-4*.ids | grep -qx "$id"; then :  # older: none
  else pick=615                                                  # newer than Debian's lists
  fi
done
case $(cat /proc/cmdline) in *cherry.nvidia=615*) pick=615 ;; *cherry.nvidia=550*) pick=550 ;; esac

LIVE=/usr/share/cherry/nvidia-live-packages
NV=/usr/share/cherry/nvidia-live-nvidia-packages
remove() {  # remove the packages in $1, unless that would take anything else with them
  extra=$(apt-get -s purge $(cat "$1") | awk '/^Purg|^Remv/{print $2}' | grep -vxF -f "$1")
  [ -z "$extra" ] || set -- "$NV"
  apt-get purge -y $(cat "$1")
  rm -f /etc/apt/sources.list.d/cuda-*.list /etc/apt/preferences.d/cherry-nvidia /usr/share/keyrings/cuda-archive-keyring.gpg
  dpkg -l cuda-keyring 2>/dev/null | grep -q ^ii && apt-get purge -y cuda-keyring
  return 0
}
case $pick in
  none)
    remove "$LIVE"
    exit 0 ;;
  615)
    # Build it again so dkms signs it with a key made for this computer.
    dkms status | sed -n 's|^\([^/]*\)/\([^,]*\), \([^,]*\),.*: installed.*|\1 \2 \3|p' | while read -r m v k; do
      dkms remove -m "$m" -v "$v" -k "$k" && dkms install -m "$m" -v "$v" -k "$k" || exit 1
    done || exit 1 ;;
  550)
    remove "$NV"
    apt-get install -y $(cat /usr/share/cherry/nvidia-packages-550) || exit 1
    cat > /etc/modprobe.d/cherry-nvidia.conf <<'CONF'
# Cherry OS: Wayland desktops need the driver's own mode setting, and suspend needs video memory saved.
options nvidia-drm modeset=1 fbdev=1
options nvidia NVreg_PreserveVideoMemoryAllocations=1
CONF
    systemctl enable nvidia-suspend.service nvidia-hibernate.service nvidia-resume.service 2>/dev/null ;;
esac
if mokutil --sb-state 2>/dev/null | grep -q 'SecureBoot enabled'; then
  /usr/libexec/cherry-nvidia-key || exit 1
fi
cp /usr/share/cherry/cherry-nvidia-key.desktop /usr/share/applications/
EOF
chmod 755 /usr/libexec/cherry-nvidia-install
# Debian's driver for GTX 900 and 1000 cards. Kept on the ISO by config/installer-pool.list (the same packages).
echo "nvidia-driver nvidia-kernel-dkms nvidia-suspend-common linux-headers-amd64" > /usr/share/cherry/nvidia-packages-550

cat > /usr/libexec/cherry-nvidia-key <<EOF
#!/bin/sh
# Asks the firmware to trust the key the Nvidia driver is signed with, at the next restart (the blue MOK screen).
key=/var/lib/dkms/mok.pub
[ -f "\$key" ] || { echo "no driver signing key at \$key" >&2; exit 1; }
mokutil --test-key "\$key" 2>/dev/null | grep -q 'is already enrolled' && exit 0
hash=\$(mktemp) || exit 1
mokutil --generate-hash=$CODE > "\$hash" && mokutil --import "\$key" --hash-file "\$hash"
rc=\$?
rm -f "\$hash"
exit \$rc
EOF
chmod 755 /usr/libexec/cherry-nvidia-key

mkdir -p /usr/share/polkit-1/actions
cat > /usr/share/polkit-1/actions/org.cherryos.nvidia-key.policy <<'EOF'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE policyconfig PUBLIC "-//freedesktop//DTD PolicyKit Policy Configuration 1.0//EN"
  "http://www.freedesktop.org/standards/PolicyKit/1/policyconfig.dtd">
<policyconfig>
  <action id="org.cherryos.nvidia-key">
    <description>Approve the Nvidia driver</description>
    <message>Secure Boot needs your password to approve the Nvidia graphics driver</message>
    <icon_name>video-display</icon_name>
    <defaults>
      <allow_any>auth_admin</allow_any>
      <allow_inactive>auth_admin</allow_inactive>
      <allow_active>auth_admin</allow_active>
    </defaults>
    <annotate key="org.freedesktop.policykit.exec.path">/usr/libexec/cherry-nvidia-key</annotate>
  </action>
</policyconfig>
EOF

# Shown before restarting after the install, from the menu entry to try again, and at login after a fallback.
cat > /usr/libexec/cherry-nvidia-note <<EOF
#!/usr/bin/python3
"""cherry-nvidia-note --install: before the restart that ends the install, explain the MOK screen if a key waits.
cherry-nvidia-note --retry: ask for the key again (as root, via pkexec), explain, and offer to restart.
cherry-nvidia-note --login: if the free driver had to take over at this start, say so."""
import os
import subprocess
import sys

sys.path.insert(0, "/usr/lib/cherry")
mode = sys.argv[1] if len(sys.argv) > 1 else "--install"
CODE = "$CODE"

if mode == "--login":
    try:
        why = open("/run/cherry/nvidia-fallback").read().strip()
    except OSError:
        sys.exit(0)
    live = os.path.exists("/run/live/medium")
    if live and why == "key":
        body = ("Secure Boot is on, so this live session uses the basic graphics driver. When you install Cherry OS, "
                "it sets up Nvidia's driver and shows you how to approve it once.")
    elif live or why == "unsupported":
        sys.exit(0)
    elif why == "key":
        body = ("Its Secure Boot key isn't approved yet, so Cherry OS is using the basic graphics driver. "
                "Open “Approve Nvidia Driver” from the menu to fix this.")
    else:
        body = ("Cherry OS is using the basic graphics driver instead. Updating the system (or restarting after an "
                "update finishes) usually fixes this.")
    subprocess.run(["notify-send", "-a", "Cherry OS", "-i", "video-display", "Nvidia's driver isn't on", body])
    sys.exit(0)

if mode == "--install":
    pending = subprocess.run(["sudo", "-n", "mokutil", "--list-new"], capture_output=True, text=True).stdout
    if "Certificate" not in pending:
        sys.exit(0)
elif subprocess.run(["pkexec", "/usr/libexec/cherry-nvidia-key"]).returncode != 0:
    sys.exit(1)

from cherryos.ui import Adw, Gtk, apply_css, hbox, identify, pill, status_page  # noqa: E402

identify("cherry-nvidia-key", "Approve Nvidia Driver", "video-display")
app = Adw.Application()


def activate(app):
    apply_css()
    win = Adw.ApplicationWindow(application=app, title="Nvidia graphics", default_width=640, default_height=640)

    def restart():
        win.close()
        if mode == "--retry":
            subprocess.Popen(["systemctl", "reboot"])
    buttons = [pill("Restart", True, restart)]
    if mode == "--retry":
        buttons.insert(0, pill("Later", False, win.close))
    page = status_page(
        "video-display-symbolic", "One more step for your Nvidia graphics",
        "Secure Boot needs you to approve the Nvidia driver once. When the computer restarts, a blue screen "
        "says “Press any key to perform MOK management”. You have 10 seconds:\n\n"
        "1. Press any key, choose “Enroll MOK”, then “Continue”.\n"
        f"2. Choose “Yes” and type the code {CODE} (nothing shows as you type), then press Enter.\n"
        "3. Choose “Reboot”.\n\n"
        "If you miss it, Cherry OS still starts with its basic graphics driver, and you can try again any time "
        "from “Approve Nvidia Driver” in the menu.", hbox(12, *buttons, halign=Gtk.Align.CENTER))
    view = Adw.ToolbarView()
    view.add_top_bar(Adw.HeaderBar())
    view.set_content(page)
    win.set_content(view)
    win.present()


app.connect("activate", activate)
app.run([sys.argv[0]])
EOF
chmod 755 /usr/libexec/cherry-nvidia-note

cat > /usr/share/cherry/cherry-nvidia-key.desktop <<'EOF'
[Desktop Entry]
Type=Application
Name=Approve Nvidia Driver
Comment=Let Secure Boot load the Nvidia graphics driver
Exec=/usr/libexec/cherry-nvidia-note --retry
Icon=video-display
Categories=System;Settings;HardwareSettings;
StartupWMClass=cherry-nvidia-key
EOF
cat > /etc/xdg/autostart/cherry-nvidia-note.desktop <<'EOF'
[Desktop Entry]
Type=Application
Name=Nvidia driver check
Exec=/usr/libexec/cherry-nvidia-note --login
NoDisplay=true
X-GNOME-Autostart-Phase=Application
EOF

# ---- The installer: sort out the driver after its other packages (so the new initramfs matches), and show the
# note when "Restart now" is clicked, before restarting.
[ -d /etc/calamares ] || exit 0
MODS=/etc/calamares/modules
cat > $MODS/shellprocess@cherrynvidia.conf <<'EOF'
dontChroot: false
timeout: 1800
script:
  - command: "/usr/libexec/cherry-nvidia-install"
    timeout: 1800
EOF
sed -i 's|^restartNowCommand:.*|restartNowCommand: "touch /run/cherry-restart"|' $MODS/finished.conf
grep -q 'cherry-restart' $MODS/finished.conf
python3 - <<'PY'
import re
path = "/etc/calamares/settings.conf"
text = open(path).read()
text = re.sub(r"(?m)^instances:\n", "instances:\n- id: cherrynvidia\n  module: shellprocess\n  config: shellprocess@cherrynvidia.conf\n", text, count=1)
text, n = re.subn(r"(?m)^(\s*)- packages\n", r"\1- packages\n\1- shellprocess@cherrynvidia\n", text, count=1)
assert n == 1
open(path, "w").write(text)
PY
cat >> /usr/bin/calamares-install-debian <<'EOF'

# Cherry OS: "Restart now" was ticked. Explain the Nvidia driver's MOK screen first, if there will be one.
if [ -e /run/cherry-restart ]; then
  /usr/libexec/cherry-nvidia-note --install
  sudo -n systemctl -i reboot
fi
EOF
