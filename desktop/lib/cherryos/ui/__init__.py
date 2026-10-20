"""Shared GTK 4 / libadwaita helpers for Cherry's apps."""
from __future__ import annotations

import io
import threading
import traceback

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import Adw, Gdk, Gio, GLib, Gtk  # noqa: E402

CSS = b"""
@define-color accent_bg_color #c81040;
@define-color accent_color #e0306a;
@define-color accent_fg_color #ffffff;
.cherry-code { font-family: monospace; font-size: 22px; font-weight: bold; letter-spacing: 2px; }
.cherry-big-button { padding: 18px 24px; font-size: 16px; border-radius: 14px; }
.cherry-card { padding: 18px; border-radius: 14px; }
.cherry-preview { border-radius: 10px; }
.cherry-selected { outline: 3px solid #c81040; outline-offset: 2px; border-radius: 12px; }
"""
# CSS variables (GTK 4.16+); older GTK, e.g. Ubuntu 22.04 and Debian 12, rejects ":root".
CSS_VARIABLES = b":root { --accent-bg-color: #c81040; --accent-color: #e0306a; --accent-fg-color: #ffffff; }\n"


def apply_css() -> None:
    provider = Gtk.CssProvider()
    css = CSS + (CSS_VARIABLES if Gtk.check_version(4, 16, 0) is None else b"")
    try:
        provider.load_from_data(css)
    except TypeError:
        provider.load_from_data(css, -1)
    Gtk.StyleContext.add_provider_for_display(Gdk.Display.get_default(), provider,
                                              Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)


def identify(app_id: str, name: str, icon: str) -> None:
    """Call before the app starts. The X11 window class must equal the .desktop file's StartupWMClass,
    or docks and app switchers show a nameless app with a generic icon instead of the menu entry."""
    GLib.set_prgname(app_id)
    GLib.set_application_name(name)
    Gtk.Window.set_default_icon_name(icon)


def add_close_shortcuts(app: Gtk.Application) -> None:
    """Ctrl+Q and Ctrl+W close the window, going through its close-request handler like the X button."""
    action = Gio.SimpleAction.new("close", None)
    action.connect("activate", lambda *_: app.props.active_window and app.props.active_window.close())
    app.add_action(action)
    app.set_accels_for_action("app.close", ["<Control>q", "<Control>w"])


def run_async(work, done=None, error=None):
    """Runs `work()` on a thread; `done(result)` / `error(exc)` run back on the GTK thread. If `done` itself
    fails, `error` hears about it, so the window never stays stuck on a progress page."""
    def finish(result):
        try:
            done(result)
        except Exception as exc:  # noqa: BLE001 - reported to the user
            traceback.print_exc()
            if error:
                error(exc)
        return False

    def target():
        try:
            result = work()
        except Exception as exc:  # noqa: BLE001 - reported to the user
            traceback.print_exc()
            if error:
                # exc is unbound when the except block ends, so the callback must take its own reference.
                GLib.idle_add(lambda e=exc: error(e) and False)
            return
        if done:
            GLib.idle_add(finish, result)
    t = threading.Thread(target=target, daemon=True)
    t.start()
    return t


def ui(fn, *args):
    """Calls fn(*args) on the GTK thread (from a worker thread)."""
    GLib.idle_add(lambda: fn(*args) and False)


def qr_texture(text: str, box: int = 10):
    import qrcode
    img = qrcode.make(text, box_size=box, border=2)
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return Gdk.Texture.new_from_bytes(GLib.Bytes.new(buf.getvalue()))


def message(parent, heading: str, body: str = "", buttons=(("ok", "OK"),), callback=None, destructive=None):
    """A simple question/notice dialog that works with old and new libadwaita."""
    if not hasattr(Adw, "MessageDialog"):  # libadwaita 1.1 (Ubuntu 22.04, Linux Mint 21)
        dlg = Gtk.MessageDialog(transient_for=parent, modal=True, text=heading, secondary_text=body or None)
        for i, (_rid, label) in enumerate(buttons):
            dlg.add_button(label, i)

        def respond(d, r):
            d.destroy()
            if callback and r >= 0:
                callback(buttons[r][0])
        dlg.connect("response", respond)
        dlg.present()
        return dlg
    if hasattr(Adw, "AlertDialog"):
        dlg = Adw.AlertDialog(heading=heading, body=body)
    else:
        dlg = Adw.MessageDialog(transient_for=parent, heading=heading, body=body)
    for rid, label in buttons:
        dlg.add_response(rid, label)
    if destructive:
        dlg.set_response_appearance(destructive, Adw.ResponseAppearance.DESTRUCTIVE)
    elif len(buttons) > 1:
        dlg.set_response_appearance(buttons[-1][0], Adw.ResponseAppearance.SUGGESTED)
    if callback:
        dlg.connect("response", lambda _d, r: callback(r))
    if hasattr(Adw, "AlertDialog"):
        dlg.present(parent)
    else:
        dlg.present()
    return dlg


def open_uri(parent, uri: str) -> None:
    if hasattr(Gtk, "UriLauncher"):
        Gtk.UriLauncher.new(uri).launch(parent, None, None)
    else:  # GTK before 4.10
        Gtk.show_uri(parent, uri, Gdk.CURRENT_TIME)


def status_page(icon: str, title: str, description: str = "", child=None) -> Adw.StatusPage:
    page = Adw.StatusPage(icon_name=icon, title=title, description=description)
    if child is not None:
        page.set_child(child)
    return page


def page_header(icon: str, title: str, description: str = "") -> Gtk.Box:
    """Icon, title and description above other content (a StatusPage collapses inside a scrolled page)."""
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8, margin_top=24, margin_bottom=6)
    box.append(Gtk.Image(icon_name=icon, pixel_size=72))
    heading = Gtk.Label(label=title, wrap=True, justify=Gtk.Justification.CENTER)
    heading.add_css_class("title-1")
    box.append(heading)
    if description:
        text = Gtk.Label(label=description, wrap=True, justify=Gtk.Justification.CENTER, max_width_chars=70)
        text.set_halign(Gtk.Align.CENTER)
        box.append(text)
    return box


def pill(label: str, suggested: bool = False, callback=None) -> Gtk.Button:
    b = Gtk.Button(label=label)
    b.add_css_class("pill")
    if suggested:
        b.add_css_class("suggested-action")
    if callback:
        b.connect("clicked", lambda *_: callback())
    return b


def vbox(spacing: int = 12, margin: int = 0, *children, halign=None) -> Gtk.Box:
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=spacing)
    if margin:
        for side in ("top", "bottom", "start", "end"):
            getattr(box, f"set_margin_{side}")(margin)
    if halign is not None:
        box.set_halign(halign)
    for c in children:
        box.append(c)
    return box


def hbox(spacing: int = 12, *children, halign=None) -> Gtk.Box:
    box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=spacing)
    if halign is not None:
        box.set_halign(halign)
    for c in children:
        box.append(c)
    return box


def scrolled(child, min_height: int = 0) -> Gtk.ScrolledWindow:
    sw = Gtk.ScrolledWindow(vexpand=True, hscrollbar_policy=Gtk.PolicyType.NEVER)
    if min_height:
        sw.set_min_content_height(min_height)
    clamp = Adw.Clamp(maximum_size=760, tightening_threshold=600)
    clamp.set_child(child)
    sw.set_child(clamp)
    return sw
