"""
LegionSystem - Central dependency injection container for Legion multi-agent system.

This module provides the LegionSystem dataclass which acts as a service locator
and dependency container to break circular dependencies between Legion components.

Architecture Pattern:
- All Legion components receive LegionSystem in their __init__
- Components access peers via self.system.component_name
- Single initialization point in LegionSystem.__post_init__
- Easy to test (mock LegionSystem)
"""

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from src.data_storage import DataStorageManager
    from src.legion.archive_manager import ArchiveManager
    from src.legion.comm_router import CommRouter
    from src.legion.legion_coordinator import LegionCoordinator
    from src.legion.mcp.legion_mcp_tools import LegionMCPTools
    from src.legion.memory_manager import MemoryManager
    from src.legion.overseer_controller import OverseerController
    from src.session_coordinator import SessionCoordinator
    from src.template_manager import TemplateManager


@dataclass
class LegionSystem:
    """
    Central context container for all Legion components.
    Breaks circular dependencies by providing single initialization point.

    Usage:
        # Create system with existing infrastructure
        legion_system = LegionSystem(
            session_coordinator=session_coordinator,
            data_storage_manager=data_storage_manager
        )
        # All Legion components are automatically wired in __post_init__

        # Components access each other via system
        legion_system.legion_coordinator.create_legion(...)
        legion_system.overseer_controller.spawn_minion(...)
    """

    # Existing infrastructure (injected at creation)
    session_coordinator: 'SessionCoordinator'
    data_storage_manager: 'DataStorageManager'
    template_manager: 'TemplateManager'
    ui_websocket_manager: Any | None = None  # UIWebSocketManager (optional, for broadcasting project updates)

    # Permission callback factory (injected after creation by web_server via session_coordinator)
    # Allows legion components to create permission callbacks for spawned minions
    permission_callback_factory: Callable[[str], Callable] | None = None

    # Message callback registrar (injected after creation by web_server via session_coordinator)
    # Allows legion components to register WebSocket message callbacks for spawned minions
    message_callback_registrar: Callable[[str], None] | None = None

    # Legion components (initialized in __post_init__)
    legion_coordinator: 'LegionCoordinator' = field(init=False)
    overseer_controller: 'OverseerController' = field(init=False)
    comm_router: 'CommRouter' = field(init=False)
    memory_manager: 'MemoryManager' = field(init=False)
    archive_manager: 'ArchiveManager' = field(init=False)
    mcp_tools: 'LegionMCPTools' = field(init=False)

    def __post_init__(self):
        """
        Wire up all Legion components with reference to this system.

        Order matters: components with fewer dependencies first.
        """
        # Import here to avoid circular imports at module level
        from src.legion.archive_manager import ArchiveManager
        from src.legion.comm_router import CommRouter
        from src.legion.legion_coordinator import LegionCoordinator
        from src.legion.mcp.legion_mcp_tools import LegionMCPTools
        from src.legion.memory_manager import MemoryManager
        from src.legion.overseer_controller import OverseerController

        # Initialize components in dependency order
        # Lower-level components first (fewer dependencies)
        self.comm_router = CommRouter(self)
        self.memory_manager = MemoryManager(self)
        self.archive_manager = ArchiveManager(self)
        self.overseer_controller = OverseerController(self)

        # Higher-level components (depend on above)
        self.legion_coordinator = LegionCoordinator(self)
        self.mcp_tools = LegionMCPTools(self)

        # Register comm buffer flush on processing completion
        self.session_coordinator.session_manager.add_processing_complete_callback(
            self.comm_router.flush_buffered_comms
        )
