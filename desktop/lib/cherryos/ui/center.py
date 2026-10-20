"""Cherry Center: welcome, desktop chooser, file transfer, Windows apps and updates in one place."""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

from gi.repository import Adw, Gdk, GdkPixbuf, Gio, GLib, Gtk

from .. import __version__
from ..desktops import DESKTOPS, current_desktop, default_desktop, is_live, set_user_session, switch_live
from ..transfer import load_pending
from . import (add_close_shortcuts, apply_css, hbox, identify, message, page_header, pill, run_async, scrolled,
               status_page, vbox)
from .move import DEFAULT_INSTALLER

APP_ID = "org.cherryos.Center"
SCREENS = Path(os.environ.get("CHERRY_DATA", "/usr/share/cherry")) / "screens"
WELCOME_DONE = Path.home() / ".config/cherry/welcome-done"
SUPPORT_END = "June 2030"


def launch(*cmd: str) -> None:
    # A launch context hands the app an activation token; without one, GNOME and KDE refuse to raise an
    # app that is already running (e.g. Cherry Move hidden behind this window).
    if cmd[0] == "pkexec":
        # pkexec refuses to run once its parent has exited, and GLib's launcher exits straight after spawning,
        # so leave a shell waiting on it. (Same reason the installer command doesn't `exec pkexec`.)
        cmd = ("sh", "-c", 'pkexec "$@"', "sh", *cmd[1:])
    line = " ".join(GLib.shell_quote(c) for c in cmd)
    try:
        info = Gio.AppInfo.create_from_commandline(line, None, Gio.AppInfoCreateFlags.SUPPORTS_STARTUP_NOTIFICATION)
        info.launch([], Gdk.Display.get_default().get_app_launch_context())
    except GLib.Error as err:
        print(f"cherry-center: can't start {cmd[0]}: {err.message}")


def log_out() -> None:
    d = current_desktop()
    cmds = {
        "kde": ["dbus-send", "--session", "--dest=org.kde.Shutdown", "/Shutdown", "org.kde.Shutdown.logout"],
        "gnome": ["gnome-session-quit", "--logout", "--no-prompt"],
        "budgie": ["gnome-session-quit", "--logout", "--no-prompt"],
        "cinnamon": ["cinnamon-session-quit", "--logout", "--no-prompt"],
        "xfce": ["xfce4-session-logout", "--logout"],
    }
    cmd = cmds.get(d or "")
    if not cmd or subprocess.run(cmd).returncode != 0:
        subprocess.run(["loginctl", "terminate-session", os.environ.get("XDG_SESSION_ID", "")])


class CenterWindow(Adw.ApplicationWindow):
    def __init__(self, app, page: str = "welcome"):
        super().__init__(application=app, title="Cherry Center", default_width=1080, default_height=720)
        self.toasts = Adw.ToastOverlay()
        self.stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE, hexpand=True)
        side = Gtk.StackSidebar(stack=self.stack)
        side.set_size_request(200, -1)
        pages = [("welcome", "Welcome", self.page_welcome), ("desktop", "Desktop", self.page_desktop),
                 ("files", "Your files", self.page_files), ("store", "Apps", self.page_store),
                 ("windows", "Windows apps", self.page_windows), ("updates", "Updates & safety", self.page_updates),
                 ("about", "About", self.page_about)]
        for pid, title, build in pages:
            self.stack.add_titled(build(), pid, title)
        header = Adw.HeaderBar()
        body = hbox(0, side, Gtk.Separator(orientation=Gtk.Orientation.VERTICAL), self.stack)
        body.set_vexpand(True)
        self.toasts.set_child(vbox(0, 0, header, body))
        self.set_content(self.toasts)
        self.stack.set_visible_child_name(page)

    def toast(self, text):
        self.toasts.add_toast(Adw.Toast(title=text, timeout=4))

    # ------------------------------------------------------------ pages

    def page_welcome(self):
        live = is_live()
        buttons = vbox(10, 0, halign=Gtk.Align.CENTER)
        if live:
            b = pill("Install Cherry OS", True, lambda: launch("sh", "-c", DEFAULT_INSTALLER))
            b.add_css_class("cherry-big-button")
            buttons.append(b)
        for label, page in [("Choose your desktop", "desktop"), ("Bring your files from your old computer", "files"),
                            ("Get apps", "store")]:
            buttons.append(pill(label, not live and page == "desktop", lambda p=page: self.stack.set_visible_child_name(p)))
        desc = ("You're trying Cherry OS — nothing is changed on this computer until you install it. "
                "Try each desktop, then install when you're ready." if live else
                "Everything is set up: Windows apps open with a double-click, Python, Java and more are installed, "
                "and security updates arrive by themselves.")
        return scrolled(status_page("cherry-logo", "Welcome to Cherry OS", desc, buttons))

    def page_desktop(self):
        self.desktop_page = scrolled(self.desktop_cards())
        return self.desktop_page

    def refresh_desktops(self):
        clamp = self.desktop_page.get_child()
        if isinstance(clamp, Gtk.Viewport):
            clamp = clamp.get_child()
        clamp.set_child(self.desktop_cards())

    def desktop_cards(self):
        cur = current_desktop()
        live = is_live()
        flow = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, max_children_per_line=2, min_children_per_line=1,
                           column_spacing=18, row_spacing=18, homogeneous=True)
        for d in DESKTOPS.values():
            installed = d.session() is not None
            if live and not installed:
                continue
            shot = SCREENS / f"{d.id}.png"
            try:
                # Loaded at card size: a full-size picture would make each card as wide as the screenshot.
                small = GdkPixbuf.Pixbuf.new_from_file_at_scale(str(shot), 380, 214, True)
                pic = Gtk.Picture.new_for_paintable(Gdk.Texture.new_for_pixbuf(small))
                pic.set_size_request(380, 214)
                pic.add_css_class("cherry-preview")
            except GLib.Error:
                pic = Gtk.Image(icon_name="video-display", pixel_size=96)
            title = Gtk.Label(label=d.label, xalign=0)
            title.add_css_class("title-3")
            tag = Gtk.Label(label=d.tagline, xalign=0)
            tag.add_css_class("dim-label")
            desc = Gtk.Label(label=d.description, xalign=0, wrap=True, max_width_chars=44)
            if d.id == cur:
                action = Gtk.Label(label="✓ You're using this now")
                action.add_css_class("success")
            elif not installed:
                action = pill(f"Install {d.label}", True, lambda did=d.id: self.install_desktop(did))
            else:
                action = pill(f"Try {d.label} now" if live else f"Switch to {d.label}", True,
                              lambda did=d.id: self.switch(did))
            action.set_halign(Gtk.Align.START)
            card = vbox(8, 14, pic, title, tag, desc, action)
            card.add_css_class("card")
            if d.id == cur:
                card.add_css_class("cherry-selected")
            flow.append(card)
        intro = ("Every desktop is installed. Try them now — open apps will close while it switches. The one you're "
                 "using when you install becomes your desktop." if live else
                 "Pick the desktop you like. You'll switch the next time you log in, and your files and apps stay the same.")
        head = page_header("preferences-desktop-display-symbolic", "Choose your desktop", intro)
        return vbox(18, 18, head, flow)

    def install_desktop(self, did):
        d = DESKTOPS[did]

        def go(answer):
            if answer != "go":
                return
            wait = message(self, f"Installing {d.label}…", "Downloading it from Debian. This takes a minute or two; "
                           "you can keep using the computer.", (("hide", "Hide"),))
            run_async(lambda: subprocess.run(["pkexec", "cherry-desktop", "install", did],
                                             stdin=subprocess.DEVNULL, capture_output=True, text=True),
                      lambda res: self.installed_desktop(did, res, wait),
                      lambda err: self.installed_desktop(did, None, wait))

        message(self, f"Install {d.label}?",
                f"{d.label} downloads from Debian (about 20 MB) and updates itself with the rest of Cherry OS. "
                "Your other desktops, files and apps stay as they are.",
                (("cancel", "Cancel"), ("go", "Install")), callback=go)

    def installed_desktop(self, did, res, wait):
        d = DESKTOPS[did]
        close = getattr(wait, "force_close", None) or getattr(wait, "close", None) or getattr(wait, "destroy")
        close()
        if res is None or res.returncode or d.session() is None:
            if res is not None and res.returncode in (126, 127):  # the password window was cancelled
                return
            detail = ((res.stdout or "") + (res.stderr or "")).strip().splitlines()[-3:] if res else []
            message(self, f"Couldn't install {d.label}",
                    "Check the internet connection and try again." + ("\n\n" + "\n".join(detail) if detail else ""))
            return
        self.refresh_desktops()
        self.switch(did)

    def switch(self, did):
        d = DESKTOPS[did]
        if is_live():
            message(self, f"Switch to {d.label}?", "Open windows will close. It takes a few seconds.",
                    (("cancel", "Cancel"), ("go", "Switch")),
                    callback=lambda r: r == "go" and switch_live(did))
            return
        try:
            set_user_session(did)
        except OSError as err:
            self.toast(f"Couldn't switch: {err}")
            return
        message(self, f"{d.label} is ready", "Log out now to start using it? Save your work first.",
                (("later", "Later"), ("now", "Log out now")), callback=lambda r: r == "now" and log_out())

    def page_files(self):
        send = pill("Send files from this computer", False, lambda: launch("cherry-move", "--send"))
        get = pill("Get files from my old computer", True, lambda: launch("cherry-move", "--receive"))
        rows = Adw.PreferencesGroup(title="Where your files can wait")
        for title, sub, icon in [
            ("USB sticks", "Cherry OS spots a transfer stick by itself — even while installing. Too much for one "
                           "stick? Use several.", "drive-removable-media-symbolic"),
            ("Your own cloud", "Google Drive, OneDrive or MEGA", "folder-remote-symbolic"),
            ("A NAS at home", "Or any shared folder on your home network", "network-server-symbolic"),
        ]:
            r = Adw.ActionRow(title=title, subtitle=sub)
            r.add_prefix(Gtk.Image(icon_name=icon))
            rows.add(r)
        other = Gtk.Label(wrap=True, xalign=0, selectable=True,
                          label="Coming from Windows, a Mac or another Linux? Install the Cherry Move app there: "
                                "it's in the CherryMove folder on the Cherry OS USB stick or download.")
        head = page_header("folder-download-symbolic", "Your files",
                           "Bring documents, photos, music, browser bookmarks and more from your old computer. "
                           "Everything is encrypted before it leaves.")
        return scrolled(vbox(18, 18, head, hbox(12, get, send, halign=Gtk.Align.CENTER), rows, other))

    def page_store(self):
        store = pill("Open Cherry Store", True, lambda: launch("cherry-store"))
        sw = pill("More apps (Flathub & Debian)", False, self.open_software_center)
        return scrolled(status_page("system-software-install-symbolic", "Apps",
                                    "The Cherry Store has apps published by anyone — no gatekeeping. Every app is "
                                    "signed by its publisher and virus-scanned, with any warnings shown before you "
                                    "install. You can also get thousands of apps from Flathub and Debian.",
                                    hbox(12, store, sw, halign=Gtk.Align.CENTER)))

    def open_software_center(self):
        d = current_desktop()
        for cmd in (["plasma-discover"] if d == "kde" else []) + [["gnome-software"], ["plasma-discover"]]:
            cmd = cmd if isinstance(cmd, list) else [cmd]
            if shutil.which(cmd[0]):
                launch(*cmd)
                return
        self.toast("No software centre found")

    def page_windows(self):
        run = pill("Open a Windows program…", True, self.pick_exe)
        group = Adw.PreferencesGroup(title="Tools")
        for title, sub, cmd in [
            ("Windows settings", "Windows version, screen scaling, drives (winecfg)", ["cherry-windows-run", "--settings"]),
            ("Extra Windows components", "Fonts, .NET, Visual C++ runtimes and more (Winetricks)", ["winetricks", "--gui"]),
            ("Installed Windows programs", "Remove Windows apps you installed", ["cherry-windows-run", "--uninstaller"]),
        ]:
            r = Adw.ActionRow(title=title, subtitle=sub, activatable=True)
            r.add_suffix(Gtk.Image(icon_name="go-next-symbolic"))
            r.connect("activated", lambda _r, c=cmd: launch(*c))
            group.add(r)
        head = page_header("wine", "Windows apps",
                           "Double-click any .exe or .msi file to run or install it — installed programs appear in "
                           "your app menu like any other app. Windows apps from the Cherry Store install with one "
                           "click. Most apps work; some games with anti-cheat and a few big suites (like Adobe) don't.")
        run.set_halign(Gtk.Align.CENTER)
        return scrolled(vbox(18, 18, head, run, group))

    def pick_exe(self):
        dlg = Gtk.FileDialog(title="Choose a Windows program")
        f = Gtk.FileFilter()
        f.set_name("Windows programs")
        for pat in ("*.exe", "*.EXE", "*.msi", "*.MSI"):
            f.add_pattern(pat)
        filters = Gio.ListStore.new(Gtk.FileFilter)
        filters.append(f)
        dlg.set_filters(filters)

        def done(d, res):
            try:
                path = d.open_finish(res).get_path()
            except GLib.Error:
                return
            launch("cherry-windows-run", path)
        dlg.open(self, None, done)

    def page_updates(self):
        auto = Path("/etc/apt/apt.conf.d/20auto-upgrades")
        on = auto.exists() and 'Unattended-Upgrade "1"' in auto.read_text()
        group = Adw.PreferencesGroup(title="Staying safe")
        rows = [
            ("Automatic security updates", "On — fixes install by themselves every day" if on else "Off", "security-high-symbolic"),
            (f"Supported until {SUPPORT_END}", "Cherry OS 1.0 is built on Debian 13, which gets security fixes for 5 years "
                                              "(Debian LTS). After that, upgrade to the next Cherry OS in place.", "object-select-symbolic"),
            ("Cherry apps", "Cherry Center, Cherry Store and Cherry Move update themselves every day", "emblem-synchronizing-symbolic"),
            ("Store apps", "Signed by their publishers and virus-scanned; update them from the Cherry Store", "system-software-install-symbolic"),
        ]
        for title, sub, icon in rows:
            r = Adw.ActionRow(title=title, subtitle=sub)
            r.add_prefix(Gtk.Image(icon_name=icon))
            group.add(r)
        tools = Adw.PreferencesGroup(title="Tools")
        for title, sub, cmd in [
            ("Check for updates now", "Installs all available updates", ["cherry-update"]),
            ("System snapshots", "Take a snapshot before big changes, and roll back if something breaks (Timeshift)",
             ["pkexec", "timeshift-launcher"] if shutil.which("timeshift-launcher") else ["timeshift-gtk"]),
            ("Update Cherry Store apps", "Get the newest versions of apps you installed from the store", ["cherry-store", "--updates"]),
        ]:
            r = Adw.ActionRow(title=title, subtitle=sub, activatable=True)
            r.add_suffix(Gtk.Image(icon_name="go-next-symbolic"))
            r.connect("activated", lambda _r, c=cmd: launch(*c))
            tools.add(r)
        head = page_header("software-update-available-symbolic", "Updates & safety")
        return scrolled(vbox(18, 18, head, group, tools))

    def page_about(self):
        info = {}
        try:
            for line in Path("/etc/os-release").read_text().splitlines():
                k, _, v = line.partition("=")
                info[k] = v.strip('"')
        except OSError:
            pass
        group = Adw.PreferencesGroup()
        for title, value in [("Version", info.get("PRETTY_NAME", "Cherry OS")), ("Based on", "Debian 13 “trixie”"),
                             ("Cherry apps", __version__), ("Default desktop", DESKTOPS[default_desktop()].label),
                             ("Security updates until", SUPPORT_END)]:
            r = Adw.ActionRow(title=title, subtitle=value)
            group.add(r)
        return scrolled(vbox(18, 18, page_header("cherry-logo", "Cherry OS", "Free, friendly and yours."), group))


class CenterApp(Adw.Application):
    def __init__(self, page="welcome"):
        identify(APP_ID, "Cherry Center", "cherry-logo")
        super().__init__(application_id=APP_ID)
        self.page = page

    def do_startup(self):
        Adw.Application.do_startup(self)
        add_close_shortcuts(self)

    def do_activate(self):
        apply_css()
        win = self.props.active_window or CenterWindow(self, self.page)
        win.present()


def main(argv):
    import sys
    page = "welcome"
    if "--welcome" in argv:
        if WELCOME_DONE.exists() and not is_live():
            return 0
        # Opening now would bury the restore window behind this one; Cherry Move opens the welcome afterwards.
        if "--after-restore" not in argv and not is_live() and load_pending():
            return 0
        WELCOME_DONE.parent.mkdir(parents=True, exist_ok=True)
        WELCOME_DONE.touch()
    for a in argv:
        if a.startswith("--page="):
            page = a.split("=", 1)[1]
    return CenterApp(page).run([sys.argv[0]])
