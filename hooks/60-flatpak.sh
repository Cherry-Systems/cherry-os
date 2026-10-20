#!/bin/sh
# Runs inside the chroot. Flathub is available out of the box in every desktop's software centre.
set -eu
command -v flatpak >/dev/null || exit 0
flatpak remote-add --system --if-not-exists flathub https://dl.flathub.org/repo/flathub.flatpakrepo ||
  echo "warning: couldn't add Flathub (no network during build?)" >&2
