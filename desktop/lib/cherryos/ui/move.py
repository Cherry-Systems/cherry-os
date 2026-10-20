"""Cherry Move window: send files from this computer, or get them from your old one."""
from __future__ import annotations

import shutil
import socket
import subprocess
import threading
import time
from pathlib import Path

from gi.repository import Adw, Gio, GLib, Gtk

from .. import remote as remotes
from .. import transfer
from ..codes import BadCode, keys_from_code, pretty
from ..desktops import is_live, os_name
from . import (add_close_shortcuts, apply_css, hbox, identify, message, page_header, pill, qr_texture, run_async,
               scrolled, status_page, ui, vbox)

APP_ID = "org.cherryos.Move"
human = transfer.human
# Debian's launcher, which Cherry's installer hooks build on (the Fast Startup check, the Nvidia restart note).
DEFAULT_INSTALLER = ("command -v calamares-install-debian >/dev/null && exec calamares-install-debian"
                     " || exec pkexec calamares")
CATS_STAY_FRESH = 15 * 60  # seconds the sizes on "What should come with you?" are reused when going back to it


def setting(key: str, default: str) -> str:
    """KEY=value lines in /etc/cherry/move.conf, for operating systems that ship Cherry Move."""
    try:
        for line in Path("/etc/cherry/move.conf").read_text().splitlines():
            if line.startswith(f"{key}="):
                return line.split("=", 1)[1].strip() or default
    except OSError:
        pass
    return default


def this_os() -> str:
    return setting("OS_NAME", "") or os_name()

STORAGE = [
    ("usb", "USB stick or external drive",
     "No internet needed. Cherry Move checks it fits first, and can spread over several sticks.",
     "drive-removable-media-symbolic"),
    ("mine", "My Google Drive, OneDrive or MEGA", "Uses the free space in your own account.", "folder-remote-symbolic"),
    ("nas", "A NAS or another computer at home", "A network drive or shared folder on your home network.",
     "network-server-symbolic"),
]
CARRY = [
    ("usb", "A USB stick", "Only the code goes on it (a few KB). Plug it into the new computer and your files come "
                           "back by themselves", "drive-removable-media-symbolic"),
    ("phone", "My phone", "Scan a QR code to keep the code on your phone, then type it on the new computer",
     "phone-symbolic"),
    ("code", "A code I'll keep safe", "Write down or print 34 letters and numbers", "dialog-password-symbolic"),
]


def move_details(info: dict) -> str:
    """"1,204 files, 3.2 GB · from Windows 11 · sent 3 Oct 2026" from what a transfer says about itself."""
    parts = []
    if info.get("files"):
        parts.append(f"{info['files']:,} {'file' if info['files'] == 1 else 'files'}"
                     + (f", {human(info['size'])}" if info.get("size") else ""))
    if info.get("source_os"):
        parts.append(f"from {info['source_os']}")
    sent = transfer.sent_at(info)
    if sent:
        parts.append(f"sent {sent:%-d %b %Y} at {sent.strftime('%-I:%M %p').lower()}")
    return " · ".join(parts) or "a Cherry Move transfer"


def radio_rows(group, options, on_pick, selected=None):
    """Adds one radio row per (id, title, subtitle, icon); returns {id: row}."""
    rows, first = {}, None
    for oid, title, sub, icon in options:
        row = Adw.ActionRow(title=title, subtitle=sub)
        row.add_prefix(Gtk.Image(icon_name=icon))
        radio = Gtk.CheckButton()
        if first:
            radio.set_group(first)
        else:
            first = radio
        radio.set_active(oid == (selected or options[0][0]))
        radio.connect("toggled", lambda r, o=oid: r.get_active() and on_pick(o))
        row.add_suffix(radio)
        row.set_activatable_widget(radio)
        group.add(row)
        rows[oid] = row
    return rows


class DrivePicker:
    """A USB stick dropdown that follows sticks being plugged in. With `fit`, says whether the files fit."""

    def __init__(self, win, title: str, fit: bool = False):
        self.win, self.fit = win, fit
        self.drives: list[transfer.Drive] = []
        self.group = Adw.PreferencesGroup(title=title)
        self.dropdown = Gtk.DropDown(valign=Gtk.Align.CENTER)
        self.row = Adw.ActionRow(title="USB stick", subtitle="Looking for USB sticks…")
        self.row.add_suffix(self.dropdown)
        self.group.add(self.row)
        self.dropdown.connect("notify::selected", lambda *_: self.refresh())
        if fit:
            self.fit_icon = Gtk.Image(icon_name="content-loading-symbolic")
            self.fit_row = Adw.ActionRow(title="Space")
            self.fit_row.add_prefix(self.fit_icon)
            self.group.add(self.fit_row)

    def start(self):
        def done(drives):
            if self.group.get_root() is None:
                return
            mounted = [d for d in drives if d.mount]
            if [(d.device, d.free) for d in mounted] != [(d.device, d.free) for d in self.drives]:
                keep = self.selected()
                self.drives = mounted
                self.dropdown.set_model(Gtk.StringList.new([d.title for d in mounted] or ["(none)"]))
                for i, d in enumerate(mounted):
                    if keep and d.device == keep.device:
                        self.dropdown.set_selected(i)
                self.row.set_subtitle("Plug in a USB stick" if not mounted else f"{len(mounted)} found")
                self.refresh()
            GLib.timeout_add_seconds(3, lambda: run_async(transfer.removable_drives, done) and False)
        run_async(transfer.removable_drives, done)

    def selected(self) -> transfer.Drive | None:
        i = self.dropdown.get_selected()
        return self.drives[i] if self.drives and i < len(self.drives) else None

    def refresh(self):
        if not self.fit:
            return
        d, sizes = self.selected(), self.win.sizes
        if d is None:
            icon, title, sub = "drive-removable-media-symbolic", "Space", "Plug in a USB stick or external drive"
        elif sizes is None:
            icon, title, sub = "content-loading-symbolic", "Space", "Checking how much space your files need…"
        else:
            free = transfer.usable_space(d.mount)
            f = transfer.fit(sizes, free)
            if f.sticks == 1:
                icon, title = "emblem-ok-symbolic", "It fits"
                sub = f"Needs about {human(f.need)}; {human(free)} free"
                if f.level != transfer.NORMAL:
                    title, sub = "It fits, just", sub + ". Packing harder to fit makes it a bit slower."
            else:
                icon, title = "dialog-warning-symbolic", "This stick is too small"
                sub = (f"Needs about {human(f.need)}; only {human(free)} free. Use one with at least "
                       f"{human(bigger_stick(f.need))} free, or {f.sticks} sticks this size.")
        self.fit_icon.set_from_icon_name(icon)
        self.fit_row.set_title(title)
        self.fit_row.set_subtitle(sub)


def bigger_stick(need: int) -> int:
    return int(need * 1.05) + transfer.STICK_RESERVE


class RemoteForm:
    """Sign-in for your own cloud drive (mode "mine") or a NAS (mode "nas"). Calls on_ready(form) once connected."""

    def __init__(self, win, mode: str, descriptor: dict | None = None, on_ready=None):
        self.win, self.mode, self.on_ready = win, mode, on_ready
        self.remote: remotes.Remote | None = None
        self.free: int | None = None
        self.insecure = False  # trust the NAS's own https certificate (asked first)
        self.cancel = threading.Event()
        d = descriptor or {}
        self.group = Adw.PreferencesGroup(title="Your account" if mode == "mine" else "Your NAS")
        self.entries = []
        if mode == "mine":
            self.kinds = ["gdrive", "onedrive", "mega"]
            self.kind_row = Adw.ComboRow(title="Service",
                                         model=Gtk.StringList.new([remotes.KINDS[k].title for k in self.kinds]))
            if d.get("kind") in self.kinds:
                self.kind_row.set_selected(self.kinds.index(d["kind"]))
                self.kind_row.set_sensitive(False)
            self.kind_row.connect("notify::selected", lambda *_: self._changed())
            self.email = self._entry(Adw.EntryRow(title="Email"), d.get("user", ""))
            self.password = self._entry(Adw.PasswordEntryRow(title="Password"))
            for w in (self.kind_row, self.email, self.password):
                self.group.add(w)
        else:
            self.found: list[remotes.FoundNas] = []
            self.found_row = Adw.ComboRow(title="Found on your network", model=Gtk.StringList.new(["Looking…"]),
                                          visible=False)
            self.found_row.connect("notify::selected", lambda *_: self._pick_found())
            self.types = list(remotes.NAS_TYPES)
            self.type_row = Adw.ComboRow(title="Connect with", model=Gtk.StringList.new(list(remotes.NAS_TYPES.values())))
            if d.get("type") in self.types:
                self.type_row.set_selected(self.types.index(d["type"]))
            self.type_row.connect("notify::selected", lambda *_: self._changed())
            self.host = self._entry(Adw.EntryRow(title="Address"), d.get("url") or d.get("host", ""))
            self.folder = self._entry(Adw.EntryRow(title="Folder"), d.get("path", ""))
            self.user = self._entry(Adw.EntryRow(title="Username"), d.get("user", ""))
            self.password = self._entry(Adw.PasswordEntryRow(title="Password"))
            self.port = d.get("port")
            for w in (self.found_row, self.type_row, self.host, self.folder, self.user, self.password):
                self.group.add(w)
            if not (d.get("host") or d.get("url")):
                run_async(remotes.discover_nas, self._show_found)
        self.note = Adw.ActionRow()
        self.note.add_css_class("dim-label")
        self.status = Adw.ActionRow(title="Not signed in" if mode == "mine" else "Not connected")
        self.button = pill("Sign in", True, self.connect)
        self.button.set_valign(Gtk.Align.CENTER)
        self.status.add_suffix(self.button)
        self.link = Gtk.LinkButton(label="Browser didn't open? Open the sign-in page", visible=False)
        for w in (self.note, self.status, self.link):
            self.group.add(w)
        if not remotes.available():
            self.status.set_subtitle("Needs rclone 1.60 or newer. Install it (sudo apt install rclone) and open Cherry Move again.")
            self.button.set_sensitive(False)
        self._changed()

    def _entry(self, row, text: str = ""):
        row.set_text(text)
        row.connect("changed", lambda *_: self._invalidate())
        self.entries.append(row)
        return row

    def kind(self) -> str:
        return self.kinds[self.kind_row.get_selected()] if self.mode == "mine" else "nas"

    def nas_type(self) -> str:
        return self.types[self.type_row.get_selected()]

    def _changed(self):
        self._invalidate()
        if self.mode == "mine":
            k = remotes.KINDS[self.kind()]
            self.email.set_visible(k.id == "mega")
            self.password.set_visible(k.id == "mega")
            self.note.set_subtitle(k.note)
            self.button.set_label({"gdrive": "Sign in with Google", "onedrive": "Sign in with Microsoft"}.get(k.id, "Sign in"))
        else:
            t = self.nas_type()
            self.host.set_title("Web address (e.g. https://nas.local:5006/)" if t == "webdav"
                                else "Address (name or IP, e.g. nas.local or 192.168.1.20)")
            self.folder.set_visible(t != "webdav")
            self.folder.set_title("Shared folder, and a folder inside it if you like (e.g. Backups or Backups/Move)"
                                  if t == "smb" else "Folder (e.g. Backups or /volume1/Backups)")
            self.note.set_subtitle(remotes.KINDS["nas"].note)
            self.button.set_label("Connect")

    def _invalidate(self):
        if self.insecure:
            self.insecure = False
            self.button.set_label("Connect")
        if self.remote is not None:
            self.remote = None
            self.status.set_title("Not signed in" if self.mode == "mine" else "Not connected")
            self.status.set_subtitle("")

    def _show_found(self, found):
        self.found = found
        if found and self.group.get_root() is not None:
            self.found_row.set_model(Gtk.StringList.new(["Type it in myself"] + [f.label for f in found]))
            self.found_row.set_visible(True)

    def _pick_found(self):
        i = self.found_row.get_selected()
        if 0 < i <= len(self.found):
            f = self.found[i - 1]
            self.type_row.set_selected(self.types.index(f.type))
            self.host.set_text(f"http://{f.host}:{f.port}/" if f.type == "webdav" else f.host)
            self.port = None if f.port in (22, 445) else f.port

    def settings(self) -> dict:
        if self.mode == "mine":
            return {"user": self.email.get_text().strip()} if self.kind() == "mega" else {}
        t = self.nas_type()
        if t == "webdav":
            s = {"type": t, "url": self.host.get_text().strip(), "user": self.user.get_text().strip()}
        else:
            s = {"type": t, "host": self.host.get_text().strip(), "path": self.folder.get_text().strip(),
                 "user": self.user.get_text().strip()}
            if self.port:
                s["port"] = self.port
        if self.insecure:
            s["insecure"] = True
        return s

    def connect(self):
        s = self.settings()
        if self.mode == "nas" and not (s.get("host") or s.get("url")):
            self.status.set_subtitle("Type the NAS's address first.")
            return
        if self.mode == "nas" and s["type"] == "smb" and not remotes.split_address(s).get("path"):
            self.status.set_subtitle("Type the name of the shared folder to use.")
            return
        if self.kind() == "mega" and not s["user"]:
            self.status.set_subtitle("Type your MEGA email and password.")
            return
        r = remotes.Remote(self.kind(), s)
        password = self.password.get_text() if self.password.get_visible() else ""
        self.button.set_sensitive(False)
        browser = self.kind() in ("gdrive", "onedrive")
        self.status.set_title("Signing in…" if self.mode == "mine" else "Connecting…")
        self.status.set_subtitle("Finish signing in in your web browser, then come back here." if browser else "")
        self.cancel.clear()

        def on_url(url):
            ui(lambda: (self.link.set_uri(url), self.link.set_visible(True)))

        def work():
            r.sign_in(password, on_url=on_url, cancel=self.cancel)
            return r.free_space()

        def done(free):
            self.remote, self.free = r, free
            self.win.remotes.append(r)
            self.button.set_sensitive(True)
            self.button.set_label("Sign in again" if self.mode == "mine" else "Connect again")
            self.link.set_visible(False)
            self.status.set_title("Signed in" if self.mode == "mine" else "Connected")
            self.status.set_subtitle(f"{human(free)} free" if free is not None else "Ready")
            if self.on_ready:
                self.on_ready(self)

        def failed(err):
            self.button.set_sensitive(True)
            self.link.set_visible(False)
            self.status.set_title("Couldn't sign in" if self.mode == "mine" else "Couldn't connect")
            self.status.set_subtitle("" if isinstance(err, InterruptedError) else str(err))
            if isinstance(err, remotes.UntrustedCertificate):
                self.insecure = True
                self.status.set_title("Is this your NAS?")
                self.button.set_label("Connect anyway")
        run_async(work, done, failed)


class MoveWindow(Adw.ApplicationWindow):
    def __init__(self, app, start: str = "home", drive: transfer.Drive | None = None, pending: dict | None = None):
        super().__init__(application=app, title="Cherry Move", default_width=820, default_height=720)
        self.cancel = threading.Event()
        self.busy = False
        self.sticks_page = None
        self.found_group = None
        self.stick_toast = None
        self.sizes = None
        self.plan = None
        self.cats: list[transfer.Category] | None = None
        self.cats_counted = 0.0
        self.checks: dict[str, Gtk.CheckButton] = {}
        self.count_cancel: threading.Event | None = None
        self.plan_cancel: threading.Event | None = None
        self.remotes: list[remotes.Remote] = []
        self.toasts = Adw.ToastOverlay()
        self.stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.SLIDE_LEFT_RIGHT)
        header = Adw.HeaderBar()
        self.back = Gtk.Button(icon_name="go-previous-symbolic", tooltip_text="Back")
        self.back.connect("clicked", lambda *_: self.go_home())
        header.pack_start(self.back)
        root = vbox(0, 0, header, self.stack)
        self.stack.set_vexpand(True)
        self.toasts.set_child(root)
        self.set_content(self.toasts)
        self.connect("close-request", self.on_close)
        if start == "receive":
            self.show_receive(drive)
        elif start == "send":
            self.show_choose()
        elif start == "pending" and pending:
            self.restore_pending(pending)
        else:
            self.go_home()

    # ------------------------------------------------------------ helpers

    def set_page(self, widget, back: bool = True):
        if widget.get_parent() is None:
            self.stack.add_named(widget, f"p{id(widget)}")
        self.stack.set_visible_child(widget)
        self.back.set_visible(back)
        # The second click of a double-click would otherwise land on whatever is now under the pointer (e.g.
        # untick a box on the next page).
        widget.set_can_target(False)
        GLib.timeout_add(500, lambda: widget.set_can_target(True))
        # Drop pages we've left so the stack doesn't grow forever.
        GLib.timeout_add(600, self._prune, widget)

    def _prune(self, keep):
        if self.stack.get_visible_child() is not keep:
            return False
        child = self.stack.get_first_child()
        while child:
            nxt = child.get_next_sibling()
            if child is not keep:
                self.stack.remove(child)
            child = nxt
        return False

    def toast(self, text: str):
        self.toasts.add_toast(Adw.Toast(title=text, timeout=4))

    def on_receive_page(self) -> bool:
        return self.found_group is not None and self.found_group.get_root() is not None

    def stick_plugged(self):
        """The USB watcher found a transfer stick while this window was already open."""
        if self.busy or self.on_receive_page() or self.stack.get_visible_child() is self.sticks_page:
            return  # a running restore or the receive page picks the stick up itself
        if self.stick_toast is None:
            self.stick_toast = Adw.Toast(title="A transfer USB stick was plugged in", button_label="Show", timeout=0)
            self.stick_toast.connect("button-clicked", lambda *_: self.show_receive(None))
            self.stick_toast.connect("dismissed", lambda *_: setattr(self, "stick_toast", None))
            self.toasts.add_toast(self.stick_toast)

    def on_close(self, *_):
        if self.busy:
            message(self, "Stop moving files?", "The transfer isn't finished yet.",
                    (("cancel", "Keep going"), ("stop", "Stop")), destructive="stop",
                    callback=lambda r: r == "stop" and (self.cancel.set(), self.sign_out(), self.destroy()))
            return True
        self.stop_planning()
        self.sign_out()
        return False

    def sign_out(self):
        """Sign-ins to someone's cloud drive or NAS only last while Cherry Move is open."""
        for r in self.remotes:
            r.forget()
        self.remotes.clear()

    def progress_page(self, title: str, text: str):
        bar = Gtk.ProgressBar(show_text=True)
        bar.set_size_request(420, -1)
        detail = Gtk.Label(label="Getting ready…", ellipsize=3, max_width_chars=60)
        cancel = pill("Cancel", False, lambda: self.cancel.set())
        self.progress_widget = status_page("folder-download-symbolic", title, text, vbox(12, 0, bar, detail, cancel))
        self.set_page(self.progress_widget, back=False)
        last = [0.0]

        def progress(done, total, name):
            now = time.monotonic()
            if now - last[0] < 0.15:
                return
            last[0] = now
            GLib.idle_add(lambda: (bar.set_fraction(done / total), bar.set_text(f"{human(done)} of {human(total)}"),
                                   detail.set_label(name)) and False)
        return progress

    # ------------------------------------------------------------ home

    def go_home(self):
        self.stop_planning()
        send = pill("Send files from this computer", True, self.show_choose)
        get = pill("Get files from my old computer", False, lambda: self.show_receive(None))
        for b in (send, get):
            b.add_css_class("cherry-big-button")
        intro = ("Moving to Cherry OS, a new PC or another Linux? Send your files from the old computer onto USB "
                 "sticks, your own cloud drive or a NAS, then get them back on the new one.")
        page = status_page("cherry-logo", "Move your files", intro, vbox(12, 0, send, get, halign=Gtk.Align.CENTER))
        self.set_page(page, back=False)

    # ------------------------------------------------------------ send: choose what

    def show_choose(self):
        self.stop_planning()
        group = Adw.PreferencesGroup(title="What should come with you?",
                                     description="Counting your files…")
        nxt = pill("Counting your files…", True, self.show_where)
        nxt.set_halign(Gtk.Align.END)
        nxt.set_sensitive(False)
        box = vbox(18, 24, group, nxt)
        self.set_page(scrolled(box))
        before = {cid: check.get_active() for cid, check in self.checks.items()}
        fresh = self.cats is not None and time.monotonic() - self.cats_counted < CATS_STAY_FRESH
        self.checks = {}

        def sized(c) -> str:
            return f"{human(c.size)} · {c.files:,} file{'' if c.files == 1 else 's'}"

        def ready():
            group.set_description("Everything is compressed and encrypted on this computer before it leaves.")
            nxt.set_label("Next")
            nxt.set_sensitive(True)

        def listed(cats):
            if group.get_root() is None:
                return
            self.cats = cats
            rows = {}
            for c in cats:
                row = Adw.ActionRow(title=c.label, subtitle=sized(c) if fresh else "Counting…")
                check = Gtk.CheckButton(active=before.get(c.id, c.default))
                row.add_prefix(check)
                row.set_activatable_widget(check)
                group.add(row)
                self.checks[c.id] = check
                rows[c.id] = row
            if fresh:
                ready()
                return
            self.cats_counted = 0.0
            cancel = self.count_cancel = threading.Event()
            last = [0.0]

            def counted(c, n):
                if time.monotonic() - last[0] > 0.3:
                    last[0] = time.monotonic()
                    ui(rows[c.id].set_subtitle, f"Counting… {n:,} files so far")

            def work():
                for c in cats:
                    transfer.scan_category(c, counted=lambda n, c=c: counted(c, n), cancel=cancel)
                    ui(rows[c.id].set_subtitle, sized(c))

            def done(_):
                self.cats_counted = time.monotonic()
                if group.get_root() is not None:
                    ready()

            def failed(e):
                if not isinstance(e, InterruptedError):
                    self.toast(f"Couldn't look at your files: {e}")
            run_async(work, done, failed)

        if fresh:
            listed(self.cats)
        else:
            run_async(lambda: transfer.categories(measure_sizes=False), listed,
                      lambda e: self.toast(f"Couldn't look at your files: {e}"))

    def chosen(self):
        return [c for c in self.cats if self.checks[c.id].get_active()]

    # ------------------------------------------------------------ send: where to keep the files

    def show_where(self):
        cats = self.chosen()
        if not cats:
            self.toast("Pick at least one thing to bring")
            return
        self.send_cats = cats
        self.plan = self.sizes = None
        raw = sum(c.size for c in cats)
        self.stop_planning()
        cancel = self.plan_cancel = threading.Event()
        token = self.plan_token = object()
        group = Adw.PreferencesGroup(title="Where should your files go?",
                                     description=f"{human(raw)} to move. Working out how small it packs…")
        self.storage = "usb"
        rows = radio_rows(group, STORAGE, self._set_storage)
        if not remotes.available():
            for k in ("mine", "nas"):
                rows[k].set_sensitive(False)
                rows[k].set_subtitle("Needs rclone 1.60 or newer (sudo apt install rclone)")
        self.usb_picker = DrivePicker(self, "Which USB stick?", fit=True)
        self.mine_form = RemoteForm(self, "mine")
        self.nas_form = RemoteForm(self, "nas")
        self.next_btn = pill("Working out the size…", True, self.where_next)
        self.next_btn.set_sensitive(False)
        self.next_btn.set_halign(Gtk.Align.END)
        box = vbox(18, 24, group, self.usb_picker.group, self.mine_form.group, self.nas_form.group, self.next_btn)
        self.set_page(scrolled(box))
        self._set_storage("usb")
        self.usb_picker.start()
        last = [0.0]

        def progress(done, total, name):
            if time.monotonic() - last[0] > 0.3:
                last[0] = time.monotonic()
                ui(group.set_description, f"{human(raw)} to move. {name}… {done * 100 // max(total, 1)}%")

        def work():
            plan = transfer.make_plan(cats, progress=progress, cancel=cancel)
            return plan, transfer.estimate(plan)

        def done(result):
            if self.plan_token is not token:
                return
            self.plan, self.sizes = result
            need = self.sizes[transfer.NORMAL]
            text = f"{human(raw)} to move."
            if self.plan.duplicates:
                text += f" {self.plan.duplicates:,} duplicate files ({human(self.plan.saved)}) are only stored once."
            if need < self.plan.size * 0.9:
                text += f" It packs down to about {human(need)}, with some room to spare."
            else:
                text += (f" Photos and videos are already compressed, so it needs about {human(need)}, with some "
                         "room to spare.")
            group.set_description(text)
            self.next_btn.set_label("Next")
            self.next_btn.set_sensitive(True)
            self.usb_picker.refresh()

        def failed(err):
            if not isinstance(err, InterruptedError):
                group.set_description(f"Couldn't look through your files: {err}")
        run_async(work, done, failed)

    def stop_planning(self):
        """Stops counting files or looking for duplicates for a page that's been left."""
        for cancel in (self.count_cancel, self.plan_cancel):
            if cancel is not None:
                cancel.set()

    def _set_storage(self, storage):
        self.storage = storage
        self.usb_picker.group.set_visible(storage == "usb")
        self.mine_form.group.set_visible(storage == "mine")
        self.nas_form.group.set_visible(storage == "nas")

    def where_next(self):
        if self.sizes is None:
            return
        need = self.sizes[transfer.NORMAL]
        if self.storage == "usb":
            drive = self.usb_picker.selected()
            if drive is None:
                self.toast("Plug in a USB stick first")
                return
            free = transfer.usable_space(drive.mount)
            f = transfer.fit(self.sizes, free)
            self.method = "usb"
            if f.sticks == 1:
                self.start_send("usb", drive=drive, level=f.level)
                return
            message(self, "This stick is too small",
                    f"Your files pack down to about {human(f.need)}, but this stick only has {human(free)} free.\n\n"
                    f"Use a USB stick or drive with at least {human(bigger_stick(f.need))} free, or {f.sticks} sticks "
                    "this size. With several sticks, Cherry Move tells you when to swap them.",
                    (("cancel", "Choose another stick"), ("several", f"Use {f.sticks} sticks")),
                    callback=lambda r: r == "several" and self.start_send("usb", drive=drive, level=f.level))
        else:
            form = self.mine_form if self.storage == "mine" else self.nas_form
            if form.remote is None:
                self.toast("Sign in first" if self.storage == "mine" else "Connect to your NAS first")
                return
            if form.free is not None and need > form.free:
                message(self, f"Not enough space in {form.remote.title}",
                        f"Your files pack down to about {human(need)}, and there's {human(form.free)} free. Free up "
                        "some space or choose somewhere else.",
                        (("cancel", "Back"), ("try", "Try anyway")),
                        callback=lambda r: r == "try" and self.show_carry("remote", form.remote))
                return
            self.show_carry("remote", form.remote)

    # ------------------------------------------------------------ send: how to find them again

    def show_carry(self, storage: str, remote: remotes.Remote):
        group = Adw.PreferencesGroup(title="How will you find them on the new computer?",
                                     description=f"Your files will be in {remote.title}, encrypted. The new computer needs "
                                                 "a code to find and unlock them.")
        self.method = "usb"
        picker = DrivePicker(self, "Which USB stick?")
        radio_rows(group, CARRY, lambda m: (setattr(self, "method", m), picker.group.set_visible(m == "usb")))

        def go():
            drive = picker.selected() if self.method == "usb" else None
            if self.method == "usb" and drive is None:
                self.toast("Plug in a USB stick first")
                return
            self.start_send(storage, drive=drive, remote=remote)
        start = pill("Start", True, go)
        start.set_halign(Gtk.Align.END)
        self.set_page(scrolled(vbox(18, 24, group, picker.group, start)))
        picker.start()

    # ------------------------------------------------------------ send: run

    def start_send(self, storage: str, drive: transfer.Drive | None = None, remote: remotes.Remote | None = None,
                   level: int = transfer.NORMAL):
        what = "copying to your USB stick" if storage == "usb" else f"uploading to {remote.title}"
        progress = self.progress_page("Packing your files", f"Compressing, encrypting and {what}")
        self.busy = True
        self.cancel.clear()
        cats, plan = self.send_cats, self.plan

        def work():
            try:
                return transfer.send(cats, storage, drive=drive.mount if drive else None, progress=progress,
                                     cancel=self.cancel, plan=plan, level=level, remote=remote,
                                     next_stick=lambda part, problem: self.ask_stick(part, problem, sending=True))
            finally:
                if remote is not None:
                    remote.forget()

        def done(result):
            self.busy = False
            self.show_sent(result)

        def failed(err):
            self.busy = False
            if isinstance(err, InterruptedError):
                self.go_home()
                self.toast("Cancelled")
                return
            self.set_page(status_page("dialog-error-symbolic", "Something went wrong", str(err),
                                      pill("Back", True, self.go_home)))
        run_async(work, done, failed)

    def ask_stick(self, part: int, problem: str | None, sending: bool) -> Path:
        """Worker thread: shows "plug in stick N" and waits until one is plugged in."""
        chosen = threading.Event()
        box = {}
        ui(self.show_stick_prompt, part, problem, sending, chosen, box)
        while not chosen.wait(0.3):
            if self.cancel.is_set():
                raise InterruptedError("cancelled")
        ui(self.set_page, self.progress_widget, False)
        return box["mount"]

    def show_stick_prompt(self, part, problem, sending, chosen, box):
        if sending:
            title = f"Stick {part - 1} is full"
            text = (f"Unplug it and write “{part - 1}” on it. Then plug in stick {part}. Anything already on that "
                    "stick stays there.")
        else:
            title = f"Plug in stick {part}"
            text = f"Your files are spread over several USB sticks. Plug in the one you wrote “{part}” on."
        err = Gtk.Label(label=problem or "", wrap=True, visible=bool(problem))
        err.add_css_class("error")
        sticks = Adw.PreferencesGroup(title="USB sticks plugged in")
        cancel = pill("Cancel", False, lambda: self.cancel.set())
        page = status_page("drive-removable-media-symbolic", title, text, vbox(12, 0, err, sticks, cancel))
        self.set_page(page, back=False)
        rows: dict = {}
        seen: list = [None]

        def pick(d):
            if not chosen.is_set():
                box["mount"] = d.mount
                chosen.set()

        def scan(drives):
            if chosen.is_set() or self.cancel.is_set() or page.get_root() is None:
                return
            mounted = [d for d in drives if d.mount]
            keys = {(d.device, d.label, d.size) for d in mounted}
            for d in mounted:
                key = (d.device, d.label, d.size)
                m = d.marker or {}
                if not sending and m.get("code") == self.restore_code and m.get("part", 1) == part:
                    return pick(d)
                if sending and seen[0] is not None and key not in seen[0]:
                    return pick(d)
                if key not in rows:
                    row = Adw.ActionRow(title=d.title, subtitle=f"{human(d.free)} free")
                    b = pill("Use this one", False, lambda d=d: pick(d))
                    b.set_valign(Gtk.Align.CENTER)
                    row.add_suffix(b)
                    sticks.add(row)
                    rows[key] = row
            for key in [k for k in rows if k not in keys]:
                sticks.remove(rows.pop(key))
            seen[0] = keys
            GLib.timeout_add_seconds(2, lambda: run_async(transfer.removable_drives, scan) and False)
        run_async(transfer.removable_drives, scan)

    def show_sent(self, result: transfer.SendResult):
        if result.storage == "usb":
            where = "on your USB stick" if result.sticks <= 1 else f"spread over {result.sticks} USB sticks"
        else:
            where = f"encrypted in {result.remote['title']}"
        code_label = Gtk.Label(label=pretty(result.code), selectable=True, wrap=True)
        code_label.add_css_class("cherry-code")
        copy = pill("Copy code", False, lambda: (self.get_clipboard().set(pretty(result.code)), self.toast("Copied")))
        save = pill("Save code as a file…", False, lambda: self.save_code(result))
        printb = pill("Print code", False, lambda: self.print_code(result))
        code_box = vbox(10, 0, code_label, hbox(8, copy, save, printb, halign=Gtk.Align.CENTER))
        if self.method == "usb":
            plug = "the stick" if result.sticks <= 1 else f"stick 1 (write 1 to {result.sticks} on them)"
            title, desc = "All done!", (f"{result.files} files are {where}. Plug {plug} into your new computer while "
                                        "installing or using Cherry OS and your files come back automatically.")
            reveal = Gtk.Expander(label="Show the code as a backup")
            reveal.set_child(code_box)
            child = vbox(12, 0, reveal)
        elif self.method == "phone":
            pic = Gtk.Picture.new_for_paintable(qr_texture(f"Cherry Move code: {pretty(result.code)}"))
            pic.set_size_request(280, 280)
            title, desc = "Scan this with your phone", ("Your phone's camera shows the code: save it in your notes or take "
                                                        "a photo. On the new computer, choose “Type a code”. "
                                                        f"{result.files} files are {where}.")
            reveal = Gtk.Expander(label="No camera? Show the code")
            reveal.set_child(code_box)
            child = vbox(12, 0, pic, reveal)
        else:
            title, desc = "Keep this code safe", (f"You'll type it on your new computer. Anyone with it can get your files, "
                                                  f"so don't share it. {result.files} files are {where}.")
            child = code_box
        if result.skipped:
            n = len(result.skipped)
            stayed = Gtk.Expander(label=f"{n} {'file' if n == 1 else 'files'} couldn't be read (another user's, or "
                                        "removed while packing) and stayed behind")
            stayed.set_child(Gtk.Label(label="\n".join(result.skipped[:30]) + ("\n…" if n > 30 else ""),
                                       selectable=True, wrap=True, xalign=0))
            child.append(stayed)
        child.append(pill("Finish", True, self.close))
        self.set_page(scrolled(status_page("object-select-symbolic", title, desc, child)), back=False)

    def save_code(self, result):
        def write(f):
            Path(f.get_path()).write_text(self._code_text(result))
            self.toast("Saved")

        if not hasattr(Gtk, "FileDialog"):  # GTK before 4.10 (Debian 12)
            chooser = Gtk.FileChooserNative(title="Save transfer code", transient_for=self,
                                            action=Gtk.FileChooserAction.SAVE)
            chooser.set_current_name("cherry-transfer-code.txt")

            def respond(c, r):
                if r == Gtk.ResponseType.ACCEPT:
                    write(c.get_file())
                c.destroy()
            chooser.connect("response", respond)
            chooser.show()
            self._chooser = chooser  # the native dialog closes if nothing holds on to it
            return
        dlg = Gtk.FileDialog(title="Save transfer code", initial_name="cherry-transfer-code.txt")

        def done(d, res):
            try:
                write(d.save_finish(res))
            except GLib.Error:
                return
        dlg.save(self, None, done)

    def _code_text(self, result):
        if result.storage == "remote":
            where = f"Your files are in {result.remote['title']}. You'll sign in to it again on the new computer."
        else:
            where = "Your files are on the USB stick" + (f"s (1 to {result.sticks})." if result.sticks > 1 else ".")
        return (f"Cherry Move transfer code\n{result.label}\n\n    {pretty(result.code)}\n\n"
                f"On your new computer open Cherry Move (or install Cherry OS), choose “Type a code” and enter it.\n"
                f"{where}\n")

    def print_code(self, result):
        path = Path(GLib.get_tmp_dir()) / "cherry-transfer-code.txt"
        path.write_text(self._code_text(result))
        if shutil.which("lp"):
            subprocess.Popen(["lp", str(path)])
            self.toast("Sent to your printer")
        else:
            subprocess.Popen(["xdg-open", str(path)])

    # ------------------------------------------------------------ receive

    def show_receive(self, drive: transfer.Drive | None):
        if self.stick_toast is not None:
            self.stick_toast.dismiss()
        found = Adw.PreferencesGroup(title="Transfer USB sticks", description="Plug in your Cherry transfer USB stick")
        self.found_group = found
        self.found_rows = {}
        code = pill("Type a code", False, self.show_code_entry)
        ways = Adw.PreferencesGroup(title="No stick?", description="Type the code you wrote down, printed or kept on "
                                                                   "your phone.")
        ways.add(hbox(12, code, halign=Gtk.Align.START))
        online = Adw.PreferencesGroup(title="Kept them online or on a NAS?")
        for title, sub, icon, kind in [
            ("Google Drive, OneDrive or MEGA", "Sign in to the same account", "folder-remote-symbolic", "mine"),
            ("A NAS or another computer at home", "Connect to it the same way", "network-server-symbolic", "nas"),
        ]:
            row = Adw.ActionRow(title=title, subtitle=sub, activatable=True)
            row.add_prefix(Gtk.Image(icon_name=icon))
            row.add_suffix(Gtk.Image(icon_name="go-next-symbolic"))
            row.connect("activated", lambda _r, k=kind: self.show_find_remote(k))
            if not remotes.available():
                row.set_sensitive(False)
                row.set_subtitle("Needs rclone 1.60 or newer (sudo apt install rclone)")
            online.add(row)
        note = ""
        if is_live():
            note = (f"You're trying {this_os()}. Pick your transfer now and your files will be restored straight "
                    f"after {this_os()} is installed.")
        page = scrolled(vbox(18, 24, page_header("folder-download-symbolic", "Get your files", note), found, online,
                             ways))
        self.set_page(page)
        if drive and drive.marker:
            self._add_found(drive)
        self._watch_markers()

    def _add_found(self, drive):
        if stick_key(drive) in self.found_rows:
            return
        m = drive.marker
        where = {"usb": f"on {drive.title}",
                 "remote": f"in {(m.get('remote') or {}).get('title', 'your storage')}"}.get(m.get("storage"), "")
        if m.get("storage") == "usb" and (m.get("part", 1) > 1 or not m.get("final", True)):
            where += f" (stick {m.get('part', 1)}" + (f" of {m['parts']})" if m.get("parts") else " of several)")
        row = Adw.ActionRow(title=m.get("label") or "Files from your old computer",
                            subtitle=f"{m.get('files', '?')} {'file' if m.get('files') == 1 else 'files'}, "
                                     f"{human(m.get('size', 0))} · from "
                                     f"{m.get('source_os', 'Linux')} · {where}")
        btn = pill("Restore", True, lambda: self.restore(m["code"], drive.mount, m.get("label") or "your old computer",
                                                       remote_desc=m.get("remote")))
        btn.set_valign(Gtk.Align.CENTER)
        row.add_suffix(btn)
        row.add_prefix(Gtk.Image(icon_name="drive-removable-media-symbolic"))
        self.found_group.add(row)
        self.found_rows[stick_key(drive)] = row
        self.found_group.set_description("")

    def _watch_markers(self):
        def done(drives):
            if self.found_group.get_root() is None:
                return
            here = {stick_key(d) for d in drives if d.marker}
            for key in [k for k in self.found_rows if k not in here]:
                self.found_group.remove(self.found_rows.pop(key))
            if not self.found_rows:
                self.found_group.set_description("Plug in your Cherry transfer USB stick")
            for d in drives:
                if d.marker:
                    self._add_found(d)
            GLib.timeout_add_seconds(3, lambda: run_async(transfer.removable_drives, done) and False)
        run_async(transfer.removable_drives, done)

    def show_find_remote(self, kind: str):
        """Drive or NAS picked on the receive page: connect first, then pick the transfer, then type its code."""
        mode = "nas" if kind == "nas" else "mine"
        if mode == "mine":
            icon, text = "folder-remote-symbolic", "Sign in to the account you sent your files to from your old computer."
        else:
            icon, text = "network-server-symbolic", "Connect to the NAS folder you sent your files to from your old computer."
        form = RemoteForm(self, mode, on_ready=lambda f: self.find_moves(f.remote, kind))
        self.set_page(scrolled(vbox(18, 24, page_header(icon, "Find your files", text), form.group)))

    def find_moves(self, remote: remotes.Remote, kind: str):
        self.set_page(status_page("edit-find-symbolic", f"Looking in {remote.title}", "Finding your transfers…",
                                  Gtk.Spinner(spinning=True, halign=Gtk.Align.CENTER)))
        page = self.stack.get_visible_child()

        def done(moves):
            if self.stack.get_visible_child() is page:
                self.show_moves(remote, moves, kind)

        def failed(err):
            if self.stack.get_visible_child() is page:
                self.set_page(status_page("dialog-error-symbolic", f"Couldn't look in {remote.title}", str(err),
                                          hbox(8, pill("Try again", True, lambda: self.find_moves(remote, kind)),
                                               pill("Back", False, lambda: self.show_receive(None)),
                                               halign=Gtk.Align.CENTER)))
        run_async(lambda: transfer.list_remote_moves(remote), done, failed)

    def show_moves(self, remote: remotes.Remote, moves: list, kind: str):
        if not moves:
            other = "Try another account" if kind == "mine" else "Try another folder"
            self.set_page(status_page(
                "edit-find-symbolic", f"No transfers in {remote.title}",
                "Cherry Move didn't find any finished transfers here. Check it's the same account or NAS folder you "
                "picked on your old computer, and that Cherry Move said \"All done!\" there.",
                hbox(8, pill(other, True, lambda: self.show_find_remote(kind)),
                     pill("Back", False, lambda: self.show_receive(None)), halign=Gtk.Align.CENTER)))
            return
        if len(moves) == 1:
            self.show_code_entry(remote, moves[0])
            return
        group = Adw.PreferencesGroup()
        for m in moves:
            row = Adw.ActionRow(title=m.label, subtitle=move_details(m.info), activatable=True)
            row.add_prefix(Gtk.Image(icon_name="folder-download-symbolic"))
            row.add_suffix(Gtk.Image(icon_name="go-next-symbolic"))
            row.connect("activated", lambda _r, mv=m: self.show_code_entry(remote, mv))
            group.add(row)
        self.set_page(scrolled(vbox(18, 24, page_header("folder-download-symbolic",
                                                        f"Your transfers in {remote.title}",
                                                        "Pick the one to bring back."), group)))

    def show_code_entry(self, remote: remotes.Remote | None = None, move: transfer.RemoteMove | None = None):
        """With a transfer already found in someone's storage, the code only has to unlock it."""
        entry = Gtk.Entry(placeholder_text="ABCDE-FGHIJ-KLMNO-…", width_chars=44)
        entry.add_css_class("cherry-code")
        err = Gtk.Label(wrap=True)
        err.add_css_class("error")

        def go(*_):
            try:
                keys = keys_from_code(entry.get_text())
            except BadCode as e:
                err.set_label(str(e).capitalize())
                return
            if move is not None and keys.id != move.id:
                err.set_label("That code is for a different transfer. Check each letter, or go back and pick "
                              "another one.")
                return
            self.restore(entry.get_text(), None, move.label if move else "your transfer", remote=remote)
        entry.connect("activate", go)
        if move is not None:
            title = f"Unlock {move.label}"
            text = (f"Found it: {move_details(move.info)}. Your files are encrypted, so type the code Cherry Move "
                    "showed when it finished on your old computer (or the one you saved, printed or kept on your "
                    "phone). Dashes and spaces don't matter.")
        else:
            title = "Type your transfer code"
            text = "It's 34 letters and numbers. Dashes and spaces don't matter."
        self.set_page(status_page("dialog-password-symbolic", title, text,
                                  vbox(12, 0, entry, err, pill("Get my files", True, go))))
        entry.grab_focus()

    def show_locate(self, code: str, label: str, pending: bool):
        """A typed code doesn't say where the files are: ask where they were kept."""
        group = Adw.PreferencesGroup()
        for title, sub, icon, action in [
            ("USB sticks", "Plug in the stick (or stick 1)", "drive-removable-media-symbolic",
             lambda: self.wait_for_sticks(code, label, pending)),
            ("Google Drive, OneDrive or MEGA", "Sign in to the same account", "folder-remote-symbolic",
             lambda: self.show_remote_sign_in(code, label, {"kind": "mine"}, None, pending)),
            ("A NAS or another computer at home", "Connect to it the same way", "network-server-symbolic",
             lambda: self.show_remote_sign_in(code, label, {"kind": "nas"}, None, pending)),
        ]:
            row = Adw.ActionRow(title=title, subtitle=sub, activatable=True)
            row.add_prefix(Gtk.Image(icon_name=icon))
            row.add_suffix(Gtk.Image(icon_name="go-next-symbolic"))
            row.connect("activated", lambda _r, a=action: a())
            group.add(row)
        self.set_page(scrolled(vbox(18, 24, page_header("edit-find-symbolic", "Where did you send your files?",
                                                        "Where did you choose to keep them on the old computer?"),
                                    group)), back=not pending)

    def wait_for_sticks(self, code: str, label: str, pending: bool):
        self.set_page(status_page("drive-removable-media-symbolic", "Plug in your transfer USB stick",
                                  f"{label} is stored on it. If you used several sticks, start with any of them.",
                                  Gtk.Spinner(spinning=True)), back=not pending)
        page = self.stack.get_visible_child()
        self.sticks_page = page
        want = keys_from_code(code).code

        def done(drives):
            if page.get_root() is None or self.stack.get_visible_child() is not page:
                return
            for d in drives:
                if d.marker and d.marker.get("code") == want:
                    self.restore(code, d.mount, label, pending=pending)
                    return
            GLib.timeout_add_seconds(3, lambda: run_async(transfer.removable_drives, done) and False)
        run_async(transfer.removable_drives, done)

    def show_remote_sign_in(self, code: str, label: str, descriptor: dict, drive: Path | None, pending: bool):
        kind = descriptor.get("kind")
        mode = "nas" if kind == "nas" else "mine"
        title = descriptor.get("title") or ("your NAS" if mode == "nas" else "your cloud drive")
        if mode == "mine":
            text = "Sign in to the same account you used on your old computer."
        else:
            text = "Connect to it the same way as on your old computer (check the address, in case it changed)."
        form = RemoteForm(self, mode, descriptor if kind not in ("mine", None) else None,
                          on_ready=lambda f: self.restore(code, drive, label, pending, remote=f.remote))
        icon = "network-server-symbolic" if mode == "nas" else "folder-remote-symbolic"
        self.set_page(scrolled(vbox(18, 24, page_header(icon, f"Your files are in {title}", text), form.group)))

    # ------------------------------------------------------------ receive: run

    def restore_pending(self, pending: dict):
        """First login after installing: restore what was picked in the live session."""
        label = pending.get("label") or "your old computer"
        if pending.get("remote"):
            self.show_remote_sign_in(pending["code"], label, pending["remote"], None, True)
        elif pending.get("drive_label"):
            self.wait_for_sticks(pending["code"], label, True)
        else:
            self.show_locate(pending["code"], label, True)

    def restore(self, code: str, drive: Path | None, label: str, pending: bool = False,
                remote: remotes.Remote | None = None, remote_desc: dict | None = None):
        marker = transfer.read_marker(drive) if drive else None
        if remote is None and not remote_desc and not marker:
            self.show_locate(code, label, pending)
            return
        if is_live():
            self.save_for_after_install(code, label, drive, remote_desc or (remote.descriptor() if remote else None))
            return
        if remote is None and remote_desc:
            self.show_remote_sign_in(code, label, remote_desc, drive, pending)
            return
        storage = "remote" if remote else marker.get("storage", "usb")
        where = "Reading your USB stick" if storage == "usb" else f"Downloading from {remote.title if remote else ''}"
        progress = self.progress_page(f"Bringing back {label}",
                                      f"{where}, decrypting and putting your files back where they were")
        self.busy = True
        self.cancel.clear()
        self.restore_code = keys_from_code(code).code

        def work():
            return transfer.restore(code, drive, progress=progress, cancel=self.cancel, remote=remote,
                                    next_stick=lambda part, problem: self.ask_stick(part, problem, sending=False))

        def done(manifest):
            self.busy = False
            if pending:
                transfer.clear_pending()
            self.show_restored(manifest, code, remote)

        def failed(err):
            self.busy = False
            if isinstance(err, InterruptedError):
                self.go_home()
                return
            if isinstance(err, transfer.NeedRemote):
                self.show_remote_sign_in(code, label, err.descriptor, drive, pending)
                return
            self.set_page(status_page("dialog-error-symbolic", "Couldn't get your files", str(err),
                                      hbox(8, pill("Try again", True, lambda: self.restore(code, drive, label, pending,
                                                                                           remote)),
                                           pill("Back", False, self.go_home), halign=Gtk.Align.CENTER)))
        run_async(work, done, failed)

    def show_restored(self, manifest, code, remote):
        apps = manifest.get("apps") or {}
        n_apps = transfer.reinstall_count(apps)
        child = vbox(12, 0)
        if n_apps:
            old_name = manifest.get("hostname") or ""
            rename = None
            if old_name and old_name != socket.gethostname():
                rename = Gtk.CheckButton(active=True)
                rename.set_child(Gtk.Label(label=f"Call this computer “{old_name}” like the old one, so things that "
                                                 "connect to it by name still find it", wrap=True, xalign=0))
                child.append(rename)
            btn = pill(f"Reinstall your apps ({n_apps})", True,
                       lambda: self.reinstall(apps, btn, old_name if rename and rename.get_active() else None))
            child.append(btn)
        if remote is not None:
            delete = pill(f"Delete the copy in {remote.title}", False, lambda: self.delete_copy(code, delete, remote))
            child.append(delete)
        child.append(pill("Open my files", False, lambda: subprocess.Popen(["xdg-open", str(Path.home())])))
        child.append(pill("Finish", n_apps == 0, self.close))
        n, skipped = manifest["restored_files"], manifest.get("skipped_files")
        desc = (f"{n} {'file' if n == 1 else 'files'} from {manifest.get('hostname', 'your old computer')} "
                f"({manifest.get('source_os', 'Linux')}) {'is' if n == 1 else 'are'} back in your home folder.")
        if skipped:
            desc += f" {skipped} {'was' if skipped == 1 else 'were'} already here."
        self.set_page(status_page("object-select-symbolic", "Welcome home!", desc, child), back=False)

    def reinstall(self, apps, btn, hostname=None):
        btn.set_sensitive(False)
        btn.set_label("Installing apps…")

        def done(problems):
            if problems:
                btn.set_label(f"Done, but {len(problems)} didn't work (see ~/{transfer.REINSTALL_LOG})")
            else:
                btn.set_label("Apps installed")
        run_async(lambda: transfer.reinstall_apps(apps, log=lambda m: GLib.idle_add(btn.set_label, m),
                                                  hostname=hostname),
                  done, lambda e: btn.set_label(f"Some apps failed: {e}"))

    def delete_copy(self, code, btn, remote):
        btn.set_sensitive(False)
        run_async(lambda: transfer.RemoteSource(remote, keys_from_code(code)).delete(),
                  lambda _: btn.set_label("Deleted"),
                  lambda e: (btn.set_sensitive(True), self.toast(f"Couldn't delete: {e}")))

    def save_for_after_install(self, code, label, drive, remote_desc=None):
        try:
            transfer.save_pending(code, label, drive, system=True, remote=remote_desc)
        except Exception as e:  # noqa: BLE001
            self.toast(f"Couldn't remember the transfer: {e}")
            return
        marker = transfer.read_marker(drive) if drive else None
        extra = ""
        if remote_desc:
            extra = f" You'll sign in to {remote_desc.get('title', 'your storage')} again then."
        elif marker and marker.get("storage") == "usb":
            several = not marker.get("final", True) or marker.get("parts", 1) > 1
            extra = (" Keep all the USB sticks handy until your files are back." if several
                     else " Keep the USB stick handy until your files are back.")
        install = pill(f"Install {this_os()}", True, lambda: subprocess.Popen(
            ["sh", "-c", setting("INSTALLER", DEFAULT_INSTALLER)]))
        self.set_page(status_page("object-select-symbolic", "Your files will be waiting",
                                  f"After {this_os()} is installed and you log in for the first time, {label} is restored "
                                  f"automatically.{extra}",
                                  vbox(12, 0, install, pill("Close", False, self.close), halign=Gtk.Align.CENTER)),
                      back=False)


class MoveApp(Adw.Application):
    def __init__(self, mode="home"):
        identify(APP_ID, "Cherry Move", "cherry-move")
        super().__init__(application_id=APP_ID)
        self.mode = mode
        for name, handler in (("send", self.on_send), ("receive", self.on_receive), ("stick", self.on_stick)):
            action = Gio.SimpleAction.new(name, None)
            action.connect("activate", handler)
            self.add_action(action)

    def do_startup(self):
        Adw.Application.do_startup(self)
        add_close_shortcuts(self)

    def do_activate(self):
        apply_css()
        win = self.props.active_window
        if win:
            win.present()
            return
        if self.mode == "pending":
            MoveWindow(self, "pending", pending=transfer.load_pending()).present()
        elif self.mode in ("receive", "stick"):
            MoveWindow(self, "receive").present()
        elif self.mode == "send":
            MoveWindow(self, "send").present()
        else:
            MoveWindow(self).present()

    # Sent by a second `cherry-move --send` / `--receive` / `--stick` while this one is already open.
    def on_send(self, *_):
        win = self.props.active_window
        if win is None:
            return self.do_activate()
        if not win.busy:
            win.show_choose()
        win.present()

    def on_receive(self, *_):
        win = self.props.active_window
        if win is None:
            return self.do_activate()
        self.withdraw_notification("stick")
        if not win.busy and not win.on_receive_page():
            win.show_receive(None)
        win.present()

    def on_stick(self, *_):
        win = self.props.active_window
        if win is None:
            return self.do_activate()
        win.stick_plugged()
        if not win.is_active():
            # Wayland won't raise a window for a background process, but clicking a notification may.
            note = Gio.Notification.new("Transfer USB stick plugged in")
            note.set_body("Open Cherry Move to get your files from it.")
            note.set_default_action("app.receive")
            self.send_notification("stick", note)
        win.present()


def watch() -> None:
    """Background: when a Cherry transfer USB is plugged in, offer to restore it (once each time it's plugged in)."""
    seen = set()
    here = socket.gethostname()
    while True:
        sticks = {stick_key(d): d for d in transfer.removable_drives() if d.marker}
        seen &= sticks.keys()  # sticks are often swapped in the same port, so the device name comes back
        for key, d in sticks.items():
            if key in seen:
                continue
            seen.add(key)
            if is_live() and Path(transfer.PENDING_SYSTEM).exists():
                continue
            if not is_live() and (d.marker.get("hostname") == here or restoring_or_restored(d.marker.get("code"))):
                continue  # a stick this computer just sent from, or files that are already back
            subprocess.Popen(["cherry-move", "--stick"])
        time.sleep(4)


def restoring_or_restored(code: str | None) -> bool:
    """Already restored here, or about to be by the restore that runs at login after installing."""
    if transfer.was_restored(code):
        return True
    pending = transfer.load_pending()
    try:
        return bool(pending and code and keys_from_code(pending["code"]).id == keys_from_code(code).id)
    except (ValueError, KeyError):
        return False


def stick_key(d: transfer.Drive) -> tuple:
    return d.device, d.marker.get("code"), d.marker.get("part")


def main(argv):
    import sys
    if "--watch" in argv:
        watch()
        return 0
    if "--restore-pending" in argv:
        if not transfer.load_pending():
            return 0
        rc = MoveApp("pending").run([sys.argv[0]])
        subprocess.Popen(["cherry-center", "--welcome", "--after-restore"], start_new_session=True)
        return rc
    mode = next((m for m in ("stick", "receive", "send") if f"--{m}" in argv), "home")
    app = MoveApp(mode)
    if mode != "home":
        app.register(None)
        if app.get_is_remote():
            app.activate_action(mode, None)
            app.get_dbus_connection().flush_sync(None)
            return 0
    return app.run([sys.argv[0]])
