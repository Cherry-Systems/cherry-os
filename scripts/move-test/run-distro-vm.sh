#!/bin/sh
# Boots another distro's live ISO in a headless QEMU VM for testing the Cherry Move Linux app there.
#   scripts/move-test/run-distro-vm.sh ISO VM_DIR
# Watch and drive it with VM_DIR=… scripts/qmp.py (shot, click, type, key, usb-add FILE NAME, usb-remove NAME).
# The VM reaches this computer as 10.0.2.2.
set -e
iso=$(realpath "$1") dir=$2
mkdir -p "$dir"
umask 000
exec qemu-system-x86_64 -machine q35 -m 4G -smp 4 -enable-kvm -cpu host \
  -vga none -device virtio-vga,xres=1600,yres=900 -display none \
  -nic user,model=virtio-net-pci -audiodev none,id=snd \
  -device qemu-xhci,id=xhci -device usb-tablet -device usb-kbd \
  -qmp "unix:$dir/qmp.sock,server=on,wait=off" \
  -cdrom "$iso" -boot d
