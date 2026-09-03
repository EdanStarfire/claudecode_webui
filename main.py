#!/usr/bin/env python3
"""
Main entry point for the Frontend API process (issue #498).

Serves the browser, authenticates it, and relays every domain request to a
Backend control-plane process — either a manually-configured remote Backend
(--remote-backend-url/--remote-backend-token, wired here) or, once Phase 3
lands, an auto-started local one when neither flag is given.
"""

import argparse
import secrets
import sys
from pathlib import Path

import uvicorn

# Add project root to path (so imports like "from shared..." work)
sys.path.insert(0, str(Path(__file__).parent))

from shared.logging_config import configure_logging
from src.frontend_config import check_network_binding, ensure_config_file, load_frontend_config
from src.web_server import create_app


def main():
    """Main function to start the Frontend API server."""

    # Parse command-line arguments
    parser = argparse.ArgumentParser(
        description="Claude Code WebUI Frontend API",
    )

    # Server options
    parser.add_argument(
        '--host', default='127.0.0.1',
        help='Bind address (default: 127.0.0.1, localhost only). Use --host 0.0.0.0 to allow remote access.'
    )
    parser.add_argument('--port', type=int, default=8000, help='Port to bind to (default: 8000)')

    # Backend connection (issue #498) — manual path only until Phase 3's
    # backend_supervisor.py adds auto-start for the single-user self-hosted case.
    parser.add_argument(
        '--remote-backend-url', type=str, default=None,
        help='URL of a manually-configured Backend (e.g. http://127.0.0.1:8100). '
             'Required until Phase 3 adds auto-start.'
    )
    parser.add_argument(
        '--remote-backend-token', type=str, default=None,
        help='Backend-scoped bearer token for --remote-backend-url.'
    )

    # Authentication options (issue #728)
    parser.add_argument(
        '--no-auth', action='store_true',
        help='Disable authentication even when binding to non-localhost addresses'
    )
    parser.add_argument(
        '--token', type=str, default=None,
        help='Use a specific auth token instead of generating a random one'
    )
    parser.add_argument(
        '--force-auth', action='store_true',
        help='Force authentication even when binding to localhost'
    )

    # Debug flags (Frontend-relevant subset — session-execution debug flags moved
    # to backend/main.py, since that's the only process with anything to debug there)
    debug_group = parser.add_argument_group("Debug Flags")
    debug_group.add_argument('--debug-polling', action='store_true', help='Enable poll transport signal logging (events-returned lines)')
    debug_group.add_argument('--debug-all-polling', action='store_true', help='Enable full uvicorn access-log for /api/poll/* (high volume)')
    debug_group.add_argument('--debug-error-handler', action='store_true', help='Enable error handler debugging')
    debug_group.add_argument('--debug-all', action='store_true', help='Enable all debug logging (excludes --debug-all-polling)')

    args = parser.parse_args()

    # Ensure config file exists (creates with safe defaults on first run)
    config_file = ensure_config_file()
    frontend_config = load_frontend_config(config_file)

    # Validate network binding permission
    if not check_network_binding(args.host, frontend_config, config_file):
        sys.exit(1)

    # Configure logging with debug flags
    configure_logging(
        debug_polling=args.debug_polling,
        debug_all_polling=args.debug_all_polling,
        debug_error_handler=args.debug_error_handler,
        debug_all=args.debug_all,
        log_dir="data/logs",
    )

    # Determine authentication settings (issue #728)
    is_localhost = args.host in ('127.0.0.1', 'localhost', '::1')
    if args.force_auth:
        auth_enabled = True
    elif args.no_auth:
        auth_enabled = False
    else:
        auth_enabled = not is_localhost

    auth_token = args.token or secrets.token_urlsafe(32)

    if auth_enabled:
        print("\n" + "=" * 60)
        print("  AUTHENTICATION ENABLED")
        print(f"  Token: {auth_token}")
        print()
        print("  Open in browser with token:")
        print(f"  http://{args.host}:{args.port}/?token={auth_token}")
        print("=" * 60 + "\n")
    else:
        print("Authentication disabled (localhost binding)")

    # Resolve Backend connection: CLI flags override config's backend_connection
    # section, which overrides nothing yet (Phase 3 adds auto-start as the final
    # fallback when neither is set).
    backend_url = args.remote_backend_url or frontend_config.backend_connection.remote_backend_url
    backend_token = args.remote_backend_token or frontend_config.backend_connection.remote_backend_token
    if not backend_url or not backend_token:
        print(
            "ERROR: --remote-backend-url and --remote-backend-token are required "
            "(or set backend_connection in config) until Phase 3 adds Backend auto-start.",
            file=sys.stderr,
        )
        sys.exit(1)

    # Create FastAPI app
    app = create_app(
        backend_url=backend_url,
        backend_token=backend_token,
        config_file=config_file,
        auth_token=auth_token if auth_enabled else None,
        auth_enabled=auth_enabled,
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
