#!/usr/bin/env bash
# Checks that Cherry Move and the Cherry Store are installed exactly as Cherry OS @VERSION@ has them
# (or newer, once the Cherry Store has updated them).
# Usage: ./check-cherry-apps.sh                  the running system (a VM of your built image)
#        sudo ./check-cherry-apps.sh --root DIR  an image's root filesystem
# Every line must say PASS (WARN lines are worth fixing). Exits non-zero if anything FAILs.
set -uo pipefail
root=/
[[ ${1:-} == --root ]] && root="$(realpath "${2:?usage: $0 [--root DIR]}")"
want=@VERSION@
fails=0
pass() { echo "PASS  $*"; }
fail() { echo "FAIL  $*"; fails=$((fails + 1)); }
warn() { echo "WARN  $*"; }
status() { dpkg-query --admindir="$root/var/lib/dpkg" -W -f='${db:Status-Status} ${Version}' "$1" 2>/dev/null; }
in_root() { if [[ $root == / ]]; then "$@"; else chroot "$root" "$@"; fi; }

declare -A updated
for p in cherry-move cherry-store-client; do
  s="$(status "$p")"
  case "$s" in
    "installed $want") pass "$p $want is installed" ;;
    installed*)
      if dpkg --compare-versions "${s#installed }" gt "$want"; then
        updated[$p]=1
        pass "$p is ${s#installed }, newer than the kit's $want (updated through the Cherry Store)"
      else
        fail "$p is version ${s#installed }, but Cherry OS uses $want: install the one in install-into-image/"
      fi ;;
    *) fail "$p is not installed: run install-cherry-apps.sh" ;;
  esac
done
[[ $(status cherry-desktop) == installed* ]] &&
  fail "cherry-desktop is installed: it's Cherry OS's own package, not for other systems. Remove it." ||
  pass "cherry-desktop (Cherry OS only) is not installed"
[[ $(status cherry-move-app) == installed* ]] &&
  fail "cherry-move-app is installed: that's the app for people's OLD computers, which only goes on the install USB as a file. Remove it." ||
  pass "cherry-move-app (old-computer app) is not installed"

# The same files as the kit's packages, so nothing was rebuilt, patched or swapped for another version.
# A package the store has updated has newer files, which the store checked against Cherry OS's signature.
while read -r pkg sum file; do
  if [[ -n ${updated[$pkg]:-} ]]; then
    continue
  elif [[ ! -e $root/$file ]]; then
    fail "/$file is missing"
  elif [[ $(sha256sum < "$root/$file" | cut -d' ' -f1) != "$sum" ]]; then
    fail "/$file differs from Cherry OS $want's copy: reinstall the packages from install-into-image/"
  else
    pass "/$file matches Cherry OS $want"
  fi
done <<'EOF'
@FILE_SUMS@
EOF

grep -qx 'StartupWMClass=org.cherryos.Move' "$root/usr/share/applications/cherry-move.desktop" 2>/dev/null &&
grep -qx 'StartupWMClass=org.cherryos.Store' "$root/usr/share/applications/cherry-store.desktop" 2>/dev/null &&
  pass "menu entries keep their names (docks show the right icons)" ||
  fail "a menu entry's StartupWMClass was changed: keep org.cherryos.Move and org.cherryos.Store"
[[ -L $root/etc/systemd/system/timers.target.wants/cherry-store-self-update.timer ]] &&
  pass "daily self-update timer is enabled" ||
  fail "cherry-store-self-update.timer isn't enabled: run 'systemctl enable cherry-store-self-update.timer' in the image"
[[ -x $root/usr/bin/rclone ]] && pass "rclone is installed (cloud drives and NAS)" ||
  warn "rclone isn't installed: Cherry Move's cloud drive and NAS options are greyed out"
[[ -x $root/usr/bin/avahi-browse ]] && pass "avahi-utils is installed (finds NAS boxes)" ||
  warn "avahi-utils isn't installed: people have to type their NAS's address"
if [[ -n $(ls -A "$root/var/lib/cherry" 2>/dev/null) && $root != / ]]; then
  warn "/var/lib/cherry isn't empty in the image: trusted keys and pending transfers are per computer, so empty it"
fi

if [[ $root == / || $EUID -eq 0 ]]; then
  in_root cherry-move --help >/dev/null 2>&1 && pass "cherry-move runs" || fail "cherry-move --help fails: its dependencies are missing"
  in_root cherry --help >/dev/null 2>&1 && pass "cherry runs" || fail "cherry --help fails: its dependencies are missing"
else
  warn "not root, so cherry-move and cherry weren't started inside $root"
fi

echo
if ((fails)); then
  echo "$fails check(s) failed."
  exit 1
fi
echo "All checks passed: Cherry Move and the Cherry Store are installed the same way as on Cherry OS $want."
