#!/usr/bin/env python3
"""
Main entry point for Claude Code WebUI server.
"""

import argparse
import sys
from pathlib import Path

import uvicorn

# Add project root to path (so imports like "from src.legion..." work)
sys.path.insert(0, str(Path(__file__).parent))

from src.logging_config import configure_logging
from src.web_server import create_app, shutdown_event, startup_event


def main():
    """Main function to start the Claude Code WebUI server."""

    # Parse command-line arguments
    parser = argparse.ArgumentParser(
        description="Claude Code WebUI Server",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Debug Flags:
  --debug-websocket         Enable WebSocket lifecycle debugging
  --debug-ping-pong         Enable WebSocket ping/pong logging (high volume)
  --debug-sdk               Enable SDK integration debugging
  --debug-permissions       Enable permission callback debugging
  --debug-storage           Enable data storage debugging
  --debug-parser            Enable message parser debugging
  --debug-error-handler     Enable error handler debugging
  --debug-legion            Enable Legion multi-agent system debugging
  --debug-session-manager   Enable session manager debugging
  --debug-template-manager  Enable template manager debugging
  --debug-all               Enable all debug logging (excludes ping/pong)
        """
    )

    # Server options
    parser.add_argument('--host', default='0.0.0.0', help='Host to bind to (default: 0.0.0.0)')
    parser.add_argument('--port', type=int, default=8000, help='Port to bind to (default: 8000)')
    parser.add_argument('--data-dir', default='./data', help='Data directory location (default: ./data)')

    # Debug flags
    parser.add_argument('--debug-websocket', action='store_true', help='Enable WebSocket lifecycle debugging')
    # Note: debug_all excludes ping/pong logging due to excessive noise that obscures other debug output.
    # Ping/pong keepalive messages occur every 3 seconds per WebSocket connection, generating thousands
    # of log entries that make it difficult to identify relevant debugging information.
    parser.add_argument('--debug-ping-pong', action='store_true', help='Enable WebSocket ping/pong logging (high volume)')
    parser.add_argument('--debug-sdk', action='store_true', help='Enable SDK integration debugging')
    parser.add_argument('--debug-permissions', action='store_true', help='Enable permission callback debugging')
    parser.add_argument('--debug-storage', action='store_true', help='Enable data storage debugging')
    parser.add_argument('--debug-parser', action='store_true', help='Enable message parser debugging')
    parser.add_argument('--debug-error-handler', action='store_true', help='Enable error handler debugging')
    parser.add_argument('--debug-legion', action='store_true', help='Enable Legion multi-agent system debugging')
    parser.add_argument('--debug-session-manager', action='store_true', help='Enable session manager debugging')
    parser.add_argument('--debug-template-manager', action='store_true', help='Enable template manager debugging')
    parser.add_argument('--debug-all', action='store_true', help='Enable all debug logging (excludes ping/pong)')

    args = parser.parse_args()

    # Validate and create data directory
    data_dir_path = Path(args.data_dir).resolve()
    try:
        data_dir_path.mkdir(parents=True, exist_ok=True)
        print(f"Using data directory: {data_dir_path}")
    except Exception as e:
        print(f"Failed to create data directory {data_dir_path}: {e}", file=sys.stderr)
        sys.exit(1)

    # Configure logging with debug flags
    configure_logging(
        debug_websocket=args.debug_websocket,
        debug_ping_pong=args.debug_ping_pong,
        debug_sdk=args.debug_sdk,
        debug_permissions=args.debug_permissions,
        debug_storage=args.debug_storage,
        debug_parser=args.debug_parser,
        debug_error_handler=args.debug_error_handler,
        debug_legion=args.debug_legion,
        debug_session_manager=args.debug_session_manager,
        debug_template_manager=args.debug_template_manager,
        debug_all=args.debug_all,
        log_dir=str(data_dir_path / "logs")
    )

    # Create FastAPI app
    app = create_app(data_dir=data_dir_path)

    # Add startup/shutdown events
    app.add_event_handler("startup", startup_event)
    app.add_event_handler("shutdown", shutdown_event)

    # Run the server
    uvicorn.run(
        app,
        host=args.host,
        port=args.port,
        log_level="info",
        access_log=True
    )


if __name__ == "__main__":
    main()
