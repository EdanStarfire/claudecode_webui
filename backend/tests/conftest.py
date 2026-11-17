"""Backend test-suite guards."""

from __future__ import annotations

import sys

import pytest


@pytest.fixture(autouse=True)
def _permission_service_names_not_leaked():
    """Fail the test that leaves a mock in backend.permission_service (issue #2357).

    A leaked patch of PermissionRequestMessage / PermissionInfo otherwise surfaces
    as a failure in an unrelated, later test that depends on test order.
    """
    yield
    module = sys.modules.get("backend.permission_service")
    messages = sys.modules.get("backend.models.messages")
    if module is None or messages is None:
        return
    for name in ("PermissionRequestMessage", "PermissionInfo"):
        real = getattr(messages, name)
        current = getattr(module, name)
        assert current is real, (
            f"backend.permission_service.{name} left patched after this test "
            f"(got {current!r}); likely interleaved patch() calls in concurrent tasks"
        )
