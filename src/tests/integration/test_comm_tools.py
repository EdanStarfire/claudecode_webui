"""
Integration tests for MCP communication tools.

Tests: send_comm, send_comm_to_channel
"""

import json
from pathlib import Path

import pytest


# Placeholder - verify fixture works
@pytest.mark.asyncio
async def test_fixture_creates_legion(legion_test_env):
    """Verify legion_test_env fixture creates test environment."""
    env = legion_test_env

    # Check all expected keys exist
    assert "session_coordinator" in env
    assert "legion_system" in env
    assert "legion_id" in env
    assert "create_minion" in env
    assert "data_dir" in env

    # Verify legion project was created
    assert env["project"].is_multi_agent is True
    assert env["project"].name == "Test Legion"

    # Verify data directory exists
    assert env["data_dir"].exists()
    assert env["data_dir"].name == "test_data_mcp"


@pytest.mark.asyncio
async def test_create_minion_helper(legion_test_env):
    """Verify create_minion helper function works."""
    env = legion_test_env

    # Create test minion
    minion = await env["create_minion"]("test_minion", role="Test Role")

    # Verify minion was created
    assert minion.name == "test_minion"
    assert minion.role == "Test Role"
    assert minion.is_minion is True
    assert minion.project_id == env["legion_id"]

    # Verify minion session directory exists
    session_dir = env["data_dir"] / "sessions" / minion.session_id
    assert session_dir.exists()
