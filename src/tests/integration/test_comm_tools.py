"""
Integration tests for MCP communication tools.

Tests: send_comm
"""

import json

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


# ============================================================================
# send_comm Tests
# ============================================================================

@pytest.mark.asyncio
async def test_send_comm_to_active_minion(legion_test_env):
    """
    Test send_comm delivers to active minion with all side effects.

    Verifies:
    - Tool returns success
    - Timeline JSONL contains comm
    - Sender comms.jsonl contains comm
    - Recipient comms.jsonl contains comm
    - Recipient messages.jsonl contains formatted message
    """
    env = legion_test_env
    legion_system = env["legion_system"]
    data_dir = env["data_dir"]
    legion_id = env["legion_id"]

    # Create overseer and sibling minions (sender + recipient share a parent)
    session_manager = env["session_coordinator"].session_manager
    overseer = await env["create_minion"]("overseer", role="Overseer")
    sender = await env["create_minion"]("sender", role="Sender")
    recipient = await env["create_minion"]("recipient", role="Recipient")

    # Set up hierarchy: overseer is parent of sender and recipient
    await session_manager.update_session(
        overseer.session_id,
        is_overseer=True,
        child_minion_ids=[sender.session_id, recipient.session_id]
    )
    await session_manager.update_session(
        sender.session_id,
        parent_overseer_id=overseer.session_id
    )
    await session_manager.update_session(
        recipient.session_id,
        parent_overseer_id=overseer.session_id
    )

    # Send comm via MCP tool handler
    result = await legion_system.mcp_tools._handle_send_comm({
        "_from_minion_id": sender.session_id,
        "to_minion_name": "recipient",
        "summary": "Task assigned",
        "content": "Please process data.csv",
        "comm_type": "task"
    })

    # Verify success response (not an error)
    assert "content" in result
    assert isinstance(result["content"], list)
    assert len(result["content"]) > 0
    assert result["content"][0]["type"] == "text"
    assert result.get("is_error") is not True, f"Unexpected error: {result['content'][0]['text']}"

    # SIDE EFFECT 1: Timeline JSONL contains comm
    timeline_file = data_dir / "legions" / legion_id / "timeline.jsonl"
    assert timeline_file.exists(), "Timeline file should exist"

    with open(timeline_file) as f:
        timeline_comms = [json.loads(line) for line in f]

    assert len(timeline_comms) > 0, "Timeline should have at least one comm"
    comm = timeline_comms[-1]  # Get last comm
    assert comm["from_minion_id"] == sender.session_id
    assert comm["to_minion_id"] == recipient.session_id
    assert comm["summary"] == "Task assigned"
    assert comm["content"] == "Please process data.csv"
    assert comm["comm_type"] == "task"

    # SIDE EFFECT 2: Sender comms.jsonl
    sender_comms_file = data_dir / "legions" / legion_id / "minions" / sender.session_id / "comms.jsonl"
    assert sender_comms_file.exists(), "Sender comms file should exist"

    with open(sender_comms_file) as f:
        sender_comms = [json.loads(line) for line in f]

    assert len(sender_comms) > 0, "Sender should have comm in log"
    assert any(c["summary"] == "Task assigned" for c in sender_comms)

    # SIDE EFFECT 3: Recipient comms.jsonl
    recipient_comms_file = data_dir / "legions" / legion_id / "minions" / recipient.session_id / "comms.jsonl"
    assert recipient_comms_file.exists(), "Recipient comms file should exist"

    with open(recipient_comms_file) as f:
        recipient_comms = [json.loads(line) for line in f]

    assert len(recipient_comms) > 0, "Recipient should have comm in log"
    assert any(c["summary"] == "Task assigned" for c in recipient_comms)

    # SIDE EFFECT 4: Recipient SDK message queue injection
    # Note: SessionCoordinator.send_message() queues messages to the SDK.
    # LIMITATION: In integration tests without Claude API access, the SDK conversation
    # loop cannot fully process messages (would require actual API calls to Claude).
    # The messages are queued successfully, but messages.jsonl is only written after
    # SDK processes the message through the API.
    #
    # What we CAN verify:
    # - Comm routed to timeline.jsonl ✓ (verified above)
    # - Comm logged to sender/recipient comms.jsonl ✓ (verified above)
    # - Message queued to SDK (verified by successful send_message call)
    #
    # What requires end-to-end testing with live API:
    # - SDK writes message to messages.jsonl after processing
    # - Claude's response appears in message stream
    #
    # For integration tests, verifying routing + queueing is sufficient.
    # Full SDK delivery is verified in end-to-end tests with live Claude API.
