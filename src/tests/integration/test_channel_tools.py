"""
Integration tests for MCP channel management tools.

Tests: create_channel, list_channels
"""

import json
from pathlib import Path

import pytest


# ============================================================================
# create_channel Tests
# ============================================================================

@pytest.mark.asyncio
async def test_create_channel_basic(legion_test_env):
    """
    Test create_channel creates channel with creator as member.

    Verifies:
    - Tool returns success with channel ID and member count
    - Creator automatically added as member
    - Channel persisted to channels/{channel_id}/channel_state.json
    - SessionInfo.channel_ids updated for creator
    """
    env = legion_test_env
    legion_system = env["legion_system"]
    data_dir = env["data_dir"]
    legion_id = env["legion_id"]

    # Create minion
    creator = await env["create_minion"]("creator", role="Creator")

    # Create channel via MCP tool
    result = await legion_system.mcp_tools._handle_create_channel({
        "_from_minion_id": creator.session_id,
        "name": "test-channel",
        "description": "Test channel for testing",
        "purpose": "coordination"
    })

    # Verify success response
    assert "content" in result
    assert result.get("is_error") is not True

    response_text = result["content"][0]["text"]
    assert "Successfully created channel" in response_text
    assert "#test-channel" in response_text
    assert "Members: 1 (including you)" in response_text

    # SIDE EFFECT 1: Channel persisted to disk
    channels = await legion_system.channel_manager.list_channels(legion_id)
    assert len(channels) == 1
    channel = channels[0]
    assert channel.name == "test-channel"
    assert channel.description == "Test channel for testing"
    assert channel.purpose == "coordination"
    assert creator.session_id in channel.member_minion_ids

    # Verify channel state file exists
    channel_state_file = data_dir / "legions" / legion_id / "channels" / channel.channel_id / "channel_state.json"
    assert channel_state_file.exists()

    # SIDE EFFECT 2: Creator's SessionInfo updated
    creator_updated = await env["session_coordinator"].session_manager.get_session_info(creator.session_id)
    assert channel.channel_id in creator_updated.channel_ids


@pytest.mark.asyncio
async def test_create_channel_with_initial_members(legion_test_env):
    """
    Test create_channel with initial_members parameter.

    Verifies:
    - Tool returns success with multiple member count
    - Creator + initial members all added
    - All members have SessionInfo.channel_ids updated
    - Warning for non-existent members included in response
    """
    env = legion_test_env
    legion_system = env["legion_system"]
    legion_id = env["legion_id"]

    # Create creator and 2 members
    creator = await env["create_minion"]("creator", role="Creator")
    member1 = await env["create_minion"]("member1", role="Member")
    member2 = await env["create_minion"]("member2", role="Member")

    # Create channel with initial members (including non-existent)
    result = await legion_system.mcp_tools._handle_create_channel({
        "_from_minion_id": creator.session_id,
        "name": "team-channel",
        "description": "Team collaboration channel",
        "purpose": "planning",
        "initial_members": ["member1", "member2", "nonexistent"]
    })

    # Verify success response
    assert result.get("is_error") is not True

    response_text = result["content"][0]["text"]
    assert "Successfully created channel" in response_text
    assert "Members: 3 (including you)" in response_text  # creator + member1 + member2
    assert "Could not add 'nonexistent'" in response_text  # Warning for missing member

    # Verify all members added
    channels = await legion_system.channel_manager.list_channels(legion_id)
    channel = channels[0]
    assert creator.session_id in channel.member_minion_ids
    assert member1.session_id in channel.member_minion_ids
    assert member2.session_id in channel.member_minion_ids
    assert len(channel.member_minion_ids) == 3

    # Verify all SessionInfo updated
    creator_updated = await env["session_coordinator"].session_manager.get_session_info(creator.session_id)
    member1_updated = await env["session_coordinator"].session_manager.get_session_info(member1.session_id)
    member2_updated = await env["session_coordinator"].session_manager.get_session_info(member2.session_id)

    assert channel.channel_id in creator_updated.channel_ids
    assert channel.channel_id in member1_updated.channel_ids
    assert channel.channel_id in member2_updated.channel_ids


@pytest.mark.asyncio
async def test_create_channel_duplicate_name(legion_test_env):
    """
    Test create_channel with duplicate name.

    Verifies:
    - Tool returns error for duplicate name
    - No second channel created
    - Error message: "already exists"
    """
    env = legion_test_env
    legion_system = env["legion_system"]
    legion_id = env["legion_id"]

    # Create minion
    creator = await env["create_minion"]("creator", role="Creator")

    # Create first channel
    result1 = await legion_system.mcp_tools._handle_create_channel({
        "_from_minion_id": creator.session_id,
        "name": "duplicate-channel",
        "description": "First channel",
        "purpose": "coordination"
    })
    assert result1.get("is_error") is not True

    # Try to create second channel with same name
    result2 = await legion_system.mcp_tools._handle_create_channel({
        "_from_minion_id": creator.session_id,
        "name": "duplicate-channel",
        "description": "Second channel",
        "purpose": "planning"
    })

    # Verify error response
    assert result2.get("is_error") is True

    response_text = result2["content"][0]["text"]
    assert "already exists" in response_text.lower()
    assert "duplicate-channel" in response_text

    # Verify only one channel created
    channels = await legion_system.channel_manager.list_channels(legion_id)
    assert len(channels) == 1
    assert channels[0].description == "First channel"


@pytest.mark.asyncio
async def test_create_channel_empty_name(legion_test_env):
    """
    Test create_channel with empty name parameter.

    Verifies:
    - Tool returns error for empty name
    - Error message: "'name' parameter is required"
    """
    env = legion_test_env
    legion_system = env["legion_system"]

    # Create minion
    creator = await env["create_minion"]("creator", role="Creator")

    # Try to create channel with empty name
    result = await legion_system.mcp_tools._handle_create_channel({
        "_from_minion_id": creator.session_id,
        "name": "",
        "description": "Test description",
        "purpose": "coordination"
    })

    # Verify error response
    assert result.get("is_error") is True

    response_text = result["content"][0]["text"]
    assert "required" in response_text.lower()
    assert "name" in response_text.lower()


@pytest.mark.asyncio
async def test_create_channel_empty_description(legion_test_env):
    """
    Test create_channel with empty description parameter.

    Verifies:
    - Tool returns error for empty description
    - Error message: "'description' parameter is required"
    """
    env = legion_test_env
    legion_system = env["legion_system"]

    # Create minion
    creator = await env["create_minion"]("creator", role="Creator")

    # Try to create channel with empty description
    result = await legion_system.mcp_tools._handle_create_channel({
        "_from_minion_id": creator.session_id,
        "name": "test-channel",
        "description": "",
        "purpose": "coordination"
    })

    # Verify error response
    assert result.get("is_error") is True

    response_text = result["content"][0]["text"]
    assert "required" in response_text.lower()
    assert "description" in response_text.lower()


@pytest.mark.asyncio
async def test_create_channel_invalid_purpose(legion_test_env):
    """
    Test create_channel with invalid purpose parameter.

    Verifies:
    - Tool returns error for invalid purpose
    - Error message: "Must be one of: coordination, planning, research, scene"
    """
    env = legion_test_env
    legion_system = env["legion_system"]

    # Create minion
    creator = await env["create_minion"]("creator", role="Creator")

    # Try to create channel with invalid purpose
    result = await legion_system.mcp_tools._handle_create_channel({
        "_from_minion_id": creator.session_id,
        "name": "test-channel",
        "description": "Test description",
        "purpose": "invalid_purpose"
    })

    # Verify error response
    assert result.get("is_error") is True

    response_text = result["content"][0]["text"]
    assert "invalid purpose" in response_text.lower()
    assert "coordination" in response_text
    assert "planning" in response_text
    assert "research" in response_text
    assert "scene" in response_text


# ============================================================================
# list_channels Tests
# ============================================================================

@pytest.mark.asyncio
async def test_list_channels_multiple(legion_test_env):
    """
    Test list_channels with multiple channels.

    Verifies:
    - Tool returns success with formatted list
    - Channels sorted alphabetically by name
    - Response includes: name, description, purpose, member count
    - Member badge shown for caller's channels
    """
    env = legion_test_env
    legion_system = env["legion_system"]
    legion_id = env["legion_id"]

    # Create minion
    minion = await env["create_minion"]("lister", role="Lister")

    # Create 3 channels (out of order)
    await legion_system.mcp_tools._handle_create_channel({
        "_from_minion_id": minion.session_id,
        "name": "zebra-channel",
        "description": "Last alphabetically",
        "purpose": "coordination"
    })

    await legion_system.mcp_tools._handle_create_channel({
        "_from_minion_id": minion.session_id,
        "name": "alpha-channel",
        "description": "First alphabetically",
        "purpose": "planning"
    })

    await legion_system.mcp_tools._handle_create_channel({
        "_from_minion_id": minion.session_id,
        "name": "middle-channel",
        "description": "Middle alphabetically",
        "purpose": "research"
    })

    # List channels
    result = await legion_system.mcp_tools._handle_list_channels({
        "_from_minion_id": minion.session_id
    })

    # Verify success response
    assert result.get("is_error") is not True

    response_text = result["content"][0]["text"]

    # Verify count
    assert "Channels in this Legion (3)" in response_text

    # Verify all channels listed
    assert "#alpha-channel" in response_text
    assert "#middle-channel" in response_text
    assert "#zebra-channel" in response_text

    # Verify details included
    assert "First alphabetically" in response_text
    assert "Middle alphabetically" in response_text
    assert "Last alphabetically" in response_text
    assert "coordination" in response_text
    assert "planning" in response_text
    assert "research" in response_text

    # Verify alphabetical ordering
    alpha_pos = response_text.find("#alpha-channel")
    middle_pos = response_text.find("#middle-channel")
    zebra_pos = response_text.find("#zebra-channel")
    assert alpha_pos < middle_pos < zebra_pos

    # Verify member badges (minion is member of all)
    assert response_text.count("**(member)**") == 3


@pytest.mark.asyncio
async def test_list_channels_empty(legion_test_env):
    """
    Test list_channels with no channels.

    Verifies:
    - Tool returns success (not error)
    - Response: "No channels found"
    - Suggests creating channel
    """
    env = legion_test_env
    legion_system = env["legion_system"]

    # Create minion
    minion = await env["create_minion"]("lister", role="Lister")

    # List channels (none exist)
    result = await legion_system.mcp_tools._handle_list_channels({
        "_from_minion_id": minion.session_id
    })

    # Verify success response (empty is not an error)
    assert result.get("is_error") is not True

    response_text = result["content"][0]["text"]
    assert "No channels found" in response_text
    assert "create_channel" in response_text


@pytest.mark.asyncio
async def test_list_channels_member_badge(legion_test_env):
    """
    Test list_channels shows member badge correctly.

    Verifies:
    - Tool marks channels caller is member of with "**(member)**"
    - Non-member channels don't have badge
    """
    env = legion_test_env
    legion_system = env["legion_system"]
    legion_id = env["legion_id"]

    # Create 2 minions
    minion1 = await env["create_minion"]("minion1", role="Member")
    minion2 = await env["create_minion"]("minion2", role="Member")

    # Minion1 creates channel (becomes member)
    await legion_system.mcp_tools._handle_create_channel({
        "_from_minion_id": minion1.session_id,
        "name": "member-channel",
        "description": "Minion1 is member",
        "purpose": "coordination"
    })

    # Minion2 creates channel (minion1 is NOT member)
    await legion_system.mcp_tools._handle_create_channel({
        "_from_minion_id": minion2.session_id,
        "name": "non-member-channel",
        "description": "Minion1 is NOT member",
        "purpose": "planning"
    })

    # Minion1 lists channels
    result = await legion_system.mcp_tools._handle_list_channels({
        "_from_minion_id": minion1.session_id
    })

    # Verify success
    assert result.get("is_error") is not True

    response_text = result["content"][0]["text"]

    # Verify member-channel has badge (with space: "**#member-channel** **(member)**")
    assert "#member-channel" in response_text
    member_channel_section = response_text[response_text.find("#member-channel"):]
    non_member_section = response_text[response_text.find("#non-member-channel"):]

    # Member channel should have (member) badge
    assert "**(member)**" in member_channel_section[:100]  # Within first 100 chars after name

    # Non-member channel should NOT have (member) badge
    # Find the description line to check only the channel header
    desc_pos = non_member_section.find("- Description")
    assert "**(member)**" not in non_member_section[:desc_pos]


@pytest.mark.asyncio
async def test_list_channels_count(legion_test_env):
    """
    Test list_channels shows correct count.

    Verifies:
    - Response shows correct total: "Channels in this Legion (N)"
    """
    env = legion_test_env
    legion_system = env["legion_system"]
    legion_id = env["legion_id"]

    # Create minion
    minion = await env["create_minion"]("lister", role="Lister")

    # Create 5 channels
    for i in range(5):
        await legion_system.mcp_tools._handle_create_channel({
            "_from_minion_id": minion.session_id,
            "name": f"channel-{i}",
            "description": f"Channel number {i}",
            "purpose": "coordination"
        })

    # List channels
    result = await legion_system.mcp_tools._handle_list_channels({
        "_from_minion_id": minion.session_id
    })

    # Verify success
    assert result.get("is_error") is not True

    response_text = result["content"][0]["text"]
    assert "Channels in this Legion (5)" in response_text
