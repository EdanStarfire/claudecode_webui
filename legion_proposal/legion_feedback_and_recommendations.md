# Legion Architecture: Feedback and Recommendations

## 1. Introduction

This document provides feedback and concrete recommendations based on a review of the Legion system design documents and our subsequent discussion. The proposals aim to address potential architectural challenges and solidify plans for the MVP, focusing on simplicity, reliability, and extensibility.

---

## 2. Managing System Complexity with a Dependency Injection Strategy

**Observation**: The proposed architecture has several components with circular dependencies (e.g., `LegionCoordinator` depends on `OverseerController`, which in turn needs to coordinate with other systems managed by the coordinator). This can lead to issues with initialization order, testing, and maintenance.

**Recommendation**: Implement a "context" object that holds all major system components. This object is created at startup and passed to the components that need it, acting as a central service locator and breaking circular dependencies at the constructor level.

### Example Implementation

1.  **Define a `LegionSystem` context class:**

    ```python
    # In a new file, e.g., src/legion_system.py
    from dataclasses import dataclass, field
    from typing import TYPE_CHECKING

    # Forward-declare classes to handle circular type hints
    if TYPE_CHECKING:
        from .session_coordinator import SessionCoordinator
        from .data_storage import DataStorageManager
        from .legion_coordinator import LegionCoordinator
        from .overseer_controller import OverseerController
        from .channel_manager import ChannelManager
        from .comm_router import CommRouter
        from .mcp_tools import LegionMCPTools

    @dataclass
    class LegionSystem:
        """A container for all major system components to resolve circular dependencies."""
        # Existing components, loaded at startup
        session_coordinator: 'SessionCoordinator'
        data_storage_manager: 'DataStorageManager'

        # New components are initialized after the main system object is created
        legion_coordinator: 'LegionCoordinator' = field(init=False)
        overseer_controller: 'OverseerController' = field(init=False)
        channel_manager: 'ChannelManager' = field(init=False)
        comm_router: 'CommRouter' = field(init=False)
        mcp_tools: 'LegionMCPTools' = field(init=False)

        def __post_init__(self):
            """Instantiate and wire up all the new components."""
            # Pass 'self' (the LegionSystem instance) to the constructors
            self.comm_router = CommRouter(self)
            self.channel_manager = ChannelManager(self)
            self.overseer_controller = OverseerController(self)
            self.legion_coordinator = LegionCoordinator(self)
            self.mcp_tools = LegionMCPTools(self)
    ```

2.  **Components receive the `LegionSystem` object:**

    ```python
    # e.g., in src/overseer_controller.py
    class OverseerController:
        def __init__(self, system: 'LegionSystem'):
            self.system = system

        async def spawn_minion(self, ...):
            # Access other components via the system object
            session_id = await self.system.session_coordinator.create_session(...)
            await self.system.comm_router.route_comm(...)
    ```

### Benefits of this Approach
-   **Resolves Circular Dependencies**: Components are no longer dependent on each other in their constructors.
-   **Centralized Wiring**: The relationship between components is defined in one place (`LegionSystem`), making the architecture easier to understand.
-   **Improved Testability**: It's easier to mock dependencies by creating a `LegionSystem` with mock components during testing.

---

## 3. Improving Communication Reliability with Explicit Tagging

**Observation**: The system relies on minion names being unique for natural language referencing. This can be brittle if a minion's output accidentally contains a word that is also a minion's name.

**Recommendation**: Adopt a simple, explicit tagging syntax for referencing minions and channels within `Comm` content. This makes parsing unambiguous and more reliable.

### Proposed Syntax
-   **Minions**: `#minion-name`
-   **Channels**: `#channel-name`

### Example
A `Comm` content string would look like this:
> "Ok, I've completed the analysis. I'm sending the report to the #implementation-planning channel. #sso-expert, can you please review the authentication section?"

### Benefits
-   **Unambiguous**: The `CommRouter` or UI can reliably parse these tags to identify references without complex NLP.
-   **Low Overhead**: This is a simple string-parsing task that doesn't add significant processing overhead.
-   **Extensible**: The pattern is clear and can be used for other reference types in the future.
-   **User-Friendly**: The syntax is familiar from many existing platforms.

---

## 4. A Pragmatic Approach to Capability Discovery for the MVP

**Observation**: There's a tension between the simplicity of a gossip protocol and the potential maintenance overhead and search limitations of a central registry.

**Recommendation**: For the MVP, start with a simple, keyword-based central registry. This approach is easier to implement, more efficient for a small number of minions, and provides a solid foundation for more advanced search capabilities later.

### Proposed MVP Implementation
1.  **Central Registry**: `LegionCoordinator` maintains a simple dictionary mapping `minion_id` to a list of capabilities.
    ```python
    # In LegionCoordinator
    self.capability_registry: Dict[str, List[str]] = {}
    ```
2.  **Capability Declaration**: Minions declare their capabilities as a list of keywords or short phrases (e.g., `["database_migration", "oauth_expert", "python_scripting"]`). This can be part of the `MinionInfo` model.
3.  **Simple Search**: The `search_capability` MCP tool performs a case-insensitive keyword match against the values in the registry.

### Addressing Future Growth
Your concern about querying a structured registry versus asking a plain-text question is valid. The keyword-based MVP registry can evolve into a more advanced system.

**Future Iteration Idea**:
> An LLM could be used as a "query planner" for the registry. A minion could ask a natural language question like, "Who knows about security tokens?" The `search_capability` tool handler would then use an LLM to translate this into a set of keywords (e.g., `["oauth", "jwt", "saml", "security_tokens"]`) to query against the simple registry.

This hybrid approach gives you the best of both worlds: the reliability of a structured registry and the flexibility of natural language search, without the high communication overhead of a pure gossip protocol.
