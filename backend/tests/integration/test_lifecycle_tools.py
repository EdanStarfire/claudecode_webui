"""
Integration tests for MCP lifecycle tools.

Tests: spawn_minion, dispose_minion
"""

from contextlib import contextmanager
from unittest.mock import patch

import pytest

from backend.config_resolution import resolve_effective_config
from backend.session_manager import SessionState

pytestmark = pytest.mark.slow


@contextmanager
def _capture_persisted_comms(comm_router):
    """Spy on CommRouter._persist_comm(), capturing each comm as it's routed.

    Issue #2259: spawn_minion()/dispose_minion() no longer construct a SPAWN/
    DISPOSE Comm at all (there's no to-user delivery channel for it to go to)
    — this spy is used to assert that no such comm is persisted, with
    created_at/disposed_at on the hierarchy entry now the activity signal
    instead.
    """
    persisted_comms = []
    original_persist_comm = comm_router._persist_comm

    async def tracked_persist_comm(comm):
        persisted_comms.append(comm)
        return await original_persist_comm(comm)

    with patch.object(comm_router, "_persist_comm", side_effect=tracked_persist_comm):
        yield persisted_comms


# ============================================================================
# spawn_minion Tests
# ============================================================================

@pytest.mark.asyncio
async def test_spawn_minion_minimal(legion_test_env):
    """
    Test spawn_minion with minimal parameters (name/role/system_prompt/template_name).

    Issue #1912: template_name is now a required parameter — minions can only be
    spawned from a template, so this test supplies one instead of exercising the
    (now removed) templateless default-permissions path.

    Verifies:
    - Tool returns success with child_minion_id
    - Child session created with ACTIVE state
    - Parent marked as overseer with child in child_minion_ids
    - No SPAWN comm persisted (issue #2259); hierarchy's created_at is the
      activity signal instead
    """
    env = legion_test_env
    legion_system = env["legion_system"]
    legion_id = env["legion_id"]
    template_manager = env["template_manager"]

    # Create parent minion (will wait for ACTIVE state)
    parent = await env["create_minion"]("parent", role="Parent")

    templates = await template_manager.list_templates()
    if not templates:
        pytest.skip("No templates available for testing")
    template_name = templates[0].name

    # Spawn child via MCP tool handler
    with _capture_persisted_comms(legion_system.comm_router) as persisted_comms:
        result = await legion_system.mcp_tools._handle_spawn_minion({
            "_parent_overseer_id": parent.session_id,
            "name": "child",
            "role": "Child Worker",
            "system_prompt": "You are a test child minion for integration testing.",
            "template_name": template_name,
        })

    # Verify success response (not an error)
    assert "content" in result
    assert isinstance(result["content"], list)
    assert len(result["content"]) > 0
    assert result["content"][0]["type"] == "text"
    assert result.get("is_error") is not True, f"Unexpected error: {result['content'][0]['text']}"

    # Extract child_minion_id from response
    response_text = result["content"][0]["text"]
    # Parse Minion ID from response (format: "Minion ID: <uuid>")
    import re
    minion_id_match = re.search(r'Minion ID:\s+([a-f0-9-]+)', response_text)
    assert minion_id_match is not None, f"Response should contain Minion ID. Got: {response_text}"
    child_id = minion_id_match.group(1)

    # SIDE EFFECT 1: Child session created with ACTIVE state
    # (Poll for ACTIVE state with timeout)
    import asyncio
    import time
    max_wait = 50.0
    poll_interval = 0.1
    start_time = time.time()

    child_info = None
    while True:
        elapsed = time.time() - start_time

        if elapsed >= max_wait:
            raise TimeoutError(
                f"Child session {child_id} did not become ACTIVE within {max_wait}s"
            )

        child_info = await env["session_coordinator"].session_manager.get_session_info(child_id)
        if child_info.state == SessionState.ACTIVE:
            print(f"  > Child session became ACTIVE in {elapsed:.2f}s")
            break

        await asyncio.sleep(poll_interval)

    # Issue #349: is_minion removed - all sessions are minions
    assert child_info is not None
    assert child_info.name == "child"
    assert child_info.role == "Child Worker"
    assert child_info.project_id == legion_id
    assert child_info.parent_overseer_id == parent.session_id

    # SIDE EFFECT 2: Parent marked as overseer with child in child_minion_ids
    parent_updated = await env["session_coordinator"].session_manager.get_session_info(parent.session_id)
    assert parent_updated.is_overseer is True
    assert child_id in parent_updated.child_minion_ids

    # SIDE EFFECT 3 (issue #2259 T2): no SPAWN comm persisted — there's no
    # to-user delivery channel for one to go to anymore.
    spawn_comms = [c for c in persisted_comms if c.comm_type.value == "spawn"]
    assert spawn_comms == [], f"Expected no SPAWN comm; got {spawn_comms}"

    # SIDE EFFECT 4 (issue #2259 T2): hierarchy shows the child's created_at
    # as its activity signal instead.
    hierarchy = await legion_system.legion_coordinator.assemble_minion_hierarchy(legion_id)
    parent_node = next(n for n in hierarchy["children"] if n["id"] == parent.session_id)
    child_node = next(n for n in parent_node["children"] if n["id"] == child_id)
    assert child_node["created_at"] is not None
    assert child_node["disposed_at"] is None


@pytest.mark.asyncio
async def test_spawn_minion_without_template_fails(legion_test_env):
    """
    Issue #1912: template_name is now required — spawn_minion must reject a call
    that omits it instead of silently applying restricted default permissions.
    """
    env = legion_test_env
    legion_system = env["legion_system"]

    parent = await env["create_minion"]("parent", role="Parent")

    result = await legion_system.mcp_tools._handle_spawn_minion({
        "_parent_overseer_id": parent.session_id,
        "name": "child",
        "role": "Child Worker",
        "system_prompt": "You are a test child minion for integration testing.",
    })

    assert result.get("is_error") is True
    assert "template_name" in result["content"][0]["text"]


@pytest.mark.asyncio
async def test_spawn_minion_with_template(legion_test_env):
    """
    Test spawn_minion with template.

    Verifies:
    - Template permissions applied to child
    - system_prompt prepended
    """
    env = legion_test_env
    legion_system = env["legion_system"]
    template_manager = env["template_manager"]

    # Create parent minion
    parent = await env["create_minion"]("parent", role="Parent")

    # Get available templates
    templates = await template_manager.list_templates()
    if not templates:
        pytest.skip("No templates available for testing")

    template_name = templates[0].name

    # Spawn child with template
    result = await legion_system.mcp_tools._handle_spawn_minion({
        "_parent_overseer_id": parent.session_id,
        "name": "child",
        "role": "Child Worker",
        "system_prompt": "Test child with template.",
        "template_name": template_name
    })

    # Verify success
    assert result.get("is_error") is not True, f"Unexpected error: {result['content'][0]['text']}"

    # Extract child_minion_id
    response_text = result["content"][0]["text"]
    import re
    minion_id_match = re.search(r'Minion ID:\s+([a-f0-9-]+)', response_text)
    assert minion_id_match is not None, f"Response should contain Minion ID. Got: {response_text}"
    child_id = minion_id_match.group(1)

    # Wait for child to become ACTIVE
    import asyncio
    import time
    max_wait = 50.0
    poll_interval = 0.1
    start_time = time.time()

    while True:
        elapsed = time.time() - start_time
        if elapsed >= max_wait:
            raise TimeoutError(f"Child session did not become ACTIVE within {max_wait}s")

        child_info = await env["session_coordinator"].session_manager.get_session_info(child_id)
        if child_info.state == SessionState.ACTIVE:
            print(f"  > Child session with template became ACTIVE in {elapsed:.2f}s")
            break

        await asyncio.sleep(poll_interval)

    # Verify template was applied (system_prompt should include template)
    # Note: Template application affects permissions and system prompt
    effective = await resolve_effective_config(child_info, template_manager)
    assert effective.system_prompt is not None
    # The system prompt should include the system_prompt we provided
    assert "Test child with template" in effective.system_prompt

    # SECURITY CRITICAL: Verify template permissions were applied
    # Templates are a security mechanism to prevent privilege escalation
    template = await template_manager.get_template_by_name(template_name)
    if template.config.get("permission_mode"):
        assert child_info.current_permission_mode == template.config["permission_mode"], \
            f"Child permission_mode should match template: {template.config['permission_mode']}"
    if template.config.get("allowed_tools"):
        # Verify template's allowed_tools are in child's allowed_tools
        child_tools = set(effective.allowed_tools or [])
        template_tools = set(template.config.get("allowed_tools"))
        assert template_tools.issubset(child_tools), \
            f"Child should have template's allowed_tools: {template.config.get('allowed_tools')}"
    # Verify template's system_prompt was prepended
    if template.config.get("system_prompt"):
        assert template.config["system_prompt"] in effective.system_prompt, \
            "Child system_prompt should include template's system_prompt"


@pytest.mark.asyncio
async def test_spawn_minion_with_template_process_wrapper(legion_test_env):
    """
    Issue #1672: process_wrapper must propagate from template to spawned child,
    mirroring the existing cli_path propagation path (issue #489).
    """
    from backend.session_config import SessionConfig

    env = legion_test_env
    legion_system = env["legion_system"]
    template_manager = env["template_manager"]

    parent = await env["create_minion"]("parent", role="Parent")

    template = await template_manager.create_template(
        name="Process Wrapper Template",
        config=SessionConfig(
            permission_mode="default",
            process_wrapper="/opt/launcher/wrapper.sh",
        ),
        role="Wrapped Worker",
    )

    result = await legion_system.mcp_tools._handle_spawn_minion({
        "_parent_overseer_id": parent.session_id,
        "name": "wrapped-child",
        "role": "Wrapped Worker",
        "system_prompt": "Test child with process_wrapper template.",
        "template_name": template.name,
    })

    assert result.get("is_error") is not True, f"Unexpected error: {result['content'][0]['text']}"

    response_text = result["content"][0]["text"]
    import re
    minion_id_match = re.search(r'Minion ID:\s+([a-f0-9-]+)', response_text)
    assert minion_id_match is not None, f"Response should contain Minion ID. Got: {response_text}"
    child_id = minion_id_match.group(1)

    import asyncio
    import time
    max_wait = 50.0
    poll_interval = 0.1
    start_time = time.time()

    while True:
        elapsed = time.time() - start_time
        if elapsed >= max_wait:
            raise TimeoutError(f"Child session did not become ACTIVE within {max_wait}s")

        child_info = await env["session_coordinator"].session_manager.get_session_info(child_id)
        if child_info.state == SessionState.ACTIVE:
            break

        await asyncio.sleep(poll_interval)

    effective = await resolve_effective_config(child_info, template_manager)
    assert effective.process_wrapper == "/opt/launcher/wrapper.sh"


@pytest.mark.asyncio
async def test_spawn_minion_with_capabilities(legion_test_env):
    """
    Test spawn_minion with capabilities.

    Verifies:
    - Capabilities registered in capability registry
    """
    env = legion_test_env
    legion_system = env["legion_system"]
    template_manager = env["template_manager"]

    # Create parent minion
    parent = await env["create_minion"]("parent", role="Parent")

    templates = await template_manager.list_templates()
    if not templates:
        pytest.skip("No templates available for testing")

    # Spawn child with capabilities
    result = await legion_system.mcp_tools._handle_spawn_minion({
        "_parent_overseer_id": parent.session_id,
        "name": "child",
        "role": "Data Scientist",
        "system_prompt": "Test child with capabilities.",
        "template_name": templates[0].name,
        "capabilities": ["python", "data analysis", "machine learning"]
    })

    # Verify success
    assert result.get("is_error") is not True, f"Unexpected error: {result['content'][0]['text']}"

    # Extract child_minion_id
    response_text = result["content"][0]["text"]
    import re
    minion_id_match = re.search(r'Minion ID:\s+([a-f0-9-]+)', response_text)
    assert minion_id_match is not None, f"Response should contain Minion ID. Got: {response_text}"
    child_id = minion_id_match.group(1)

    # Wait for child to become ACTIVE
    import asyncio
    import time
    max_wait = 50.0
    poll_interval = 0.1
    start_time = time.time()

    while True:
        elapsed = time.time() - start_time
        if elapsed >= max_wait:
            raise TimeoutError(f"Child session did not become ACTIVE within {max_wait}s")

        child_info = await env["session_coordinator"].session_manager.get_session_info(child_id)
        if child_info.state == SessionState.ACTIVE:
            print(f"  > Child session with capabilities became ACTIVE in {elapsed:.2f}s")
            break

        await asyncio.sleep(poll_interval)

    # SIDE EFFECT: Capabilities registered
    assert child_info.capabilities is not None
    assert "python" in child_info.capabilities
    assert "data analysis" in child_info.capabilities
    assert "machine learning" in child_info.capabilities

    # Verify capability search finds the minion
    search_result = await legion_system.mcp_tools._handle_search_capability({
        "_from_minion_id": parent.session_id,
        "capability": "python"
    })
    assert search_result.get("is_error") is not True
    # Parse response to verify child is in results
    response_text = search_result["content"][0]["text"]
    assert child_id in response_text or "child" in response_text


@pytest.mark.asyncio
async def test_spawn_minion_error_duplicate_name(legion_test_env):
    """
    Test spawn_minion error: duplicate name.

    Verifies:
    - Tool returns error for duplicate minion name
    """
    env = legion_test_env
    legion_system = env["legion_system"]
    template_manager = env["template_manager"]

    # Create parent minion
    parent = await env["create_minion"]("parent", role="Parent")

    templates = await template_manager.list_templates()
    if not templates:
        pytest.skip("No templates available for testing")
    template_name = templates[0].name

    # Spawn first child
    result1 = await legion_system.mcp_tools._handle_spawn_minion({
        "_parent_overseer_id": parent.session_id,
        "name": "duplicate",
        "role": "Worker",
        "system_prompt": "First test child.",
        "template_name": template_name,
    })
    assert result1.get("is_error") is not True

    # Try to spawn second child with same name
    result2 = await legion_system.mcp_tools._handle_spawn_minion({
        "_parent_overseer_id": parent.session_id,
        "name": "duplicate",
        "role": "Worker",
        "system_prompt": "Second test child (should fail).",
        "template_name": template_name,
    })

    # Verify error response
    assert result2.get("is_error") is True
    error_text = result2["content"][0]["text"]
    assert "already exists" in error_text.lower() or "duplicate" in error_text.lower()


@pytest.mark.asyncio
async def test_spawn_minion_error_nonexistent_template(legion_test_env):
    """
    Test spawn_minion error: non-existent template.

    Verifies:
    - Tool returns error for invalid template name
    """
    env = legion_test_env
    legion_system = env["legion_system"]

    # Create parent minion
    parent = await env["create_minion"]("parent", role="Parent")

    # Spawn child with non-existent template
    result = await legion_system.mcp_tools._handle_spawn_minion({
        "_parent_overseer_id": parent.session_id,
        "name": "child",
        "role": "Worker",
        "system_prompt": "Test child with invalid template.",
        "template_name": "nonexistent_template_12345"
    })

    # Verify error response
    assert result.get("is_error") is True
    error_text = result["content"][0]["text"]
    assert "template" in error_text.lower() and ("not found" in error_text.lower() or "does not exist" in error_text.lower())


# ============================================================================
# dispose_minion Tests
# ============================================================================

@pytest.mark.asyncio
async def test_dispose_minion_direct_child(legion_test_env):
    """
    Test dispose_minion for direct child.

    Verifies:
    - Child session terminated (TERMINATED state)
    - Parent's child_minion_ids updated (child removed)
    - No DISPOSE comm persisted (issue #2259)
    """
    env = legion_test_env
    legion_system = env["legion_system"]
    template_manager = env["template_manager"]

    # Create parent and child
    parent = await env["create_minion"]("parent", role="Parent")

    templates = await template_manager.list_templates()
    if not templates:
        pytest.skip("No templates available for testing")

    spawn_result = await legion_system.mcp_tools._handle_spawn_minion({
        "_parent_overseer_id": parent.session_id,
        "name": "child",
        "role": "Worker",
        "system_prompt": "Test child for disposal.",
        "template_name": templates[0].name,
    })
    assert spawn_result.get("is_error") is not True

    # Extract child_id
    response_text = spawn_result["content"][0]["text"]
    import re
    minion_id_match = re.search(r'Minion ID:\s+([a-f0-9-]+)', response_text)
    assert minion_id_match is not None, f"Response should contain Minion ID. Got: {response_text}"
    child_id = minion_id_match.group(1)

    # Wait for child to become ACTIVE
    import asyncio
    import time
    max_wait = 50.0
    poll_interval = 0.1
    start_time = time.time()

    while True:
        elapsed = time.time() - start_time
        if elapsed >= max_wait:
            raise TimeoutError(f"Child session did not become ACTIVE within {max_wait}s")

        child_info = await env["session_coordinator"].session_manager.get_session_info(child_id)
        if child_info.state == SessionState.ACTIVE:
            print(f"  > Child session became ACTIVE in {elapsed:.2f}s")
            break

        await asyncio.sleep(poll_interval)

    # Dispose child via MCP tool handler (hard delete to remove from parent's child_minion_ids)
    with _capture_persisted_comms(legion_system.comm_router) as persisted_comms:
        dispose_result = await legion_system.mcp_tools._handle_dispose_minion({
            "_parent_overseer_id": parent.session_id,
            "minion_name": "child",
            "reason": "Task completed",
            "delete": True
        })

    # Verify success
    assert dispose_result.get("is_error") is not True, f"Unexpected error: {dispose_result['content'][0]['text']}"

    # SIDE EFFECT 1: Child session deleted (hard delete removes session entirely)
    child_final = await env["session_coordinator"].session_manager.get_session_info(child_id)
    assert child_final is None, "Hard-deleted session should no longer exist"

    # SIDE EFFECT 2: Parent's child_minion_ids updated
    parent_updated = await env["session_coordinator"].session_manager.get_session_info(parent.session_id)
    assert child_id not in parent_updated.child_minion_ids

    # SIDE EFFECT 3 (issue #2259): no DISPOSE comm persisted — there's no
    # to-user delivery channel for one to go to anymore.
    dispose_comms = [c for c in persisted_comms if c.comm_type.value == "dispose"]
    assert dispose_comms == [], f"Expected no DISPOSE comm; got {dispose_comms}"


@pytest.mark.asyncio
async def test_dispose_minion_with_descendants(legion_test_env):
    """
    Test dispose_minion with descendants (recursive disposal).

    Verifies:
    - All descendants terminated
    - Response includes descendants_count
    """
    env = legion_test_env
    legion_system = env["legion_system"]
    template_manager = env["template_manager"]

    # Create parent, child, and grandchild
    parent = await env["create_minion"]("parent", role="Parent")

    templates = await template_manager.list_templates()
    if not templates:
        pytest.skip("No templates available for testing")
    template_name = templates[0].name

    # Spawn child
    child_result = await legion_system.mcp_tools._handle_spawn_minion({
        "_parent_overseer_id": parent.session_id,
        "name": "child",
        "role": "Worker",
        "system_prompt": "Test child for recursive disposal.",
        "template_name": template_name,
    })
    assert child_result.get("is_error") is not True

    response_text = child_result["content"][0]["text"]
    import re
    minion_id_match = re.search(r'Minion ID:\s+([a-f0-9-]+)', response_text)
    assert minion_id_match is not None, f"Response should contain Minion ID. Got: {response_text}"
    child_id = minion_id_match.group(1)

    # Wait for child to become ACTIVE
    import asyncio
    import time
    max_wait = 50.0
    poll_interval = 0.1
    start_time = time.time()

    while True:
        elapsed = time.time() - start_time
        if elapsed >= max_wait:
            raise TimeoutError(f"Child session did not become ACTIVE within {max_wait}s")

        child_info = await env["session_coordinator"].session_manager.get_session_info(child_id)
        if child_info.state == SessionState.ACTIVE:
            print(f"  > Child session became ACTIVE in {elapsed:.2f}s")
            break

        await asyncio.sleep(poll_interval)

    # Spawn grandchild from child
    grandchild_result = await legion_system.mcp_tools._handle_spawn_minion({
        "_parent_overseer_id": child_id,
        "name": "grandchild",
        "role": "Sub-worker",
        "system_prompt": "Test grandchild for recursive disposal.",
        "template_name": template_name,
    })
    assert grandchild_result.get("is_error") is not True

    response_text = grandchild_result["content"][0]["text"]
    minion_id_match = re.search(r'Minion ID:\s+([a-f0-9-]+)', response_text)
    assert minion_id_match is not None, f"Response should contain Minion ID. Got: {response_text}"
    grandchild_id = minion_id_match.group(1)

    # Wait for grandchild to become ACTIVE
    start_time = time.time()
    while True:
        elapsed = time.time() - start_time
        if elapsed >= max_wait:
            raise TimeoutError(f"Grandchild session did not become ACTIVE within {max_wait}s")

        grandchild_info = await env["session_coordinator"].session_manager.get_session_info(grandchild_id)
        if grandchild_info.state == SessionState.ACTIVE:
            print(f"  > Grandchild session became ACTIVE in {elapsed:.2f}s")
            break

        await asyncio.sleep(poll_interval)

    # Dispose child (should recursively dispose grandchild)
    dispose_result = await legion_system.mcp_tools._handle_dispose_minion({
        "_parent_overseer_id": parent.session_id,
        "minion_name": "child",
        "reason": "Cleanup"
    })

    # Verify success
    assert dispose_result.get("is_error") is not True, f"Unexpected error: {dispose_result['content'][0]['text']}"

    # SIDE EFFECT 1: Both child and grandchild terminated
    child_final = await env["session_coordinator"].session_manager.get_session_info(child_id)
    assert child_final.state == SessionState.TERMINATED

    grandchild_final = await env["session_coordinator"].session_manager.get_session_info(grandchild_id)
    assert grandchild_final.state == SessionState.TERMINATED

    # SIDE EFFECT 2: Response includes descendants_count
    response_text = dispose_result["content"][0]["text"]
    # Response should mention recursive disposal
    assert "grandchild" in response_text.lower() or "descendant" in response_text.lower() or "recursive" in response_text.lower()

    # SIDE EFFECT 3 (issue #2259 T3): disposed_at is stamped on both the
    # soft-disposed child and grandchild, and surfaces on the hierarchy entry.
    assert child_final.disposed_at is not None
    assert grandchild_final.disposed_at is not None

    legion_id = env["legion_id"]
    hierarchy = await legion_system.legion_coordinator.assemble_minion_hierarchy(legion_id)
    parent_node = next(n for n in hierarchy["children"] if n["id"] == parent.session_id)
    child_node = next(n for n in parent_node["children"] if n["id"] == child_id)
    assert child_node["disposed_at"] is not None


@pytest.mark.asyncio
async def test_dispose_minion_error_nonexistent_child(legion_test_env):
    """
    Test dispose_minion error: non-existent child.

    Verifies:
    - Tool returns error for non-existent child name
    """
    env = legion_test_env
    legion_system = env["legion_system"]

    # Create parent (no children)
    parent = await env["create_minion"]("parent", role="Parent")

    # Try to dispose non-existent child
    result = await legion_system.mcp_tools._handle_dispose_minion({
        "_parent_overseer_id": parent.session_id,
        "minion_name": "nonexistent_child",
        "reason": "Test"
    })

    # Verify error response
    assert result.get("is_error") is True
    error_text = result["content"][0]["text"]
    # Error message should mention the minion was not found
    assert ("found in your subtree" in error_text.lower() or
            "no minion with name" in error_text.lower() or
            "not found" in error_text.lower())


@pytest.mark.asyncio
async def test_dispose_minion_error_not_your_child(legion_test_env):
    """
    Test dispose_minion error: permission denied (not your child).

    Verifies:
    - Tool returns error when trying to dispose another minion's child
    """
    env = legion_test_env
    legion_system = env["legion_system"]
    template_manager = env["template_manager"]

    # Create parent1 and child
    parent1 = await env["create_minion"]("parent1", role="Parent 1")

    templates = await template_manager.list_templates()
    if not templates:
        pytest.skip("No templates available for testing")

    spawn_result = await legion_system.mcp_tools._handle_spawn_minion({
        "_parent_overseer_id": parent1.session_id,
        "name": "child",
        "role": "Worker",
        "system_prompt": "Test child for permission test.",
        "template_name": templates[0].name,
    })
    assert spawn_result.get("is_error") is not True

    # Wait for child to become ACTIVE
    import asyncio
    import time
    max_wait = 50.0
    poll_interval = 0.1
    start_time = time.time()

    response_text = spawn_result["content"][0]["text"]
    import re
    minion_id_match = re.search(r'Minion ID:\s+([a-f0-9-]+)', response_text)
    assert minion_id_match is not None, f"Response should contain Minion ID. Got: {response_text}"
    child_id = minion_id_match.group(1)

    while True:
        elapsed = time.time() - start_time
        if elapsed >= max_wait:
            raise TimeoutError(f"Child session did not become ACTIVE within {max_wait}s")

        child_info = await env["session_coordinator"].session_manager.get_session_info(child_id)
        if child_info.state == SessionState.ACTIVE:
            print(f"  > Child session became ACTIVE in {elapsed:.2f}s")
            break

        await asyncio.sleep(poll_interval)

    # Create parent2 (unrelated)
    parent2 = await env["create_minion"]("parent2", role="Parent 2")

    # Try to dispose child from parent2 (should fail - not your child)
    result = await legion_system.mcp_tools._handle_dispose_minion({
        "_parent_overseer_id": parent2.session_id,
        "minion_name": "child",
        "reason": "Unauthorized"
    })

    # Verify error response
    assert result.get("is_error") is True
    error_text = result["content"][0]["text"]
    # Error should indicate the minion is not their child (permission denied)
    assert ("found in your subtree" in error_text.lower() or
            "no minion with name" in error_text.lower() or
            "not your child" in error_text.lower() or
            "permission denied" in error_text.lower())
