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

from src.config_manager import check_network_binding, ensure_config_file, load_config
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
  --debug-skill-manager     Enable skill manager debugging
  --debug-queue-manager     Enable queue manager debugging
  --debug-queue-processor   Enable queue processor debugging
  --debug-archive           Enable archive manager debugging
  --debug-project-manager   Enable project manager debugging
  --debug-all               Enable all debug logging (excludes ping/pong)
        """
    )

    # Server options
    parser.add_argument(
        '--host', default='127.0.0.1',
        help='Bind address (default: 127.0.0.1, localhost only). Use --host 0.0.0.0 to allow remote access.'
    )
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
    parser.add_argument('--debug-skill-manager', action='store_true', help='Enable skill manager debugging')
    parser.add_argument('--debug-queue-manager', action='store_true', help='Enable queue manager debugging')
    parser.add_argument('--debug-queue-processor', action='store_true', help='Enable queue processor debugging')
    parser.add_argument('--debug-archive', action='store_true', help='Enable archive manager debugging')
    parser.add_argument('--debug-project-manager', action='store_true', help='Enable project manager debugging')
    parser.add_argument('--debug-all', action='store_true', help='Enable all debug logging (excludes ping/pong)')

    # Experimental features
    parser.add_argument('--experimental', action='store_true', help='Enable experimental features (Agent Teams)')

    # Mock SDK mode (for browser automation testing — issue #561)
    parser.add_argument(
        '--mock-sdk', action='store_true',
        help='Use MockClaudeSDK with fixture replay instead of real SDK'
    )
    parser.add_argument(
        '--fixtures-dir', type=str, default=None,
        help='Directory containing named fixture subdirectories (required with --mock-sdk)'
    )

    args = parser.parse_args()

    # Ensure config file exists (creates with safe defaults on first run)
    config_file = ensure_config_file()
    app_config = load_config(config_file)

    # Validate network binding permission
    if not check_network_binding(args.host, app_config, config_file):
        sys.exit(1)

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
        debug_skill_manager=args.debug_skill_manager,
        debug_queue_manager=args.debug_queue_manager,
        debug_queue_processor=args.debug_queue_processor,
        debug_archive=args.debug_archive,
        debug_project_manager=args.debug_project_manager,
        debug_all=args.debug_all,
        log_dir=str(data_dir_path / "logs")
    )

    # Validate mock SDK arguments (issue #561)
    if args.mock_sdk:
        if not args.fixtures_dir:
            parser.error("--fixtures-dir is required when --mock-sdk is specified")
        fixtures_path = Path(args.fixtures_dir).resolve()
        if not fixtures_path.is_dir():
            parser.error(f"Fixtures directory does not exist: {fixtures_path}")
        available_fixtures = sorted(
            d.name for d in fixtures_path.iterdir() if d.is_dir()
        )
        if not available_fixtures:
            parser.error(f"No fixture subdirectories found in: {fixtures_path}")
        print(f"Mock SDK mode enabled. Available fixtures: {', '.join(available_fixtures)}")
    else:
        fixtures_path = None
        available_fixtures = None

    # Create FastAPI app
    app = create_app(
        data_dir=data_dir_path,
        experimental=args.experimental,
        mock_sdk=args.mock_sdk,
        fixtures_dir=fixtures_path if args.mock_sdk else None,
        available_fixtures=available_fixtures,
    )

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
