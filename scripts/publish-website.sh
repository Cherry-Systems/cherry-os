#!/bin/sh
# Publishes the Cherry Systems website to GitHub Pages: https://cherry-systems.github.io
#   /          website/            Cherry Systems home, with /os/ and /move/
#   /store/    cloud/public/       the Cherry Store page (Cherry Cloud serves the same page)
#   /products/ products.json       other systems and apps, added by agents through the cherry-site MCP; kept as is
# The store's app list is read live from its index.json, so this is only needed when a page changes.
#   scripts/publish-website.sh [--dry-run]    --dry-run only assembles build/website-repo
set -eu
cd "$(dirname "$0")/.."
REPO=${WEBSITE_REPO:-Cherry-Systems/cherry-systems.github.io}
DIR=build/website-repo

if [ -d "$DIR/.git" ]; then
  git -C "$DIR" pull -q --ff-only
else
  rm -rf "$DIR"
  git clone -q "https://github.com/$REPO.git" "$DIR"
  git -C "$DIR" config credential.helper '!gh auth git-credential'
fi

find "$DIR" -mindepth 1 -maxdepth 1 ! -name .git ! -name products ! -name products.json -exec rm -rf {} +
cp -r website/. "$DIR/"
cp cloud/public/logo.svg "$DIR/logo.svg"
cp desktop/icons/cherry-store.svg desktop/icons/cherry-move.svg "$DIR/images/"
mkdir -p "$DIR/store"
cp -r cloud/public/. "$DIR/store/"
touch "$DIR/.nojekyll"
cat > "$DIR/README.md" <<'EOF'
# Cherry Systems website

The website at https://cherry-systems.github.io: Cherry OS, the
[Cherry Store](https://cherry-systems.github.io/store/) and Cherry Move.
EOF

[ "${1:-}" = --dry-run ] && { echo "Assembled in $DIR"; exit 0; }
cd "$DIR"
git add -A
if git diff --cached --quiet; then
  echo "Website is already up to date."
  exit 0
fi
git commit -q -m "Update the Cherry Systems website"
git branch -M main
git push -q -u origin main
echo "Published: https://${REPO#*/}"
