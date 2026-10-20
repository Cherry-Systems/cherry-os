# 🍒 Cherry OS

A Debian 13 (trixie) desktop Linux where:

- **Anyone can publish apps.** The Cherry Store has no review queue and nobody judges whether an app is
  "good enough" or AI-made. Apps are ordinary `.deb` packages kept on GitHub, signed by their publisher
  and virus-scanned (a hit only shows a warning), and people can report malware for takedown.
  `cherry install NAME` from the terminal, or the Cherry Store app.
- **Windows apps just work.** Double-click a `.exe` or `.msi` and it runs through Wine, which is
  preinstalled along with Mono and Gecko. Store apps can be Windows programs and get their own menu
  entries.
- **Programming languages come preinstalled:** Python 3 (with pip, venv, pipx, Tk and Thonny), Java
  (OpenJDK 21 and 25, Maven, Gradle), Node.js + npm, C/C++ (GCC, GDB, CMake), Rust, Go, Ruby, PHP, Lua,
  Perl and SQLite.
- **Five desktops** (KDE Plasma, GNOME, Cinnamon, Xfce and Budgie) can be tried live before installing.
  Pick one in the installer and switch any time in Cherry Center.
- **Moving from your old computer is easy** with Cherry Move, which runs on Windows, macOS and any Linux
  distro. Your files are compressed (zstd, with identical files stored once) and encrypted on the old
  computer, then kept:
  - **on USB sticks:** no internet needed. Cherry Move works out the packed size first and says straight
    away if the stick is too small: use a bigger one, or several, and it asks for each in turn.
  - **in your own Google Drive, OneDrive or MEGA** (15, 5 or 20 GB free), via rclone. Google Drive uses
    the hidden app folder, so Cherry Move can't see your other files.
  - **on a NAS or another computer at home:** Windows/Samba shares, SSH/SFTP or WebDAV, found on the
    network automatically where possible.

  The new computer finds them by itself when you plug in the **USB stick** (even in the Cherry
  installer), or with a **34-character code** you keep safe: print it, save it, or scan its QR code with
  your phone. Nothing ever leaves unencrypted, and sign-ins to your cloud drive or NAS are forgotten when
  Cherry Move closes.

  **Keep a transfer stick safe:** a USB stick move carries its own code so it restores by itself, so
  anyone with the stick can restore the files (and saved passwords, if browsers or apps were brought).
  Delete the `CHERRY-TRANSFER` folder once the files are back. Files in your own cloud drive or NAS
  can't be read without the code.
- **Updates for as long as possible:** security updates install automatically every day until June 2030
  (Debian security plus Debian LTS). See [docs/SUPPORT.md](docs/SUPPORT.md).

**[Download Cherry OS 1.0](https://cherry-cloud.isaiahcannings.workers.dev/download/cherry-os.iso)**
(one 4.7 GB file, `cherry-1.0-amd64.iso`) ·
[Checksums and Cherry Move downloads](https://github.com/Cherry-Systems/cherry-os/releases/latest) ·
[Website](https://cherry-systems.github.io/os/)

[docs/INSTALLING.md](docs/INSTALLING.md) covers making the USB stick, Secure Boot, and installing next to
Windows.

## Source code

This repository is the source code for Cherry OS and the apps that come with it:

| App | Source |
| --- | --- |
| Cherry OS, the live/installer ISO | `build.sh`, `config/`, `hooks/`, `overlay/` |
| Cherry Center, the Cherry Store app, Cherry Move on Cherry OS, the `cherry` command | `desktop/` |
| Cherry Move for Windows, macOS and other Linux | `move/` |
| Cherry Store index, virus scan and publish checks | `store/` (also [Cherry-Systems/cherry-store](https://github.com/Cherry-Systems/cherry-store)) |
| Cherry Cloud (accounts and uploads) | `cloud/` |

## Layout

| Folder | What's in it |
| --- | --- |
| `build.sh`, `config/`, `hooks/`, `overlay/` | Builds the live/installer ISO (debootstrap → squashfs → GRUB, BIOS + UEFI with Secure Boot) |
| `desktop/` | Cherry's own apps (Python, GTK 4 + libadwaita): `cherry` CLI, Cherry Store, Cherry Center, Cherry Move, desktop switcher, Windows runner |
| `move/` | The Cherry Move app for Windows, macOS and other Linux distros (Go, no install needed) |
| `store/` | The Cherry Store's GitHub repository (Cherry-Systems/cherry-store): intake checks, virus scan, index |
| `cloud/` | Cherry Cloud, a Cloudflare Worker + D1: Cherry accounts, uploads for the store, reports |
| `scripts/` | Artwork generator, .deb packaging, QEMU runner, Cherry Move test scripts |
| `tests/` | Unit tests (offline) and end-to-end tests against a local Cherry Cloud |

## Building

```sh
make move             # Cherry Move app for Windows/macOS/Linux → build/move/ (copied onto the ISO)
make build-docker     # no sudo or host tools needed, just Docker → out/cherry-1.0-amd64.iso
make run              # try it in QEMU (make run-install / run-installed to rehearse an install)
make test             # unit tests
make cloud-deps cloud-dev   # local Cherry Cloud on :8790, then in another terminal: make test-e2e
```

The build needs about 25 GB of free space and an internet connection. The finished ISO is about 4 GB
because every desktop and language is included and the installer works offline.

## The Cherry Store

Store apps are ordinary Debian packages kept in the
[Cherry-Systems/cherry-store](https://github.com/Cherry-Systems/cherry-store) repository on GitHub
(the `.deb` files are its Releases, `index.json` lists them; its source is `store/` here). On Cherry OS:

```sh
cherry search paint
cherry install some-app     # asks for your password, like "sudo apt install"
cherry update
cherry remove some-app
```

Cherry checks each download's hash and its publisher's Ed25519 signature, shows the store's warnings,
then installs it with apt. The first publisher key seen for a package name is remembered on each
computer, so nobody else can push an "update" to someone's app.

### Publishing an app

```sh
cherry init                 # writes cherry.toml in the current folder
cherry build                # makes dist/NAME_VERSION_ARCH.deb (files go to /opt/NAME)
cherry publish --github     # with your GitHub account (run "gh auth login" once)
cherry publish --cherry     # or with a Cherry account ("cherry signup NAME" once)
```

`kind = "native"` is for Linux programs, `"windows"` for `.exe` files (optionally with an installer that
runs once per user) and `"appimage"` for AppImages. `apt_depends` lists Debian packages your app needs.
Any `.deb` you built some other way can be published too: `cherry publish --github my-app_1.0_amd64.deb`.

With `--github`, the package goes to a release in your own `cherry-packages` repository and Cherry opens
a "Publish: NAME VERSION" issue on the store. With `--cherry`, Cherry Cloud keeps the upload until the
store collects it. The store's workflow then checks the signature, virus-scans the package with ClamAV,
copies it into a release and adds it to `index.json`, all in a minute or two. There's no review. Anything
unusual (install scripts, files outside `/opt` and `/usr`, setuid programs, replacing Debian packages,
a virus-scanner hit) is recorded as a warning that people see before installing. Every package is
scanned again each night. Reports become issues on the store, and harmful packages are taken down
with its *Publish* workflow (`takedown`).

### On other Debian-based systems

People on Ubuntu, Linux Mint, Debian, Pop!_OS and similar systems (Ubuntu 22.04 / Debian 12 or
newer) can download the Cherry Store app from the
[Cherry Store website](https://cherry-systems.github.io/store/), which links straight to the newest
`cherry-store-client` package. For scripts, Cherry Cloud's
`https://cherry-cloud.isaiahcannings.workers.dev/download/cherry-store.deb` always redirects to it
too. That package holds the `cherry` command, the Cherry Store app, its polkit rule, and a daily systemd
timer that updates the app itself.

The app is published through the store like any other app, but signed with a Cherry OS release
key. Only that key may publish names starting with `cherry`. Its public half is in
`store/tools/official-keys.txt` and in `OFFICIAL_KEYS` in `desktop/lib/cherryos/pkg.py`. Its
private half stays on the maintainer's computer (`~/.config/cherry/cherry-os-release.json`) and
is never committed.

### Updating Cherry's own apps

Cherry OS's own packages, `cherry-desktop` (Cherry Center, the store, the desktop switcher and so
on) and `cherry-move`, are published through the store the same way, with the same key.

- **Installed Cherry OS systems** have a daily timer, `cherry-store-self-update.timer`, which runs
  `cherry update -y cherry-store-client cherry-desktop cherry-move`. "Check for updates now" in
  Cherry Center does this too.
- **Other systems with the Cherry Store app** run the same timer, so a `cherry-move` installed
  there updates as well.
- **These packages are hidden from Explore**, and the store refuses to remove them.

To release a new version:

1. Bump `__version__` in `desktop/lib/cherryos/__init__.py`.
2. Run `scripts/publish-release.sh`. It publishes `cherry-move`, then `cherry-desktop`, then
   `cherry-store-client`.

`scripts/build-debs.sh OUT_DIR` and `scripts/build-store-client.sh OUT_DIR` only build the
packages.

To build the Cherry Store and Cherry Move into another OS, start with
[docs/ADDING-CHERRY-APPS.md](docs/ADDING-CHERRY-APPS.md). It links to the detailed guides,
[docs/CHERRY-STORE-SETUP.md](docs/CHERRY-STORE-SETUP.md) and
[docs/CHERRY-MOVE-SETUP.md](docs/CHERRY-MOVE-SETUP.md). `scripts/make-os-kit.sh` packs everything
another OS builder needs into `build/cherry-kit/`.

The Cherry Store page is `cloud/public`, which Cherry Cloud serves. `website/` is the Cherry Systems
site (home, `/os/`, `/move/`). `scripts/publish-website.sh` puts both on GitHub Pages
(Cherry-Systems/cherry-systems.github.io), with the store page at `/store/`. Run it only when a page
changes: the app list is read live from the store.

## Cherry Cloud

`cloud/` is a Cloudflare Worker + D1 on the free plan. It holds Cherry accounts (Ed25519 keys, no
passwords). It also keeps Cherry-account uploads only until the store's workflow has collected them
(storage is `STORAGE` in `cloud/wrangler.jsonc`: D1 by default, or R2/S3), and it turns `cherry report`
into issues on the store.

Deploying:

```sh
cd cloud
npx wrangler d1 create cherry-cloud          # put the id into wrangler.jsonc
npx wrangler d1 migrations apply cherry-cloud --remote
npx wrangler secret put GITHUB_TOKEN         # fine-grained token for Cherry-Systems/cherry-store: Contents + Issues read/write
npx wrangler secret put STORE_TOKEN          # any long random string; also the store repository's STORE_TOKEN Actions secret
npx wrangler secret put IP_SALT
make -C .. cloud-deploy
```

Then set the store repository's `CHERRY_CLOUD_URL` Actions variable to the worker's address.

## Cherry Move on Windows, macOS and other distros

`make move` builds an installer per system into `build/move/` (it needs Go and Docker), and the ISO build
copies them into a `CherryMove` folder on the Cherry OS USB stick:

| File | For |
| --- | --- |
| `CherryMove-Setup.exe` | Windows 10/11, x64 and ARM64: Start menu, desktop shortcut and an entry in Apps to remove it; no administrator needed |
| `CherryMove.dmg` | macOS 11 or newer, Apple Silicon and Intel: drag the app onto Applications |
| `cherry-move-app_*_amd64.deb`, `_arm64.deb` | Ubuntu, Debian, Mint and other apt distros |
| `cherry-move-app-*.x86_64.rpm`, `.aarch64.rpm` | Fedora, openSUSE and other rpm distros |
| `cherry-move-linux-x86_64`, `cherry-move-linux-arm64` | Any other Linux, without installing |

The app opens in its own window (WebView2 on Windows, which the installer adds if it's missing; WKWebView on
macOS; WebKitGTK on Linux, falling back to the browser). It's a single copy at a time, asks before closing
while files are being moved, and saves or prints the transfer code through the system's own boxes. The
Linux package is called `cherry-move-app` because Cherry OS's own `cherry-move` package is a different
program.

The installers aren't code-signed, so Windows SmartScreen asks once ("More info" → "Run anyway"). On a Mac,
the first launch is blocked: open System Settings → Privacy & Security and click "Open Anyway" next to
Cherry Move (on macOS 14 or older, right-click the app → Open also works). Unsigned, a Mac also asks again
after every update, both to open it and for folder access. Signing needs a paid Authenticode certificate and an
Apple Developer ID. `move/README.txt` is the user guide that goes next to them.
