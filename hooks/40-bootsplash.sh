#!/bin/sh
# Runs inside the chroot. Makes the cherry the boot loading screen (Plymouth).
set -eu
command -v plymouth-set-default-theme >/dev/null || exit 0

plymouth-set-default-theme cherry
update-initramfs -u -k all

if [ -d /etc/calamares/modules ]; then
  echo 'plymouth_theme: cherry' > /etc/calamares/modules/plymouthcfg.conf
fi
