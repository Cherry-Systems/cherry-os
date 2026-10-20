# Adding the Cherry Store and Cherry Move to your operating system

This kit puts **Cherry Move** and the **Cherry Store** into a Debian- or Ubuntu-based OS, using
exactly the same packages Cherry OS ships (the version is in the file names, e.g. `1.0.20`).
Scripts do the work. Run them; don't recreate what they do by hand.

Read this whole page before changing anything. Everything you need is here. **If something
isn't covered, or you're unsure, ask the user. Don't guess, and don't fill gaps with what other
operating systems usually do.**

## 1. Words used on this page

- **Your OS**: the operating system you're building. The user installs it on a computer.
- **The new computer**: the one your OS gets installed on.
- **The old computer**: the one the person is moving away from. It may run Windows, macOS or any
  Linux. Nothing in this kit is installed on your OS for its sake; it only gets a file to open
  from your install USB.
- **Image root**: the folder holding your OS's root filesystem while you build it (for example
  your debootstrap or mmdebstrap target). Inside it you'd find `etc/`, `usr/`, `var/`.
- **ISO tree**: the folder whose contents become the top level of your ISO / USB stick.

## 2. Rules

1. **Only two packages go into your OS:** `cherry-move` and `cherry-store-client`, the two
   `.deb` files in `install-into-image/`. `install-cherry-apps.sh` installs them. Nothing else
   from this kit is installed into your OS.
2. **`for-the-usb-stick/` is not for your OS.** It contains the Cherry Move app for the old
   computer: a Windows installer (`.exe`), a Mac app (`.dmg`), packages for other Linux
   distros (`.rpm` files and a `cherry-move-app` `.deb`), and plain Linux binaries.
   `add-cherrymove-to-iso.sh` copies them onto your ISO as plain files, for people to open on
   their old computer before they install your OS. Never install, run, unpack, inspect or test
   them on your OS or in your build. Don't use Wine.
3. **Don't make other versions.** Don't build Cherry Move or the Cherry Store from source,
   download them from anywhere else, use a different version, package them for Fedora, Arch,
   Flatpak, Snap or AppImage, or install them with pip.
4. **Don't install `cherry-desktop`.** That's Cherry OS's own package. The Cherry Store inside it
   is the same code as `cherry-store-client`; the rest is Cherry OS's own apps.
5. **Don't change anything the packages install:** no renaming, moving, patching or replacing
   files. `check-cherry-apps.sh` compares every installed file with Cherry OS's copy and fails
   if any differ.
6. **If a script prints `STOP` or `FAIL`, stop** and tell the user exactly what it printed.
   Don't work around it.
7. **Don't publish anything** to GitHub, the Cherry Store or anywhere else, and don't sign up
   for accounts. Nothing here needs an account.

## 3. Check these facts about your OS first (don't assume them)

Run each check and write down the answer; you'll need them in your report.

| Fact | How to check | What it must be |
|---|---|---|
| Base distro and version | `cat <image root>/etc/os-release` | Debian 12 or 13, Ubuntu 24.04 or newer, or a derivative (`ID_LIKE` contains `debian` or `ubuntu`). Ubuntu 22.04 and Linux Mint 21 are **too old**. Anything not apt-based (Fedora, Arch, openSUSE, NixOS, …) **can't** have these apps: stop and tell the user. |
| apt and dpkg present | `ls <image root>/usr/bin/apt-get <image root>/usr/bin/dpkg` | Both exist. |
| CPU architecture | `chroot <image root> dpkg --print-architecture` | `amd64` or `arm64`. (The packages are `all`, but their dependencies must exist for your architecture.) |
| How your image is built | Look at your build scripts | One of: a folder you can `chroot` into (debootstrap/mmdebstrap), live-build (`config/` folder, `lb build`), or something else. If it's something else, ask the user how packages get installed. |
| Network inside the build | `chroot <image root> apt-get update` | Works. apt downloads the dependencies (about 150 packages on a minimal system, mostly GTK). |
| systemd | `ls <image root>/usr/lib/systemd/systemd` | Exists. Without systemd the apps work, but the daily self-update timer doesn't run. Tell the user if so. |
| Desktop environment | Your package lists | A graphical desktop that runs XDG autostart entries: GNOME, KDE Plasma, Xfce, Cinnamon, MATE or Budgie all do. Cherry Move's stick watcher and its after-install restore depend on autostart. |
| Installer (only for section 6, step 4) | Your package lists / build scripts | Calamares, Debian's installer, Ubuntu's installer, or another. |
| Live-session kernel command line (step 4 only) | Your bootloader config (`grub.cfg`, `isolinux.cfg`) | Contains `boot=live` or `boot=casper`. Cherry Move only treats the session as "live" with one of those. |
| Live user's sudo (step 4 only) | In the live session: `sudo -n true && echo ok` | Prints `ok` (passwordless sudo). |

If any answer doesn't match the "must be" column, stop and ask the user before going on.

## 4. What's in the kit

| Path | What it is |
|---|---|
| `START-HERE.md` | This page. |
| `install-cherry-apps.sh` | Installs the two packages into your image root, then runs the check. |
| `check-cherry-apps.sh` | Checks an image root, or a running system, has exactly Cherry OS's files. |
| `add-cherrymove-to-iso.sh` | Puts the old-computer app (`CherryMove/`) at the top of your ISO tree. |
| `install-into-image/` | `cherry-move_<version>_all.deb`, `cherry-store-client_<version>_all.deb`, and `SHA256SUMS`. |
| `for-the-usb-stick/CherryMove.tar` | The old-computer app. Only `add-cherrymove-to-iso.sh` uses it. |
| `testing/test-transfer-stick.img` | A 64 MiB (67.1 MB) USB stick image with a real Cherry Move transfer of four sample files, for testing (section 7). |
| `docs/CHERRY-MOVE-SETUP.md`, `docs/CHERRY-STORE-SETUP.md` | The full reference guides. Where they and this page differ, this page wins. |

## 5. What the two packages put on your OS

Nothing here needs configuring; it's so you know what to expect.

**`cherry-move`** (Cherry Move, for getting files from the old computer, or sending them):

- `/usr/bin/cherry-move`, and its Python code in `/usr/lib/cherry-move/cherryos/`
- the menu entry "Cherry Move" (`/usr/share/applications/cherry-move.desktop`) and its icons
- two autostart entries in `/etc/xdg/autostart/`, which run for every user at login:
  - `cherry-move-watch.desktop`: watches for Cherry transfer USB sticks and opens Cherry Move
    when one is plugged in;
  - `cherry-move-pending.desktop`: restores a transfer picked in the live session, at the new
    user's first login. It does nothing if there isn't one.
- depends on: Python 3, GTK 4, libadwaita 1.2+, `python3-cryptography`, `python3-zstandard`,
  `python3-qrcode`, `python3-pil`, `udisks2`, `xdg-user-dirs`, `ca-certificates`; recommends
  `rclone` (cloud drives and NAS) and `avahi-utils` (finding NAS boxes), which the install
  script adds.

**`cherry-store-client`** (the Cherry Store: apps anyone can publish, installed through apt):

- `/usr/bin/cherry` (command line) and `/usr/bin/cherry-store` (the app), with Python code in
  `/usr/lib/cherry/cherryos/`
- the menu entry "Cherry Store" and its icon
- a polkit action so the app can ask for the password ("Install or remove Cherry Store apps")
- `cherry-store-self-update.timer`, enabled by the package: once a day it runs
  `cherry update -y cherry-store-client cherry-desktop cherry-move`, which updates both apps
  from the Cherry Store (signed with the Cherry OS release key). It needs internet at that time.

The two packages share no files. At run time the Cherry Store needs internet access to
`raw.githubusercontent.com`, `github.com` and `objects.githubusercontent.com`. Cherry Move needs
no internet for USB sticks.

## 6. Steps

1. **Install the apps into your image** (as root):

   ```sh
   sudo ./install-cherry-apps.sh <image root>
   ```

   - If your image root isn't mounted for chroot, the script bind-mounts `/proc`, `/sys` and
     `/dev` itself and unmounts them afterwards. It runs `apt-get update`, installs the two
     packages and `rclone avahi-utils`, then runs the check. The last line should be
     `All checks passed: …`.
   - **live-build** instead: copy the two files from `install-into-image/` to
     `config/packages.chroot/`, add a file `config/package-lists/cherry.list.chroot`
     containing `rclone avahi-utils`, build, and run the check in a VM (step 5).
   - **Another build system:** ask the user. Don't adapt the script.

2. **Put the old-computer app on your ISO:**

   ```sh
   ./add-cherrymove-to-iso.sh <ISO tree> "<your OS's name>"
   ```

   This creates `<ISO tree>/CherryMove/` (about 45 MB) and replaces "Cherry OS" with your OS's
   name in its `README.txt`. With live-build, the ISO tree is `config/includes.binary`.

3. **Updates.** If your OS has its own updater, make it also run `cherry update -y` after its
   apt upgrade. That updates apps people installed from the Cherry Store. (Cherry Move and the
   Cherry Store already update themselves daily.) If your OS has no updater of its own, skip
   this and say so in your report.

4. **Optional: restore straight after installing.** Do this only if your installer can run
   commands after it creates the user, and the facts in section 3 for this step are true.
   In the live session, people can then pick their transfer, install, and get their files at
   first login. Your installer must run, after the new user exists and before the new system is
   unmounted:

   ```sh
   # $TARGET: where the new system is mounted. $NEWUSER: the username the person just chose.
   mkdir -p "$TARGET/var/lib/cherry"
   cp /var/lib/cherry/pending-transfer.json "$TARGET/var/lib/cherry/" 2>/dev/null || true
   chroot "$TARGET" cherry-move --adopt-pending "$NEWUSER" || true
   ```

   - **Calamares:** use the ready-made configuration in section 5 of
     `docs/CHERRY-MOVE-SETUP.md` (that's exactly what Cherry OS uses).
   - **Debian's installer:** `preseed/late_command`, with `/target` and `in-target`.
   - **Ubuntu's installer:** autoinstall `late-commands`, with `/target` and `curtin in-target --`.
   - Cherry Move's "Install" button in the live session runs `install-debian` if it exists,
     otherwise `pkexec calamares`. If your installer is started some other way, create
     `/etc/cherry/move.conf` in the image with the line `INSTALLER=<command that starts it>`.
   - If you're not sure your installer can do this, skip this step and tell the user.

5. **Build your ISO and boot it in a VM** (section 7).

## 7. Testing (in a VM of your built ISO)

Use a VM with at least 4 GB of memory and internet access. Write down what you see at each
step; the user wants the actual results, not "it works".

### 7.1 The check script

Copy the kit into the VM (or share the folder) and run, as the normal user:

```sh
./check-cherry-apps.sh
```

Expected: about 35 `PASS` lines (one per file, plus package and setting checks), no `FAIL`,
and the last line `All checks passed: Cherry Move and the Cherry Store are installed the same way
as on Cherry OS <version>.` Include the whole output in your report.

On a system that has already updated itself through the Cherry Store, a package can be newer than
the kit's. That's a `PASS` (`… newer than the kit's …`), and that package's files aren't compared,
because the store checked the newer ones against Cherry OS's signature. An *older* version is a `FAIL`.

### 7.2 The Cherry Store

In a terminal:

```sh
cherry search hello
```

Expected (the version may be newer):

```
hello-cherry 1.0.1  by github:blhcode
    A tiny test app for the Cherry Store
```

Then:

| Command | Expected |
|---|---|
| `cherry install hello-cherry` | Asks for your password (sudo), then ends with `Installed hello-cherry 1.0.1 (by github:blhcode)` |
| `hello-cherry` | `Hello from the Cherry Store! (hello-cherry 1.0.1)` |
| `cherry list` | Lists `hello-cherry`, `cherry-store-client <version>  by Cherry OS` and `cherry-move <version>  by Cherry OS` |
| `cherry update -y` | `Everything is up to date.` (or it updates, if a newer release came out) |
| `cherry remove hello-cherry` | Removes it (asks for your password, unless sudo still remembers it from a few minutes ago; `sudo -k` first makes it ask). There's no `-y` option for `remove`. |

Then open **Cherry Store** from the app menu:

- the window is titled "Cherry Store" and the dock shows the Cherry Store icon (a generic icon
  means a menu entry was changed);
- type "hello" in the "Search the Cherry Store" box, open `hello-cherry` and click **Install**:
  your desktop's password window appears, saying "Authentication is required to install or
  remove apps"; after the password, it installs;
- the "Installed apps" list shows it; **Remove** removes it (the password window only appears
  again if more than a few minutes have passed since installing).

### 7.3 Cherry Move: the app

Open **Cherry Move** from the app menu:

- the dock shows the Cherry Move icon;
- the first page is "Move your files", with a button to send and one to get files;
- the send side lists your folders (Documents, Pictures, …) with sizes;
- the get side ("Get your files") shows "Transfer USB sticks", "Google Drive, OneDrive or MEGA",
  "A NAS or another computer at home" and "Type a code". The two online options must not be
  greyed out; if they say "Needs rclone 1.60 or newer", rclone is missing.

Don't sign in to any cloud account; that's not part of this test.

### 7.4 Cherry Move: a transfer stick (`testing/test-transfer-stick.img`)

The image holds a real transfer made on 6 Oct 2026 with an older Cherry Move (1.0.6), from a computer
called `sams-old-pc` running Debian 13. The stick doesn't change between kits on purpose: newer
Cherry Move versions must still restore transfers made by older ones, so this also tests that. Its
contents:

| File | Restored to |
|---|---|
| `cherry-move-test.txt` | `~/Documents/cherry-move-test.txt` |
| `School/homework.txt` | `~/Documents/School/homework.txt` |
| `red-square.png` | `~/Pictures/red-square.png` |
| `shopping-list.txt` | `~/Desktop/shopping-list.txt` |

Its transfer code (you only need it for the terminal test, because the stick carries it):
`DHDOF-425EB-TXLXT-5RDUN-ZUL2I-OKXRR-CY64`

1. **Copy the image first** (`cp testing/test-transfer-stick.img /tmp/stick.img`) and use the
   copy. Plugging it in can change it.
2. **Installed system, plug in after login.** Boot an installed copy of your OS, log in, wait
   15 seconds, then attach the stick as a USB device. With QEMU, start the VM with
   `-device qemu-xhci,id=xhci` and attach it while running, from the QEMU monitor:

   ```
   drive_add 0 if=none,id=stick,format=raw,file=/tmp/stick.img
   device_add usb-storage,bus=xhci.0,drive=stick,id=stickdev,removable=on
   ```

   (Or start the VM with it attached:
   `-drive if=none,id=stick,format=raw,file=/tmp/stick.img -device usb-storage,bus=xhci.0,drive=stick,removable=on`.)

   Expected, within about 10 seconds: Cherry Move opens on "Get your files" (on Wayland it may
   stay behind other windows and show a notification "Transfer USB stick plugged in" instead;
   click it). Under "Transfer USB sticks" there's a row `sams-old-pc — 6 Oct 2026`, with
   `4 files, 820 B · from Debian GNU/Linux 13 (trixie) · on OLDPC (67.1 MB)` under it, and a
   **Restore** button.
3. Click **Restore**. Expected: a page titled **Welcome home!** saying
   `4 files from sams-old-pc (Debian GNU/Linux 13 (trixie)) are back in your home folder.`
   Check the four files are where the table says, and that `~/Documents/cherry-move-test.txt`
   contains `If you can read this on the new computer, the transfer worked.`
   The page also has a **Reinstall your apps (4)** button and a ticked box, "Call this computer
   “sams-old-pc” like the old one…". Neither is part of this test: the box only renames the
   computer if you click **Reinstall your apps**, so close the window (**Finish** or its X) and
   the computer keeps its name.
4. Unplug and plug the stick back in. Cherry Move should **not** open again (those files are
   already back).
5. **Terminal test** (works without a desktop): mount the stick, then
   `cherry-move get DHDOF-425EB-TXLXT-5RDUN-ZUL2I-OKXRR-CY64 --usb <where it's mounted>`.
   Expected last line: `Restored 4 files (0 already here) from sams-old-pc.` (or
   `Restored 0 files (4 already here)` if you already did step 3).
6. **Live session to installed system** (only if you did step 4 of section 6): boot the live
   ISO with a fresh copy of the stick attached. Cherry Move opens; it says "You're trying
   <your OS>. Pick your transfer now…". Click **Restore**: it shows **Your files will be
   waiting** and an **Install <your OS>** button, without copying anything yet. Install, keep
   the stick attached, reboot into the installed system and log in as the new user. Expected:
   Cherry Move opens by itself and restores the four files, ending on **Welcome home!**.

## 8. If something goes wrong

| What you see | What it means | What to do |
|---|---|---|
| `STOP: … has no apt and dpkg` | Your OS isn't Debian/Ubuntu-based. | Stop. Tell the user. These apps can't go in. |
| `STOP: apt couldn't install the packages` with `held broken packages` or `gir1.2-adw-1` | The base is too old (libadwaita under 1.2), e.g. Ubuntu 22.04. | Stop. Tell the user. |
| `STOP: apt-get update failed` | No network or broken apt sources inside the image. | Fix the image's network/sources (that's your build, not the kit), then rerun. If unsure, ask. |
| `STOP: the packages in install-into-image/ are damaged` | The `.deb` files were changed or partly copied. | Get a fresh copy of the kit from the user. Don't download the packages elsewhere. |
| `FAIL … is version X, but Cherry OS uses Y` | An older version was installed. | Reinstall with `install-cherry-apps.sh`. |
| `FAIL cherry-move-app is installed` or `FAIL cherry-desktop is installed` | The wrong package went in. | `apt-get purge` it in the image, then rerun the check. |
| `FAIL /… differs from Cherry OS's copy` or `is missing` | Something changed or removed an installed file. | Find what in your build changed it, undo that, reinstall. |
| `FAIL … timer isn't enabled` | The package's setup step was skipped. | `chroot <image root> systemctl enable cherry-store-self-update.timer` |
| `WARN rclone isn't installed` | Cloud drive and NAS options will be greyed out. | Install `rclone` in the image if your distro has it; otherwise tell the user. |
| `cherry search` says it can't reach the store | No internet in the VM, or GitHub is blocked. | Check the VM's network. |
| Cherry Move doesn't open when the stick goes in | Autostart isn't running, the stick isn't mounted, or it was already restored. | Check `/etc/xdg/autostart/cherry-move-watch.desktop` exists, that `udisksctl status` lists the stick, and that you logged in more than 10 seconds before plugging it in. Report what you found. |
| Generic icon in the dock | A menu entry was changed or replaced. | Undo that; the check script also fails on it. |

Anything else: stop and report it with the exact message.

## 9. Your report to the user

Include:

1. The answers to every check in section 3.
2. What you changed in your build (files and commands), and which of section 6's steps you did
   or skipped, and why.
3. The full output of `check-cherry-apps.sh`, from the VM (and from the image, if you ran it
   there).
4. For each test in section 7: what you did, what you expected, and what actually happened.
5. The exact text of any `STOP`, `FAIL`, `WARN` or other error message.
