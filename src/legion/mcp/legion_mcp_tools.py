"""
LegionMCPTools - MCP tool definitions for Legion multi-agent capabilities.

This module provides MCP tools that minions can use for:
- Communication (send_comm, send_comm_to_channel)
- Lifecycle (spawn_minion, dispose_minion)
- Discovery (search_capability, list_minions, get_minion_info)
- Channels (join_channel, create_channel, list_channels)

All minion SDK sessions receive these tools on creation.

Note: These are "in-code" MCP tools passed directly to the Claude Agent SDK,
not a separate MCP server process.
"""

from typing import TYPE_CHECKING, List, Dict, Any

if TYPE_CHECKING:
    from src.legion_system import LegionSystem


class LegionMCPTools:
    """
    MCP tools for Legion multi-agent capabilities.
    Single instance shared across all minion SDK sessions.
    """

    def __init__(self, system: 'LegionSystem'):
        """
        Initialize LegionMCPTools with LegionSystem.

        Args:
            system: LegionSystem instance for accessing other components
        """
        self.system = system

        # Tool definitions for SDK
        self.tool_schemas = self._define_tool_schemas()

    def _define_tool_schemas(self) -> List[Dict[str, Any]]:
        """
        Define all MCP tool schemas for minions.

        These schemas follow the MCP tool format and will be registered
        with the Claude Agent SDK when creating minion sessions.

        Returns:
            List of tool schema dictionaries
        """
        return [
            # Communication Tools
            {
                "name": "send_comm",
                "description": (
                    "Send a communication (message) to another minion by name. "
                    "Use this for direct collaboration, asking questions, delegating tasks, "
                    "or reporting findings. You can reference other minions in your message "
                    "using #minion-name syntax (e.g., 'Check with #DatabaseArchitect about schema')."
                ),
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "to_minion_name": {
                            "type": "string",
                            "description": "Exact name of target minion (case-sensitive). Example: 'AuthExpert' or 'DatabaseArchitect'"
                        },
                        "content": {
                            "type": "string",
                            "description": "Message content. You can reference other minions using #minion-name or channels using #channel-name for clarity."
                        },
                        "comm_type": {
                            "type": "string",
                            "enum": ["task", "question", "report", "guide"],
                            "description": "Type of communication: 'task' (assign work), 'question' (request info), 'report' (provide findings), 'guide' (additional instructions)"
                        }
                    },
                    "required": ["to_minion_name", "content", "comm_type"]
                }
            },
            {
                "name": "send_comm_to_channel",
                "description": (
                    "Broadcast a message to all members of a channel. All channel members will "
                    "receive your message. Use for group coordination, status updates, or questions "
                    "to the team. Reference specific minions using #minion-name syntax."
                ),
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "channel_name": {
                            "type": "string",
                            "description": "Name of channel to broadcast to (e.g., 'implementation-planning', 'database-changes')"
                        },
                        "content": {
                            "type": "string",
                            "description": "Message to broadcast. Use #minion-name to reference specific team members (e.g., 'Great work #AuthExpert!')."
                        },
                        "comm_type": {
                            "type": "string",
                            "enum": ["task", "question", "report", "guide"],
                            "description": "Type of communication",
                            "default": "report"
                        }
                    },
                    "required": ["channel_name", "content"]
                }
            },

            # Lifecycle Tools
            {
                "name": "spawn_minion",
                "description": (
                    "Create a new child minion to delegate specialized work. You become the overseer "
                    "of this minion and can later dispose of it when done. The child minion will be a "
                    "full Claude agent with access to tools."
                ),
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "name": {
                            "type": "string",
                            "description": "Unique name for new minion (must not already exist in legion). Choose descriptive names like 'SSOAnalyzer' or 'DatabaseMigrationPlanner'."
                        },
                        "role": {
                            "type": "string",
                            "description": "Human-readable role description (e.g., 'SSO Configuration Analyst', 'Database Migration Planner')"
                        },
                        "initialization_context": {
                            "type": "string",
                            "description": "System prompt defining the minion's expertise, responsibilities, and constraints. Be specific about what they should do and what they should know."
                        },
                        "channels": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "List of channel names the minion should join immediately",
                            "default": []
                        }
                    },
                    "required": ["name", "role", "initialization_context"]
                }
            },
            {
                "name": "dispose_minion",
                "description": (
                    "Terminate a child minion you created when their task is complete. You can only "
                    "dispose minions you spawned (your children). Their knowledge will be transferred "
                    "to you before termination."
                ),
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "minion_name": {
                            "type": "string",
                            "description": "Name of child minion to dispose (must be your child)"
                        }
                    },
                    "required": ["minion_name"]
                }
            },

            # Discovery Tools
            {
                "name": "search_capability",
                "description": (
                    "Find minions with a specific capability by searching the central capability registry. "
                    "Returns ranked list of matching minions sorted by expertise scores. Use keywords like "
                    "'database', 'oauth', 'authentication', 'payment_processing', etc."
                ),
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "capability": {
                            "type": "string",
                            "description": "Capability keyword to search for (e.g., 'database', 'oauth', 'authentication'). Case-insensitive partial matching."
                        }
                    },
                    "required": ["capability"]
                }
            },
            {
                "name": "list_minions",
                "description": (
                    "Get a list of all active minions in the legion with their names, roles, and "
                    "current states. Useful for understanding who's available to collaborate with."
                ),
                "input_schema": {
                    "type": "object",
                    "properties": {},
                    "required": []
                }
            },
            {
                "name": "get_minion_info",
                "description": (
                    "Get detailed information about a specific minion, including their role, capabilities, "
                    "current task, parent/children, and channels."
                ),
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "minion_name": {
                            "type": "string",
                            "description": "Name of minion to query"
                        }
                    },
                    "required": ["minion_name"]
                }
            },

            # Channel Tools
            {
                "name": "join_channel",
                "description": (
                    "Join an existing channel to collaborate with its members. You'll receive all "
                    "future broadcasts to this channel."
                ),
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "channel_name": {
                            "type": "string",
                            "description": "Name of channel to join"
                        }
                    },
                    "required": ["channel_name"]
                }
            },
            {
                "name": "create_channel",
                "description": (
                    "Create a new channel for group communication. You'll be automatically added as a member. "
                    "Use channels to coordinate with multiple minions on a shared task."
                ),
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "name": {
                            "type": "string",
                            "description": "Unique name for channel (e.g., 'DatabaseChanges', 'SSOImplementation')"
                        },
                        "description": {
                            "type": "string",
                            "description": "Purpose and description of the channel"
                        },
                        "purpose": {
                            "type": "string",
                            "enum": ["coordination", "planning", "research", "scene"],
                            "description": "Channel purpose category"
                        },
                        "initial_members": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "List of minion names to add as initial members",
                            "default": []
                        }
                    },
                    "required": ["name", "description", "purpose"]
                }
            },
            {
                "name": "list_channels",
                "description": (
                    "Get list of all channels in the legion with their names, descriptions, and member counts."
                ),
                "input_schema": {
                    "type": "object",
                    "properties": {},
                    "required": []
                }
            },
        ]

    # Tool handler methods - Implementation in Phase 2/3
    # These will be called by the SDK when a minion uses a tool

    async def handle_tool_call(self, tool_name: str, minion_id: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
        """
        Route tool calls to appropriate handlers.

        Args:
            tool_name: Name of the tool being called
            minion_id: ID of the minion making the call
            arguments: Tool arguments from the SDK

        Returns:
            Tool result dictionary
        """
        # TODO: Implement in Phase 2/3
        # Route to specific handlers based on tool_name
        return {
            "success": False,
            "error": f"Tool '{tool_name}' not yet implemented"
        }

    # TODO: Implement handler methods in Phase 2/3:
    # - _handle_send_comm()
    # - _handle_send_comm_to_channel()
    # - _handle_spawn_minion()
    # - _handle_dispose_minion()
    # - _handle_search_capability()
    # - _handle_list_minions()
    # - _handle_get_minion_info()
    # - _handle_join_channel()
    # - _handle_create_channel()
    # - _handle_list_channels()
