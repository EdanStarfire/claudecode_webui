"""
Integration tests for MCP discovery tools.

Tests: search_capability, list_minions, get_minion_info
"""

import pytest

# ============================================================================
# search_capability Tests
# ============================================================================

@pytest.mark.asyncio
async def test_search_capability_with_matches(legion_test_env):
    """
    Test search_capability finds minions with matching capabilities.

    Verifies:
    - Tool returns success with ranked results
    - Results include expertise scores
    - Results are ordered by expertise
    """
    env = legion_test_env
    legion_system = env["legion_system"]

    # Create minions with different expertise levels
    minion1 = await env["create_minion"]("expert", role="Expert", capabilities=["python", "testing"])
    minion2 = await env["create_minion"]("novice", role="Novice", capabilities=["python"])

    # Register capabilities with different expertise scores
    await legion_system.legion_coordinator.register_capability(
        minion_id=minion1.session_id,
        capability="python",
        expertise_score=0.9
    )
    await legion_system.legion_coordinator.register_capability(
        minion_id=minion2.session_id,
        capability="python",
        expertise_score=0.3
    )

    # Search for "python" capability from minion1's perspective
    result = await legion_system.mcp_tools._handle_search_capability({
        "_from_minion_id": minion1.session_id,
        "capability": "python"
    })

    # Verify success response
    assert "content" in result
    assert result["content"][0]["type"] == "text"
    assert result.get("is_error") is not True

    # Verify results include both minions
    response_text = result["content"][0]["text"]
    assert "expert" in response_text
    assert "novice" in response_text

    # Verify expertise scores are shown
    assert "90%" in response_text  # expert's 0.9 → 90%
    assert "30%" in response_text  # novice's 0.3 → 30%

    # Verify ranking (expert should appear before novice)
    expert_pos = response_text.find("expert")
    novice_pos = response_text.find("novice")
    assert expert_pos < novice_pos, "Results should be ranked by expertise (highest first)"


@pytest.mark.asyncio
async def test_search_capability_no_matches(legion_test_env):
    """
    Test search_capability with no matching minions.

    Verifies:
    - Tool returns success (not error) with no matches message
    """
    env = legion_test_env
    legion_system = env["legion_system"]

    # Create minion with no capabilities
    minion = await env["create_minion"]("worker", role="Worker")

    # Search for non-existent capability
    result = await legion_system.mcp_tools._handle_search_capability({
        "_from_minion_id": minion.session_id,
        "capability": "nonexistent"
    })

    # Verify success response (empty results are not errors)
    assert "content" in result
    assert result.get("is_error") is not True

    response_text = result["content"][0]["text"]
    assert "No minions found" in response_text
    assert "nonexistent" in response_text


@pytest.mark.asyncio
async def test_search_capability_empty_parameter(legion_test_env):
    """
    Test search_capability with empty capability parameter.

    Verifies:
    - Tool returns error for empty parameter
    """
    env = legion_test_env
    legion_system = env["legion_system"]

    # Create minion
    minion = await env["create_minion"]("searcher", role="Searcher")

    # Search with empty capability
    result = await legion_system.mcp_tools._handle_search_capability({
        "_from_minion_id": minion.session_id,
        "capability": ""
    })

    # Verify error response
    assert "content" in result
    assert result["is_error"] is True

    response_text = result["content"][0]["text"]
    assert "required" in response_text.lower()


@pytest.mark.asyncio
async def test_search_capability_partial_match(legion_test_env):
    """
    Test search_capability with keyword matching multiple capabilities.

    Verifies:
    - Tool finds minions with partial keyword matches
    - Results include matched capability name
    """
    env = legion_test_env
    legion_system = env["legion_system"]

    # Create minion with multiple related capabilities
    minion = await env["create_minion"]("dev", role="Developer")

    # Register multiple "test" related capabilities
    await legion_system.legion_coordinator.register_capability(
        minion_id=minion.session_id,
        capability="testing",
        expertise_score=0.8
    )
    await legion_system.legion_coordinator.register_capability(
        minion_id=minion.session_id,
        capability="test_automation",
        expertise_score=0.7
    )

    # Search for "test" (should match both)
    result = await legion_system.mcp_tools._handle_search_capability({
        "_from_minion_id": minion.session_id,
        "capability": "test"
    })

    # Verify success
    assert result.get("is_error") is not True

    response_text = result["content"][0]["text"]
    assert "dev" in response_text


# ============================================================================
# list_minions Tests
# ============================================================================

@pytest.mark.asyncio
async def test_list_minions_with_multiple(legion_test_env):
    """
    Test list_minions returns all minions in legion.

    Verifies:
    - Tool returns success with formatted list
    - USER_MINION_ID always included first
    - All created minions are listed
    - Response includes name, role, state, capabilities
    """
    env = legion_test_env
    legion_system = env["legion_system"]

    # Create multiple minions with different attributes
    minion1 = await env["create_minion"]("worker1", role="Worker", capabilities=["task-execution"])
    minion2 = await env["create_minion"]("worker2", role="Analyst", capabilities=["data-analysis"])

    # List minions from minion1's perspective
    result = await legion_system.mcp_tools._handle_list_minions({
        "_from_minion_id": minion1.session_id
    })

    # Verify success response
    assert "content" in result
    assert result.get("is_error") is not True

    response_text = result["content"][0]["text"]

    # Verify "user" minion is always first
    assert "user" in response_text
    user_minion_id = "user"  # USER_MINION_ID constant

    # Verify both created minions are listed
    assert "worker1" in response_text
    assert "worker2" in response_text

    # Verify roles are shown
    assert "Worker" in response_text
    assert "Analyst" in response_text

    # Verify count is correct (3 total: user + 2 minions)
    assert "Active Minions (3)" in response_text


@pytest.mark.asyncio
async def test_list_minions_empty_legion(legion_test_env):
    """
    Test list_minions with only the caller in legion.

    Verifies:
    - Tool returns success even with minimal minions
    - USER_MINION_ID is always present
    """
    env = legion_test_env
    legion_system = env["legion_system"]

    # Create single minion
    minion = await env["create_minion"]("solo", role="Solo Worker")

    # List minions (should only show user + solo)
    result = await legion_system.mcp_tools._handle_list_minions({
        "_from_minion_id": minion.session_id
    })

    # Verify success
    assert result.get("is_error") is not True

    response_text = result["content"][0]["text"]

    # Verify user is present
    assert "user" in response_text

    # Verify solo minion is present
    assert "solo" in response_text

    # Verify count (2 total)
    assert "Active Minions (2)" in response_text


@pytest.mark.asyncio
async def test_list_minions_invalid_caller(legion_test_env):
    """
    Test list_minions with invalid caller ID.

    Verifies:
    - Tool returns error for invalid caller
    """
    env = legion_test_env
    legion_system = env["legion_system"]

    # Call with non-existent minion ID
    result = await legion_system.mcp_tools._handle_list_minions({
        "_from_minion_id": "nonexistent-id"
    })

    # Verify error response
    assert "content" in result
    assert result["is_error"] is True

    response_text = result["content"][0]["text"]
    assert "Error" in response_text


# ============================================================================
# get_minion_info Tests
# ============================================================================

@pytest.mark.asyncio
async def test_get_minion_info_basic(legion_test_env):
    """
    Test get_minion_info returns detailed minion profile.

    Verifies:
    - Tool returns success with formatted profile
    - Profile includes name, ID, role, state
    - Profile includes capabilities with expertise scores
    """
    env = legion_test_env
    legion_system = env["legion_system"]

    # Create target minion with capabilities
    target = await env["create_minion"]("target", role="Target Minion", capabilities=["testing"])
    caller = await env["create_minion"]("caller", role="Caller")

    # Register capability with expertise
    await legion_system.legion_coordinator.register_capability(
        minion_id=target.session_id,
        capability="testing",
        expertise_score=0.85
    )

    # Get info about target from caller's perspective
    result = await legion_system.mcp_tools._handle_get_minion_info({
        "_from_minion_id": caller.session_id,
        "minion_name": "target"
    })

    # Verify success response
    assert "content" in result
    assert result.get("is_error") is not True

    response_text = result["content"][0]["text"]

    # Verify profile header
    assert "Minion Profile: target" in response_text

    # Verify basic info
    assert "Target Minion" in response_text  # role
    assert target.session_id[:8] in response_text  # ID prefix

    # Verify capabilities with expertise
    assert "testing" in response_text
    assert "85%" in response_text  # 0.85 → 85%


@pytest.mark.asyncio
async def test_get_minion_info_not_found(legion_test_env):
    """
    Test get_minion_info with non-existent minion.

    Verifies:
    - Tool returns error for non-existent minion
    """
    env = legion_test_env
    legion_system = env["legion_system"]

    # Create caller
    caller = await env["create_minion"]("caller", role="Caller")

    # Try to get info for non-existent minion
    result = await legion_system.mcp_tools._handle_get_minion_info({
        "_from_minion_id": caller.session_id,
        "minion_name": "nonexistent"
    })

    # Verify error response
    assert "content" in result
    assert result["is_error"] is True

    response_text = result["content"][0]["text"]
    assert "not found" in response_text.lower()
    assert "nonexistent" in response_text


@pytest.mark.asyncio
async def test_get_minion_info_empty_name(legion_test_env):
    """
    Test get_minion_info with empty minion_name parameter.

    Verifies:
    - Tool returns error for empty parameter
    """
    env = legion_test_env
    legion_system = env["legion_system"]

    # Create caller
    caller = await env["create_minion"]("caller", role="Caller")

    # Try to get info with empty name
    result = await legion_system.mcp_tools._handle_get_minion_info({
        "_from_minion_id": caller.session_id,
        "minion_name": ""
    })

    # Verify error response
    assert "content" in result
    assert result["is_error"] is True

    response_text = result["content"][0]["text"]
    assert "required" in response_text.lower()
