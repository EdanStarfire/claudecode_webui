"""Issue #2357: permission tests must not depend on test order.

Interleaved patch() contexts (concurrent tasks each entering their own patch) restore
out of LIFO order and leave a MagicMock on the module, which broke the #1302 tests
whenever #858 ran first.
"""

from __future__ import annotations

import asyncio
import itertools
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

TESTS_DIR = Path(__file__).parent
REPO_ROOT = TESTS_DIR.parent.parent
FILES = {
    "858": "test_issue_858_permission_race.py",
    "1694": "test_issue_1694_permission_ordering.py",
    "1302": "test_issue_1302_enriched_permission_context.py",
}


@pytest.mark.asyncio
async def test_interleaved_patches_leak_and_single_patch_does_not():
    """Pins the mechanism: per-task patch leaks, one patch around the gather does not."""
    import backend.permission_service as ps
    from backend.models.messages import PermissionRequestMessage

    async def per_task():
        with patch("backend.permission_service.PermissionRequestMessage"):
            await asyncio.sleep(0.01)

    try:
        await asyncio.gather(per_task(), per_task(), per_task())
        assert ps.PermissionRequestMessage is not PermissionRequestMessage
    finally:
        ps.PermissionRequestMessage = PermissionRequestMessage

    async def shared():
        await asyncio.sleep(0.01)

    with patch("backend.permission_service.PermissionRequestMessage", MagicMock()):
        await asyncio.gather(shared(), shared(), shared())
    assert ps.PermissionRequestMessage is PermissionRequestMessage


def test_858_concurrent_test_restores_module_attributes():
    import backend.permission_service as ps
    from backend.models.messages import PermissionInfo, PermissionRequestMessage
    from backend.tests.test_issue_858_permission_race import (
        test_issue_858_multiple_concurrent_permissions as concurrent_test,
    )

    asyncio.run(concurrent_test())
    assert ps.PermissionRequestMessage is PermissionRequestMessage
    assert ps.PermissionInfo is PermissionInfo


@pytest.mark.slow
@pytest.mark.parametrize("order", list(itertools.permutations(FILES)), ids="-".join)
def test_permission_files_pass_in_every_order(order):
    paths = [str(TESTS_DIR / FILES[key]) for key in order]
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-p", "no:randomly", "-q", *paths],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert result.returncode == 0, result.stdout[-3000:] + result.stderr[-1000:]
