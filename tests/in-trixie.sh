#!/bin/sh
# Runs tests inside Debian trixie with the same Python packages and rclone as Cherry OS:
#   docker run --rm --network host -v "$PWD:$PWD" -w "$PWD" debian:trixie sh tests/in-trixie.sh [pytest args]
set -e
mkdir -p build/apt-cache
rm -f /etc/apt/apt.conf.d/docker-clean
apt-get update -qq
DEBIAN_FRONTEND=noninteractive apt-get install -y -qq --no-install-recommends -o Dir::Cache::archives="$PWD/build/apt-cache" \
  python3-pytest python3-cryptography python3-zstandard python3-qrcode python3-pil python3-gi gir1.2-adw-1 rclone python3-venv \
  ca-certificates >/dev/null
export PYTHONDONTWRITEBYTECODE=1 CHERRY_TEST_APT=1
exec python3 -m pytest -p no:cacheprovider "$@"
