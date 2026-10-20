#!/usr/bin/env bash
# Installs Cherry Move and the Cherry Store into your OS image: the same @VERSION@ packages Cherry OS @VERSION@ ships.
#   cherry-move          Cherry Move, identical to Cherry OS's
#   cherry-store-client  the Cherry Store: the same code Cherry OS has in its cherry-desktop package, without
#                        Cherry OS's other apps
# Usage: sudo ./install-cherry-apps.sh CHROOT
#   CHROOT is your image's root filesystem (debootstrap/mmdebstrap target), or / for the running system.
# live-build instead: copy install-into-image/*.deb to config/packages.chroot/ and add "rclone avahi-utils" to a
# package list in config/package-lists/, then run ./check-cherry-apps.sh in a VM of the built image.
set -euo pipefail
root="${1:?usage: sudo $0 CHROOT}"
root="$(realpath "$root")"
kit="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
debs=(cherry-move_@VERSION@_all.deb cherry-store-client_@VERSION@_all.deb)

stop() {
  printf '\nSTOP: %s\n' "$*" >&2
  printf 'Do not work around this: no Wine, no rpm/Flatpak/AppImage/Snap versions, no building from source, no other\n' >&2
  printf 'versions, nothing from for-the-usb-stick/. Tell the user exactly what this said.\n' >&2
  exit 1
}

[[ $EUID -eq 0 ]] || stop "run this as root (sudo)."
(cd "$kit/install-into-image" && sha256sum --quiet -c SHA256SUMS) ||
  stop "the packages in install-into-image/ are damaged or have been changed."
[[ -x $root/usr/bin/apt-get && -x $root/usr/bin/dpkg ]] ||
  stop "$root has no apt and dpkg. Cherry Move and the Cherry Store only install on Debian 12+, Ubuntu 24.04+ and
their derivatives (Linux Mint 22+, Pop!_OS 24.04+, ...)."
os="$(. "$root/etc/os-release" 2>/dev/null && echo "${PRETTY_NAME:-unknown}")"
echo "Installing Cherry Move and the Cherry Store @VERSION@ into $root ($os)"

in_root() { if [[ $root == / ]]; then "$@"; else chroot "$root" "$@"; fi; }
mounted=()
cleanup() {
  rm -rf "$root/tmp/cherry-kit"
  for m in "${mounted[@]}"; do umount "$m" 2>/dev/null || true; done
}
trap cleanup EXIT
if [[ $root != / ]]; then
  for fs in proc sys dev; do
    if ! mountpoint -q "$root/$fs"; then
      mount --bind "/$fs" "$root/$fs"
      mounted=("$root/$fs" "${mounted[@]}")
    fi
  done
fi

mkdir -p "$root/tmp/cherry-kit"
for d in "${debs[@]}"; do cp "$kit/install-into-image/$d" "$root/tmp/cherry-kit/"; done
export DEBIAN_FRONTEND=noninteractive
in_root apt-get update || stop "apt-get update failed inside $root (no network or broken apt sources?)."
in_root apt-get install -y "${debs[@]/#//tmp/cherry-kit/}" ||
  stop "apt couldn't install the packages (see apt's message above). The base is probably too old: Cherry Move needs
libadwaita 1.2 or newer, so Ubuntu 22.04 and Linux Mint 21 can't have it."
# Cloud drives and NAS in Cherry Move need these; they're only Recommends, which some builds turn off.
in_root apt-get install -y rclone avahi-utils ||
  echo "WARNING: couldn't install rclone and avahi-utils. Cherry Move still works with USB sticks, but cloud drives and NAS are greyed out." >&2

echo
"$kit/check-cherry-apps.sh" --root "$root"
