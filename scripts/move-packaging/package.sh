#!/bin/sh
# Makes the Windows installer and the Mac disk image from the programs in /parts. Runs inside the
# cherry-move-packager container (scripts/build-move.sh starts it) with the source at /src, the programs
# in /parts, rcodesign at /rcodesign and the results going to /out.
set -eu
HERE=/src/scripts/move-packaging
ART=/parts/art
python3 "$HERE/make_art.py" /src/desktop/icons/cherry-move.svg "$ART"

echo "Windows installer"
version4=$(echo "$VERSION.0.0.0" | cut -d. -f1-4)
makensis -V2 -DVERSION="$VERSION" -DVERSION4="$version4" -DPARTS=/parts -DART="$ART" \
  -DOUT=/out/CherryMove-Setup.exe "$HERE/installer.nsi"

echo "Mac app"
app="/parts/mac/Cherry Move.app"
rm -rf /parts/mac
mkdir -p "$app/Contents/MacOS" "$app/Contents/Resources"
python3 "$HERE/lipo.py" "$app/Contents/MacOS/CherryMove" /parts/CherryMove-darwin-amd64 /parts/CherryMove-darwin-arm64
cp "$ART/CherryMove.icns" "$app/Contents/Resources/"
sed "s/@VERSION@/$VERSION/g" "$HERE/Info.plist" > "$app/Contents/Info.plist"
printf 'APPL????' > "$app/Contents/PkgInfo"
# macOS only opens apps that are signed, at least ad hoc (no Apple account needed).
/rcodesign sign "$app" >/dev/null

echo "Mac disk image"
stage=$(mktemp -d)
cp -a "$app" "$stage/"
cp "$ART/CherryMove.icns" "$stage/.VolumeIcon.icns"
hfs=$(mktemp)
dd if=/dev/zero of="$hfs" bs=1M count=$(( $(du -sm "$stage" | cut -f1) * 12 / 10 + 8 )) status=none
mkfs.hfsplus -v "Cherry Move" "$hfs" >/dev/null
hfsplus "$hfs" addall "$stage" >/dev/null
hfsplus "$hfs" symlink /Applications /Applications >/dev/null
# Finder flags: the volume has a custom icon (.VolumeIcon.icns).
printf '\0\0\0\0\0\0\0\0\4\0\0\0\0\0\0\0\0\0\0\0\0\0\0\0\0\0\0\0\0\0\0\0' > "$stage.finderinfo"
hfsplus "$hfs" attr / com.apple.FinderInfo "$stage.finderinfo" >/dev/null
rm -f /out/CherryMove.dmg
dmg build "$hfs" /out/CherryMove.dmg >/dev/null
rm -rf "$stage" "$stage.finderinfo" "$hfs"
