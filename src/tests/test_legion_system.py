"""
Tests for LegionSystem dependency injection container.
"""

from unittest.mock import Mock

from src.legion_system import LegionSystem


def test_legion_system_initialization():
    """Test that LegionSystem initializes with mocked dependencies."""
    # Create mock infrastructure
    mock_session_coordinator = Mock()
    mock_data_storage = Mock()
    mock_template_manager = Mock()

    # Create LegionSystem
    system = LegionSystem(
        session_coordinator=mock_session_coordinator,
        data_storage_manager=mock_data_storage,
        template_manager=mock_template_manager
    )

    # Verify infrastructure was injected
    assert system.session_coordinator == mock_session_coordinator
    assert system.data_storage_manager == mock_data_storage

    # Verify all Legion components were created
    assert system.comm_router is not None
    assert system.memory_manager is not None
    assert system.overseer_controller is not None
    assert system.legion_coordinator is not None
    assert system.mcp_tools is not None


def test_legion_components_have_system_reference():
    """Test that all components have reference to LegionSystem."""
    # Create LegionSystem with mocks
    system = LegionSystem(
        session_coordinator=Mock(),
        data_storage_manager=Mock(),
        template_manager=Mock()
    )

    # Verify each component has system reference
    assert system.comm_router.system == system
    assert system.memory_manager.system == system
    assert system.overseer_controller.system == system
    assert system.legion_coordinator.system == system
    assert system.mcp_tools.system == system


def test_legion_coordinator_has_capability_registry():
    """Test that LegionCoordinator initializes capability registry."""
    system = LegionSystem(
        session_coordinator=Mock(),
        data_storage_manager=Mock(),
        template_manager=Mock()
    )

    # Verify capability registry exists and is empty
    assert hasattr(system.legion_coordinator, 'capability_registry')
    assert system.legion_coordinator.capability_registry == {}


def test_legion_coordinator_has_state_dicts():
    """Test that LegionCoordinator initializes state dictionaries."""
    system = LegionSystem(
        session_coordinator=Mock(),
        data_storage_manager=Mock(),
        template_manager=Mock()
    )

    # Verify Legion-specific state dictionaries exist and are empty
    # Note: legions/minions are managed in ProjectManager/SessionManager
    assert hasattr(system.legion_coordinator, 'capability_registry')
    assert system.legion_coordinator.capability_registry == {}
