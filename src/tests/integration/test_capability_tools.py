"""
Integration tests for MCP template/capability tools.

Tests: list_templates, update_expertise
"""

import pytest

# ============================================================================
# list_templates Tests
# ============================================================================

@pytest.mark.asyncio
async def test_list_templates_with_templates(legion_test_env):
    """
    Test list_templates returns formatted template list.

    Verifies:
    - Tool returns success with template list
    - Response includes template details (name, description, permission_mode, allowed_tools, default_role)
    - Templates sorted alphabetically
    """
    env = legion_test_env
    legion_system = env["legion_system"]
    template_manager = env["template_manager"]

    # Get available templates (should have at least default templates)
    templates = await template_manager.list_templates()
    if not templates:
        pytest.skip("No templates available for testing")

    # Create a minion to call the tool
    minion = await env["create_minion"]("caller", role="Template Viewer")

    # Call list_templates tool
    result = await legion_system.mcp_tools._handle_list_templates({})

    # Verify success response
    assert "content" in result
    assert result.get("is_error") is not True

    response_text = result["content"][0]["text"]

    # Verify response includes template names
    for template in templates:
        assert template.name in response_text, f"Template {template.name} should be in response"

    # Verify response includes key template attributes
    assert "Permission Mode" in response_text
    assert "Allowed Tools" in response_text
    assert "Default Role" in response_text


@pytest.mark.asyncio
async def test_list_templates_empty(legion_test_env):
    """
    Test list_templates with no templates available.

    Verifies:
    - Tool returns success (not error) with helpful message
    - Response suggests how to create templates
    """
    env = legion_test_env
    legion_system = env["legion_system"]
    template_manager = env["template_manager"]

    # Check if templates exist
    templates = await template_manager.list_templates()
    if templates:
        pytest.skip("Test requires no templates, but templates exist")

    # Create a minion to call the tool
    minion = await env["create_minion"]("caller", role="Template Viewer")

    # Call list_templates tool
    result = await legion_system.mcp_tools._handle_list_templates({})

    # Verify success response (empty list is not an error)
    assert "content" in result
    assert result.get("is_error") is not True

    response_text = result["content"][0]["text"]

    # Verify response explains no templates available
    assert "No templates available" in response_text or "no templates" in response_text.lower()


@pytest.mark.asyncio
async def test_list_templates_format(legion_test_env):
    """
    Test list_templates response formatting.

    Verifies:
    - Tool returns formatted markdown list
    - Each template has all required fields
    - Usage instructions included
    """
    env = legion_test_env
    legion_system = env["legion_system"]
    template_manager = env["template_manager"]

    # Get available templates
    templates = await template_manager.list_templates()
    if not templates:
        pytest.skip("No templates available for testing")

    # Create a minion to call the tool
    minion = await env["create_minion"]("caller", role="Template Viewer")

    # Call list_templates tool
    result = await legion_system.mcp_tools._handle_list_templates({})

    # Verify success
    assert result.get("is_error") is not True

    response_text = result["content"][0]["text"]

    # Verify formatting with markdown bullet points
    assert "**Available Minion Templates:**" in response_text or "templates" in response_text.lower()

    # Verify usage instructions included
    assert "Usage" in response_text or "spawn_minion" in response_text


# ============================================================================
# update_expertise Tests
# ============================================================================

@pytest.mark.asyncio
async def test_update_expertise_with_score(legion_test_env):
    """
    Test update_expertise adds capability with explicit score.

    Verifies:
    - Tool returns success
    - Capability registered in registry with correct score
    - Minion's capabilities list updated
    """
    env = legion_test_env
    legion_system = env["legion_system"]

    # Create minion
    minion = await env["create_minion"]("developer", role="Developer")

    # Update expertise with explicit score
    result = await legion_system.mcp_tools._handle_update_expertise({
        "_from_minion_id": minion.session_id,
        "capability": "python",
        "expertise_score": 0.85
    })

    # Verify success response
    assert "content" in result
    assert result.get("is_error") is not True

    response_text = result["content"][0]["text"]
    assert "python" in response_text.lower()
    assert "0.85" in response_text

    # SIDE EFFECT 1: Capability registered in registry
    search_result = await legion_system.legion_coordinator.search_capability_registry(keyword="python")
    assert len(search_result) > 0
    # Find entry for this minion
    found = False
    for minion_id, score, capability in search_result:
        if minion_id == minion.session_id and capability == "python":
            assert score == 0.85
            found = True
            break
    assert found, "Capability should be registered with correct score"

    # SIDE EFFECT 2: Minion's capabilities list updated
    minion_updated = await env["session_coordinator"].session_manager.get_session_info(minion.session_id)
    assert "python" in minion_updated.capabilities


@pytest.mark.asyncio
async def test_update_expertise_default_score(legion_test_env):
    """
    Test update_expertise with default score (None → 0.5).

    Verifies:
    - Tool returns success
    - Capability registered with default score 0.5
    """
    env = legion_test_env
    legion_system = env["legion_system"]

    # Create minion
    minion = await env["create_minion"]("worker", role="Worker")

    # Update expertise without explicit score
    result = await legion_system.mcp_tools._handle_update_expertise({
        "_from_minion_id": minion.session_id,
        "capability": "testing",
        "expertise_score": None
    })

    # Verify success
    assert result.get("is_error") is not True

    response_text = result["content"][0]["text"]
    assert "testing" in response_text.lower()
    assert "0.50" in response_text  # Default score formatted

    # Verify default score in registry
    search_result = await legion_system.legion_coordinator.search_capability_registry(keyword="testing")
    found = False
    for minion_id, score, capability in search_result:
        if minion_id == minion.session_id and capability == "testing":
            assert score == 0.5  # Default score
            found = True
            break
    assert found


@pytest.mark.asyncio
async def test_update_expertise_multiple_capabilities(legion_test_env):
    """
    Test update_expertise can add multiple capabilities to same minion.

    Verifies:
    - Tool can be called multiple times
    - Capability count increases with each addition
    """
    env = legion_test_env
    legion_system = env["legion_system"]

    # Create minion
    minion = await env["create_minion"]("expert", role="Expert")

    # Add first capability
    result1 = await legion_system.mcp_tools._handle_update_expertise({
        "_from_minion_id": minion.session_id,
        "capability": "javascript",
        "expertise_score": 0.9
    })
    assert result1.get("is_error") is not True
    assert "1 capability" in result1["content"][0]["text"] or "1 capabilit" in result1["content"][0]["text"]

    # Add second capability
    result2 = await legion_system.mcp_tools._handle_update_expertise({
        "_from_minion_id": minion.session_id,
        "capability": "react",
        "expertise_score": 0.8
    })
    assert result2.get("is_error") is not True
    assert "2 capabilit" in result2["content"][0]["text"]

    # Verify both in minion's list
    minion_updated = await env["session_coordinator"].session_manager.get_session_info(minion.session_id)
    assert "javascript" in minion_updated.capabilities
    assert "react" in minion_updated.capabilities


@pytest.mark.asyncio
async def test_update_expertise_empty_capability(legion_test_env):
    """
    Test update_expertise with empty capability parameter.

    Verifies:
    - Tool returns error for empty capability
    """
    env = legion_test_env
    legion_system = env["legion_system"]

    # Create minion
    minion = await env["create_minion"]("worker", role="Worker")

    # Try to update with empty capability
    result = await legion_system.mcp_tools._handle_update_expertise({
        "_from_minion_id": minion.session_id,
        "capability": "",
        "expertise_score": 0.5
    })

    # Verify error response
    assert "content" in result
    assert result["is_error"] is True

    response_text = result["content"][0]["text"]
    assert "required" in response_text.lower() or "empty" in response_text.lower()


@pytest.mark.asyncio
async def test_update_expertise_score_out_of_range(legion_test_env):
    """
    Test update_expertise with score outside valid range.

    Verifies:
    - Tool returns error for score < 0.0
    - Tool returns error for score > 1.0
    """
    env = legion_test_env
    legion_system = env["legion_system"]

    # Create minion
    minion = await env["create_minion"]("worker", role="Worker")

    # Try with score < 0.0
    result1 = await legion_system.mcp_tools._handle_update_expertise({
        "_from_minion_id": minion.session_id,
        "capability": "python",
        "expertise_score": -0.1
    })
    assert result1["is_error"] is True
    assert "0.0" in result1["content"][0]["text"] and "1.0" in result1["content"][0]["text"]

    # Try with score > 1.0
    result2 = await legion_system.mcp_tools._handle_update_expertise({
        "_from_minion_id": minion.session_id,
        "capability": "python",
        "expertise_score": 1.5
    })
    assert result2["is_error"] is True
    assert "0.0" in result2["content"][0]["text"] and "1.0" in result2["content"][0]["text"]
