#!/bin/sh
# A macOS VM for testing Cherry Move's Mac app (Intel side of the universal app), using OSX-KVM
# (https://github.com/kholia/OSX-KVM). Headless: drive it with scripts/qmp.py (VM_DIR=build/macvm) and SSH.
#   scripts/move-test/run-mac-vm.sh [install]     install: also attach the macOS recovery image
# First time: git clone https://github.com/kholia/OSX-KVM build/macvm/OSX-KVM, then in there
# `python3 fetch-macOS-v2.py -s sonoma` and `qemu-img convert BaseSystem.dmg -O raw BaseSystem.img`.
# SSH (once Remote Login is on in the VM): ssh -p ${MAC_SSH_PORT:-2232} USER@127.0.0.1
set -e
cd "$(dirname "$0")/../.."
dir=build/macvm
kvm=$dir/OSX-KVM
[ -f "$kvm/OpenCore/OpenCore.qcow2" ] || { echo "clone OSX-KVM into $kvm first (see the top of this script)" >&2; exit 1; }
[ -f "$dir/disk.qcow2" ] || qemu-img create -q -f qcow2 "$dir/disk.qcow2" 64G
[ -f "$dir/OVMF_VARS.fd" ] || cp "$kvm/OVMF_VARS-1920x1080.fd" "$dir/OVMF_VARS.fd"
install=""
[ "$1" = install ] && install="-device ide-hd,bus=sata.3,drive=InstallMedia
  -drive id=InstallMedia,if=none,file=$kvm/BaseSystem.img,format=raw,snapshot=on"
exec qemu-system-x86_64 -enable-kvm -m "${MAC_RAM:-6144}" \
  -cpu Skylake-Client,-hle,-rtm,kvm=on,vendor=GenuineIntel,+invtsc,vmware-cpuid-freq=on,+ssse3,+sse4.2,+popcnt,+avx,+aes,+xsave,+xsaveopt,check \
  -machine q35 -smp 4,cores=2,sockets=1 \
  -device qemu-xhci,id=xhci -device usb-kbd,bus=xhci.0 -device usb-tablet,bus=xhci.0 \
  -device isa-applesmc,osk="ourhardworkbythesewordsguardedpleasedontsteal(c)AppleComputerInc" \
  -drive if=pflash,format=raw,readonly=on,file="$kvm/OVMF_CODE_4M.fd" \
  -drive if=pflash,format=raw,file="$dir/OVMF_VARS.fd" \
  -smbios type=2 \
  -device ich9-ahci,id=sata \
  -drive id=OpenCoreBoot,if=none,snapshot=on,format=qcow2,file="$kvm/OpenCore/OpenCore.qcow2" \
  -device ide-hd,bus=sata.2,drive=OpenCoreBoot \
  $install \
  -drive id=MacHDD,if=none,file="$dir/disk.qcow2",format=qcow2 -device ide-hd,bus=sata.4,drive=MacHDD \
  -netdev user,id=net0,hostfwd=tcp:127.0.0.1:"${MAC_SSH_PORT:-2232}"-:22 \
  -device virtio-net-pci,netdev=net0,id=net0,mac=52:54:00:c9:18:27 \
  -device VGA,vgamem_mb=64 -display none \
  -qmp unix:"$dir/qmp.sock",server,nowait
