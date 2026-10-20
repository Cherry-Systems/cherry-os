# Setting up Cherry Move on another operating system

This guide is for whoever builds a Linux OS image and wants **Cherry Move** in it: the app that
brings someone's files over from their old computer. It assumes no knowledge of Cherry OS.

**If you were given the Cherry kit** (`START-HERE.md`), its `install-cherry-apps.sh` and
`add-cherrymove-to-iso.sh` scripts do steps 1 and 2 below with the same files Cherry OS uses. The
`CherryMove/` folder is for people's **old** computers: it only goes on your install USB as files,
and is never installed or run on your OS.

The short version:

1. Install the `cherry-move` package into the image.
2. Put the `CherryMove/` folder (the app for the old computer) on your install USB.
3. Optionally, make your installer run two commands so files picked in the live session come
   back after installing (section 5).

Everything else here is detail.

---

## 1. What Cherry Move is

Cherry Move has two halves.

| Half | Runs on | What it does |
|---|---|---|
| **The Cherry Move app** (`CherryMove/` folder) | The **old** computer: Windows 10/11, macOS 11+, or any Linux | Packs up documents, pictures, music, videos, desktop, downloads and browser bookmarks. Compresses and encrypts them, then stores them on USB stick(s), the person's own Google Drive / OneDrive / MEGA, or a NAS. Gives them a 34-character transfer code. |
| **The `cherry-move` package** | **Your** OS (the new computer) | Gets the files back, given the stick or the code. Also sends, so your OS can be the "old" computer next time. Notices transfer sticks by itself, and can restore straight after installing. |

How a move works:

- **USB stick.** The old computer writes a `CHERRY-TRANSFER/` folder onto the stick. It holds
  `cherry-transfer.json`, which describes the transfer, plus encrypted chunks. When that stick is
  plugged into your OS, Cherry Move opens and offers to restore it. If the files didn't fit, the
  app spreads them over several sticks and asks for each one in turn.
- **Cloud drive or NAS.** The files go to the person's own storage, through
  [rclone](https://rclone.org). Only the code goes on a USB stick, or onto a phone (as a QR code),
  or on paper. On the new computer they sign in to the same storage and type the code.
- **Nothing is sent to Cherry Systems.** Files go straight between the person's own computers and
  storage. The encryption key is part of the code, so the cloud or NAS copy can't be read without
  the code.
- **A USB stick move is not locked.** `cherry-transfer.json` holds the code in plain text, so the
  stick restores by itself. Anyone with the stick can restore everything on it, including the
  keyring's saved passwords when browsers, personal files or apps were brought. Without Cherry
  Move, the chunks are unreadable, but the JSON also shows the transfer's name, the old computer's
  name and OS, the date, the size and the file count. Tell people to keep the stick safe and delete
  `CHERRY-TRANSFER/` once their files are back.

## 2. Requirements for your OS

| Need | Why |
|---|---|
| Debian 12 or Ubuntu 24.04 or newer, or a derivative of those (Linux Mint 22+, Pop!_OS 24.04+, …) | The package is a `.deb`. It needs libadwaita 1.2 or newer, so **Ubuntu 22.04 and Linux Mint 21 are too old**. (The Cherry Store app does support those; Cherry Move doesn't.) |
| `python3` 3.9+, `python3-gi`, `gir1.2-gtk-4.0`, `gir1.2-adw-1` (1.2+) | The app is Python with GTK 4 and libadwaita. |
| `python3-cryptography`, `python3-zstandard`, `python3-qrcode`, `python3-pil` | Encryption, compression, and the QR code for phones. |
| `udisks2` | Finds and mounts USB sticks (`udisksctl`). Sticks must end up under `/media/` or `/run/media/`, which is udisks's default. |
| `xdg-user-dirs` | Finds Documents, Pictures and the other folders, in the user's language. |
| `ca-certificates` | Cloud sign-ins use HTTPS. |
| `rclone` 1.60+ (recommended) | Google Drive, OneDrive, MEGA and NAS (SMB, SFTP, WebDAV). Without it, only USB sticks work, and those options are greyed out with a note. Debian 12 and Ubuntu 24.04 ship 1.60.1. |
| `avahi-utils` (recommended) | Lists NAS boxes on the home network, so people don't have to type an address. |
| `sudo`, passwordless for the live user (only for section 5) | In the live session, the transfer that was picked is saved to `/var/lib/cherry/` with `sudo -n`. Most live systems already allow that. |
| A desktop that runs XDG autostart entries | Watches for transfer sticks, and restores after installing. GNOME, KDE Plasma, Xfce, Cinnamon, MATE and Budgie all do this. |

The `.deb` declares these as dependencies or recommendations, so apt pulls them in.

## 3. Installing the package into your image

You were given `cherry-move_<version>_all.deb`. To rebuild it from the Cherry OS source, run
`scripts/build-debs.sh OUT_DIR`, which makes `cherry-move` and Cherry OS's own `cherry-desktop`.

What it contains:

```
/usr/bin/cherry-move                               the app (and its command-line mode)
/usr/lib/cherry-move/cherryos/                     its Python library (private to Cherry Move)
/usr/share/applications/cherry-move.desktop        menu entry: "Cherry Move"
/usr/share/icons/hicolor/scalable/apps/cherry-move.svg, cherry-logo.svg
/etc/xdg/autostart/cherry-move-watch.desktop       at login: watch for transfer USB sticks
/etc/xdg/autostart/cherry-move-pending.desktop     at login: restore a transfer picked before installing
```

Install it in the image's chroot like any local package:

```sh
# debootstrap / mmdebstrap / a chroot you control:
cp cherry-move_*_all.deb "$CHROOT/tmp/"
chroot "$CHROOT" apt-get install -y /tmp/cherry-move_*_all.deb
rm "$CHROOT"/tmp/cherry-move_*_all.deb

# live-build: drop the file in config/packages.chroot/ and it's installed automatically
cp cherry-move_*_all.deb config/packages.chroot/
```

It installs next to the Cherry Store app (`cherry-store-client`) without any clash. The two
packages don't share any files.

**Updates:** `cherry-move` is published through the Cherry Store, signed with the Cherry OS
release key. If the Cherry Store app (`cherry-store-client`) is installed too, its daily timer
updates Cherry Move automatically. Without the store app, nothing updates it; ship new versions
in your OS updates instead.

### What not to change

- **Keep the menu entry's name and `StartupWMClass=org.cherryos.Move`.** The window reports that
  class, and docks match on it. If they differ, docks and app switchers show a nameless window
  with a generic icon instead of Cherry Move.
- **Keep `/usr/bin/cherry-move` at that path.** The autostart entries, the installer step in
  section 5 and the app itself all run it by name.

## 4. What happens by itself after installing

The package includes two autostart entries. They run for every user at login.

- **`cherry-move --watch`**, started 8 seconds after login: checks for transfer sticks every
  4 seconds, mounting new sticks through udisks. When one is plugged in, it opens Cherry Move on
  "Get your files". It ignores a stick this same computer just sent from. On Wayland, a
  background program can't bring a window to the front, so Cherry Move also shows a
  notification, which people can click.
- **`cherry-move --restore-pending`**, started 6 seconds after login: if
  `~/.config/cherry/pending-transfer.json` exists, it opens Cherry Move and restores that
  transfer, asking for the USB stick or the cloud sign-in if needed. Otherwise it exits straight
  away without showing anything.

Restored files go into the person's home folder: Documents into Documents, and so on. A file
that already exists there is left alone and counted as "already here". Nothing on the old
computer or the USB stick is deleted. With a cloud drive or NAS, the last screen offers to delete
the copy stored there.

If the old computer ran Linux, the last screen also offers "Reinstall your apps". It installs
whichever of these are available on your OS:

- the person's old Flatpak apps (from Flathub, if `flatpak` is installed);
- their Cherry Store apps (if the `cherry` command is installed);
- their Debian packages that your apt sources have, through `pkexec`.

## 5. Optional: restore straight after installing

This is Cherry Move's nicest trick, and it needs a small hook in your installer.

What the person sees:

1. They boot your live USB and plug in their transfer stick (or open Cherry Move and type a
   code).
2. Cherry Move notices it's running in the live session. It says "You're trying *Your OS*. Pick
   your transfer now and your files will be restored straight after *Your OS* is installed."
3. They pick the transfer. Cherry Move saves what to restore, though not the files themselves,
   to `/var/lib/cherry/pending-transfer.json`, and shows an "Install *Your OS*" button.
4. They install. Your installer copies that file into the new system and gives it to the new
   user.
5. At their first login, the `cherry-move --restore-pending` autostart entry brings the files
   back.

What your OS needs for this:

- **Live-session detection.** Cherry Move treats the session as live when the kernel command
  line contains `boot=live` (Debian live-boot / live-build) or `boot=casper` (Ubuntu). Anything
  else counts as an installed system, and steps 2–4 simply don't happen.
- **Passwordless `sudo` for the live user**, to write `/var/lib/cherry/pending-transfer.json`.
- **The installer hook**, which runs after the new user exists and before the target is
  unmounted:

  ```sh
  # $TARGET is the mounted new system, $NEWUSER the username just created.
  # 1. outside the new system: copy the live session's file in (it's only in the live session's memory)
  mkdir -p "$TARGET/var/lib/cherry"
  cp /var/lib/cherry/pending-transfer.json "$TARGET/var/lib/cherry/" 2>/dev/null || true
  # 2. inside the new system (as root): move it to the new user's ~/.config/cherry/, owned by them
  chroot "$TARGET" cherry-move --adopt-pending "$NEWUSER" || true
  ```

  `cherry-move --adopt-pending` prints "No transfer was picked before installing." and exits
  successfully if there's nothing to do, so it's safe to run on every install.

- **The "Install" button** runs `install-debian` if it exists, otherwise `pkexec calamares`. If
  your installer is something else, set its command in `/etc/cherry/move.conf` (section 6).

### Calamares

This is exactly what Cherry OS does. Add two `shellprocess` instances:

`/etc/calamares/modules/shellprocess@cherrytransfercopy.conf`

```yaml
dontChroot: true
timeout: 30
script:
  - "-mkdir -p ${ROOT}/var/lib/cherry"
  - "-cp /var/lib/cherry/pending-transfer.json ${ROOT}/var/lib/cherry/pending-transfer.json"
```

`/etc/calamares/modules/shellprocess@cherrytransfer.conf`

```yaml
dontChroot: false
timeout: 30
script:
  - "-cherry-move --adopt-pending ${USER}"
```

Then, in `/etc/calamares/settings.conf`, declare them under `instances:` and run them in the
`exec` sequence after `users` and before `umount`:

```yaml
instances:
- id: cherrytransfercopy
  module: shellprocess
  config: shellprocess@cherrytransfercopy.conf
- id: cherrytransfer
  module: shellprocess
  config: shellprocess@cherrytransfer.conf

sequence:
  # … show: …
  - exec:
    # … partition, mount, unpackfs, …, users, displaymanager, …, bootloader, …
    - shellprocess@cherrytransfercopy
    - shellprocess@cherrytransfer
    - umount
```

In Calamares, `${ROOT}` is the target's mount point and `${USER}` is the new username. The
leading `-` makes a failing command harmless, so an install never fails because of Cherry Move.

### Other installers

Run the two commands from the snippet above at the equivalent point:

- **Debian's text installer:** `preseed/late_command`, with `/target` and `in-target`.
- **Ubuntu's installer:** autoinstall `late-commands`, with `/target` and `curtin in-target --`.

Whichever installer you use, the username has to be the one the person just chose.

## 6. Settings (optional)

`/etc/cherry/move.conf`, with `KEY=value` lines:

| Key | Default | Meaning |
|---|---|---|
| `OS_NAME` | `NAME=` from `/etc/os-release` | The name on the live-session screens: "You're trying …", "Install …", "After … is installed". |
| `INSTALLER` | `command -v install-debian >/dev/null && exec install-debian \|\| exec pkexec calamares` | Shell command run by the live session's "Install" button. |

`/etc/cherry/oauth.json` (optional) sets your own OAuth app for cloud sign-ins:

```json
{"gdrive": {"client_id": "…", "client_secret": "…"}, "onedrive": {"client_id": "…", "client_secret": "…"}}
```

- Without it, rclone's own built-in app is used. That works, but it's shared by every rclone
  user, and Google can slow it down at busy times.
- The environment variables `CHERRY_GDRIVE_CLIENT_ID`, `CHERRY_GDRIVE_CLIENT_SECRET`,
  `CHERRY_ONEDRIVE_CLIENT_ID` and `CHERRY_ONEDRIVE_CLIENT_SECRET` override the file.
- Google Drive copies go into the app's hidden app folder, not into the person's visible files.
- If you set your own Google app, give it the `drive.appfolder` scope.

## 7. The app for the old computer, on your install USB

People need the Cherry Move app on their old computer **before** they install your OS. The
easiest place for it is the install USB itself. A `CherryMove/` folder at the top of the ISO
shows up when the USB stick is opened on Windows or macOS. Cherry OS does exactly this.

You were given the folder. To rebuild it from the Cherry OS source, run `make move`, which
needs Go and Docker. It contains:

| File | For |
|---|---|
| `CherryMove-Setup.exe` | Windows 10 and 11 (Intel/AMD and ARM). Installs per user, no administrator needed. |
| `CherryMove.dmg` | macOS 11 or newer (Apple silicon and Intel). |
| `cherry-move-app_<v>_amd64.deb`, `_arm64.deb` | Debian, Ubuntu, Mint (`sudo apt install ./cherry-move-app_*.deb`). |
| `cherry-move-app-<v>-1.x86_64.rpm`, `.aarch64.rpm` | Fedora, openSUSE. |
| `cherry-move-linux-x86_64`, `-arm64` | Any other Linux: make it executable and run it. |
| `README.txt` | Instructions for people, in plain language. |

None of these go into your OS. Don't install, run or test them there (no Wine), and don't
repackage them. With the kit, `./add-cherrymove-to-iso.sh ISO_TREE "Your OS name"` does the
copying and the README change below. Without it, copy the folder into your ISO tree before making
the image:

```sh
# live-build: anything in config/includes.binary/ lands at the top of the ISO
mkdir -p config/includes.binary/CherryMove
cp CherryMove/* config/includes.binary/CherryMove/

# your own xorriso / grub-mkrescue tree:
cp -r CherryMove "$ISO_TREE/"
```

It adds about 45 MB. Notes:

- **`README.txt` says "Cherry OS".** It says "bring your files to Cherry OS" and "plug the stick
  in while installing Cherry OS". Replace "Cherry OS" with your OS's name in your copy.
- **The old-computer app can't restore.** It only sends. Restoring is done by the `cherry-move`
  package on your OS.
- **It downloads rclone if needed.** On the old computer, rclone is downloaded the first time
  someone picks a cloud drive or NAS, if it isn't already installed. USB sticks need nothing.
- **It isn't code-signed yet.** Windows shows "Windows protected your PC" (More info → Run
  anyway). macOS blocks it the first time (System Settings → Privacy & Security → Open Anyway).
  The README walks people through both.
- **`cherry-move-app` and `cherry-move` are different packages.** They have different file names
  and can both be installed. Your OS only needs `cherry-move`.

## 8. Branding

- **The app is called Cherry Move.** Its home page says "Moving to Cherry OS, a new PC or another
  Linux?". The live-session screens use your OS's name (section 6).
- **The `README.txt` it writes onto transfer sticks mentions Cherry OS**, alongside "or open
  Cherry Move on any Linux computer".
- **The window class and app ID are `org.cherryos.Move`.** Leave them, as explained in section 3.

## 9. Files Cherry Move uses

| Path | What |
|---|---|
| `~/.config/cherry/pending-transfer.json` (mode 600) | A transfer to restore at the next login: code, label, and where the files are. Deleted once restored. |
| `/var/lib/cherry/pending-transfer.json` (mode 600) | Live session only: the transfer picked before installing, until the installer hands it over. |
| `~/.config/cherry/rclone.conf` | Cloud and NAS sign-ins, kept only while Cherry Move is open and removed when it closes. Separate from the person's own rclone setup. |
| `CHERRY-TRANSFER/` on a USB stick | `cherry-transfer.json` (the marker) plus encrypted chunks. The person can delete it or reformat the stick once their files are back. |
| `CherryMove/` folder in the person's cloud drive or NAS | The encrypted transfer, when they used their own storage. Cherry Move offers to delete it afterwards. |

## 10. Check it works (in the built image or a VM of it)

Quick checks in a terminal:

```sh
dpkg -s cherry-move | grep -E '^(Status|Version)'   # install ok installed
cherry-move --help                                   # prints the usage
ls /etc/xdg/autostart/cherry-move-*.desktop          # both entries
rclone version | head -1                             # rclone v1.60 or newer
cherry-move --adopt-pending "$USER"                  # as root: "No transfer was picked before installing."
```

Then open **Cherry Move** from the app menu. Check that:

- its own icon shows in the dock;
- "Send files from this computer" lists your folders with their sizes;
- "Get files from my old computer" shows "USB stick", "Google Drive, OneDrive or MEGA" and "A NAS or
  another computer at home". The last two shouldn't be greyed out (they are if rclone is missing).

A full round trip in two VMs (QEMU):

```sh
qemu-img create -f raw stick.img 4G && mkfs.vfat -n STICK stick.img
# add to both VMs (one at a time):
#   -device qemu-xhci -drive if=none,id=stick,format=raw,file=stick.img -device usb-storage,drive=stick
```

1. **Sending VM** (your OS, or anything with the old-computer app): open Cherry Move, then Send,
   then USB stick. Note the code, then shut the VM down.
2. **Receiving VM** (your OS): boot it with the stick attached. Within about 15 seconds of
   logging in, Cherry Move should open on "Get your files" with the stick listed. Restore it.
   The files should be back in Documents, Pictures and so on, and it should say "Welcome home!".
3. **Live-to-installed:** boot your **live** image with the stick attached and pick the
   transfer. Press "Install …", install, reboot and log in as the new user. Cherry Move should
   open and restore the files without asking for the code.

From a terminal, the same round trip is
`cherry-move send --to usb --usb /media/$USER/STICK`, then `cherry-move get CODE --usb /media/$USER/STICK`.

## 11. Known gaps

- **Ubuntu 22.04 / Linux Mint 21 aren't supported.** Their libadwaita (1.1) is too old.
- **Google Drive, OneDrive and MEGA aren't tested end to end yet.** Signing in, sending and
  restoring through them hasn't been tried with real accounts. USB sticks (including several
  sticks) and NAS over SMB, SFTP and WebDAV have been tested.
- **The old-computer apps aren't code-signed** (see section 7).
- **Automatic updates need the Cherry Store app.** Without `cherry-store-client`, installed
  copies of `cherry-move` don't update themselves.
- **Only Calamares has been tested for the live-to-installed hand-off**, on Cherry OS. With
  other installers, use the generic commands in section 5.
- **Tested so far:**
  - the package installed next to the Cherry Store app, and every main screen opened, on
    Debian 12, Debian 13 and Ubuntu 24.04;
  - full transfers (USB, several sticks, NAS) and the Calamares hand-off on Cherry OS, which is
    Debian 13.
  - The `--adopt-pending` command replaced an equivalent one-line Python call in version 1.0.4.
    It has been tested on its own, but not yet in a full Cherry OS install.

## 12. Checklist for the OS builder

- [ ] Base is Debian 12 / Ubuntu 24.04 or newer (or a derivative), with apt.
- [ ] `cherry-move_<version>_all.deb` is installed in the image. Apt pulls in its dependencies
      and the recommended `rclone` and `avahi-utils`.
- [ ] Nothing renamed: `/usr/bin/cherry-move`, `cherry-move.desktop` (`StartupWMClass=org.cherryos.Move`).
- [ ] Both autostart entries are present in `/etc/xdg/autostart/` (the `.deb` does this), and
      your desktop runs XDG autostart entries.
- [ ] `CherryMove/` (Windows, macOS and Linux apps plus `README.txt`) is at the top of your ISO,
      with "Cherry OS" in the README replaced by your OS's name.
- [ ] Optional: the installer runs the copy and `cherry-move --adopt-pending NEWUSER` steps
      (section 5), the live user has passwordless sudo, and the kernel command line has
      `boot=live` or `boot=casper`.
- [ ] Optional: `/etc/cherry/move.conf` sets `INSTALLER=` if your installer isn't Calamares or
      `install-debian`.
- [ ] In a VM of the finished image, section 10 passes, including the live-to-installed restore
      if you added the installer hook.
