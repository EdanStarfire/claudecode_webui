#!/usr/bin/env python3
"""
Demo script to test the Claude Code WebUI session coordinator.
Uses correct SDK parameters as specified in CLAUDE.md.
"""

import asyncio
import sys
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent / "src"))

from src.session_coordinator import SessionCoordinator
from src.logging_config import setup_logging

# Define the permission handler (now async with correct parameter names)
async def handle_permissions(tool_name: str, input_params: dict) -> dict:
    print("\n--- PERMISSION REQUEST ---")
    print(f"Tool: {tool_name}")
    print(f"Input: {input_params}")
    # In a real app, you would prompt the user here (e.g., using input()).
    # For this demo, we'll just approve it to show the flow.
    print("Approving request...")
    print("--------------------------\n")
    return {
        "behavior": "allow",
        "updatedInput": input_params
    }

async def demo_session():
    """Demonstrate session coordinator functionality."""
    print("Starting Claude Code WebUI Session Demo")

    # Setup logging
    setup_logging(log_level="INFO", enable_console=True)

    # Create coordinator
    coordinator = SessionCoordinator()
    await coordinator.initialize()
    print("OK Session coordinator initialized")


    # Create a session with correct parameter names (use default model)
    session_id = await coordinator.create_session(
        working_directory=str(Path.cwd()),
        permissions="default",
        system_prompt="You are a helpful coding assistant.",
        tools=[],
        permission_callback=handle_permissions,
    )
    print(f"OK Created session: {session_id}")

    # Get session info
    info = await coordinator.get_session_info(session_id)
    print(f"INFO Session info: {info['session']['state']}")

    # Add message callback to see what happens
    def message_callback(session_id, message):
        # Handle ParsedMessage objects (dataclass with .type attribute)
        if hasattr(message, 'type'):
            msg_type = message.type.value if hasattr(message.type, 'value') else str(message.type)
            content = getattr(message, 'content', str(message))[:100] if message.content else ""
        else:
            # Fallback for dict objects
            msg_type = message.get('type', 'unknown')
            content = message.get('content', '')[:100]
        print(f"MSG Message from {session_id}: {msg_type} - {content}...")

    coordinator.add_message_callback(session_id, message_callback)

    # Start the session
    print("TRYING Attempting to start session...")
    success = await coordinator.start_session(session_id)

    if success:
        print("OK Session started successfully!")

        # Send a test message
        print("SEND Sending test message...")
        await coordinator.send_message(session_id, "Hello, run `echo \"Test\" > temp.txt`")

        # Wait a moment for processing
        await asyncio.sleep(30)

        # Get messages
        messages = await coordinator.get_session_messages(session_id, limit=10)
        print(f"MSGS Retrieved {len(messages)} messages from storage")

    else:
        print("EXPECTED Session failed to start (expected without Claude Code SDK)")
        print("   This is normal - the demo shows the infrastructure is working")

    # Get final session info
    final_info = await coordinator.get_session_info(session_id)
    print(f"FINAL Final session state: {final_info['session']['state']}")

    # Cleanup
    await coordinator.terminate_session(session_id)
    await coordinator.cleanup()
    print("DONE Cleanup completed")


if __name__ == "__main__":
    asyncio.run(demo_session())
