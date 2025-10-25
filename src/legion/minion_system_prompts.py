"""
Minion System Prompt Templates - Legion Multi-Agent System

This module provides system prompt templates that teach minions how to use
MCP tools for autonomous hierarchy management and inter-agent communication.

Templates are loaded from markdown files and appended to the Claude Code preset
system prompt to provide Legion-specific guidance while preserving core
Claude Code functionality.
"""

from pathlib import Path
from typing import Dict, Any, Optional


# Path to the Legion MCP Tools Guide markdown file
GUIDE_PATH = Path(__file__).parent / "legion_mcp_tools_guide.md"


def _load_legion_guide() -> str:
    """
    Load the Legion MCP Tools Guide from the markdown file.

    Returns:
        str: The content of the guide

    Raises:
        FileNotFoundError: If the guide file doesn't exist
        IOError: If the file cannot be read
    """
    if not GUIDE_PATH.exists():
        raise FileNotFoundError(
            f"Legion MCP Tools Guide not found at {GUIDE_PATH}. "
            f"Ensure legion_mcp_tools_guide.md exists in the same directory."
        )

    try:
        return GUIDE_PATH.read_text(encoding='utf-8')
    except Exception as e:
        raise IOError(f"Failed to read Legion MCP Tools Guide: {e}")


def build_minion_system_prompt(initialization_context: str = "") -> Dict[str, Any]:
    """
    Build a complete system prompt for a minion by appending Legion MCP guide
    and initialization context to the Claude Code preset.

    The guide is loaded from legion_mcp_tools_guide.md, allowing for easy
    iteration and refinement without modifying code.

    Args:
        initialization_context: Minion-specific instructions and context

    Returns:
        System prompt configuration dict compatible with ClaudeAgentOptions

    Raises:
        FileNotFoundError: If the guide markdown file doesn't exist
        IOError: If the guide file cannot be read

    Example:
        >>> prompt = build_minion_system_prompt("You are a database expert...")
        >>> # Returns: {"type": "preset", "preset": "claude_code", "append": "..."}
    """
    # Load Legion MCP guide from markdown file
    legion_guide = _load_legion_guide()

    # Combine Legion MCP guide with initialization context
    append_content_parts = [legion_guide]

    if initialization_context:
        # Add separator and minion-specific context
        append_content_parts.append("\n\n---\n\n# Your Specific Role and Task\n\n")
        append_content_parts.append(initialization_context)

    append_content = "".join(append_content_parts)

    # Return system prompt config that appends to Claude Code preset
    # Format per SDK docs: https://docs.claude.com/en/api/agent-sdk/modifying-system-prompts
    return {
        "type": "preset",
        "preset": "claude_code",
        "append": append_content
    }


def get_legion_guide_only() -> str:
    """
    Get just the Legion MCP tools guide without initialization context.

    Returns:
        str: The Legion MCP tools guide template loaded from markdown file

    Raises:
        FileNotFoundError: If the guide markdown file doesn't exist
        IOError: If the guide file cannot be read

    Use this for documentation or testing purposes.
    """
    return _load_legion_guide()
