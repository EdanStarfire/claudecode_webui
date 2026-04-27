"""
Regression tests for issue #1175 — ProxyAddon.load() must be synchronous.

mitmproxy calls load() from a sync context; async def load() raises
AddonManagerError at startup. These tests guard against that regression.

mitmproxy is not available in the test environment, so we patch the import
before importing addon.py.
"""

import sys
import types
from unittest.mock import MagicMock

import pytest

def _install_mitmproxy_stubs():
    """Install minimal mitmproxy stubs (non-spec MagicMock-safe HTTPFlow)."""
    mitmproxy = types.ModuleType("mitmproxy")
    ctx_mod = types.ModuleType("mitmproxy.ctx")
    ctx_obj = MagicMock()
    ctx_mod.ctx = ctx_obj

    http_mod = types.ModuleType("mitmproxy.http")

    class _HTTPFlow:
        def __init__(self, host=""):
            self.request = MagicMock()
            self.request.pretty_host = host
            self.request.path = "/test"
            self.request.method = "GET"
            self.request.scheme = "https"
            self.request.port = 443
            self.request.headers = {}
            self.response = None
            self.client_conn = MagicMock()
            self.client_conn.peername = ("127.0.0.1", 12345)
            self.metadata = {}

    http_mod.HTTPFlow = _HTTPFlow
    http_mod.Response = MagicMock()

    tcp_mod = types.ModuleType("mitmproxy.tcp")
    tcp_mod.TCPFlow = MagicMock

    mitmproxy.ctx = ctx_obj
    mitmproxy.http = http_mod
    mitmproxy.tcp = tcp_mod

    for mod_name, mod in [
        ("mitmproxy", mitmproxy),
        ("mitmproxy.ctx", ctx_mod),
        ("mitmproxy.http", http_mod),
        ("mitmproxy.tcp", tcp_mod),
    ]:
        sys.modules.setdefault(mod_name, mod)

    return ctx_obj


@pytest.fixture(scope="module")
def proxy_addon_class():
    _install_mitmproxy_stubs()
    from src.docker.proxy.addon import ProxyAddon  # noqa: PLC0415
    return ProxyAddon


def test_load_is_not_async(proxy_addon_class):
    """load() must be sync — mitmproxy calls it from a sync context (issue #1175)."""
    import inspect  # noqa: PLC0415
    assert not inspect.iscoroutinefunction(proxy_addon_class.load)


def test_running_is_async(proxy_addon_class):
    """running() must be async to safely call _fetch_resolve."""
    import inspect  # noqa: PLC0415
    assert inspect.iscoroutinefunction(proxy_addon_class.running)
