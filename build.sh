#!/usr/bin/env bash
# Builds the Cherry OS live ISO on top of Debian.
#   sudo ./build.sh              full build -> out/<id>-<version>-<arch>.iso
#   sudo ./build.sh --clean      remove build output (keeps the package download cache)
#   sudo ./build.sh --distclean  remove everything, including the cache
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR"
# shellcheck source=config/os.conf
source config/os.conf

WORK="$ROOT_DIR/build"
ROOTFS="$WORK/rootfs"
ISO_DIR="$WORK/iso"
CACHE="$WORK/cache"
OUT_DIR="$ROOT_DIR/out"
ISO="$OUT_DIR/${OS_ID}-${OS_VERSION}-${ARCH}.iso"
ISO_LABEL="$(printf '%s_%s' "$OS_ID" "$OS_VERSION" | tr 'a-z.-' 'A-Z__' | cut -c1-32)"

log() { printf '\n\033[1;35m==>\033[0m \033[1m%s\033[0m\n' "$*"; }
die() { printf '\033[1;31merror:\033[0m %s\n' "$*" >&2; exit 1; }

in_chroot() {
  chroot "$ROOTFS" /usr/bin/env -i \
    HOME=/root PATH=/usr/sbin:/usr/bin:/sbin:/bin TERM="${TERM:-xterm}" \
    LC_ALL=C DEBIAN_FRONTEND=noninteractive "$@"
}

mount_rootfs() {
  mount -t proc proc "$ROOTFS/proc"
  mount -t sysfs sys "$ROOTFS/sys"
  mount --bind /dev "$ROOTFS/dev"
  mount --bind /dev/pts "$ROOTFS/dev/pts"
  mount -t tmpfs tmpfs "$ROOTFS/run"
  mount --bind "$CACHE/apt" "$ROOTFS/var/cache/apt/archives"
}

unmount_rootfs() {
  local m
  for m in var/cache/apt/archives run dev/pts dev sys proc; do
    if mountpoint -q "$ROOTFS/$m" 2>/dev/null; then
      umount -l "$ROOTFS/$m"
    fi
  done
}

# --one-file-system stops rm from ever descending into a still-mounted /dev or /proc.
remove_tree() { unmount_rootfs; rm -rf --one-file-system "$@"; }

read_packages() { sed 's/#.*//' "$@" | tr -s '[:space:]' '\n' | sed '/^$/d'; }
read_groups() { sed -e 's/#.*//' -e '/^[[:space:]]*$/d' "$@"; }

[[ $EUID -eq 0 ]] || die "needs root: run 'sudo ./build.sh' (or 'make build-docker')"

case "${1:-}" in
  --clean) remove_tree "$ROOTFS" "$ISO_DIR" "$OUT_DIR"; echo "Cleaned."; exit 0 ;;
  --distclean) remove_tree "$WORK" "$OUT_DIR"; echo "Cleaned everything."; exit 0 ;;
  "") ;;
  *) die "unknown option: $1" ;;
esac

for tool in debootstrap mksquashfs grub-mkimage xorriso mcopy mkfs.vfat apt-ftparchive dpkg-deb; do
  command -v "$tool" >/dev/null || die "missing '$tool' — run 'make deps' or 'make build-docker'"
done
[[ -f /usr/share/keyrings/debian-archive-keyring.gpg ]] || die "missing Debian keyring — run 'make deps'"
[[ -d /usr/lib/grub/i386-pc ]] || echo "warning: grub-pc-bin not installed, the ISO will only boot on UEFI" >&2

PACKAGE_LISTS=(config/packages/base.list config/packages/login.list
               config/packages/languages.list config/packages/windows.list)
for desktop in $DESKTOPS; do
  [[ -f config/packages/desktop-$desktop.list ]] || die "no package list for desktop '$desktop'"
  PACKAGE_LISTS+=("config/packages/desktop-$desktop.list")
done
PACKAGE_LISTS+=(config/packages/installer.list)
mapfile -t PACKAGES < <(read_packages "${PACKAGE_LISTS[@]}")

trap unmount_rootfs EXIT

log "Cleaning previous build"
remove_tree "$ROOTFS" "$ISO_DIR"
mkdir -p "$ROOTFS" "$ISO_DIR/live" "$ISO_DIR/boot/grub" \
  "$CACHE/debootstrap" "$CACHE/apt/partial" "$OUT_DIR"

log "Drawing artwork"
python3 scripts/make-artwork.py "$WORK/art" || die "could not draw artwork (is python3-pil installed?)"

log "Packaging Cherry's own apps"
scripts/build-debs.sh "$WORK/debs"

log "Bootstrapping Debian $DEBIAN_SUITE ($ARCH)"
debootstrap --arch="$ARCH" --variant=minbase \
  --cache-dir="$CACHE/debootstrap" \
  --keyring=/usr/share/keyrings/debian-archive-keyring.gpg \
  --include=debian-archive-keyring,ca-certificates \
  --components=main,contrib,non-free,non-free-firmware \
  "$DEBIAN_SUITE" "$ROOTFS" "$DEBIAN_MIRROR"

log "Configuring APT sources"
rm -f "$ROOTFS/etc/apt/sources.list"
cat > "$ROOTFS/etc/apt/sources.list.d/debian.sources" <<EOF
Types: deb
URIs: $DEBIAN_MIRROR
Suites: $DEBIAN_SUITE $DEBIAN_SUITE-updates $DEBIAN_SUITE-backports
Components: main contrib non-free non-free-firmware
Signed-By: /usr/share/keyrings/debian-archive-keyring.gpg

Types: deb
URIs: http://security.debian.org/debian-security
Suites: $DEBIAN_SUITE-security
Components: main contrib non-free non-free-firmware
Signed-By: /usr/share/keyrings/debian-archive-keyring.gpg
EOF
# APT pins (one login screen) must be in place before the desktops are installed.
cp -a --no-preserve=ownership overlay/etc/apt/. "$ROOTFS/etc/apt/"

# Keep package installs from trying to start services inside the chroot.
printf '#!/bin/sh\nexit 101\n' > "$ROOTFS/usr/sbin/policy-rc.d"
chmod 755 "$ROOTFS/usr/sbin/policy-rc.d"
rm -f "$ROOTFS/etc/resolv.conf"
cp -L /etc/resolv.conf "$ROOTFS/etc/resolv.conf"

echo "$OS_HOSTNAME" > "$ROOTFS/etc/hostname"
cat > "$ROOTFS/etc/hosts" <<EOF
127.0.0.1 localhost
127.0.1.1 $OS_HOSTNAME
::1       localhost ip6-localhost ip6-loopback
EOF

mount_rootfs

log "Installing ${#PACKAGES[@]} packages (all desktops, languages, Windows support)"
in_chroot dpkg --add-architecture i386
in_chroot apt-get update
echo "gdm3 shared/default-x-display-manager select gdm3" | in_chroot debconf-set-selections
in_chroot apt-get install -y "${PACKAGES[@]}"

log "Installing Cherry's apps"
mkdir -p "$ROOTFS/tmp/debs"
cp "$WORK"/debs/*.deb "$ROOTFS/tmp/debs/"
in_chroot sh -c 'apt-get install -y /tmp/debs/*.deb'

log "Building the installer's offline package repository"
mkdir -p "$ROOTFS/tmp/pool/partial"
while read -r -a group; do
  in_chroot apt-get install -y --download-only -o Dir::Cache::archives=/tmp/pool "${group[@]}" </dev/null
done < <(read_groups config/installer-pool.list)
REPO_INDEX="$ISO_DIR/dists/$DEBIAN_SUITE/main/binary-$ARCH"
mkdir -p "$ISO_DIR/pool/main" "$REPO_INDEX"
mv "$ROOTFS"/tmp/pool/*.deb "$ISO_DIR/pool/main/"
(cd "$ISO_DIR" && apt-ftparchive packages pool > "$REPO_INDEX/Packages")
gzip -9kf "$REPO_INDEX/Packages"
apt-ftparchive \
  -o APT::FTPArchive::Release::Suite="$DEBIAN_SUITE" \
  -o APT::FTPArchive::Release::Codename="$DEBIAN_SUITE" \
  -o APT::FTPArchive::Release::Components=main \
  -o APT::FTPArchive::Release::Architectures="$ARCH" \
  release "$ISO_DIR/dists/$DEBIAN_SUITE" > "$WORK/Release"
mv "$WORK/Release" "$ISO_DIR/dists/$DEBIAN_SUITE/Release"

log "Applying overlay and artwork"
cp -a --no-preserve=ownership overlay/. "$ROOTFS/"
install -D -m 644 "$WORK"/art/*.png -t "$ROOTFS/usr/share/$OS_ID/artwork/"
install -D -m 644 "$WORK"/art/wallpapers/*.png -t "$ROOTFS/usr/share/backgrounds/$OS_ID/"
install -D -m 644 "$WORK"/art/screens/*.png -t "$ROOTFS/usr/share/$OS_ID/screens/"
install -D -m 644 "$WORK/art/grub.png" "$ISO_DIR/boot/grub/background.png"
install -D -m 644 "$WORK/art/plymouth-logo.png" "$WORK/art/spinner.png" \
  -t "$ROOTFS/usr/share/plymouth/themes/cherry/"
mkdir -p "$ROOTFS/etc/cherry"
printf 'CLOUD_URL=%s\n' "$CLOUD_URL" > "$ROOTFS/etc/cherry/cloud.conf"
printf '%s\n' "$DEFAULT_DESKTOP" > "$ROOTFS/etc/cherry/default-desktop"

HOOK_ENV=(
  "OS_NAME=$OS_NAME" "OS_ID=$OS_ID" "OS_VERSION=$OS_VERSION" "OS_CODENAME=$OS_CODENAME"
  "OS_HOSTNAME=$OS_HOSTNAME" "DEBIAN_SUITE=$DEBIAN_SUITE" "DESKTOPS=$DESKTOPS"
  "DEFAULT_DESKTOP=$DEFAULT_DESKTOP" "LIVE_USERNAME=$LIVE_USERNAME" "TIMEZONE=$TIMEZONE"
)
for hook in hooks/*.sh; do
  [[ -e $hook ]] || continue
  log "Running hook $(basename "$hook")"
  install -m 755 "$hook" "$ROOTFS/tmp/hook.sh"
  in_chroot "${HOOK_ENV[@]}" /tmp/hook.sh
  rm -f "$ROOTFS/tmp/hook.sh"
done

log "Cleaning up the root filesystem"
umount -l "$ROOTFS/var/cache/apt/archives"
in_chroot apt-get clean
rm -rf "$ROOTFS"/var/lib/apt/lists/* "$ROOTFS"/tmp/* "$ROOTFS/var/lib/dbus/machine-id"
rm -f "$ROOTFS/usr/sbin/policy-rc.d" "$ROOTFS/etc/resolv.conf"
: > "$ROOTFS/etc/machine-id"
unmount_rootfs

log "Compressing root filesystem (this takes a while)"
KERNEL="$(find "$ROOTFS/boot" -maxdepth 1 -name 'vmlinuz-*' | sort -V | tail -n1)"
[[ -n $KERNEL ]] || die "no kernel found in $ROOTFS/boot"
cp "$KERNEL" "$ISO_DIR/live/vmlinuz"
cp "$ROOTFS/boot/initrd.img-${KERNEL##*/vmlinuz-}" "$ISO_DIR/live/initrd.img"
# Each zstd-19 thread needs a lot of memory; on all cores at once it can starve the rest of the computer.
procs=${SQUASHFS_PROCESSORS:-$(( $(nproc) > 16 ? 8 : ($(nproc) + 1) / 2 ))}
mksquashfs "$ROOTFS" "$ISO_DIR/live/filesystem.squashfs" -noappend -comp zstd -Xcompression-level 19 -b 1M \
  -processors "$procs" -mem 1G

if [[ -f build/move/CherryMove-Setup.exe ]]; then
  log "Adding the Cherry Move app for Windows, macOS and Linux"
  mkdir -p "$ISO_DIR/CherryMove"
  cp build/move/CherryMove-Setup.exe build/move/CherryMove.dmg build/move/*.deb build/move/*.rpm \
    build/move/cherry-move-linux-* move/README.txt "$ISO_DIR/CherryMove/"
else
  log "No build/move/CherryMove-Setup.exe (run make move): the ISO won't carry Cherry Move for Windows and macOS"
fi

log "Creating bootable ISO"
LIVE_ARGS="live-config.hostname=$OS_HOSTNAME live-config.username=$LIVE_USERNAME"
LIVE_ARGS+=" live-config.user-fullname=Cherry"
LIVE_ARGS+=" live-config.locales=$LOCALE live-config.timezone=$TIMEZONE"
LIVE_ARGS+=" live-config.keyboard-layouts=$KEYBOARD_LAYOUT"
DESKTOP_ENTRIES="$(python3 desktop/lib/cherryos/desktops.py grub-entries "$DESKTOPS" "$DEFAULT_DESKTOP")"
python3 - "$ISO_DIR/boot/grub/grub.cfg" <<PY
import sys
text = open("config/grub.cfg.in").read()
for key, value in {"@OS_NAME@": "$OS_NAME", "@OS_VERSION@": "$OS_VERSION",
                   "@LIVE_ARGS@": "$LIVE_ARGS", "@DEFAULT_DESKTOP@": "$DEFAULT_DESKTOP"}.items():
    text = text.replace(key, value)
text = text.replace("@DESKTOP_ENTRIES@", """$DESKTOP_ENTRIES""".replace("@LIVE_ARGS@", "$LIVE_ARGS"))
open(sys.argv[1], "w").write(text)
PY

scripts/make-iso.sh "$ISO_DIR" "$ISO" "$ISO_LABEL" "$OS_NAME $OS_VERSION $ARCH" "$WORK"
chown "${SUDO_UID:-0}:${SUDO_GID:-0}" "$OUT_DIR" "$ISO"

log "Done: $ISO ($(du -h "$ISO" | cut -f1))"
echo "Boot it with: make run"
