# Setting up the Cherry Store on another operating system

This guide is for whoever builds a Linux OS image and wants the Cherry Store in it: the `cherry`
command, the graphical **Cherry Store** app, and (optionally) publishing apps to the store. It
assumes no knowledge of Cherry OS.

**If you were given the Cherry kit** (`START-HERE.md`, `install-cherry-apps.sh`), install with its
script instead of the steps in section 3. It installs the same `cherry-store-client` version Cherry
OS ships, and its check script confirms the files match.

The short version: install the Cherry Store app package into the image, then check that
`cherry search` lists `hello-cherry`. Everything else here is detail. The package's newest
version is always at:

```
https://cherry-cloud.isaiahcannings.workers.dev/download/cherry-store.deb
```

That's the same package ordinary users download from the Cherry Store website,
https://cherry-systems.github.io/store/. Once installed, the app
keeps itself up to date.

---

## 1. What the Cherry Store is

- **Apps are ordinary Debian packages (`.deb`).** Each one is stored as a file in a GitHub
  Release of the public repository
  [Cherry-Systems/cherry-store](https://github.com/Cherry-Systems/cherry-store).
- **One file lists every app:**
  `https://raw.githubusercontent.com/Cherry-Systems/cherry-store/main/index.json`.
- **The client** (`cherry`) downloads that index. To install an app it:
  1. downloads the `.deb`;
  2. checks its size and SHA-256 against the index;
  3. checks the publisher's Ed25519 signature;
  4. checks that the package's name and version match the listing;
  5. shows any warnings;
  6. hands the file to `apt-get install`.

  Because apt does the installing, dependencies, upgrades and removal behave like any other
  Debian package.
- **Nobody reviews apps by hand.** Instead:
  - Every upload is signed by its publisher.
  - Every upload is virus-scanned with ClamAV, and all apps are scanned again every night.
  - Anything unusual about a package is recorded as a warning that the client shows before
    installing.
- **Trust on first use:** the first signing key seen for an app name is remembered on each
  computer. If a later version is signed by a different key, the client refuses to install it.
- **Publishing:**
  - Anyone with a GitHub account can publish (`cherry publish --github`).
  - So can anyone with a Cherry account (`cherry publish --cherry`). That route goes through
    Cherry Cloud, a small web service at `https://cherry-cloud.isaiahcannings.workers.dev`.
  - Both routes end in the same GitHub Actions workflow, which checks the upload, scans it and
    adds it to `index.json`.

Both routes were tested end to end on 2026-10-05. The test app `hello-cherry` was published
(1.0.0 through GitHub, 1.0.1 through a Cherry account), then searched, installed, updated and
removed on clean Debian 12, Debian 13, Ubuntu 22.04 and Ubuntu 24.04 systems.

The Cherry Store app itself (`cherry-store-client`) is published through the store too, signed
with a Cherry OS release key. Only that key may publish names starting with `cherry`, and the
client checks it before installing them. That's how installed copies update themselves safely.
Cherry Move (`cherry-move`) and Cherry OS's own `cherry-desktop` are published the same way.

## 2. Requirements for your OS

| Need | Why |
|---|---|
| Debian-based (Debian 12 or Ubuntu 22.04 or newer; Linux Mint 21+, Pop!_OS, Zorin and other derivatives of those) with `apt` and `dpkg` | Apps are `.deb` files installed with apt. Other package systems (rpm, pacman, etc.) can't use the store. |
| `python3` 3.10 or newer, `python3-cryptography` | The client is Python and verifies Ed25519 signatures. (Building apps with `cherry build` needs Python 3.11.) |
| `ca-certificates` | All downloads use HTTPS (GitHub). Without it, every command fails with `CERTIFICATE_VERIFY_FAILED`. |
| `pkexec` (polkit) and/or `sudo` | Installing and removing apps needs root. The client re-runs itself with `sudo` in a terminal, and with `pkexec` from the graphical app. |
| `python3-gi`, `gir1.2-gtk-4.0`, `gir1.2-adw-1` | The graphical Cherry Store app (GTK 4.6+ and libadwaita 1.1+). |
| systemd | Runs the daily job that updates the Cherry Store app itself (and Cherry Move, if installed). Without it, everything else still works; run `cherry update -y cherry-store-client cherry-move` yourself. |
| `gh` (GitHub CLI) | Only for publishing with a GitHub account. |
| Internet access to `raw.githubusercontent.com`, `github.com`, `objects.githubusercontent.com` | The index and the packages. Plus the Cherry Cloud URL above for Cherry accounts and reports. |
| CPU: `amd64` or `arm64` | Packages are `all` (scripts), `amd64` or `arm64`. |

The `.deb` below declares these as dependencies, so apt pulls them in.

## 3. Installing the client into your image

### Option A (recommended): the `cherry-store-client` package

Download it from the link at the top of this guide, which saves it as `cherry-store.deb`, or use
the copy you were given. To rebuild it from the Cherry OS source, run
`scripts/build-store-client.sh OUT_DIR`. It contains:

```
/usr/bin/cherry                         the command-line client (also what polkit runs)
/usr/bin/cherry-store                   the graphical Cherry Store app
/usr/lib/cherry/cherryos/__init__.py    shared Python library
/usr/lib/cherry/cherryos/pkg.py           store client: index, verify, install, build, publish
/usr/lib/cherry/cherryos/cloud.py         Cherry Cloud API client (Cherry accounts, reports)
/usr/lib/cherry/cherryos/ui/__init__.py   GTK helpers
/usr/lib/cherry/cherryos/ui/store.py      the Cherry Store window
/usr/share/applications/cherry-store.desktop
/usr/share/icons/hicolor/scalable/apps/cherry-store.svg
/usr/share/polkit-1/actions/org.cherryos.cherry.policy
/usr/lib/systemd/system/cherry-store-self-update.service   runs: cherry update -y cherry-store-client cherry-desktop cherry-move
/usr/lib/systemd/system/cherry-store-self-update.timer     daily; enabled by the package's postinst
```

Install it inside the image's chroot like any local package:

```sh
wget -O cherry-store.deb https://cherry-cloud.isaiahcannings.workers.dev/download/cherry-store.deb

# debootstrap / mmdebstrap / a chroot you control:
cp cherry-store.deb "$CHROOT/tmp/"
chroot "$CHROOT" apt-get install -y /tmp/cherry-store.deb
rm "$CHROOT/tmp/cherry-store.deb"

# live-build: drop the file in config/packages.chroot/ and it's installed automatically
cp cherry-store.deb config/packages.chroot/
```

It pulls in everything it needs, including the GTK packages for the graphical app. Its install
script enables the daily self-update timer, which works in a chroot too: it creates the symlink,
and the timer starts on first boot.

It declares `Conflicts: cherry-desktop`, because Cherry OS's own `cherry-desktop` package
already contains these same files. Don't install both. Cherry Move (`cherry-move`, see
[CHERRY-MOVE-SETUP.md](CHERRY-MOVE-SETUP.md)) is fine alongside it: the two share no files.

### Option B: copy the files yourself

If you can't use the `.deb`, copy the files listed above from the Cherry OS source tree to the
same places:

| Destination | Source | Mode |
|---|---|---|
| `/usr/bin/cherry`, `/usr/bin/cherry-store` | `desktop/bin/` | 755 |
| `/usr/lib/cherry/cherryos/` | `desktop/lib/cherryos/` | 644 |
| `/usr/share/applications/cherry-store.desktop` | `desktop/applications/` | 644 |
| `/usr/share/icons/hicolor/scalable/apps/cherry-store.svg` | `desktop/icons/` | 644 |
| `/usr/share/polkit-1/actions/org.cherryos.cherry.policy` | `desktop/polkit/` | 644 |

Copying the files yourself means the store app won't update itself: the daily job only updates
the `cherry-store-client` package. Prefer option A.

Then install the dependencies from section 2 yourself. The scripts look for their library in
`/usr/lib/cherry` (or `../lib` next to themselves), so keep that layout. Keep `/usr/bin/cherry`
at exactly that path, because the polkit policy names it.

### What not to change

- Don't rename `/usr/bin/cherry`. The polkit action `org.cherryos.cherry.install` is tied to that
  path, and that's what lets the graphical app show "Install or remove Cherry Store apps" and
  remember the password for a few minutes (`auth_admin_keep`).
- Don't pre-create `/var/lib/cherry/trusted-keys.json` with entries. Trust is meant to start
  empty on each machine.

## 4. Check it works (do this in the built image or a VM of it)

```sh
cherry search hello          # lists: hello-cherry 1.0.1  by github:blhcode
cherry info hello-cherry     # publisher, size, "Virus scan: clean", description
sudo cherry install -y hello-cherry
hello-cherry                 # prints: Hello from the Cherry Store! (hello-cherry 1.0.1)
cherry list                  # hello-cherry 1.0.1 by github:blhcode, and cherry-store-client by Cherry OS
cherry update --check        # Everything is up to date.
sudo cherry remove hello-cherry
systemctl list-timers cherry-store-self-update.timer   # the daily self-update is scheduled
```

Also open **Cherry Store** from the app menu (or run `cherry-store`). The Explore tab should list
`hello-cherry`. Install it from there: polkit should ask for the admin password once, and the
app should then show up in the menu as "Hello Cherry".

If `cherry search` says `can't reach the Cherry Store … CERTIFICATE_VERIFY_FAILED`, install
`ca-certificates`. If it says `No module named 'cryptography'`, install `python3-cryptography`.

## 5. Configuration (optional)

With no configuration, the client uses the official store. Settings are read from, in order:

1. environment variables;
2. `~/.config/cherry/store.conf` (per user);
3. `/etc/cherry/store.conf` (whole system).

Files use `KEY=value` lines:

| Key (file) / environment variable | Default | Meaning |
|---|---|---|
| `INDEX_URL` / `CHERRY_STORE_INDEX_URL` | `https://raw.githubusercontent.com/Cherry-Systems/cherry-store/main/index.json` | Where the app list comes from. `file:///path/index.json` works too, for offline testing. |
| `REPO` / `CHERRY_STORE_REPO` | `Cherry-Systems/cherry-store` | Where `cherry publish --github` and `cherry yank --github` open their requests. |

Cherry Cloud's address is set separately, with `CLOUD_URL=` in `~/.config/cherry/cloud.conf` or
`/etc/cherry/cloud.conf`, or the environment variable `CHERRY_CLOUD_URL`. It defaults to
`https://cherry-cloud.isaiahcannings.workers.dev`.

Put system-wide settings in `/etc/cherry/*.conf`, not in environment variables.

- The graphical app runs installs through `pkexec`, which clears the environment.
- Installs run as root, so they read root's files, not the user's `~/.config`.
- From a terminal, `cherry` passes `CHERRY_STORE_INDEX_URL` and `CHERRY_CLOUD_URL` through
  `sudo` itself. Nothing else is passed.

## 6. Files the client keeps on each computer

| Path | What |
|---|---|
| `/var/lib/cherry/installed/NAME.json` | One per app installed from the store: version, publisher, key id. `cherry list` and `cherry update` use these together with dpkg's own records. |
| `/var/lib/cherry/trusted-keys.json` | Trust on first use: which key(s) each app name may be signed with. |
| `/var/cache/cherry/index.json` (root), `~/.cache/cherry/index.json` (users) | The index, reused for 5 minutes. |
| `~/.config/cherry/publisher.json` (mode 600) | Only for publishers: their Ed25519 signing key, plus their Cherry account name if they have one. |

Removing an app with `cherry remove` (or `apt remove`) leaves the user's own files alone.

## 7. Hooking it into the rest of your OS

- **Updates:** the Cherry Store app updates itself daily (the systemd timer above), and so does
  Cherry Move if it's installed. The store hides these from Explore and won't remove them. Apps people
  install from the store update when someone runs `cherry update -y` as root or presses
  "Update all" in the app. So, run `cherry update -y` alongside `apt-get full-upgrade` in
  whatever your OS uses to update. Cherry OS's updater runs apt, then Flatpak, then the Cherry
  Store, in that order.
  - `cherry update NAME…` updates only the apps named.
  - `cherry update --check` lists available updates without installing (no root needed).
  - `cherry-store --updates` opens the graphical app on its Installed tab.
  - New versions take up to 5 minutes to show up, because GitHub caches `index.json`.
- **Menu entries:** store apps install their menu entries as
  `/usr/share/applications/cherry-NAME-N.desktop`, with `X-Cherry-Package=NAME` in them. If your
  menu or launcher shows "where did this come from", that key tells you.
- **Command line:** store apps put their programs in `/opt/NAME/` and symlink their commands
  into `/usr/bin/`.
- **Windows apps:** packages with `kind = "windows"` depend on `wine` and on Cherry OS's
  `cherry-desktop` package (for its `cherry-windows-run` launcher). On another OS, apt can't
  satisfy that, so those apps won't install. Native and AppImage apps work. If you want Windows
  apps too, you'd need to package `desktop/bin/cherry-windows-run` from Cherry OS with
  `Provides: cherry-desktop`, and drop the client's `Conflicts: cherry-desktop` to match.
- **Branding:** the client's messages say "Cherry Store" and the polkit vendor is "Cherry OS".
  Leave them as they are unless you're running your own store (section 10).

## 8. Publishing an app (for app developers on your OS)

### Make the package

```sh
mkdir my-app && cd my-app
cherry init            # writes cherry.toml with comments
```

Here is the `cherry.toml` of the real test app:

```toml
name = "hello-cherry"          # 2-63 chars: a-z 0-9 . + -   ("cherry" and "cherry-..." are reserved)
version = "1.0.0"              # starts with a digit; letters, digits . + ~ -   (no epochs)
summary = "A tiny test app for the Cherry Store"
description = """
Says hello. It's here to show that publishing to and installing from
the Cherry Store works.
"""
license = "MIT"
homepage = "https://github.com/Cherry-Systems/cherry-store"
kind = "native"                # native | windows | appimage
architecture = "all"           # all | amd64 | arm64
apt_depends = []               # Debian packages it needs, e.g. ["python3-gi"]
depends = []                   # other Cherry Store apps it needs

[[apps]]                       # menu entries (any number)
name = "Hello Cherry"
exec = "bin/hello-cherry"      # path inside this folder
icon = "icon.png"
categories = "Utility;"
terminal = true

[bin]                          # commands added to PATH
hello-cherry = "bin/hello-cherry"
```

The folder looks like this (`dist/` appears after building):

```
hello-cherry/
  cherry.toml
  icon.png
  bin/hello-cherry       (executable)
```

`cherry build` produces `dist/NAME_VERSION_ARCH.deb`:

- Everything in the folder goes to `/opt/NAME/`. `cherry.toml`, `dist/`, `.git`, `node_modules`,
  `.venv` and `__pycache__` are left out.
- Each `[bin]` command becomes a symlink in `/usr/bin`.
- Each `[[apps]]` entry becomes a menu entry.
- `cherry.toml` is kept inside as `/opt/NAME/cherry.json`.

You can also publish a `.deb` built some other way; pass the file instead of a folder.

The first time you build or publish, `cherry` creates your signing key in
`~/.config/cherry/publisher.json`. **Back it up** (`cherry key export`). Later versions of your
app must be signed with the same key, and nobody can recover it for you.

### Publish it

With a GitHub account (needs `gh auth login` first):

```sh
cherry publish --github        # or: cherry publish --github path/to/app_1.0_all.deb
```

This:

1. creates a public `YOUR_USER/cherry-packages` repository on first use;
2. uploads the `.deb` to a release there;
3. opens an issue titled `Publish: NAME VERSION` on the store repository, with the signed request
   in a ```json block;
4. waits (up to 15 minutes) for the store to answer on that issue.

In testing it took about a minute.

With a Cherry account (no GitHub needed):

```sh
cherry signup USERNAME         # once; attaches your signing key to a new Cherry account
cherry publish --cherry
```

The `.deb` is uploaded to Cherry Cloud in 16 MB chunks (up to 200 MB; use `--github` for bigger
apps). Cherry Cloud asks
the store repository to fetch it, and `cherry` waits for the result. In testing that took about
40 seconds.

On a second computer, use `cherry login KEYFILE` with the text from `cherry key export`.

Plain `cherry publish` uses your Cherry account if you have one, and GitHub otherwise.

### What the store checks

An upload is **refused** if:

- the name or version doesn't follow the rules above, or the name is reserved:
  `admin`, `root`, `system`, `official`, `debian`, `apt`, plus `cherry` and `cherry-*` unless
  signed with a Cherry OS release key (`tools/official-keys.txt`);
- the file doesn't match its SHA-256, or the signature doesn't match;
- the `.deb`'s own `Package` and `Version` differ from the request;
- the architecture isn't `all`, `amd64` or `arm64`;
- the name already belongs to a different key, or that version already exists (bump the version);
- the app was taken down;
- it's bigger than 1 GB;
- the publisher has already published 20 versions in the past day.

An upload is **accepted with warnings**, shown to people before they install, if it:

- runs install scripts (`preinst`, `postinst` and so on) as root;
- shares a name with a Debian package;
- replaces, conflicts with or breaks Debian packages;
- puts files outside `/opt`, `/usr/bin`, `/usr/games`, `/usr/lib`, `/usr/libexec` or `/usr/share`;
- has setuid programs;
- uses `Pre-Depends`;
- gets a ClamAV hit, or couldn't be scanned.

Packages made with `cherry build` don't trigger any of these.

### Withdraw a version

```sh
cherry yank NAME VERSION --github    # or --cherry
```

Withdrawing is signed with the same key. Installed copies stay installed, and the newest version
that hasn't been withdrawn becomes the one people get.

## 9. How the store side works (reference)

Repository layout (`Cherry-Systems/cherry-store`, public):

```
index.json                       the app list (the only thing clients read)
tools/store.py                   checks a .deb, keeps index.json (check/add/yank/takedown/restore/rescan)
tools/pipeline.py                runs one request inside GitHub Actions
tools/official-keys.txt          Cherry OS release keys: the only ones allowed to publish cherry-* names
.github/workflows/publish.yml    on: issue opened ("Publish:"/"Withdraw:"), repository_dispatch
                                 (cherry-upload/cherry-yank from Cherry Cloud), manual takedown/restore
.github/workflows/rescan.yml     nightly ClamAV rescan of every app's newest version
.github/ISSUE_TEMPLATE/report.yml  "report an app" form
```

When a request comes in, the Publish workflow:

1. downloads the `.deb`, from the publisher's release or from Cherry Cloud;
2. runs `store.py check`, which does the signature, rules, warnings and ClamAV scan;
3. copies the file into a release named `NAME_VERSION` in the store repository;
4. adds it to `index.json`, then commits and pushes;
5. answers: on the issue (comment, then close as completed or not planned), or back to Cherry
   Cloud. If the workflow itself breaks, it still closes the issue with an explanation.

Runs are queued one at a time, so two uploads never edit `index.json` at once.

`index.json` format (real entry, trimmed):

```json
{
 "format": 1,
 "updated": 1791157438,
 "packages": {
  "hello-cherry": {
   "name": "hello-cherry",
   "publisher": "github:blhcode",
   "key_id": "d473ce7f11034288",
   "public_key": "pg2Ao1yaT8xDbpt5W37V9fLYL5EzDExIOe6wcEvLBFM=",
   "summary": "A tiny test app for the Cherry Store",
   "description": "Says hello. …",
   "homepage": "https://github.com/Cherry-Systems/cherry-store",
   "license": "MIT",
   "kind": "native",
   "icon_url": "",
   "latest": "1.0.1",
   "created": 1791157438,
   "versions": {
    "1.0.0": {
     "url": "https://github.com/Cherry-Systems/cherry-store/releases/download/hello-cherry_1.0.0/hello-cherry_1.0.0_all.deb",
     "size": 10232,
     "sha256": "22c84097…",
     "signature": "RI6DZTuB…",
     "architecture": "all",
     "depends": "",
     "warnings": [],
     "scan": {"status": "clean", "detail": "", "at": 1791157435},
     "via": "github:blhcode",
     "created": 1791157438,
     "yanked": false
    }
   }
  }
 }
}
```

Keys and signatures:

- `public_key` is a raw 32-byte Ed25519 public key in base64.
- `key_id` is the first 16 hex characters of the SHA-256 of that raw key.
- `signature` is base64 Ed25519 over the UTF-8 text
  `cherry-package\n{name}\n{version}\n{sha256 hex}`.
- Withdrawals sign `cherry-yank\n{name}\n{version}`.
- `latest` is the highest version that hasn't been withdrawn (compared with
  `dpkg --compare-versions`), or `null` when every version is withdrawn.

Other fields:

- An app taken down for abuse gets `"taken_down": {"reason": …, "at": …}`, and clients refuse to
  install it.
- `scan.status` is `clean`, `flagged`, `error` or `pending`. Anything other than `clean` shows a
  warning.

Reports: `cherry report NAME` (and the app's "Report" button) file an issue on the store
repository through Cherry Cloud, so no GitHub account is needed. If Cherry Cloud can't be
reached, the client prints a link to the GitHub report form instead. Store maintainers take an
app down by running the Publish workflow manually with `action: takedown`.

## 10. Running your own separate store (only if you need one)

Most OSes should just use the official Cherry Store. If yours needs its own app list:

1. Create a new public GitHub repository containing `tools/`, `.github/` and an empty
   `index.json` (`{"format": 1, "packages": {}}`), copied from Cherry-Systems/cherry-store.
   - Make it public: Actions minutes are free on public repositories.
   - Pushing files under `.github/workflows/` needs a token with the `workflow` scope (for
     example `gh auth refresh -s workflow`). Without it, the workflows are silently left out and
     publish requests are never answered.
2. On every machine, point the client at it in `/etc/cherry/store.conf`:
   ```
   INDEX_URL=https://raw.githubusercontent.com/YOUR_ORG/YOUR_STORE/main/index.json
   REPO=YOUR_ORG/YOUR_STORE
   ```
3. GitHub publishing works with just that.
4. Cherry-account publishing and reports also need your own Cherry Cloud (the `cloud/` folder of
   Cherry OS, a Cloudflare Worker with a D1 database). Its connection to the store repository
   needs:
   - Worker secret `GITHUB_TOKEN`: a fine-grained token for that one repository with
     Contents: read/write and Issues: read/write. It creates `repository_dispatch` events and
     report issues.
   - Worker secret `STORE_TOKEN`: any long random string.
   - The same value as an Actions secret named `STORE_TOKEN` on the store repository. The
     workflow uses it to fetch uploads from Cloud and report results back.
   - An Actions variable `CHERRY_CLOUD_URL` on the store repository, set to the Worker's URL.
   - `CLOUD_URL=` in `/etc/cherry/cloud.conf` on clients.

Trust records (`/var/lib/cherry/trusted-keys.json`) are keyed by app name only, not by store. A
machine that switches stores will refuse an app whose name it already trusts under a different
key, unless the user passes `--force`.

## 11. Known gaps

- The store shows a generic icon for every app. The index has an `icon_url` field, but neither
  `cherry publish` nor the graphical app uses it yet.
- `kind = "windows"` apps need Cherry OS's Windows runner (see section 7).
- New versions take up to 5 minutes to appear (GitHub's cache of `index.json`).
- Tested in containers:
  - the command line on Debian 12 and 13 and Ubuntu 22.04 and 24.04;
  - the graphical app's main screens on all four;
  - its fallback dialogs on Ubuntu 22.04.
- Tested on real desktops:
  - a clean Ubuntu 24.04 desktop in a VM: downloaded from the website, then checked the menu
    entry and dock icon, installed an app with the password prompt, and updated the Cherry
    Store app itself from 1.0.2 to 1.0.3;
  - an installed Cherry OS (GNOME) VM.
- Linux Mint, KDE-based Ubuntu flavours and Debian 12 desktops haven't been tried on a real
  desktop.

## 12. Checklist for the OS builder

- [ ] Base is Debian 12 / Ubuntu 22.04 or newer (or a derivative), with apt.
- [ ] `cherry-store.deb` (package `cherry-store-client`) is installed in the image.
- [ ] `ca-certificates`, `python3-cryptography`, the GTK packages and `pkexec` are present (the `.deb` pulls them in).
- [ ] `cherry-store-self-update.timer` is enabled (the `.deb` does this).
- [ ] Nothing renamed: `/usr/bin/cherry` and `/usr/share/polkit-1/actions/org.cherryos.cherry.policy`.
- [ ] `/var/lib/cherry/` is absent or empty in the image (no pre-trusted keys, nothing pre-recorded).
- [ ] The OS updater also runs `cherry update -y`.
- [ ] In a VM of the finished image, section 4 passes, including installing from the graphical
      app with the password prompt.
