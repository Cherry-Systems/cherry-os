"""The desktops Cherry OS ships, and switching between them.

One GDM login screen serves every desktop (GNOME's screen lock only works with GDM; the other desktops
bring their own lockers). Which desktop starts is GDM's DefaultSession for new users and the live
system, and each user's saved session in AccountsService after that.

Run as a script for build-time helpers:  desktops.py grub-entries "kde gnome" kde
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

GDM_CONF = Path("/etc/gdm3/daemon.conf")
ACCOUNTS_DIR = Path("/var/lib/AccountsService/users")
CHOICE_FILE = Path("/var/lib/cherry/desktop-choice")
INSTALLER_CHOOSER = Path("/etc/calamares/modules/packagechooser@desktop.conf")


@dataclass(frozen=True)
class Desktop:
    id: str
    label: str
    tagline: str
    description: str
    layout: str
    # (type, session file name) in order of preference; the first one installed wins.
    sessions: tuple = field(default_factory=tuple)
    # Debian packages installed when someone picks this desktop (install_desktop). Empty: it's on the ISO.
    on_demand: tuple = field(default_factory=tuple)

    def session(self, root: Path = Path("/")) -> tuple[str, str] | None:
        for kind, name in self.sessions:
            folder = "wayland-sessions" if kind == "wayland" else "xsessions"
            if (root / "usr/share" / folder / f"{name}.desktop").exists():
                return kind, name
        return None

    @property
    def session_name(self) -> str:
        found = self.session()
        return found[1] if found else self.sessions[0][1]


DESKTOPS = {d.id: d for d in [
    Desktop("kde", "KDE Plasma", "Familiar, like Windows",
            "A taskbar and start menu along the bottom, with a setting for almost everything. "
            "The easiest switch from Windows.",
            "bottom-panel", (("wayland", "plasma"), ("x", "plasmax11"))),
    Desktop("gnome", "GNOME", "Clean and focused",
            "A top bar, a dock and a full-screen overview of your apps and windows. "
            "Feels a bit like a Mac or a tablet.",
            "top-bar-dock", (("wayland", "gnome"), ("x", "gnome-xorg"))),
    Desktop("cinnamon", "Cinnamon", "Classic and comfortable",
            "The traditional desktop from Linux Mint: a menu, taskbar and system tray where you expect them.",
            "bottom-panel", (("x", "cinnamon"),)),
    Desktop("xfce", "Xfce", "Light and fast",
            "Uses very little memory, so it keeps older and slower computers feeling quick.",
            "light", (("x", "xfce"),)),
    Desktop("budgie", "Budgie", "Modern and simple",
            "A tidy panel with a slide-out side panel for notifications, calendar and quick settings.",
            "side-panel", (("x", "budgie-desktop"),)),
    Desktop("hyprland", "Hyprland", "Tiling, for keyboard fans",
            "Windows arrange themselves side by side, with smooth animations, and everything has a keyboard "
            "shortcut. Downloads when you pick it.",
            "tiling", (("wayland", "hyprland"),),
            on_demand=("hyprland", "xdg-desktop-portal-hyprland", "xdg-desktop-portal-gtk", "hyprlock", "hypridle",
                       "hyprpolkitagent", "waybar", "fuzzel", "mako-notifier", "kitty", "swaybg", "grim", "slurp",
                       "wl-clipboard", "brightnessctl", "playerctl", "dex", "network-manager-gnome", "pavucontrol",
                       "fonts-font-awesome", "qt6-wayland", "libnotify-bin", "hyprland-guiutils")),
]}

# Hyprland is only in Debian 13's backports. These are the packages to take from there: Hyprland's own, and the
# one shared library it needs newer (libxkbcommon). Everything else stays on Debian's stable versions. Written only
# when someone installs Hyprland, so other computers never take anything from backports.
HYPRLAND_PIN = """# Cherry OS: Hyprland (installed from Cherry Center) comes from Debian's backports.
Package: hypr* libhypr* libaquamarine* libudis86* xdg-desktop-portal-hyprland qml6-module-org-hyprland-* src:libxkbcommon:any
Pin: release n={codename}-backports
Pin-Priority: 500
"""
HYPRLAND_UPDATES = """// Cherry OS: keep Hyprland (from Debian's backports) updated with everything else.
Unattended-Upgrade::Origins-Pattern {{
        "origin=Debian Backports,codename={codename}-backports";
}};
"""
# Debian starts these for every desktop's session; Cherry starts them only in Hyprland (desktop/hyprland/start).
HYPRLAND_USER_UNITS = ("waybar.service", "mako.service", "hypridle.service", "hyprpaper.service",
                       "hyprpolkitagent.service")


def is_live() -> bool:
    """Running from the USB stick before installing (Debian live-boot, or Ubuntu's casper)."""
    try:
        cmdline = Path("/proc/cmdline").read_text().split()
    except OSError:
        return False
    return "boot=live" in cmdline or "boot=casper" in cmdline


def os_name() -> str:
    """This system's name, from os-release ("Cherry OS", "Ubuntu", …)."""
    for path in ("/etc/os-release", "/usr/lib/os-release"):
        try:
            for line in Path(path).read_text().splitlines():
                if line.startswith("NAME="):
                    return line.split("=", 1)[1].strip().strip('"\'') or "Linux"
        except OSError:
            continue
    return "Linux"


def default_desktop() -> str:
    try:
        value = Path("/etc/cherry/default-desktop").read_text().strip()
        if value in DESKTOPS:
            return value
    except OSError:
        pass
    return "kde"


def desktop_for_session(session: str | None) -> str | None:
    for d in DESKTOPS.values():
        if session in (name for _, name in d.sessions):
            return d.id
    return None


def current_desktop() -> str | None:
    """The desktop of the running session, from the environment the login screen gives it."""
    session = os.environ.get("DESKTOP_SESSION") or os.environ.get("GDMSESSION")
    found = desktop_for_session(session)
    if found:
        return found
    xdg = os.environ.get("XDG_CURRENT_DESKTOP", "").lower()
    for key, did in (("kde", "kde"), ("gnome", "gnome"), ("cinnamon", "cinnamon"),
                     ("xfce", "xfce"), ("budgie", "budgie"), ("hyprland", "hyprland")):
        if key in xdg:
            return did
    return None


def set_ini_key(text: str, section: str, key: str, value: str | None) -> str:
    """Sets (or with value=None removes) key in [section] of an INI file, keeping everything else as is."""
    lines = text.splitlines()
    start = next((i for i, ln in enumerate(lines) if ln.strip() == f"[{section}]"), None)
    if start is None:
        if value is not None:
            lines += ([""] if lines else []) + [f"[{section}]", f"{key}={value}"]
        return "\n".join(lines) + "\n"
    end = next((i for i in range(start + 1, len(lines)) if lines[i].lstrip().startswith("[")), len(lines))
    body = [ln for ln in lines[start + 1:end] if not re.match(rf"\s*{re.escape(key)}\s*=", ln)]
    if value is not None:
        pos = len(body)
        while pos and not body[pos - 1].strip():
            pos -= 1
        body.insert(pos, f"{key}={value}")
    lines[start + 1:end] = body
    return "\n".join(lines) + "\n"


def accounts_file_text(old: str, desk: Desktop, root: Path = Path("/")) -> str:
    """AccountsService user file pointing at this desktop (what GDM starts for that user)."""
    found = desk.session(root) or desk.sessions[0]
    kind, name = found
    text = old or "[User]\nSystemAccount=false\n"
    text = set_ini_key(text, "User", "Session", name)
    text = set_ini_key(text, "User", "XSession", name if kind == "x" else "")
    return set_ini_key(text, "User", "SessionType", "wayland" if kind == "wayland" else "x11")


def write_user_session(username: str, desktop_id: str, root: Path = Path("/")) -> None:
    """Root only: make this user's next login start the desktop."""
    path = root / str(ACCOUNTS_DIR).lstrip("/") / username
    old = path.read_text() if path.exists() else ""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(accounts_file_text(old, DESKTOPS[desktop_id], root))
    path.chmod(0o644)


def set_user_session(desktop_id: str) -> None:
    """Make the current user's next login use this desktop (installed systems)."""
    kind, name = DESKTOPS[desktop_id].session() or DESKTOPS[desktop_id].sessions[0]
    found = subprocess.run(
        ["busctl", "--system", "call", "org.freedesktop.Accounts", "/org/freedesktop/Accounts",
         "org.freedesktop.Accounts", "FindUserByName", "s", os.environ.get("USER", "")],
        capture_output=True, text=True).stdout.split('"')
    if len(found) < 2:
        raise OSError("couldn't reach AccountsService")
    for method, value in (("SetSession", name), ("SetXSession", name if kind == "x" else ""),
                          ("SetSessionType", "wayland" if kind == "wayland" else "x11")):
        subprocess.run(["busctl", "--system", "call", "org.freedesktop.Accounts", found[1],
                        "org.freedesktop.Accounts.User", method, "s", value], capture_output=True)


def gdm_default_text(old: str, desk: Desktop, root: Path = Path("/")) -> str:
    kind, name = desk.session(root) or desk.sessions[0]
    return set_ini_key(old or "[daemon]\n", "daemon", "DefaultSession", f"{name}.desktop")


def _sudo_write(path: Path, text: str) -> None:
    subprocess.run(["sudo", "-n", "install", "-d", str(path.parent)], check=True)
    subprocess.run(["sudo", "-n", "tee", str(path)], input=text, text=True, stdout=subprocess.DEVNULL, check=True)


def installer_default_text(text: str, desktop_id: str) -> str:
    """The installer's desktop page starts on the desktop being tried."""
    return re.sub(r"(?m)^default:.*$", f"default: {desktop_id}", text)


def switch_live(desktop_id: str) -> None:
    """Live session: restart the login screen straight into another desktop (closes open apps)."""
    desk = DESKTOPS[desktop_id]
    user = os.environ.get("USER", "cherry")
    _sudo_write(GDM_CONF, gdm_default_text(GDM_CONF.read_text() if GDM_CONF.exists() else "", desk))
    accounts = ACCOUNTS_DIR / user
    old = subprocess.run(["sudo", "-n", "cat", str(accounts)], capture_output=True, text=True).stdout
    _sudo_write(accounts, accounts_file_text(old, desk))
    _sudo_write(CHOICE_FILE, desktop_id + "\n")
    if INSTALLER_CHOOSER.exists():
        _sudo_write(INSTALLER_CHOOSER, installer_default_text(INSTALLER_CHOOSER.read_text(), desktop_id))
    # accounts-daemon caches users; restart it so GDM sees the new session. The user's systemd manager carries
    # the old desktop's environment (GNOME's input-method variables make IBus nag on Plasma), so start it afresh,
    # from a system unit because this app may itself run under that manager.
    subprocess.Popen(["sudo", "-n", "systemd-run", "--no-block", "--quiet", "sh", "-c",
                      f"systemctl restart accounts-daemon; systemctl stop user@{os.getuid()}.service; "
                      "sleep 1; systemctl restart display-manager"])


def codename(root: Path = Path("/")) -> str:
    for line in (root / "etc/os-release").read_text().splitlines():
        if line.startswith("VERSION_CODENAME="):
            return line.split("=", 1)[1].strip().strip('"')
    return "trixie"


def prepare_install(desktop_id: str, root: Path = Path("/")) -> list[list[str]]:
    """Root only: the apt settings an on-demand desktop needs; returns the commands that then install it."""
    desk = DESKTOPS[desktop_id]
    if not desk.on_demand:
        raise ValueError(f"{desk.label} is already part of Cherry OS")
    cmds = [["apt-get", "update"],
            ["apt-get", "install", "-y", "--no-install-recommends", *desk.on_demand]]
    if desktop_id == "hyprland":
        name = codename(root)
        pin = root / "etc/apt/preferences.d/cherry-hyprland"
        pin.parent.mkdir(parents=True, exist_ok=True)
        pin.write_text(HYPRLAND_PIN.format(codename=name))
        updates = root / "etc/apt/apt.conf.d/53cherry-hyprland"
        updates.parent.mkdir(parents=True, exist_ok=True)
        updates.write_text(HYPRLAND_UPDATES.format(codename=name))
    return cmds


def finish_install(desktop_id: str, root: Path = Path("/")) -> list[list[str]]:
    """The commands that run once the packages are in: Hyprland's helpers mustn't start in other desktops."""
    if desktop_id != "hyprland":
        return []
    units = [u for u in HYPRLAND_USER_UNITS if (root / "usr/lib/systemd/user" / u).exists()]
    return [["systemctl", "--global", "disable", *units]] if units else []


def install_desktop(desktop_id: str) -> int:
    """Root only: downloads and installs an on-demand desktop, showing apt's progress."""
    env = dict(os.environ, DEBIAN_FRONTEND="noninteractive")
    for cmd in prepare_install(desktop_id):
        print("$ " + " ".join(cmd), flush=True)
        if code := subprocess.run(cmd, env=env).returncode:
            if not DESKTOPS[desktop_id].session():
                for path in ("etc/apt/preferences.d/cherry-hyprland", "etc/apt/apt.conf.d/53cherry-hyprland"):
                    (Path("/") / path).unlink(missing_ok=True)
            return code
    for cmd in finish_install(desktop_id):
        print("$ " + " ".join(cmd), flush=True)
        subprocess.run(cmd)
    return 0 if DESKTOPS[desktop_id].session() else 1


def write_system_default(desktop_id: str, root: Path = Path("/"), user: str | None = None) -> None:
    """Root only: the desktop new users (and the live system) start with; with user, that user's too."""
    desk = DESKTOPS[desktop_id]
    conf = root / str(GDM_CONF).lstrip("/")
    conf.parent.mkdir(parents=True, exist_ok=True)
    conf.write_text(gdm_default_text(conf.read_text() if conf.exists() else "", desk, root))
    if user:
        write_user_session(user, desktop_id, root)
    target = root / "etc/cherry/default-desktop"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(desktop_id + "\n")


XFCE_SCREENSAVER = """<?xml version="1.0" encoding="UTF-8"?>
<channel name="xfce4-screensaver" version="1.0">
  <property name="lock" type="empty"><property name="enabled" type="bool" value="false"/></property>
</channel>
"""


def live_no_lock(root: Path = Path("/")) -> None:
    """Root, live system only: the live user has no password anyone knows, so Plasma and Xfce mustn't lock
    the screen (GNOME, Budgie and Cinnamon get this from the live-only GSettings override)."""
    rc = root / "etc/xdg/kscreenlockerrc"
    text = rc.read_text() if rc.exists() else ""
    for key in ("Autolock", "LockOnResume"):
        text = set_ini_key(text, "Daemon", key, "false")
    rc.parent.mkdir(parents=True, exist_ok=True)
    rc.write_text(text)
    xfce = root / "etc/xdg/xfce4/xfconf/xfce-perchannel-xml/xfce4-screensaver.xml"
    xfce.parent.mkdir(parents=True, exist_ok=True)
    xfce.write_text(XFCE_SCREENSAVER)


def live_no_update_prompts(root: Path = Path("/")) -> None:
    """Root, live system only: updating the USB stick's session is pointless, so Plasma mustn't offer it
    (GNOME Software gets this from the live-only GSettings override)."""
    notifier = root / "etc/xdg/autostart/org.kde.discover.notifier.desktop"
    if notifier.exists():
        notifier.write_text(set_ini_key(notifier.read_text(), "Desktop Entry", "Hidden", "true"))


def grub_entries(ids: list[str], default: str) -> str:
    ordered = [default] + [i for i in ids if i != default]
    out = []
    for did in ordered:
        d = DESKTOPS[did]
        out.append(
            f'menuentry "Try Cherry OS with {d.label}" {{\n'
            f"  linux /live/vmlinuz boot=live components quiet splash cherry.desktop={d.id} @LIVE_ARGS@\n"
            f"  initrd /live/initrd.img\n}}\n")
    return "\n".join(out)


if __name__ == "__main__":
    if len(sys.argv) >= 4 and sys.argv[1] == "grub-entries":
        print(grub_entries(sys.argv[2].split(), sys.argv[3]))
    else:
        print(__doc__)
