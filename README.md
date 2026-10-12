# Cherry OS

A friendly desktop for your PC, built on Debian 13. Try it straight from a USB stick, then install it
when you're ready.

**[Download Cherry OS 1.0](https://cherry-cloud.isaiahcannings.workers.dev/download/cherry-os.iso)**
(one 4.7 GB file, `cherry-1.0-amd64.iso`) · [All downloads and checksums](https://github.com/Cherry-Systems/cherry-os/releases/latest)
· [Website](https://cherry-systems.github.io/os/)

## What's inside

- **Five desktops to choose from:** GNOME, KDE Plasma, Cinnamon, Xfce and Budgie.
- **Windows apps just work:** double-click a `.exe` or `.msi` and it runs through Wine.
- **The [Cherry Store](https://cherry-systems.github.io/store/):** apps signed by their publishers and
  virus-scanned, with warnings shown before you install.
- **Cherry Move:** bring your files and settings from your old Windows, Mac or Linux computer.
- **Nvidia graphics from the first boot,** tablets, touch screens, on-screen keyboards and webcams.
- **Secure Boot can stay on,** and Cherry OS installs next to Windows if you want to keep it.

## What you need

- A 64-bit PC (Intel or AMD) with at least 4 GB of memory (8 GB is comfortable) and 30 GB of free disk
  space.
- A USB stick of 8 GB or more. Everything on it is erased.

## Making the USB stick

- **[balenaEtcher](https://etcher.balena.io)** (Windows, macOS, Linux): pick the ISO, pick the stick, click Flash.
- **[Rufus](https://rufus.ie)** (Windows): pick the stick and the ISO, then Start.
- **On Linux:** GNOME Disks, "Restore Disk Image".

## Installing

1. Plug the stick in and restart the computer. While it starts, press the boot menu key and pick the
   USB stick: usually **F12** (Dell, Lenovo, Acer), **F9** (HP), **F8** (ASUS), **F11** (MSI) or **Esc**.
2. Pick a desktop to try. Nothing on the computer changes until you click **Install Cherry OS**.
3. If the picture is garbled or stays black, pick **"Try Cherry OS (safe graphics)"** in the menu.

**Keeping Windows:** choose **Install alongside** in the installer. Turn off Windows' Fast Startup first
(Control Panel → Power Options → "Choose what the power buttons do"), and if Windows uses BitLocker,
have your recovery key ready.

**Nvidia graphics with Secure Boot on:** after installing, Cherry OS asks you to approve the Nvidia
driver once. When the computer restarts, a blue screen says "Press any key to perform MOK management"
for 10 seconds: press a key, choose **Enroll MOK**, **Continue**, **Yes**, type `12345678` and press
Enter, then choose **Reboot**. If you miss it, Cherry OS still starts with its basic graphics driver, and
you can try again any time from **Approve Nvidia Driver** in the menu.

If the computer won't start from the stick because of Secure Boot, turn on **"Allow Microsoft 3rd Party
UEFI CA"** in the firmware settings (the name varies), or turn Secure Boot off.

## Checking the download

The [release](https://github.com/Cherry-Systems/cherry-os/releases/latest) lists the SHA-256 checksum
of the ISO in `SHA256SUMS`. On Linux: `sha256sum -c SHA256SUMS --ignore-missing`.

The ISO is stored on GitHub in pieces (GitHub allows at most 2 GB per file); the download link above
joins them into the one file. To join them yourself:
`cat cherry-1.0-amd64.iso.part* > cherry-1.0-amd64.iso` (or on Windows,
`copy /b cherry-1.0-amd64.iso.part1 + cherry-1.0-amd64.iso.part2 + cherry-1.0-amd64.iso.part3 cherry-1.0-amd64.iso`).

## Cherry Move

Cherry Move is on every Cherry OS USB stick, and in the
[release](https://github.com/Cherry-Systems/cherry-os/releases/latest) for Windows
(`CherryMove-Setup.exe`), macOS (`CherryMove.dmg`) and Linux (`.deb`, `.rpm` or the standalone
`cherry-move-linux-*`).
