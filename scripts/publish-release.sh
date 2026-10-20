#!/usr/bin/env bash
# Publishes Cherry's own packages to the Cherry Store, signed with a Cherry OS release key, so installed
# copies update themselves (daily timer, or `cherry update`):
#   cherry-move           Cherry Move (Cherry OS, and other systems that ship it)
#   cherry-desktop        Cherry OS's apps
#   cherry-store-client   the Cherry Store app for other Debian-based systems
# cherry-move goes first: cherry-desktop needs that version of it.
# Bump __version__ in desktop/lib/cherryos/__init__.py first. Needs gh (logged in) and the release key, which is
# CHERRY_RELEASE_KEY or ~/.config/cherry/cherry-os-release.json and must never be committed.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
KEY="${CHERRY_RELEASE_KEY:-$HOME/.config/cherry/cherry-os-release.json}"
[[ -f $KEY ]] || { echo "No Cherry OS release key at $KEY" >&2; exit 1; }
OUT="$(mktemp -d)"
trap 'rm -rf "$OUT"' EXIT
scripts/build-debs.sh "$OUT" >/dev/null
scripts/build-store-client.sh "$OUT" >/dev/null
for name in cherry-move cherry-desktop cherry-store-client; do
  CHERRY_IDENTITY="$KEY" python3 desktop/bin/cherry publish --github "$OUT/${name}_"*_all.deb
done
