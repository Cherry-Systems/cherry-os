import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "desktop/lib"))


def pytest_configure(config):
    config.addinivalue_line("markers", "real_keyring: runs against a private keyring it starts itself")


@pytest.fixture(autouse=True)
def no_real_system_extras(monkeypatch):
    """Sends in tests mustn't pack the test computer's own Docker servers and .deb apps."""
    from cherryos import system_extras
    monkeypatch.setattr(system_extras, "collect", lambda out_dir: {"debs": [], "system": {}, "docker": {}})


@pytest.fixture(autouse=True)
def no_real_keyring(monkeypatch, request):
    """Nor read or add to the test computer's own keyring (tests marked real_keyring start a private one)."""
    if request.node.get_closest_marker("real_keyring"):
        return
    from cherryos import secret_store
    monkeypatch.setattr(secret_store, "read_all", lambda: [])
    monkeypatch.setattr(secret_store, "add_all", lambda items: [])
