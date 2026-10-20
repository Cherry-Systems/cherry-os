#!/bin/sh
# Boots a throwaway Windows 10 VM for testing Cherry Move (installs itself unattended on first run).
#   WIN_ISO=~/Downloads/Win10_22H2_English_x64.iso scripts/win-test/run-windows-vm.sh
# WIN_FIRMWARE=uefi installs Windows the way most PCs have it: UEFI with Secure Boot on and a GPT disk.
# The firmware's variables are kept as $VM/uefi-secure-vars.fd, like run-qemu.sh --secure-boot.
# Drive it with scripts/qmp.py (VM_DIR=build/win10) and scripts/win-test/winvm.py.
set -e
cd "$(dirname "$0")/../.."
VM=${VM_DIR:-build/win10}
: "${WIN_ISO:?set WIN_ISO to a Windows 10 ISO}"
mkdir -p "$VM"
[ -f "$VM/disk.qcow2" ] || qemu-img create -q -f qcow2 "$VM/disk.qcow2" 60G
xml=scripts/win-test/autounattend.xml
machine=q35 firmware=
if [ "${WIN_FIRMWARE:-bios}" = uefi ]; then
  python3 - "$xml" "$VM/autounattend.xml" <<'PY'
import re, sys
xml = open(sys.argv[1]).read()
gpt = """<CreatePartitions>
            <CreatePartition wcm:action="add"><Order>1</Order><Type>EFI</Type><Size>260</Size></CreatePartition>
            <CreatePartition wcm:action="add"><Order>2</Order><Type>MSR</Type><Size>16</Size></CreatePartition>
            <CreatePartition wcm:action="add"><Order>3</Order><Type>Primary</Type><Extend>true</Extend></CreatePartition>
          </CreatePartitions>
          <ModifyPartitions>
            <ModifyPartition wcm:action="add"><Order>1</Order><PartitionID>1</PartitionID><Format>FAT32</Format><Label>System</Label></ModifyPartition>
            <ModifyPartition wcm:action="add"><Order>2</Order><PartitionID>2</PartitionID></ModifyPartition>
            <ModifyPartition wcm:action="add"><Order>3</Order><PartitionID>3</PartitionID><Format>NTFS</Format><Label>Windows</Label><Letter>C</Letter></ModifyPartition>
          </ModifyPartitions>"""
xml = re.sub(r"<CreatePartitions>.*?</ModifyPartitions>", gpt, xml, flags=re.S)
xml = xml.replace("<InstallTo><DiskID>0</DiskID><PartitionID>1</PartitionID></InstallTo>",
                  "<InstallTo><DiskID>0</DiskID><PartitionID>3</PartitionID></InstallTo>")
open(sys.argv[2], "w").write(xml)
PY
  xml=$VM/autounattend.xml
  [ -f "$VM/uefi-secure-vars.fd" ] || cp /usr/share/OVMF/OVMF_VARS_4M.ms.fd "$VM/uefi-secure-vars.fd"
  machine=q35,smm=on
  firmware="-global driver=cfi.pflash01,property=secure,value=on
    -drive if=pflash,format=raw,readonly=on,file=/usr/share/OVMF/OVMF_CODE_4M.ms.fd
    -drive if=pflash,format=raw,file=$VM/uefi-secure-vars.fd"
fi
genisoimage -quiet -J -r -V CHERRYTEST -o "$VM/unattend.iso" \
  "$xml" scripts/win-test/agent.ps1 scripts/win-test/agent.cmd
rm -f "$VM/qmp.sock"
# shellcheck disable=SC2086
exec qemu-system-x86_64 -enable-kvm -machine $machine -cpu host -smp 4 -m 4096 -rtc base=localtime $firmware \
  -device ahci,id=ahci \
  -drive file="$VM/disk.qcow2",if=none,id=disk -device ide-hd,drive=disk,bus=ahci.0 \
  -drive file="$WIN_ISO",media=cdrom,if=none,id=win,readonly=on -device ide-cd,drive=win,bus=ahci.1 \
  -drive file="$VM/unattend.iso",media=cdrom,if=none,id=unattend,readonly=on -device ide-cd,drive=unattend,bus=ahci.2 \
  -boot order=cd -nic user,model=e1000 \
  -device qemu-xhci,id=xhci -device usb-tablet,bus=xhci.0 \
  -vga std -display none -qmp unix:"$VM/qmp.sock",server,nowait
