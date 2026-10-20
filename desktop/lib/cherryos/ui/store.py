"""Cherry Store: browse, install and report apps from the Cherry Store (Debian packages kept on GitHub)."""
from __future__ import annotations

import datetime as dt
import subprocess
import sys

from gi.repository import Adw, GLib, Gtk

from .. import pkg
from . import (add_close_shortcuts, apply_css, hbox, identify, message, open_uri, pill, run_async, scrolled,
               status_page, vbox)

APP_ID = "org.cherryos.Store"


def cherry_cmd(*args: str) -> subprocess.CompletedProcess:
    """Runs the cherry CLI as root (pkexec asks for the password once)."""
    return subprocess.run(["pkexec", "cherry", *args], capture_output=True, text=True)


def publisher(p: dict) -> str:
    how, _, who = p.get("publisher", "").partition(":")
    return {"github": f"{who} (GitHub)", "cherry": f"{who} (Cherry account)"}.get(how, p.get("publisher", "?"))


class StoreWindow(Adw.ApplicationWindow):
    def __init__(self, app, show_updates=False):
        super().__init__(application=app, title="Cherry Store", default_width=960, default_height=720)
        self.toasts = Adw.ToastOverlay()
        self.stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.SLIDE_LEFT_RIGHT, vexpand=True)
        header = Adw.HeaderBar()
        self.back = Gtk.Button(icon_name="go-previous-symbolic", visible=False)
        self.back.connect("clicked", lambda *_: self.show_list())
        header.pack_start(self.back)
        self.switcher = Gtk.StackSwitcher()
        self.tabs = Gtk.Stack()
        self.switcher.set_stack(self.tabs)
        header.set_title_widget(self.switcher)
        self.search = Gtk.SearchEntry(placeholder_text="Search the Cherry Store", hexpand=True)
        self.search.connect("search-changed", lambda *_: self.load())
        self.results = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        self.results.add_css_class("boxed-list")
        browse = vbox(14, 18, self.search, self.results)
        self.installed_box = vbox(14, 18)
        self.tabs.add_titled(scrolled(browse), "browse", "Explore")
        self.tabs.add_titled(scrolled(self.installed_box), "installed", "Installed")
        self.tabs.connect("notify::visible-child-name", lambda *_: self.tabs.get_visible_child_name() == "installed"
                          and self.load_installed())
        self.stack.add_named(self.tabs, "list")
        self.toasts.set_child(vbox(0, 0, header, self.stack))
        self.set_content(self.toasts)
        self.load()
        if show_updates:
            self.tabs.set_visible_child_name("installed")

    def toast(self, text):
        self.toasts.add_toast(Adw.Toast(title=text, timeout=5))

    def show_list(self):
        self.stack.set_visible_child_name("list")
        self.back.set_visible(False)
        self.switcher.set_visible(True)

    # ------------------------------------------------------------ browse

    def load(self):
        words = self.search.get_text().split()
        run_async(lambda: pkg.search(words), self.show_results, lambda e: self.show_error(e))

    def show_error(self, err):
        self._clear(self.results)
        row = Adw.ActionRow(title="Can't reach the Cherry Store", subtitle="Check your internet connection.")
        self.results.append(row)

    def _clear(self, box):
        child = box.get_first_child()
        while child:
            nxt = child.get_next_sibling()
            box.remove(child)
            child = nxt

    def show_results(self, packages):
        self._clear(self.results)
        if not packages:
            self.results.append(Adw.ActionRow(title="No apps found",
                                              subtitle="Be the first: run 'cherry init', 'cherry build' and 'cherry publish' in your project"))
        installed = pkg.installed()
        for p in packages:
            sub = f"{p.get('summary', '')}\nby {publisher(p)}"
            row = Adw.ActionRow(title=GLib.markup_escape_text(p["name"]), subtitle=GLib.markup_escape_text(sub),
                                activatable=True, subtitle_lines=2)
            icon = Gtk.Image(icon_name="wine" if p.get("kind") == "windows" else "application-x-executable", pixel_size=40)
            row.add_prefix(icon)
            if pkg.warnings_for(p):
                warn = Gtk.Image(icon_name="dialog-warning-symbolic", tooltip_text="Has warnings — see details")
                row.add_suffix(warn)
            if p["name"] in installed:
                lbl = Gtk.Label(label="Installed")
                lbl.add_css_class("dim-label")
                row.add_suffix(lbl)
            row.add_suffix(Gtk.Image(icon_name="go-next-symbolic"))
            row.connect("activated", lambda _r, n=p["name"]: self.show_details(n))
            self.results.append(row)

    # ------------------------------------------------------------ details

    def show_details(self, name):
        spinner = Gtk.Spinner(spinning=True, width_request=48, height_request=48)
        page = vbox(0, 0, spinner)
        self._set_detail(page)

        def failed(err):
            page = status_page("dialog-error-symbolic", "Couldn't load this app", str(err))
            if name in pkg.installed() and name not in pkg.SYSTEM_PACKAGES:
                page.set_child(pill("Remove", False, lambda: self.remove(name)))
            self._set_detail(page)
        run_async(lambda: pkg.listing(name), lambda p: self._set_detail(self.detail_page(p)), failed)

    def _set_detail(self, widget):
        old = self.stack.get_child_by_name("detail")
        if old:
            self.stack.remove(old)
        self.stack.add_named(widget, "detail")
        self.stack.set_visible_child_name("detail")
        self.back.set_visible(True)
        self.switcher.set_visible(False)

    def detail_page(self, p):
        name, v = p["name"], p["versions"][p["latest"]]
        is_installed = name in pkg.installed()
        title = Gtk.Label(label=pkg.display_name(name), xalign=0)
        title.add_css_class("title-1")
        summary = Gtk.Label(label=p.get("summary", ""), xalign=0, wrap=True)
        summary.add_css_class("title-4")
        warns = pkg.warnings_for(p)
        action = pill("Remove" if is_installed else "Install", not is_installed,
                      lambda: (self.remove(name) if is_installed else self.install(name, warns)))
        if is_installed:
            action.add_css_class("destructive-action")
        report = pill("Report", False, lambda: self.report(name))
        info = Adw.PreferencesGroup()
        updated = dt.datetime.fromtimestamp(v["created"]).strftime("%-d %b %Y")
        scan = v.get("scan", {}).get("status", "pending")
        for t, s in [("Publisher", publisher(p)), ("Version", f"{p['latest']} · {updated}"),
                     ("Size", pkg.human_size(v['size'])), ("Kind", {"native": "Linux app", "windows": "Windows app (runs with Wine)",
                                                                       "appimage": "AppImage"}.get(p.get("kind"), "Linux app")),
                     ("Virus scan", {"clean": "No threats found", "pending": "Not scanned yet",
                                     "flagged": "Warning", "error": "Couldn't scan"}.get(scan, scan)),
                     ("License", p.get("license") or "Not stated"), ("Website", p.get("homepage") or "—")]:
            info.add(Adw.ActionRow(title=t, subtitle=GLib.markup_escape_text(s)))
        buttons = hbox(8, report) if name in pkg.SYSTEM_PACKAGES else hbox(8, action, report)
        box = vbox(14, 24, title, summary, buttons)
        for w in warns:
            lbl = Gtk.Label(label="⚠  " + w, xalign=0, wrap=True)
            lbl.add_css_class("warning")
            box.append(lbl)
        if p.get("description"):
            box.append(Gtk.Label(label=p["description"].strip(), xalign=0, wrap=True, selectable=True))
        box.append(info)
        return scrolled(box)

    def install(self, name, warns):
        def go():
            self.toast(f"Installing {name}…")

            def done(res):
                if res.returncode == 0:
                    self.toast(f"{name} is installed — find it in your app menu")
                    self.show_details(name)
                elif res.returncode not in (126, 127):
                    message(self, f"Couldn't install {name}", (res.stderr or res.stdout)[-800:])
            run_async(lambda: cherry_cmd("install", "-y", name), done)
        if warns:
            message(self, f"Install {name}?", "\n\n".join(warns), (("cancel", "Cancel"), ("install", "Install anyway")),
                    callback=lambda r: r == "install" and go())
        else:
            go()

    def remove(self, name):
        def done(res):
            if res.returncode == 0:
                self.toast(f"Removed {name}")
                self.show_details(name)
            elif res.returncode not in (126, 127):
                message(self, f"Couldn't remove {name}", (res.stderr or res.stdout)[-800:])
        run_async(lambda: cherry_cmd("remove", name), done)

    def report(self, name):
        reasons = [("malware", "Malware or a virus"), ("illegal", "Illegal content"),
                   ("impersonation", "Pretends to be someone else's app"), ("broken", "Doesn't work"), ("other", "Something else")]
        drop = Gtk.DropDown.new_from_strings([r[1] for r in reasons])
        details = Gtk.Entry(placeholder_text="What did you notice? (optional)")
        dlg = Adw.Window(transient_for=self, modal=True, title=f"Report {name}", default_width=460)
        send = pill("Send report", True)
        box = vbox(12, 18, Gtk.Label(label="Reports of malware and illegal content are checked and taken down if "
                                           "confirmed. Apps are never removed for being simple or AI-made.", wrap=True),
                   drop, details, send)
        dlg.set_content(vbox(0, 0, Adw.HeaderBar(), box))

        def submit(*_):
            reason, text = reasons[drop.get_selected()][0], details.get_text()

            def failed(err):
                dlg.close()
                open_uri(self, pkg.report_url(name))
                self.toast("Couldn't send it here, so the report page opened in your browser")
            run_async(lambda: pkg.report(name, reason, text),
                      lambda r: (dlg.close(), self.toast("Thanks — your report was sent")), failed)
        send.connect("clicked", submit)
        dlg.present()

    # ------------------------------------------------------------ installed + updates

    def load_installed(self):
        self._clear(self.installed_box)
        rows = pkg.installed()
        group = Adw.PreferencesGroup(title="Installed apps")
        if not rows:
            group.set_description("Nothing yet.")
        for name, r in rows.items():
            row = Adw.ActionRow(title=pkg.display_name(name), subtitle=GLib.markup_escape_text(
                f"{r['version']} · by {publisher(r)}"), activatable=True)
            row.add_suffix(Gtk.Image(icon_name="go-next-symbolic"))
            row.connect("activated", lambda _r, n=name: self.show_details(n))
            group.add(row)
        self.updates_group = Adw.PreferencesGroup(title="Updates", description="Checking…")
        self.installed_box.append(self.updates_group)
        self.installed_box.append(group)
        run_async(lambda: pkg.updates(), self.show_updates, lambda e: self.updates_group.set_description(
            "Can't check for updates right now."))

    def show_updates(self, ups):
        if not ups:
            self.updates_group.set_description("Everything is up to date.")
            return
        self.updates_group.set_description(f"{len(ups)} update(s) available")
        for name, old, new in ups:
            self.updates_group.add(Adw.ActionRow(title=pkg.display_name(name), subtitle=f"{old} → {new}"))
        btn = pill("Update all", True)

        def go(*_):
            btn.set_sensitive(False)
            run_async(lambda: cherry_cmd("install", "-y", *[n for n, _, _ in ups]),
                      lambda res: (self.toast("Apps updated" if res.returncode == 0 else "Some updates failed"),
                                   self.load_installed()))
        btn.connect("clicked", go)
        self.updates_group.set_header_suffix(btn) if hasattr(self.updates_group, "set_header_suffix") else self.updates_group.add(btn)


class StoreApp(Adw.Application):
    def __init__(self, updates=False):
        identify(APP_ID, "Cherry Store", "cherry-store")
        super().__init__(application_id=APP_ID)
        self.updates = updates

    def do_startup(self):
        Adw.Application.do_startup(self)
        add_close_shortcuts(self)

    def do_activate(self):
        apply_css()
        (self.props.active_window or StoreWindow(self, self.updates)).present()


def main(argv):
    return StoreApp("--updates" in argv).run([sys.argv[0]])
