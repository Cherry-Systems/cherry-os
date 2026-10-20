# How long each Cherry OS release gets updates

Each Cherry OS release is built on a Debian stable release and gets updates for as long as Debian
supports it, including the extra years from Debian LTS.

| Cherry OS | Based on | Security updates until | Next step |
| --- | --- | --- | --- |
| 1.x "blossom" | Debian 13 trixie | **30 June 2030** | In-place upgrade to Cherry 2 (Debian 14 forky) |

## What updates automatically

- **Security fixes** (`trixie-security`, which Debian LTS keeps updating after regular support ends):
  installed every day by `unattended-upgrades` whenever the computer is plugged in.
- **Stable updates and point releases** (`trixie-updates`, `trixie`): installed the same way.
- **Cherry Store apps:** Cherry Center and the store's Updates tab show them, and `cherry update`
  installs them.
- **Flatpak apps:** updated by GNOME Software / KDE Discover, or `flatpak update`.
- **Backports** (newer kernels, drivers and Mesa for brand-new hardware) are available but never
  installed unless you ask: `sudo apt install -t trixie-backports linux-image-amd64`.

Nothing reboots the computer by itself. The desktop's software centre says when a restart is needed.

"Check for updates now" in Cherry Center → Updates & safety (or `cherry-update` in a terminal) runs
all of the above at once.

## When a release reaches its end

Before June 2030, Cherry OS 2 (on Debian 14) will offer an in-place upgrade through Cherry Center. Your
files, apps and settings stay put. If you'd rather start fresh, Cherry Move brings your files across.

The end date is also in `/etc/os-release` (`SUPPORT_END=2030-06-30`), so tools can warn you
ahead of time.

## Cherry Move and Cherry Store

Cherry Move copies on USB sticks, your own cloud drive or a NAS stay until you delete them (Cherry Move
offers to once your files are back). Store packages stay available until their publisher yanks them or
they're taken down after a confirmed malware or illegal-content report.
