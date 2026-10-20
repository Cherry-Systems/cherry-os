#!/bin/sh
# Tests the macOS builds in build/move on GitHub's macOS machines (Apple Silicon and Intel): puts them and
# the test scripts in a private GitHub repo (created the first time) and pushes, which starts the run.
#   scripts/move-test/run-mac-test.sh [OWNER/REPO]      default: <you>/cherry-move-mac-test
# Then: gh run watch -R OWNER/REPO, and gh run download -R OWNER/REPO -D build/mac-results
# Needs gh (signed in) and git with access to GitHub.
set -e
cd "$(dirname "$0")/../.."
repo=${1:-$(gh api user -q .login)/cherry-move-mac-test}
dir=build/mac-test
[ -f build/move/CherryMove.dmg ] || { echo "build/move/CherryMove.dmg is missing: run make move first" >&2; exit 1; }
rm -rf "$dir/dist"
mkdir -p "$dir/dist" "$dir/.github/workflows"
cp build/move/CherryMove.dmg "$dir/dist/"
cp scripts/move-test/mac_test.py scripts/move-test/make_files.py "$dir/"
cp scripts/move-test/mac-workflow.yml "$dir/.github/workflows/mac.yml"
cd "$dir"
[ -d .git ] || git init -q -b main
gh repo view "$repo" >/dev/null 2>&1 || gh repo create "$repo" --private --description "Cherry Move macOS tests" >/dev/null
git remote get-url origin >/dev/null 2>&1 || git remote add origin "git@github.com:$repo.git"
git add -A
git commit -q -m "Test Cherry Move $(date +%F\ %H:%M)" || git commit -q --allow-empty -m "Test again $(date +%F\ %H:%M)"
git push -q -u origin main
echo "Started: https://github.com/$repo/actions"
