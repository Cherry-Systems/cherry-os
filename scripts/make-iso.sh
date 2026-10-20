#!/usr/bin/env bash
# Makes the bootable hybrid ISO from a prepared ISO tree: boots as a DVD or a USB stick, on BIOS and on
# UEFI with or without Secure Boot (the same layout Debian and Ubuntu use).
#   scripts/make-iso.sh ISO_DIR OUT.iso LABEL "INFO TEXT" WORK_DIR
# ISO_DIR needs boot/grub/grub.cfg and the installer pool (pool/main) with shim-signed,
# shim-helpers-amd64-signed and grub-efi-amd64-signed in it.
set -euo pipefail
ISO_DIR=$1 ISO=$2 LABEL=$3 INFO=$4 WORK=$5

# UEFI: Microsoft-signed shim loads Debian's signed GRUB. Debian's CD GRUB (gcdx64) finds the ISO by
# /.disk/info and reads /boot/grub/grub.cfg. Taken from the pool so the stick and the installed system match.
BOOT_DEBS="$WORK/boot-debs"
rm -rf "$BOOT_DEBS"
for p in shim-signed shim-helpers-amd64-signed grub-efi-amd64-signed; do
  deb=$(find "$ISO_DIR/pool/main" -name "${p}_*_amd64.deb" | sort -V | tail -n1)
  [[ -n $deb ]] || { echo "error: no $p in $ISO_DIR/pool/main" >&2; exit 1; }
  dpkg-deb -x "$deb" "$BOOT_DEBS"
done
mkdir -p "$ISO_DIR/.disk" "$ISO_DIR/boot/grub/fonts" "$ISO_DIR/boot/grub/i386-pc"
echo "$INFO" > "$ISO_DIR/.disk/info"
cp /usr/share/grub/unicode.pf2 "$ISO_DIR/boot/grub/fonts/"
EFI_IMG="$WORK/efi.img"
rm -f "$EFI_IMG"
mkfs.vfat -n CHERRY_EFI -C "$EFI_IMG" 10240 >/dev/null
mmd -i "$EFI_IMG" ::/EFI ::/EFI/boot
mcopy -i "$EFI_IMG" "$BOOT_DEBS/usr/lib/shim/shimx64.efi.signed" ::/EFI/boot/bootx64.efi
mcopy -i "$EFI_IMG" "$BOOT_DEBS/usr/lib/shim/mmx64.efi.signed" ::/EFI/boot/mmx64.efi
mcopy -i "$EFI_IMG" "$BOOT_DEBS/usr/lib/grub/x86_64-efi-signed/gcdx64.efi.signed" ::/EFI/boot/grubx64.efi
# Also as plain files, for USB tools on Windows that copy files instead of writing the image.
mkdir -p "$ISO_DIR/EFI/boot"
mcopy -o -i "$EFI_IMG" ::/EFI/boot/bootx64.efi ::/EFI/boot/mmx64.efi ::/EFI/boot/grubx64.efi "$ISO_DIR/EFI/boot/"

# BIOS: GRUB's El Torito image, with its modules on the ISO for grub.cfg's insmod lines.
cp /usr/lib/grub/i386-pc/*.mod /usr/lib/grub/i386-pc/*.lst "$ISO_DIR/boot/grub/i386-pc/"
grub-mkimage -O i386-pc-eltorito -d /usr/lib/grub/i386-pc -p /boot/grub -o "$ISO_DIR/boot/grub/i386-pc/eltorito.img" \
  biosdisk iso9660 part_msdos part_gpt normal configfile search linux echo test all_video gfxterm png font

# Written beside the old ISO and renamed over it, so a VM still booted from the old one keeps reading it intact.
xorriso -as mkisofs -r -V "$LABEL" -J -joliet-long -l -iso-level 3 \
  -partition_offset 16 --grub2-mbr /usr/lib/grub/i386-pc/boot_hybrid.img --mbr-force-bootable \
  -append_partition 2 0xef "$EFI_IMG" -appended_part_as_gpt \
  -c boot/boot.cat \
  -b boot/grub/i386-pc/eltorito.img -no-emul-boot -boot-load-size 4 -boot-info-table --grub2-boot-info \
  -eltorito-alt-boot -e '--interval:appended_partition_2:all::' -no-emul-boot \
  -o "$ISO.part" "$ISO_DIR"
mv -f "$ISO.part" "$ISO"
