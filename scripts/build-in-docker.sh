#!/usr/bin/env bash
# Runs build.sh inside a privileged Debian container, so the build tools don't have to be installed on
# the host and no sudo password is needed (only membership of the docker group). Same options as build.sh.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."

# The memory cap means a runaway build step is stopped instead of freezing the whole computer.
exec docker run --rm --privileged --name cherry-build --memory "${BUILD_MEMORY:-10g}" --memory-swap "${BUILD_MEMORY:-10g}" \
  -v "$PWD:$PWD" -w "$PWD" -e SUDO_UID="$(id -u)" -e SUDO_GID="$(id -g)" -e TERM -e SQUASHFS_PROCESSORS \
  debian:trixie bash -c '
    set -e
    apt-get update -qq
    DEBIAN_FRONTEND=noninteractive apt-get install -y -qq debootstrap debian-archive-keyring \
      squashfs-tools xorriso apt-utils grub-pc-bin grub-efi-amd64-bin grub-common mtools dosfstools python3-pil \
      librsvg2-bin dpkg-dev >/dev/null
    exec ./build.sh "$@"' build-in-docker "$@"
