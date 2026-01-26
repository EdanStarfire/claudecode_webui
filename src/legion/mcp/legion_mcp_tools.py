"""
LegionMCPTools - MCP tool definitions for Legion multi-agent capabilities.

This module provides MCP tools that minions can use for:
- Communication (send_comm - direct minion-to-minion)
- Lifecycle (spawn_minion, dispose_minion)
- Discovery (search_capability, list_minions, get_minion_info)
- Expertise (update_expertise)

All minion SDK sessions receive these tools on creation via MCP server.

Implementation uses Claude Agent SDK's @tool decorator and create_sdk_mcp_server.
Tools are exposed to minions with names like: mcp__legion__send_comm
"""

from typing import TYPE_CHECKING, Any

try:
    from claude_agent_sdk import create_sdk_mcp_server, tool
except ImportError:
    # For testing environments without SDK
    tool = None
    create_sdk_mcp_server = None

if TYPE_CHECKING:
    from src.legion_system import LegionSystem


class LegionMCPTools:
    """
    MCP tools for Legion multi-agent capabilities.
    Creates an MCP server with tools for minion collaboration.
    """

    def __init__(self, system: 'LegionSystem'):
        """
        Initialize LegionMCPTools with LegionSystem.

        Args:
            system: LegionSystem instance for accessing other components
        """
        self.system = system

        # Note: No longer creating a shared MCP server here
        # Each minion session gets its own session-specific server
        # via create_mcp_server_for_session()

    def create_mcp_server_for_session(self, session_id: str):
        """
        Create session-specific MCP server with all Legion tools.

        Args:
            session_id: The session ID to inject into tool calls

        Returns:
            MCP server instance with all tools registered
        """
        # Define all tools with @tool decorator
        # Tools will be named: mcp__legion__send_comm, mcp__legion__spawn_minion, etc.

        @tool(
            "send_comm",
            "Send a communication to another minion. BE CONCISE - only send when necessary "
            "(task complete, blocked/need help, milestone reached, question needing input). "
            "NEVER send acknowledgments ('OK', 'Got it'), goodbyes, or unsolicited comments. "
            "\n\nSummary must be SPECIFIC and actionable (e.g., 'Completed auth.py refactor - 3 tests added', "
            "'Blocked: missing DATABASE_URL'). AVOID generic summaries like 'Status update' or 'Progress report'. "
            "\n\nSTOP when: task acknowledged complete OR awaiting external input (no 'standing by' messages). "
            "\n\nValid comm_type: 'task', 'question', 'report', 'info'"
            "\n\nInterrupt Priority (optional):"
            "\n- 'none' (default): Queue message normally"
            "\n- 'halt': Interrupt target minion immediately (use for critical errors, urgent questions)"
            "\n- 'pivot': Interrupt target and redirect work (message must include explicit 'cease prior work' instructions)"
            "\n\nUse HALT when: critical error/blocker discovered, time-sensitive question needing immediate answer"
            "\nUse PIVOT when: requirements changed, discovered target working on wrong task, need to completely change direction"
            "\nUse NONE when: normal coordination, non-urgent updates, FYI messages (task completion notification)",
            {
                "to_minion_name": str,       # Exact name of target minion (case-sensitive)
                "summary": str,              # Specific one-sentence update (actionable)
                "content": str,              # Details only if summary needs elaboration (supports markdown)
                "comm_type": str,            # One of: task, question, report, info
                "interrupt_priority": str    # Optional: "none", "halt", or "pivot" (default: "none")
            }
        )
        async def send_comm_tool(args: dict[str, Any]) -> dict[str, Any]:
            """Send communication to another minion."""
            # Inject session context
            args["_from_minion_id"] = session_id
            return await self._handle_send_comm(args)

        @tool(
            "spawn_minion",
            "Create a new child minion to delegate specialized work. You become the overseer "
            "of this minion and can later dispose of it when done. The child minion will be a "
            "full Claude agent with access to tools."
            "\n\n**IMPORTANT - Starting Your Minion:**"
            "\nAfter spawning, you MUST send a comm message to the minion to start them working. "
            "The minion will NOT begin work automatically - they wait for explicit task instructions."
            "\n\nWorkflow:"
            "\n1. spawn_minion(name='Helper', template_name='Code Expert', initialization_context='Review auth code')"
            "\n2. send_comm(to_minion_name='Helper', summary='Begin task', content='Task details...', comm_type='task')"
            "\n\n**Using Templates (Recommended):**"
            "\nUse a template to spawn with specific permissions:"
            "\n- First, use list_templates() to see available templates"
            "\n- Then spawn: spawn_minion(name='Helper', template_name='Code Expert', initialization_context='Review auth code')"
            "\n- Template enforces permission_mode and allowed_tools (secure, user-controlled)"
            "\n\n**Without Template:**"
            "\nIf no template specified, child gets default restricted permissions:"
            "\n- permission_mode='default' (prompts for every tool use)"
            "\n- allowed_tools=[] (no pre-authorized tools)"
            "\n\n**Working Directory (Optional):**"
            "\nSpecify a custom working directory for git worktrees or multi-repo workflows:"
            "\n- working_directory='/path/to/worktree' - Use absolute or relative path"
            "\n- If not specified, child inherits parent's working directory",
            {
                "name": str,                           # Unique name for new minion
                "role": str,                           # Human-readable role description
                "initialization_context": str,         # System prompt defining expertise
                "template_name": str,                  # Template to apply for permissions (optional)
                "working_directory": str               # Custom working directory (optional)
            }
        )
        async def spawn_minion_tool(args: dict[str, Any]) -> dict[str, Any]:
            """Spawn a new child minion."""
            # Inject session context (parent overseer ID)
            args["_parent_overseer_id"] = session_id
            return await self._handle_spawn_minion(args)

        @tool(
            "dispose_minion",
            "Terminate a child minion you created when their task is complete. You can only "
            "dispose minions you spawned (your children). Their knowledge will be transferred "
            "to you before termination.",
            {
                "minion_name": str  # Name of child minion to dispose
            }
        )
        async def dispose_minion_tool(args: dict[str, Any]) -> dict[str, Any]:
            """Dispose of a child minion."""
            # Inject session context (parent overseer ID)
            args["_parent_overseer_id"] = session_id
            return await self._handle_dispose_minion(args)

        @tool(
            "search_capability",
            "Find minions with a specific capability by searching the central capability registry. "
            "Returns ranked list of matching minions sorted by expertise scores. Use keywords like "
            "'database', 'oauth', 'authentication', 'payment_processing', etc.",
            {
                "capability": str  # Capability keyword to search for
            }
        )
        async def search_capability_tool(args: dict[str, Any]) -> dict[str, Any]:
            """Search for minions by capability."""
            return await self._handle_search_capability(args)

        @tool(
            "list_minions",
            "Get a list of all active minions in your legion with their names, roles, and "
            "current states. Useful for understanding who's available to collaborate with.",
            {}  # No parameters required
        )
        async def list_minions_tool(args: dict[str, Any]) -> dict[str, Any]:
            """List all active minions."""
            args["_from_minion_id"] = session_id
            return await self._handle_list_minions(args)

        @tool(
            "get_minion_info",
            "Get detailed information about a specific minion in your legion, including their role, capabilities, "
            "current task, and parent/children hierarchy.",
            {
                "minion_name": str  # Name of minion to query
            }
        )
        async def get_minion_info_tool(args: dict[str, Any]) -> dict[str, Any]:
            """Get detailed minion information."""
            args["_from_minion_id"] = session_id
            return await self._handle_get_minion_info(args)

        @tool(
            "list_templates",
            "List all available minion templates with their permission configurations. "
            "Templates are pre-configured permission sets that can be used when spawning "
            "child minions. Use this to discover what types of minions are available and "
            "recommend appropriate templates based on task requirements."
            "\n\nExample:"
            "\n- list_templates() returns all templates"
            "\n- Use with spawn_minion to apply template: spawn_minion(name='Helper', template_name='Code Expert')"
            "\n\nTemplates include permission_mode and allowed_tools for each minion type.",
            {}  # No parameters required
        )
        async def list_templates_tool(args: dict[str, Any]) -> dict[str, Any]:
            """List all minion templates."""
            return await self._handle_list_templates(args)

        @tool(
            "update_expertise",
            "Report a new or improved technical capability. Updates your expertise score "
            "for the capability in the central registry so other minions can discover you. "
            "\n\nExpertise score should be 0.0-1.0 (0.0=no knowledge, 0.5=intermediate, 1.0=expert). "
            "Defaults to 0.5 if not provided."
            "\n\nCapability names must be lowercase with underscores (e.g., 'jwt_authentication', 'postgresql')."
            "\n\nExamples:"
            "\n- update_expertise('jwt_authentication', 0.7)"
            "\n- update_expertise('postgresql', 0.9)"
            "\n- update_expertise('docker')  # Uses default 0.5",
            {
                "capability": str,                    # Capability keyword (lowercase with underscores)
                "expertise_score": float | None       # 0.0-1.0 score (default: 0.5 if None)
            }
        )
        async def update_expertise_tool(args: dict[str, Any]) -> dict[str, Any]:
            """Update minion's expertise for a capability."""
            args["_from_minion_id"] = session_id
            return await self._handle_update_expertise(args)

        # Create and return MCP server with all tools
        return create_sdk_mcp_server(
            name="legion",
            version="1.0.0",
            tools=[
                send_comm_tool,
                spawn_minion_tool,
                dispose_minion_tool,
                search_capability_tool,
                list_minions_tool,
                get_minion_info_tool,
                list_templates_tool,
                update_expertise_tool
            ]
        )

    # Tool Handler Methods - Implementation in Phase 2
    # These are called by the @tool decorated functions above

    async def _handle_send_comm(self, args: dict[str, Any]) -> dict[str, Any]:
        """
        Handle send_comm tool call.

        Args:
            args: {"to_minion_name": str, "content": str, "comm_type": str}

        Returns:
            Tool result with content array
        """
        import uuid

        from src.models.legion_models import Comm, CommType, InterruptPriority

        # Get current minion context (from session_id in SDK context)
        # For now, we'll need to pass this through - placeholder
        from_minion_id = args.get("_from_minion_id")  # Will be injected by SDK wrapper

        if not from_minion_id:
            return {
                "content": [{
                    "type": "text",
                    "text": "Error: Unable to determine sender minion ID"
                }],
                "is_error": True
            }

        # Validate comm_type
        comm_type_str = args.get("comm_type", "task").lower()
        valid_comm_types = ["task", "question", "report", "info"]

        # Map user-facing types to CommType enum values
        comm_type_mapping = {
            "task": "task",
            "question": "question",
            "report": "report",
            "info": "info"  # Maps directly to CommType.INFO
        }

        if comm_type_str not in valid_comm_types:
            return {
                "content": [{
                    "type": "text",
                    "text": f"Error: Invalid comm_type '{comm_type_str}'. Valid values are: {', '.join(valid_comm_types)}"
                }],
                "is_error": True
            }

        # Get the internal enum value
        internal_comm_type = comm_type_mapping[comm_type_str]

        # Validate interrupt_priority (optional)
        interrupt_priority_str = args.get("interrupt_priority", "none").lower()
        valid_priorities = ["none", "halt", "pivot"]

        if interrupt_priority_str not in valid_priorities:
            return {
                "content": [{
                    "type": "text",
                    "text": f"Error: Invalid interrupt_priority '{interrupt_priority_str}'. Valid values are: {', '.join(valid_priorities)}"
                }],
                "is_error": True
            }

        # Map to InterruptPriority enum
        priority_mapping = {
            "none": InterruptPriority.NONE,
            "halt": InterruptPriority.HALT,
            "pivot": InterruptPriority.PIVOT
        }

        interrupt_priority = priority_mapping[interrupt_priority_str]

        # Look up target minion by name
        to_minion_name = args.get("to_minion_name")

        # Check if sending to the special "user" minion
        from src.models.legion_models import SYSTEM_MINION_NAME
        sending_to_user = (to_minion_name == "user")

        # Prevent sending to system (system only sends, never receives)
        if to_minion_name and to_minion_name.lower() == SYSTEM_MINION_NAME:
            return {
                "content": [{
                    "type": "text",
                    "text": f"Error: Cannot send comm to '{SYSTEM_MINION_NAME}'. System is a special identifier for system-generated messages only."
                }],
                "is_error": True
            }

        if sending_to_user:
            to_minion_id = None  # User doesn't have a minion_id, only use to_user flag
        else:
            # Get sender's legion to scope lookup
            sender_session = await self.system.session_coordinator.session_manager.get_session_info(from_minion_id)
            if not sender_session:
                return {
                    "content": [{
                        "type": "text",
                        "text": "Error: Unable to find sender session"
                    }],
                    "is_error": True
                }

            legion_id = sender_session.project_id

            # Legion-scoped lookup (only search within sender's legion)
            to_minion = await self.system.legion_coordinator.get_minion_by_name_in_legion(legion_id, to_minion_name)
            if not to_minion:
                return {
                    "content": [{
                        "type": "text",
                        "text": f"Error: Minion '{to_minion_name}' not found in legion {legion_id}"
                    }],
                    "is_error": True
                }
            to_minion_id = to_minion.session_id  # session_id IS the minion_id

        # Extract summary and content with fallback
        content = args.get("content", "")
        summary = args.get("summary", "")

        # Fallback: If summary is empty, auto-generate from first 50 chars of content
        if not summary and content:
            summary = content[:50] + ("..." if len(content) > 50 else "")

        # Look up sender minion name (for historical display)
        from_minion_name = None
        from_minion_session = await self.system.session_coordinator.session_manager.get_session_info(from_minion_id)
        if from_minion_session:
            from_minion_name = from_minion_session.name

        # Look up recipient minion name if applicable (for historical display)
        to_minion_name_captured = None
        if to_minion_id:
            to_minion_session = await self.system.session_coordinator.session_manager.get_session_info(to_minion_id)
            if to_minion_session:
                to_minion_name_captured = to_minion_session.name

        # Create Comm
        comm = Comm(
            comm_id=str(uuid.uuid4()),
            from_minion_id=from_minion_id,
            from_user=False,
            from_minion_name=from_minion_name,
            to_minion_id=to_minion_id,
            to_user=sending_to_user,
            to_minion_name=to_minion_name_captured,
            summary=summary,
            content=content,
            comm_type=CommType(internal_comm_type),
            interrupt_priority=interrupt_priority,  # Use validated priority
            visible_to_user=True
        )

        # Route the comm
        try:
            success = await self.system.comm_router.route_comm(comm)
            if success:
                return {
                    "content": [{
                        "type": "text",
                        "text": f"Message sent to {to_minion_name}"
                    }],
                    "is_error": False
                }
            else:
                return {
                    "content": [{
                        "type": "text",
                        "text": f"Failed to send message to {to_minion_name}"
                    }],
                    "is_error": True
                }
        except Exception as e:
            return {
                "content": [{
                    "type": "text",
                    "text": f"Error sending message: {str(e)}"
                }],
                "is_error": True
            }

    async def _handle_spawn_minion(self, args: dict[str, Any]) -> dict[str, Any]:
        """
        Handle spawn_minion tool call from a minion.

        Security Model:
        - Minions can ONLY use templates or default restricted permissions
        - NO ad-hoc permission specification allowed (prevents privilege escalation)
        - Templates are user-controlled and enforce specific permission sets

        Args:
            args: {
                "_parent_overseer_id": str,  # Injected by tool wrapper
                "name": str,
                "role": str,
                "initialization_context": str,
                "template_name": str,  # Optional - if provided, enforces template permissions
                "capabilities": List[str]  # Optional
            }

        Returns:
            Tool result with success/error
        """
        from src.logging_config import get_logger

        coord_logger = get_logger('legion', category='MCP_TOOLS')

        parent_overseer_id = args.get("_parent_overseer_id")
        if not parent_overseer_id:
            return {
                "content": [{
                    "type": "text",
                    "text": "Error: Unable to determine parent overseer ID"
                }],
                "is_error": True
            }

        # Extract parameters
        name = args.get("name", "").strip()
        role = args.get("role", "").strip()
        initialization_context = args.get("initialization_context", "").strip()
        template_name = args.get("template_name", "").strip()
        capabilities = args.get("capabilities", [])
        working_directory_raw = args.get("working_directory")

        # Get parent session to determine legion_id
        parent_session = await self.system.session_coordinator.session_manager.get_session_info(parent_overseer_id)
        if not parent_session:
            return {
                "content": [{
                    "type": "text",
                    "text": f"Error: Parent overseer session {parent_overseer_id} not found"
                }],
                "is_error": True
            }

        legion_id = parent_session.project_id
        if not legion_id:
            return {
                "content": [{
                    "type": "text",
                    "text": "Error: Parent overseer is not part of a legion"
                }],
                "is_error": True
            }

        # Validate required fields
        if not name:
            return {
                "content": [{
                    "type": "text",
                    "text": "Error: 'name' parameter is required and cannot be empty"
                }],
                "is_error": True
            }

        if not initialization_context:
            return {
                "content": [{
                    "type": "text",
                    "text": "Error: 'initialization_context' parameter is required and cannot be empty. Provide clear instructions for what this minion should do."
                }],
                "is_error": True
            }

        # SECURITY: Apply template permissions or use safe defaults
        # Minions cannot specify custom permissions directly
        permission_mode = None
        allowed_tools = None
        template_applied = None

        if template_name:
            # Template specified - look up and apply (no overrides allowed)
            try:
                template = await self.system.template_manager.get_template_by_name(template_name)

                if not template:
                    return {
                        "content": [{
                            "type": "text",
                            "text": f"❌ Error: Template '{template_name}' not found. Use list_templates() to see available templates."
                        }],
                        "is_error": True
                    }

                template_applied = template

                # Apply template values (enforced, no overrides)
                permission_mode = template.permission_mode
                allowed_tools = template.allowed_tools

                # Use template's default_role if role not provided
                if not role and template.default_role:
                    role = template.default_role

                # Prepend template's default_system_prompt if exists
                if template.default_system_prompt:
                    initialization_context = f"{template.default_system_prompt}\n\n{initialization_context}"

            except Exception as e:
                coord_logger.error(f"Error applying template: {e}", exc_info=True)
                return {
                    "content": [{
                        "type": "text",
                        "text": f"❌ Error applying template: {str(e)}"
                    }],
                    "is_error": True
                }
        else:
            # No template - use safe default restricted permissions
            permission_mode = "default"  # Prompts for most actions
            allowed_tools = []  # No pre-authorized tools (user must approve each tool use)

        # Validate role is set (from parameter or template)
        if not role:
            return {
                "content": [{
                    "type": "text",
                    "text": "Error: 'role' parameter is required (or use a template with default_role)"
                }],
                "is_error": True
            }

        # Validate and normalize working directory if provided
        working_directory = None
        if working_directory_raw:
            try:
                # Import validation function
                from src.web_server import validate_and_normalize_working_directory

                # Use parent's working directory as default
                working_directory = str(validate_and_normalize_working_directory(
                    working_directory_raw,
                    str(parent_session.working_directory)
                ))
            except ValueError as e:
                return {
                    "content": [{
                        "type": "text",
                        "text": f"❌ Invalid working_directory: {str(e)}"
                    }],
                    "is_error": True
                }

        # Attempt to spawn child minion
        try:
            # Map initialization_context to system_prompt (initialization_context is semantic UI term)
            spawn_result = await self.system.overseer_controller.spawn_minion(
                parent_overseer_id=parent_overseer_id,
                name=name,
                role=role,
                system_prompt=initialization_context,
                capabilities=capabilities,
                permission_mode=permission_mode,
                allowed_tools=allowed_tools,
                working_directory=working_directory
            )

            child_minion_id = spawn_result["minion_id"]

            # Build success message with permission info
            perm_info = ""
            if template_applied:
                tools_str = ", ".join(template_applied.allowed_tools) if template_applied.allowed_tools else "all"
                perm_info = (
                    f"\n**Permissions** (from template '{template_applied.name}'):\n"
                    f"  - Permission Mode: {template_applied.permission_mode}\n"
                    f"  - Allowed Tools: {tools_str}"
                )
            else:
                perm_info = (
                    "\n**Permissions** (safe defaults):\n"
                    "  - Permission Mode: default\n"
                    "  - Allowed Tools: none (user must approve each tool use)"
                )

            # Add working directory info if specified
            wd_info = ""
            if working_directory:
                wd_info = f"\nWorking Directory: {working_directory}\n"

            next_step_msg = f"Send a comm to '{name}' to start them working on their task"

            return {
                "content": [{
                    "type": "text",
                    "text": (
                        f"✅ Successfully spawned minion '{name}' with role '{role}'.\n\n"
                        f"Minion ID: {child_minion_id}\n"
                        f"{wd_info}"
                        f"{perm_info}\n"
                        f"The child minion is now active and ready to receive comms. "
                        f"You can communicate with them using send_comm(to_minion_name='{name}', ...)."
                        f"\n\n**Next Step:** {next_step_msg}"
                    )
                }],
                "is_error": False,
                "next_step": next_step_msg
            }

        except ValueError as e:
            # Handle validation errors (duplicate name, capacity, etc.)
            return {
                "content": [{
                    "type": "text",
                    "text": f"❌ Cannot spawn minion: {str(e)}"
                }],
                "is_error": True
            }

        except PermissionError as e:
            # Handle permission errors
            return {
                "content": [{
                    "type": "text",
                    "text": f"❌ Permission denied: {str(e)}"
                }],
                "is_error": True
            }

        except Exception as e:
            # Catch-all for unexpected errors
            coord_logger.error(f"Unexpected error in spawn_minion: {e}", exc_info=True)
            return {
                "content": [{
                    "type": "text",
                    "text": f"❌ Failed to spawn minion due to unexpected error: {str(e)}"
                }],
                "is_error": True
            }

    async def _handle_dispose_minion(self, args: dict[str, Any]) -> dict[str, Any]:
        """
        Handle dispose_minion tool call from a minion.

        Args:
            args: {
                "_parent_overseer_id": str,  # Injected by tool wrapper (session_id)
                "minion_name": str
            }

        Returns:
            Tool result with success/error
        """
        from src.logging_config import get_logger

        coord_logger = get_logger('legion', category='MCP_TOOLS')

        parent_overseer_id = args.get("_parent_overseer_id")  # This is actually the CALLER's session_id
        if not parent_overseer_id:
            return {
                "content": [{
                    "type": "text",
                    "text": "Error: Unable to determine caller ID"
                }],
                "is_error": True
            }

        # Extract parameters
        minion_name = args.get("minion_name", "").strip()

        # Validate required field
        if not minion_name:
            return {
                "content": [{
                    "type": "text",
                    "text": "Error: 'minion_name' parameter is required and cannot be empty"
                }],
                "is_error": True
            }

        # Attempt to dispose child minion
        try:
            result = await self.system.overseer_controller.dispose_minion(
                parent_overseer_id=parent_overseer_id,
                child_minion_name=minion_name
            )

            descendants_msg = ""
            if result["descendants_count"] > 0:
                descendants_msg = f"\n\n⚠️  Also disposed {result['descendants_count']} descendant minion(s) (children of {minion_name})."

            return {
                "content": [{
                    "type": "text",
                    "text": (
                        f"✅ Successfully disposed of minion '{minion_name}'."
                        f"{descendants_msg}\n\n"
                        f"Their knowledge has been preserved and will be available to you."
                    )
                }],
                "is_error": False
            }

        except ValueError as e:
            # Handle not found or validation errors
            return {
                "content": [{
                    "type": "text",
                    "text": f"❌ Cannot dispose minion: {str(e)}"
                }],
                "is_error": True
            }

        except PermissionError as e:
            # Handle permission errors (not your child)
            return {
                "content": [{
                    "type": "text",
                    "text": f"❌ Permission denied: {str(e)}"
                }],
                "is_error": True
            }

        except Exception as e:
            # Catch-all for unexpected errors
            coord_logger.error(f"Unexpected error in dispose_minion: {e}", exc_info=True)
            return {
                "content": [{
                    "type": "text",
                    "text": f"❌ Failed to dispose minion due to unexpected error: {str(e)}"
                }],
                "is_error": True
            }

    async def _handle_search_capability(self, args: dict[str, Any]) -> dict[str, Any]:
        """
        Handle search_capability tool call.

        Searches the central capability registry for minions with matching capabilities.
        Returns formatted text list of ranked results.

        Args:
            args: Tool arguments with 'capability' keyword

        Returns:
            Tool result with formatted minion list or error message
        """
        try:
            # Get search keyword
            keyword = args.get("capability", "").strip()

            if not keyword:
                return {
                    "content": [{
                        "type": "text",
                        "text": "❌ Error: capability parameter is required and cannot be empty"
                    }],
                    "is_error": True
                }

            # Search the capability registry
            try:
                results = await self.system.legion_coordinator.search_capability_registry(
                    keyword=keyword
                )
            except ValueError as e:
                return {
                    "content": [{
                        "type": "text",
                        "text": f"❌ Search error: {str(e)}"
                    }],
                    "is_error": True
                }

            # Handle empty results
            if not results:
                return {
                    "content": [{
                        "type": "text",
                        "text": f"No minions found with capability matching '{keyword}'"
                    }],
                    "is_error": False
                }

            # Format results as text list
            result_lines = [f"**Minions with capability '{keyword}'** (ranked by expertise):\n"]

            for minion_id, expertise_score, capability_matched in results:
                # Get minion details
                minion = await self.system.legion_coordinator.get_minion_info(minion_id)
                if not minion:
                    continue  # Skip if minion no longer exists

                name = minion.name or minion_id[:8]
                role = minion.role or "No role specified"
                state = minion.state.value if hasattr(minion.state, 'value') else str(minion.state)

                # Format expertise score as percentage
                expertise_pct = int(expertise_score * 100)

                result_lines.append(
                    f"\n• **{name}** (ID: {minion_id[:8]}...)\n"
                    f"  - Role: {role}\n"
                    f"  - State: {state}\n"
                    f"  - Expertise: {expertise_pct}% ({expertise_score:.2f})\n"
                    f"  - Matched capability: {capability_matched}"
                )

            return {
                "content": [{
                    "type": "text",
                    "text": "".join(result_lines)
                }],
                "is_error": False
            }

        except Exception as e:
            # Catch-all for unexpected errors
            return {
                "content": [{
                    "type": "text",
                    "text": f"❌ Failed to search capabilities due to unexpected error: {str(e)}"
                }],
                "is_error": True
            }

    async def _handle_list_minions(self, args: dict[str, Any]) -> dict[str, Any]:
        """
        Handle list_minions tool call.

        Lists all active minions in the caller's legion with their names, roles, and states.
        Always includes the special "user" minion for sending comms to the user.

        Returns:
            Tool result with formatted minion list
        """
        try:
            from src.models.legion_models import USER_MINION_ID

            # Get caller's legion to scope listing
            from_minion_id = args.get("_from_minion_id")
            if not from_minion_id:
                return {
                    "content": [{
                        "type": "text",
                        "text": "Error: Unable to determine caller minion ID"
                    }],
                    "is_error": True
                }

            caller_session = await self.system.session_coordinator.session_manager.get_session_info(from_minion_id)
            if not caller_session:
                return {
                    "content": [{
                        "type": "text",
                        "text": "Error: Unable to find caller session"
                    }],
                    "is_error": True
                }

            legion_id = caller_session.project_id

            # Get legion and its sessions
            legion = await self.system.legion_coordinator.get_legion(legion_id)
            if not legion:
                return {
                    "content": [{
                        "type": "text",
                        "text": "Error: Caller is not part of a legion"
                    }],
                    "is_error": True
                }

            # Get minions in this legion (project_id matches legion_id)
            session_manager = self.system.session_coordinator.session_manager
            minions = []
            for session_id in legion.session_ids:
                session = await session_manager.get_session_info(session_id)
                if session and session.is_minion:
                    minions.append(session)

            # Format minion list
            minion_lines = []

            # Always include the special "user" minion first
            minion_lines.append(
                f"• **user** (ID: {USER_MINION_ID})\n"
                f"  - Role: Human operator\n"
                f"  - State: active\n"
                f"  - Capabilities: Receives reports and can send tasks"
            )

            # Add other minions
            for minion in minions:
                name = minion.name or minion.session_id[:8]
                role = minion.role or "No role specified"
                state = minion.state.value if hasattr(minion.state, 'value') else str(minion.state)
                capabilities = ", ".join(minion.capabilities) if minion.capabilities else "None"

                minion_lines.append(
                    f"• **{name}** (ID: {minion.session_id})\n"
                    f"  - Role: {role}\n"
                    f"  - State: {state}\n"
                    f"  - Capabilities: {capabilities}"
                )

            total_count = len(minions) + 1  # +1 for user
            result_text = f"**Active Minions ({total_count}):**\n\n" + "\n\n".join(minion_lines)

            return {
                "content": [{
                    "type": "text",
                    "text": result_text
                }],
                "is_error": False
            }

        except Exception as e:
            return {
                "content": [{
                    "type": "text",
                    "text": f"Error listing minions: {str(e)}"
                }],
                "is_error": True
            }

    async def _handle_get_minion_info(self, args: dict[str, Any]) -> dict[str, Any]:
        """
        Handle get_minion_info tool call.

        Returns detailed profile of a minion including name, role, state, capabilities,
        hierarchy relationships, and legion membership.

        Args:
            args: {
                "minion_name": str  # Required - name of minion to query
            }

        Returns:
            Tool result with formatted minion profile or error message
        """
        # 1. Validate minion_name parameter
        minion_name = args.get("minion_name", "").strip() if isinstance(args.get("minion_name"), str) else ""

        if not minion_name:
            return {
                "content": [{
                    "type": "text",
                    "text": "❌ Error: 'minion_name' parameter is required and cannot be empty"
                }],
                "is_error": True
            }

        # 2. Get caller's legion to scope lookup
        from_minion_id = args.get("_from_minion_id")
        if not from_minion_id:
            return {
                "content": [{
                    "type": "text",
                    "text": "❌ Error: Unable to determine caller minion ID"
                }],
                "is_error": True
            }

        caller_session = await self.system.session_coordinator.session_manager.get_session_info(from_minion_id)
        if not caller_session:
            return {
                "content": [{
                    "type": "text",
                    "text": "❌ Error: Unable to find caller session"
                }],
                "is_error": True
            }

        legion_id = caller_session.project_id

        # Legion-scoped lookup (only search within caller's legion)
        minion = await self.system.legion_coordinator.get_minion_by_name_in_legion(legion_id, minion_name)

        if not minion:
            return {
                "content": [{
                    "type": "text",
                    "text": f"❌ Error: Minion '{minion_name}' not found in legion {legion_id}"
                }],
                "is_error": True
            }

        # 3. Build formatted profile
        profile_lines = []

        # Header
        profile_lines.append(f"# Minion Profile: {minion.name}\n")

        # Basic info
        profile_lines.append(f"**ID:** {minion.session_id[:8]}...")
        profile_lines.append(f"**Role:** {minion.role or 'No role specified'}")

        # State
        state_str = minion.state.value if hasattr(minion.state, 'value') else str(minion.state)
        profile_lines.append(f"**State:** {state_str}")

        # Capabilities with expertise scores
        if minion.capabilities:
            profile_lines.append("\n**Capabilities:**")

            # Get expertise scores from capability registry
            capability_scores = {}
            for capability, entries in self.system.legion_coordinator.capability_registry.items():
                for mid, score in entries:
                    if mid == minion.session_id:
                        capability_scores[capability] = score
                        break

            # Format capabilities with scores
            for capability in minion.capabilities:
                score = capability_scores.get(capability)
                if score is not None:
                    score_pct = int(score * 100)
                    profile_lines.append(f"- {capability}: {score_pct}%")
                else:
                    profile_lines.append(f"- {capability}")
        else:
            profile_lines.append("\n**Capabilities:** None registered")

        # Hierarchy info
        if minion.is_overseer:
            profile_lines.append("\n**Is Overseer:** Yes")

            if minion.child_minion_ids:
                # Resolve child names
                child_names = []
                for child_id in minion.child_minion_ids:
                    child_session = await self.system.session_coordinator.session_manager.get_session_info(child_id)
                    if child_session:
                        child_names.append(child_session.name or child_id[:8])
                    else:
                        child_names.append(child_id[:8])

                profile_lines.append(f"**Child Minions:** {', '.join(child_names)}")
            else:
                profile_lines.append("**Child Minions:** None")
        else:
            profile_lines.append("\n**Is Overseer:** No")

        # Parent overseer
        if minion.parent_overseer_id:
            # Resolve parent name
            parent_session = await self.system.session_coordinator.session_manager.get_session_info(minion.parent_overseer_id)
            parent_name = parent_session.name if parent_session else minion.parent_overseer_id[:8]
            profile_lines.append(f"**Parent Overseer:** {parent_name}")

        # Legion
        if minion.project_id:
            # Resolve legion name
            try:
                legion = await self.system.session_coordinator.project_manager.get_project(minion.project_id)
                legion_name = legion.name if legion else minion.project_id[:8]
            except Exception:
                legion_name = minion.project_id[:8]

            profile_lines.append(f"\n**Legion:** {legion_name}")

        # Join lines and return
        profile_text = "\n".join(profile_lines)

        return {
            "content": [{
                "type": "text",
                "text": profile_text
            }],
            "is_error": False
        }

    async def _handle_list_templates(self, args: dict[str, Any]) -> dict[str, Any]:
        """
        Handle list_templates tool call.

        Returns formatted list of all available minion templates.

        Args:
            args: {} (no parameters)

        Returns:
            Tool result with formatted template list
        """
        try:
            # Get all templates from TemplateManager
            templates = await self.system.template_manager.list_templates()

            # Handle empty case
            if not templates:
                return {
                    "content": [{
                        "type": "text",
                        "text": (
                            "No templates available.\n\n"
                            "Templates are pre-configured permission sets for minions. "
                            "Ask the user to create templates through the UI (Manage Templates)."
                        )
                    }],
                    "is_error": False
                }

            # Sort templates alphabetically by name
            templates = sorted(templates, key=lambda t: t.name)

            # Build formatted list
            result_lines = ["**Available Minion Templates:**\n"]

            for template in templates:
                # Format allowed tools
                tools_str = ", ".join(template.allowed_tools) if template.allowed_tools else "None"
                if len(tools_str) > 60:  # Truncate if too long
                    tools_str = tools_str[:60] + "..."

                # Build template entry
                result_lines.append(
                    f"• **{template.name}**\n"
                    f"  - Description: {template.description or 'No description'}\n"
                    f"  - Permission Mode: {template.permission_mode}\n"
                    f"  - Allowed Tools: {tools_str}\n"
                    f"  - Default Role: {template.default_role or 'Not specified'}\n"
                )

            # Add usage hint
            result_lines.append(
                "\n**Usage:**\n"
                "To spawn a minion with a template, use:\n"
                "spawn_minion(name='MinionName', template_name='Template Name')"
            )

            return {
                "content": [{
                    "type": "text",
                    "text": "\n".join(result_lines)
                }],
                "is_error": False
            }

        except Exception as e:
            return {
                "content": [{
                    "type": "text",
                    "text": f"Error listing templates: {str(e)}"
                }],
                "is_error": True
            }

    async def _handle_update_expertise(self, args: dict[str, Any]) -> dict[str, Any]:
        """
        Handle update_expertise tool call.

        Args:
            args: {
                "_from_minion_id": str,  # Injected by tool wrapper
                "capability": str,
                "expertise_score": float | None
            }

        Returns:
            Tool result with success/error
        """
        from_minion_id = args.get("_from_minion_id")

        if not from_minion_id:
            return {
                "content": [{
                    "type": "text",
                    "text": "Error: Unable to determine minion ID"
                }],
                "is_error": True
            }

        # Extract parameters
        capability = args.get("capability", "").strip()
        expertise_score = args.get("expertise_score")

        # Validate capability
        if not capability:
            return {
                "content": [{
                    "type": "text",
                    "text": "Error: capability parameter is required and cannot be empty"
                }],
                "is_error": True
            }

        # Validate and convert expertise_score if provided
        if expertise_score is not None:
            # Convert string to float if needed (MCP may pass as string)
            if isinstance(expertise_score, str):
                try:
                    expertise_score = float(expertise_score)
                except ValueError:
                    return {
                        "content": [{
                            "type": "text",
                            "text": "Error: expertise_score must be a number between 0.0 and 1.0"
                        }],
                        "is_error": True
                    }
            elif not isinstance(expertise_score, (int, float)):
                return {
                    "content": [{
                        "type": "text",
                        "text": "Error: expertise_score must be a number between 0.0 and 1.0"
                    }],
                    "is_error": True
                }

            # Validate range
            if expertise_score < 0.0 or expertise_score > 1.0:
                return {
                    "content": [{
                        "type": "text",
                        "text": "Error: expertise_score must be between 0.0 and 1.0"
                    }],
                    "is_error": True
                }

        # Call existing register_capability method
        try:
            await self.system.legion_coordinator.register_capability(
                minion_id=from_minion_id,
                capability=capability,
                expertise_score=expertise_score  # Will use minion's default if None
            )

            # Get updated capability count
            minion = await self.system.session_coordinator.session_manager.get_session_info(from_minion_id)
            capability_count = len(minion.capabilities) if minion else 0

            # Format score for display
            score_display = expertise_score if expertise_score is not None else 0.5

            return {
                "content": [{
                    "type": "text",
                    "text": (
                        f"✅ Successfully updated expertise for '{capability}' to {score_display:.2f}. "
                        f"You now have {capability_count} capabilit{'y' if capability_count == 1 else 'ies'}."
                    )
                }],
                "is_error": False
            }

        except ValueError as e:
            # Validation errors from register_capability
            return {
                "content": [{
                    "type": "text",
                    "text": f"❌ Error: {str(e)}"
                }],
                "is_error": True
            }
        except Exception as e:
            return {
                "content": [{
                    "type": "text",
                    "text": f"❌ Unexpected error updating expertise: {str(e)}"
                }],
                "is_error": True
            }
