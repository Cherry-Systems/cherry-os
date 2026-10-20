# Installing Cherry OS

## What you need

- A 64-bit PC (Intel or AMD) with at least 4 GB of memory (8 GB is comfortable) and 30 GB of free disk
  space. Computers from about 2010 onwards are fine, including ones that only have an old-style BIOS.
- A USB stick of 8 GB or more. Everything on it is erased.
- The Cherry OS file, `cherry-1.0-amd64.iso` (about 4.3 GB).

## Making the USB stick

Use any one of these:

- **[balenaEtcher](https://etcher.balena.io)** (Windows, macOS, Linux): pick the ISO, pick the stick,
  click Flash.
- **[Rufus](https://rufus.ie)** (Windows): pick the stick and the ISO, then Start. If it asks, "ISO
  image mode" and "DD image mode" both work.
- **[Ventoy](https://www.ventoy.net)**: copy the ISO onto a Ventoy stick next to your other ISOs.
- **On Linux**, GNOME Disks ("Restore Disk Image"), or in a terminal (replace `sdX` with the stick,
  double-checked with `lsblk`, because `dd` erases whatever it's pointed at):

  ```sh
  sudo dd if=cherry-1.0-amd64.iso of=/dev/sdX bs=4M status=progress oflag=sync
  ```

The same file also works burnt to a DVD.

## Starting from the stick

1. Plug the stick in and restart the computer.
2. While it starts, press the boot menu key and pick the USB stick. It's usually **F12** (Dell, Lenovo,
   Acer), **F9** (HP), **F8** (ASUS), **F11** (MSI) or **Esc**.
3. The Cherry menu lets you pick which desktop to try. Each one runs straight from the stick, so
   nothing on the computer changes until you click **Install Cherry OS**.

**Secure Boot can stay on.** Cherry OS starts with the same Microsoft-signed loader as Debian and
Ubuntu, so there's no need to change any firmware settings on most computers.

If the computer refuses to start from the stick with a "Secure Boot violation" or similar message, it
has Microsoft's third-party certificate turned off. This is common on Windows 11 "Secured-core" PCs,
including some Lenovo and Microsoft Surface models. Open the firmware settings (often F2 or Del at
startup) and turn on **"Allow Microsoft 3rd Party UEFI CA"** (the name varies), or turn Secure Boot off.

If the picture is garbled or stays black, pick **"Try Cherry OS (safe graphics)"** in the menu.

## Keeping Windows (dual boot)

Cherry OS can be installed next to Windows. Choose **Install alongside** in the installer and drag the
divider to decide how much space each one gets. Afterwards, a menu at startup lets you pick Cherry OS
or Windows.

Before you start, in Windows:

1. **Back up anything important.** Resizing partitions is safe, but a power cut halfway through isn't.
2. **Turn off Fast Startup**: Control Panel → Power Options → "Choose what the power buttons do" →
   "Change settings that are currently unavailable" → untick "Turn on fast startup". With it on,
   Windows never fully shuts down and its disk can't be resized. Holding **Shift** while clicking
   Shut down also works, once. If you forget, the installer notices and says so before it changes
   anything.
3. **If Windows uses BitLocker or "Device encryption"**, find your recovery key first (it's at
   [aka.ms/myrecoverykey](https://aka.ms/myrecoverykey) if you sign in with a Microsoft account).
   Windows may ask for it the first time it starts after Cherry OS is installed. The installer can't
   shrink an encrypted Windows partition: shrink it in Windows' Disk Management ("Shrink Volume"),
   then pick the free space in the installer's manual partitioning.

Cherry OS takes care of the rest:

- The startup menu starts **whichever system you used last**, so when a Windows update restarts the
  computer, it goes back into Windows to finish.
- **The clock stays right in both.** Windows keeps the computer's clock in local time, and Cherry OS
  does the same when it finds Windows.

## After installing

Take the stick out when the installer says so and restart. If you moved files from your old computer
with Cherry Move, they come back by themselves after you sign in. Cherry Center opens on the first
start to help with the rest.
