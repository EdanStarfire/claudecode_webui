#!/usr/bin/env python3
"""
Main entry point for the Backend control-plane process (issue #498).

Independently runnable: `python -m backend.main --host 127.0.0.1 --port <n> --token <t>
--data-dir <dir>`. Auto-started by the Frontend API for the single-user self-hosted
case (backend_supervisor.py, Phase 3) or pointed at manually for a remote deployment.
"""

import argparse
import os
import secrets
import sys
from pathlib import Path

import uvicorn

# Add project root to path (so imports like "from backend.legion..." / "from shared..." work)
sys.path.insert(0, str(Path(__file__).parent.parent))

from backend.config_manager import check_network_binding, ensure_config_file, load_config
from backend.secrets_keyring import configure_keyring
from backend.web_server import create_app
from shared.logging_config import configure_logging


def configure_webui_base_url(port: int) -> None:
    """Advertise this Backend's own bind port so Docker/LiteLLM-proxy sidecars call
    back on the right port (issue #498 Phase 4).

    session_coordinator.py reads WEBUI_BASE_URL via os.environ.get() and forwards it
    into spawned sidecar containers (docker_utils.py); cc-webui.internal is a
    --add-host alias (backend/docker/claude-docker) resolving to the Docker host
    machine, so only the port needs to be correct here. Docker lives entirely in
    Backend now — this used to be set by the pre-split unified main.py using
    Frontend's port, which would have been wrong for sidecars now calling Backend.
    """
    os.environ.setdefault("WEBUI_BASE_URL", f"http://cc-webui.internal:{port}")


def main():
    """Main function to start the Backend control-plane server."""

    parser = argparse.ArgumentParser(
        description="Claude Code WebUI Backend (control plane)",
    )

    # Server options
    parser.add_argument(
        '--host', default='127.0.0.1',
        help='Bind address (default: 127.0.0.1, localhost only).'
    )
    parser.add_argument('--port', type=int, default=8100, help='Port to bind to (default: 8100)')
    parser.add_argument('--data-dir', default='./data', help='Data directory location (default: ./data)')

    # Experimental features
    parser.add_argument('--experimental', action='store_true', help='Enable experimental features (Agent Teams)')

    # Backend-scoped auth token — always required, no "auth disabled" mode (issue #498:
    # two trust boundaries, browser token and backend token, are never bridged).
    parser.add_argument(
        '--token', type=str, default=None,
        help='Backend-scoped bearer token (generated if not supplied — standalone/manual use only; '
             'auto-started Backends always receive one from the Frontend supervisor)'
    )

    # Mock SDK mode (for browser automation testing — issue #561)
    parser.add_argument(
        '--mock-sdk', action='store_true',
        help='Use MockClaudeSDK with fixture replay instead of real SDK'
    )
    parser.add_argument(
        '--fixtures-dir', type=str, default=None,
        help='Directory containing named fixture subdirectories (required with --mock-sdk)'
    )

    # Debug flags (backend-relevant subset — polling/static/auth-check flags stay Frontend-side)
    debug_group = parser.add_argument_group("Debug Flags")
    debug_group.add_argument('--debug-polling', action='store_true', help='Enable poll transport signal logging (events-returned lines)')
    debug_group.add_argument('--debug-all-polling', action='store_true', help='Enable full uvicorn access-log for /api/poll/* (high volume)')
    debug_group.add_argument('--debug-sdk', action='store_true', help='Enable SDK integration debugging')
    debug_group.add_argument('--debug-permissions', action='store_true', help='Enable permission callback debugging')
    debug_group.add_argument('--debug-storage', action='store_true', help='Enable data storage debugging')
    debug_group.add_argument('--debug-parser', action='store_true', help='Enable message parser debugging')
    debug_group.add_argument('--debug-error-handler', action='store_true', help='Enable error handler debugging')
    debug_group.add_argument('--debug-legion', action='store_true', help='Enable Legion multi-agent system debugging')
    debug_group.add_argument('--debug-session-manager', action='store_true', help='Enable session manager debugging')
    debug_group.add_argument('--debug-template-manager', action='store_true', help='Enable template manager debugging')
    debug_group.add_argument('--debug-skill-manager', action='store_true', help='Enable skill manager debugging')
    debug_group.add_argument('--debug-queue-manager', action='store_true', help='Enable queue manager debugging')
    debug_group.add_argument('--debug-queue-processor', action='store_true', help='Enable queue processor debugging')
    debug_group.add_argument('--debug-archive', action='store_true', help='Enable archive manager debugging')
    debug_group.add_argument('--debug-project-manager', action='store_true', help='Enable project manager debugging')
    debug_group.add_argument('--debug-profile-manager', action='store_true', help='Enable profile manager debugging')
    debug_group.add_argument('--debug-all', action='store_true', help='Enable all debug logging (excludes --debug-all-polling)')

    args = parser.parse_args()

    # Configure keyring backend early (before ApplicationService init)
    configure_keyring()

    # Ensure config file exists (creates with safe defaults on first run) — shared
    # ~/.config/cc_webui/config.json, Backend owns everything but the networking/
    # backend_connection sections (issue #498 config-split design).
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
        debug_polling=args.debug_polling,
        debug_all_polling=args.debug_all_polling,
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
        debug_profile_manager=args.debug_profile_manager,
        debug_all=args.debug_all,
        log_dir=str(data_dir_path / "logs" / "backend")
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

    auth_token = args.token or secrets.token_urlsafe(32)
    if not args.token:
        print(f"No --token supplied; generated one for standalone use: {auth_token}")

    configure_webui_base_url(args.port)

    # Create FastAPI app
    app = create_app(
        data_dir=data_dir_path,
        experimental=args.experimental,
        mock_sdk=args.mock_sdk,
        fixtures_dir=fixtures_path if args.mock_sdk else None,
        available_fixtures=available_fixtures,
        auth_token=auth_token,
        host=args.host,
        port=args.port,
    )

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
