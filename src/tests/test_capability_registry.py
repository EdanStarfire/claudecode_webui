"""
Tests for capability registry functionality in LegionCoordinator.

Tests cover:
- Capability registration on minion creation
- Case normalization and validation
- Duplicate capability handling (score update)
- Capability search with ranking
- Empty search handling
- Invalid capability format rejection
- MCP tool search_capability
"""

from unittest.mock import AsyncMock, Mock

import pytest

from src.legion.legion_coordinator import LegionCoordinator
from src.legion.mcp.legion_mcp_tools import LegionMCPTools
from src.session_manager import SessionInfo, SessionState


@pytest.fixture
def sample_minion_1():
    """Create a sample minion SessionInfo (DatabaseExpert). Issue #349: is_minion removed."""
    mock = Mock(spec=SessionInfo)
    mock.session_id = "minion-db-123"
    mock.name = "DatabaseExpert"
    mock.project_id = "legion-456"
    mock.role = "Database architecture and schema design"
    mock.state = SessionState.ACTIVE
    mock.capabilities = ["postgresql", "database_design", "sql_optimization"]
    mock.expertise_score = 0.9
    return mock


@pytest.fixture
def sample_minion_2():
    """Create a sample minion SessionInfo (BackendDev). Issue #349: is_minion removed."""
    mock = Mock(spec=SessionInfo)
    mock.session_id = "minion-backend-456"
    mock.name = "BackendDev"
    mock.project_id = "legion-456"
    mock.role = "Python backend development"
    mock.state = SessionState.ACTIVE
    mock.capabilities = ["python", "fastapi", "database"]
    mock.expertise_score = 0.6
    return mock


@pytest.fixture
def sample_minion_3():
    """Create a sample minion SessionInfo (FrontendDev). Issue #349: is_minion removed."""
    mock = Mock(spec=SessionInfo)
    mock.session_id = "minion-frontend-789"
    mock.name = "FrontendDev"
    mock.project_id = "legion-456"
    mock.role = "Vue.js frontend development"
    mock.state = SessionState.ACTIVE
    mock.capabilities = ["vue", "javascript", "frontend"]
    mock.expertise_score = 0.7
    return mock


@pytest.fixture
def sample_minion_zero_score():
    """Create a sample minion with zero expertise score. Issue #349: is_minion removed."""
    mock = Mock(spec=SessionInfo)
    mock.session_id = "minion-zero-999"
    mock.name = "ZeroScoreMinion"
    mock.project_id = "legion-456"
    mock.role = "Unproven minion"
    mock.state = SessionState.ACTIVE
    mock.capabilities = ["python"]
    mock.expertise_score = 0.0
    return mock


@pytest.fixture
def mock_legion_system(sample_minion_1, sample_minion_2, sample_minion_3, sample_minion_zero_score):
    """Create mock LegionSystem with session coordinator."""
    system = Mock()
    system.session_coordinator = Mock()
    system.session_coordinator.session_manager = Mock()

    # Mock get_session_info to return sample minions
    async def mock_get_session_info(session_id):
        minion_map = {
            "minion-db-123": sample_minion_1,
            "minion-backend-456": sample_minion_2,
            "minion-frontend-789": sample_minion_3,
            "minion-zero-999": sample_minion_zero_score
        }
        return minion_map.get(session_id)

    system.session_coordinator.session_manager.get_session_info = AsyncMock(side_effect=mock_get_session_info)

    return system


@pytest.fixture
def legion_coordinator(mock_legion_system):
    """Create LegionCoordinator with mocked dependencies."""
    coordinator = LegionCoordinator(mock_legion_system)

    # Mock get_minion_info to delegate to session_manager (issue #349: all sessions are minions)
    async def mock_get_minion_info(minion_id):
        return await mock_legion_system.session_coordinator.session_manager.get_session_info(minion_id)

    coordinator.get_minion_info = AsyncMock(side_effect=mock_get_minion_info)

    return coordinator


@pytest.fixture
def mcp_tools(mock_legion_system):
    """Create LegionMCPTools with mocked dependencies."""
    return LegionMCPTools(mock_legion_system)


# ========================
# Capability Registration Tests
# ========================

@pytest.mark.asyncio
async def test_register_capability_valid(legion_coordinator, sample_minion_1):
    """Test registering a valid capability."""
    await legion_coordinator.register_capability(
        minion_id="minion-db-123",
        capability="postgresql",
        expertise_score=0.9
    )

    # Verify capability was registered
    assert "postgresql" in legion_coordinator.capability_registry
    assert ("minion-db-123", 0.9) in legion_coordinator.capability_registry["postgresql"]


@pytest.mark.asyncio
async def test_register_capability_case_normalization(legion_coordinator, sample_minion_1):
    """Test that capability names are normalized to lowercase."""
    await legion_coordinator.register_capability(
        minion_id="minion-db-123",
        capability="PostgreSQL",  # Mixed case input
        expertise_score=0.9
    )

    # Verify normalized to lowercase
    assert "postgresql" in legion_coordinator.capability_registry
    assert "PostgreSQL" not in legion_coordinator.capability_registry


@pytest.mark.asyncio
async def test_register_capability_default_score(legion_coordinator, sample_minion_1):
    """Test that default expertise score is used when not provided."""
    await legion_coordinator.register_capability(
        minion_id="minion-db-123",
        capability="database_design",
        expertise_score=None  # Use minion's default score
    )

    # Verify minion's default score (0.9) was used
    assert ("minion-db-123", 0.9) in legion_coordinator.capability_registry["database_design"]


@pytest.mark.asyncio
async def test_register_capability_duplicate_updates_score(legion_coordinator, sample_minion_1):
    """Test that registering same minion+capability updates the score."""
    # Register initially
    await legion_coordinator.register_capability(
        minion_id="minion-db-123",
        capability="postgresql",
        expertise_score=0.5
    )

    # Register again with different score
    await legion_coordinator.register_capability(
        minion_id="minion-db-123",
        capability="postgresql",
        expertise_score=0.9
    )

    # Verify only one entry exists with updated score
    entries = legion_coordinator.capability_registry["postgresql"]
    db_entries = [e for e in entries if e[0] == "minion-db-123"]
    assert len(db_entries) == 1
    assert db_entries[0][1] == 0.9


@pytest.mark.asyncio
async def test_register_capability_invalid_format_rejected(legion_coordinator, sample_minion_1):
    """Test that capabilities with invalid characters are rejected."""
    # Test special characters
    with pytest.raises(ValueError, match="Invalid capability format"):
        await legion_coordinator.register_capability(
            minion_id="minion-db-123",
            capability="database-design",  # Hyphen not allowed
            expertise_score=0.9
        )

    # Test spaces
    with pytest.raises(ValueError, match="Invalid capability format"):
        await legion_coordinator.register_capability(
            minion_id="minion-db-123",
            capability="database design",  # Space not allowed
            expertise_score=0.9
        )


@pytest.mark.asyncio
async def test_register_capability_nonexistent_minion(legion_coordinator):
    """Test that registering capability for non-existent minion raises error."""
    with pytest.raises(ValueError, match="does not exist"):
        await legion_coordinator.register_capability(
            minion_id="nonexistent-minion",
            capability="python",
            expertise_score=0.5
        )


@pytest.mark.asyncio
async def test_register_capability_invalid_score_range(legion_coordinator, sample_minion_1):
    """Test that expertise scores outside 0.0-1.0 range are rejected."""
    with pytest.raises(ValueError, match="must be between 0.0 and 1.0"):
        await legion_coordinator.register_capability(
            minion_id="minion-db-123",
            capability="postgresql",
            expertise_score=1.5  # Invalid: > 1.0
        )

    with pytest.raises(ValueError, match="must be between 0.0 and 1.0"):
        await legion_coordinator.register_capability(
            minion_id="minion-db-123",
            capability="postgresql",
            expertise_score=-0.1  # Invalid: < 0.0
        )


# ========================
# Capability Search Tests
# ========================

@pytest.mark.asyncio
async def test_search_capability_exact_match(legion_coordinator, sample_minion_1):
    """Test searching for exact capability match."""
    # Register capability
    await legion_coordinator.register_capability(
        minion_id="minion-db-123",
        capability="postgresql",
        expertise_score=0.9
    )

    # Search for exact match
    results = await legion_coordinator.search_capability_registry(keyword="postgresql")

    assert len(results) == 1
    assert results[0] == ("minion-db-123", 0.9, "postgresql")


@pytest.mark.asyncio
async def test_search_capability_case_insensitive(legion_coordinator, sample_minion_1):
    """Test that search is case-insensitive."""
    # Register lowercase capability
    await legion_coordinator.register_capability(
        minion_id="minion-db-123",
        capability="postgresql",
        expertise_score=0.9
    )

    # Search with uppercase
    results = await legion_coordinator.search_capability_registry(keyword="PostgreSQL")

    assert len(results) == 1
    assert results[0][0] == "minion-db-123"


@pytest.mark.asyncio
async def test_search_capability_substring_match(legion_coordinator, sample_minion_1, sample_minion_2):
    """Test that search performs substring matching."""
    # Register capabilities containing "database"
    await legion_coordinator.register_capability(
        minion_id="minion-db-123",
        capability="database_design",
        expertise_score=0.9
    )
    await legion_coordinator.register_capability(
        minion_id="minion-backend-456",
        capability="database",
        expertise_score=0.6
    )

    # Search for "database" (should match both)
    results = await legion_coordinator.search_capability_registry(keyword="database")

    assert len(results) == 2
    # Verify ranked by expertise (highest first)
    assert results[0][1] > results[1][1]


@pytest.mark.asyncio
async def test_search_capability_ranked_by_expertise(legion_coordinator, sample_minion_1, sample_minion_2):
    """Test that results are ranked by expertise score (highest first)."""
    # Register same capability with different scores
    await legion_coordinator.register_capability(
        minion_id="minion-db-123",
        capability="python",
        expertise_score=0.9  # Higher score
    )
    await legion_coordinator.register_capability(
        minion_id="minion-backend-456",
        capability="python",
        expertise_score=0.5  # Lower score
    )

    # Search
    results = await legion_coordinator.search_capability_registry(keyword="python")

    # Verify order: highest score first
    assert len(results) == 2
    assert results[0][0] == "minion-db-123"
    assert results[0][1] == 0.9
    assert results[1][0] == "minion-backend-456"
    assert results[1][1] == 0.5


@pytest.mark.asyncio
async def test_search_capability_excludes_zero_scores(legion_coordinator, sample_minion_1, sample_minion_zero_score):
    """Test that minions with zero expertise score are excluded from results."""
    # Register capabilities
    await legion_coordinator.register_capability(
        minion_id="minion-db-123",
        capability="python",
        expertise_score=0.9
    )
    await legion_coordinator.register_capability(
        minion_id="minion-zero-999",
        capability="python",
        expertise_score=0.0  # Zero score - should be excluded
    )

    # Search
    results = await legion_coordinator.search_capability_registry(keyword="python")

    # Verify zero-score minion is not in results
    assert len(results) == 1
    assert results[0][0] == "minion-db-123"


@pytest.mark.asyncio
async def test_search_capability_no_matches(legion_coordinator):
    """Test searching for capability with no matches returns empty list."""
    results = await legion_coordinator.search_capability_registry(keyword="nonexistent")

    assert len(results) == 0


@pytest.mark.asyncio
async def test_search_capability_empty_keyword_rejected(legion_coordinator):
    """Test that empty search keyword raises error."""
    with pytest.raises(ValueError, match="cannot be empty"):
        await legion_coordinator.search_capability_registry(keyword="")

    with pytest.raises(ValueError, match="cannot be empty"):
        await legion_coordinator.search_capability_registry(keyword="   ")  # Whitespace only


# ========================
# MCP Tool search_capability Tests
# ========================

@pytest.mark.asyncio
async def test_mcp_search_capability_returns_formatted_text(mcp_tools, legion_coordinator, sample_minion_1):
    """Test that MCP tool returns formatted text list."""
    # Register capability
    await legion_coordinator.register_capability(
        minion_id="minion-db-123",
        capability="postgresql",
        expertise_score=0.9
    )

    # Mock get_minion_info in mcp_tools.system.legion_coordinator
    mcp_tools.system.legion_coordinator = legion_coordinator

    # Call MCP tool
    result = await mcp_tools._handle_search_capability({"capability": "postgresql"})

    # Verify formatted text response
    assert result["is_error"] is False
    assert len(result["content"]) == 1
    assert "DatabaseExpert" in result["content"][0]["text"]
    assert "90%" in result["content"][0]["text"]  # 0.9 as percentage


@pytest.mark.asyncio
async def test_mcp_search_capability_empty_results(mcp_tools, legion_coordinator):
    """Test MCP tool with no matching results."""
    mcp_tools.system.legion_coordinator = legion_coordinator

    result = await mcp_tools._handle_search_capability({"capability": "nonexistent"})

    # Verify no error, but empty results message
    assert result["is_error"] is False
    assert "No minions found" in result["content"][0]["text"]


@pytest.mark.asyncio
async def test_mcp_search_capability_missing_parameter(mcp_tools):
    """Test MCP tool with missing capability parameter."""
    result = await mcp_tools._handle_search_capability({})

    assert result["is_error"] is True
    assert "required" in result["content"][0]["text"]


@pytest.mark.asyncio
async def test_mcp_search_capability_empty_parameter(mcp_tools):
    """Test MCP tool with empty capability parameter."""
    result = await mcp_tools._handle_search_capability({"capability": ""})

    assert result["is_error"] is True
    assert "required" in result["content"][0]["text"]


# ========================
# Integration Test: Full Workflow
# ========================

@pytest.mark.asyncio
async def test_full_capability_workflow(legion_coordinator, sample_minion_1, sample_minion_2, sample_minion_3):
    """Test complete workflow: register multiple capabilities and search."""
    # Register capabilities for multiple minions
    await legion_coordinator.register_capability("minion-db-123", "postgresql", 0.9)
    await legion_coordinator.register_capability("minion-db-123", "database_design", 0.8)
    await legion_coordinator.register_capability("minion-backend-456", "python", 0.7)
    await legion_coordinator.register_capability("minion-backend-456", "database", 0.6)
    await legion_coordinator.register_capability("minion-frontend-789", "vue", 0.8)

    # Search for "database" - should find 2 minions
    results = await legion_coordinator.search_capability_registry(keyword="database")

    # Verify results
    assert len(results) == 2
    # DatabaseExpert should be first (higher score 0.9 vs BackendDev's 0.6)
    assert results[0][0] == "minion-db-123"
    assert results[1][0] == "minion-backend-456"

    # Search for "python" - should find 1 minion
    python_results = await legion_coordinator.search_capability_registry(keyword="python")
    assert len(python_results) == 1
    assert python_results[0][0] == "minion-backend-456"
