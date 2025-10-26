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

# Size limits - Windows command-line subprocess limits
# Guide: 4000 chars, Initialization context: 2000 chars
# Total: ~6000 chars + CLI overhead stays under ~7000 char Windows limit
MAX_LEGION_GUIDE_SIZE = 4000  # characters
MAX_INITIALIZATION_CONTEXT_SIZE = 2000  # characters


def _load_legion_guide() -> str:
    """
    Load the Legion MCP Tools Guide from the markdown file with size limit.

    Due to Windows command-line length limitations in the Claude Agent SDK
    (https://github.com/anthropics/claude-agent-sdk-python/issues/267),
    the guide is truncated to MAX_LEGION_GUIDE_SIZE characters.

    Returns:
        str: The content of the guide (truncated if necessary)

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
        content = GUIDE_PATH.read_text(encoding='utf-8')

        # Truncate if exceeds size limit
        if len(content) > MAX_LEGION_GUIDE_SIZE:
            content = content[:MAX_LEGION_GUIDE_SIZE]
            # Try to truncate at last complete line to avoid breaking markdown
            last_newline = content.rfind('\n')
            if last_newline > MAX_LEGION_GUIDE_SIZE * 0.9:  # Only if we're not losing too much
                content = content[:last_newline]
            content += f"\n\n[Guide truncated at {MAX_LEGION_GUIDE_SIZE} chars due to SDK limitations]"

        return content
    except Exception as e:
        raise IOError(f"Failed to read Legion MCP Tools Guide: {e}")

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
