#!/usr/bin/env bash
# Boots Cherry OS in a QEMU virtual machine.
#   run-qemu.sh               live ISO, BIOS firmware
#   run-qemu.sh --uefi        live ISO, UEFI firmware
#   run-qemu.sh --secure-boot UEFI with Secure Boot on and Microsoft's keys, like most PCs sold since 2012
#   run-qemu.sh --disk        also attach a blank 64 GB virtual SSD to install onto
#   run-qemu.sh --installed   boot the virtual SSD without the ISO, to test the installed system
#   run-qemu.sh --usb FILE    plug in a disk image as a USB stick (e.g. a Cherry transfer USB); repeatable.
#                             Swap sticks while it runs with scripts/qmp.py usb-add / usb-remove.
#   run-qemu.sh --headless    no window; watch it with QMP screendumps (build/vm/qmp.sock)
# Options can be combined; the UEFI boot entries persist between runs like on real hardware.
# Anything after `--` goes straight to QEMU.
# ISO=FILE boots another ISO. SSD_BUS=sata attaches the virtual SSD as SATA instead of NVMe, for a disk
# that Windows was installed on as SATA (scripts/win-test), since Windows can't start from a new kind of controller.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
# shellcheck source=../config/os.conf
source config/os.conf

ISO="${ISO:-out/${OS_ID}-${OS_VERSION}-${ARCH}.iso}"
VM_DIR="${VM_DIR:-build/vm}"
DISK_SIZE="64G"
OVMF_CODE="/usr/share/OVMF/OVMF_CODE_4M.fd"
OVMF_VARS="/usr/share/OVMF/OVMF_VARS_4M.fd"

uefi=no secure=no disk=no installed=no headless=no
usb=() extra=()
while (($#)); do
  case "$1" in
    --uefi) uefi=yes ;;
    --secure-boot) uefi=yes secure=yes ;;
    --disk) disk=yes ;;
    --installed) installed=yes disk=yes ;;
    --headless) headless=yes ;;
    --usb) usb+=("$2"); shift ;;
    --) shift; extra=("$@"); break ;;
    *) echo "unknown option: $1" >&2; exit 1 ;;
  esac
  shift
done

mkdir -p "$VM_DIR"
# The USB tablet lets the pointer move in and out of the window without being grabbed.
args=(-machine q35 -m 6G -smp 4 -vga none
      -netdev user,id=net0 -device virtio-net-pci,netdev=net0,id=nic0 -audiodev none,id=snd
      -device qemu-xhci,id=xhci -device usb-tablet -device usb-kbd -name "$OS_NAME"
      -qmp "unix:$VM_DIR/qmp.sock,server=on,wait=off")
if [[ $headless == yes && $installed == yes ]]; then
  # The installed desktops' compositors only update the parts of a std VGA screen that change, so
  # screendumps come out black. With virtio-vga they're whole, after the guest repaints.
  args+=(-device virtio-vga,xres=1600,yres=900 -display none)
elif [[ $headless == yes ]]; then
  # virtio-vga only hands QMP a new frame when the guest repaints, so screendumps lag behind.
  args+=(-device VGA,xres=1600,yres=900 -display none)
else
  args+=(-device virtio-vga,xres=1600,yres=900 -display gtk,zoom-to-fit=on)
fi

if [[ -w /dev/kvm ]]; then
  args+=(-enable-kvm -cpu host)
else
  echo "warning: /dev/kvm not accessible, the VM will be very slow" >&2
fi

vars=uefi-vars.fd
if [[ $secure == yes ]]; then
  OVMF_CODE="/usr/share/OVMF/OVMF_CODE_4M.ms.fd" OVMF_VARS="/usr/share/OVMF/OVMF_VARS_4M.ms.fd"
  vars=uefi-secure-vars.fd  # holds the Secure Boot keys, so it can't be shared with plain UEFI
  args[1]=q35,smm=on  # Secure Boot firmware needs SMM
  args+=(-global driver=cfi.pflash01,property=secure,value=on)
fi
if [[ $uefi == yes ]]; then
  [[ -f $VM_DIR/$vars ]] || cp "$OVMF_VARS" "$VM_DIR/$vars"
  args+=(-drive "if=pflash,format=raw,readonly=on,file=$OVMF_CODE"
         -drive "if=pflash,format=raw,file=$VM_DIR/$vars")
fi

if [[ $disk == yes ]]; then
  [[ -f $VM_DIR/ssd.qcow2 ]] || qemu-img create -q -f qcow2 "$VM_DIR/ssd.qcow2" "$DISK_SIZE"
  args+=(-drive "file=$VM_DIR/ssd.qcow2,if=none,id=ssd,format=qcow2")
  if [[ ${SSD_BUS:-nvme} == sata ]]; then
    args+=(-device ahci,id=ahci -device ide-hd,drive=ssd,bus=ahci.0)
  else
    args+=(-device "nvme,drive=ssd,serial=cherryssd")
  fi
fi

for i in "${!usb[@]}"; do
  args+=(-drive "file=${usb[$i]},if=none,id=stick$i,format=raw"
         -device "usb-storage,bus=xhci.0,drive=stick$i,id=dev-stick$i,removable=on")
done

if [[ $installed == yes ]]; then
  [[ -f $VM_DIR/ssd.qcow2 ]] || { echo "No virtual SSD yet — install first with 'make run-install'." >&2; exit 1; }
else
  [[ -f $ISO ]] || { echo "No ISO at $ISO — run 'make build' first." >&2; exit 1; }
  args+=(-cdrom "$ISO" -boot once=d)
fi

exec qemu-system-x86_64 "${args[@]}" "${extra[@]}"
