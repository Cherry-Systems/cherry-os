"""Saved passwords in the desktop's keyring (the freedesktop Secret Service: GNOME Keyring, KWallet's bridge,
KeePassXC…), carried from one computer to the other.

The keyring file itself only opens with the old computer's login password, and browsers built on Chromium
(Chrome, Chromium, Cursor, VS Code…) keep the key to their saved passwords and cookies in it. So the send reads
each unlocked item out of the default keyring while you're logged in, they travel inside the encrypted
transfer, and the restore adds them to the new computer's keyring."""
from __future__ import annotations

import base64

BUS_NAME = "org.freedesktop.secrets"
SERVICE = "/org/freedesktop/secrets"
SERVICE_IF = "org.freedesktop.Secret.Service"
COLLECTION_IF = "org.freedesktop.Secret.Collection"
ITEM_IF = "org.freedesktop.Secret.Item"
PROPERTIES_IF = "org.freedesktop.DBus.Properties"
TIMEOUT_MS = 10_000


class _Service:
    def __init__(self):
        from gi.repository import Gio, GLib
        self.GLib = GLib
        address = Gio.dbus_address_get_for_bus_sync(Gio.BusType.SESSION, None)
        self.bus = Gio.DBusConnection.new_for_address_sync(
            address, Gio.DBusConnectionFlags.AUTHENTICATION_CLIENT | Gio.DBusConnectionFlags.MESSAGE_BUS_CONNECTION,
            None, None)
        self.session = self.call(SERVICE, SERVICE_IF, "OpenSession",
                                 GLib.Variant("(sv)", ("plain", GLib.Variant("s", ""))), "(vo)")[1]

    def call(self, path, iface, method, args=None, reply=None):
        from gi.repository import Gio
        out = self.bus.call_sync(BUS_NAME, path, iface, method, args,
                                 self.GLib.VariantType(reply) if reply else None, Gio.DBusCallFlags.NONE,
                                 TIMEOUT_MS, None)
        return out.unpack() if out is not None else None

    def get(self, path, iface, prop):
        return self.call(path, PROPERTIES_IF, "Get", self.GLib.Variant("(ss)", (iface, prop)), "(v)")[0]

    def default_collection(self) -> str | None:
        path = self.call(SERVICE, SERVICE_IF, "ReadAlias", self.GLib.Variant("(s)", ("default",)), "(o)")[0]
        if path == "/" or self.get(path, COLLECTION_IF, "Locked"):
            return None
        return path

    def close(self):
        try:
            self.call(self.session, "org.freedesktop.Secret.Session", "Close")
        except Exception:
            pass
        try:
            self.bus.close_sync(None)
        except Exception:
            pass


def read_all() -> list[dict]:
    """Every unlocked item in the default keyring: [{"label", "attributes", "secret" (base64), "type"}].
    Empty when there's no keyring or it's locked."""
    try:
        svc = _Service()
    except Exception:
        return []
    try:
        collection = svc.default_collection()
        if collection is None:
            return []
        paths = svc.get(collection, COLLECTION_IF, "Items")
        if not paths:
            return []
        secrets = svc.call(SERVICE, SERVICE_IF, "GetSecrets", svc.GLib.Variant("(aoo)", (paths, svc.session)),
                           "(a{o(oayays)})")[0]
        items = []
        for path, (_session, _params, value, kind) in sorted(secrets.items()):
            items.append({"label": svc.get(path, ITEM_IF, "Label"),
                          "attributes": dict(svc.get(path, ITEM_IF, "Attributes")),
                          "secret": base64.b64encode(bytes(value)).decode(), "type": kind or "text/plain"})
        return items
    except Exception:
        return []
    finally:
        svc.close()


def add_all(items: list[dict]) -> list[dict]:
    """Adds `items` (from read_all) to this computer's default keyring, replacing any with the same attributes.
    Returns the ones added; none when there's no unlocked keyring to add them to."""
    if not items:
        return []
    try:
        svc = _Service()
    except Exception:
        return []
    GLib = svc.GLib
    added = []
    try:
        collection = svc.default_collection()
        if collection is None:
            return []
        for item in items:
            try:
                props = {f"{ITEM_IF}.Label": GLib.Variant("s", item.get("label", "")),
                         f"{ITEM_IF}.Attributes": GLib.Variant("a{ss}", dict(item.get("attributes") or {}))}
                secret = (svc.session, b"", base64.b64decode(item["secret"]), item.get("type") or "text/plain")
                svc.call(collection, COLLECTION_IF, "CreateItem",
                         GLib.Variant("(a{sv}(oayays)b)", (props, secret, True)), "(oo)")
                added.append(item)
            except Exception:
                continue
        return added
    except Exception:
        return added
    finally:
        svc.close()
