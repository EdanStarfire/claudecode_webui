#!/usr/bin/env python3
"""
Main entry point for the Frontend API process (issue #498).

Serves the browser, authenticates it, and relays every domain request to a
Backend control-plane process. Default (single-user self-hosted): auto-starts
a local Backend bound to 127.0.0.1 with zero manual configuration
(backend_supervisor.py). Pass --remote-backend-url/--remote-backend-token to
skip auto-start and point at a manually-configured Backend instead.
"""

import argparse
import secrets
import sys
from pathlib import Path

import uvicorn

# Add project root to path (so imports like "from shared..." work)
sys.path.insert(0, str(Path(__file__).parent))

from shared.logging_config import configure_logging
from src.backend_supervisor import BackendSupervisor
from src.frontend_config import check_network_binding, ensure_config_file, load_frontend_config
from src.web_server import create_app

# Backend debug flags passed through to an auto-started Backend (issue #498) —
# Frontend itself doesn't use these, they're forwarded verbatim to backend.main.
_BACKEND_DEBUG_FLAGS = [
    'sdk', 'permissions', 'storage', 'parser', 'legion', 'session-manager',
    'template-manager', 'skill-manager', 'queue-manager', 'queue-processor',
    'archive', 'project-manager', 'profile-manager',
]


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
    parser.add_argument(
        '--data-dir', default='./data',
        help='Data directory location (default: ./data). Passed through to an auto-started '
             'Backend; unused when --remote-backend-url points at a manually-run one.'
    )

    # Backend connection (issue #498) — if neither is given, auto-starts a local
    # Backend (backend_supervisor.py). Both are required together for the manual path.
    parser.add_argument(
        '--remote-backend-url', type=str, default=None,
        help='URL of a manually-configured Backend (e.g. http://127.0.0.1:8100). '
             'Skips auto-start when given.'
    )
    parser.add_argument(
        '--remote-backend-token', type=str, default=None,
        help='Backend-scoped bearer token for --remote-backend-url.'
    )

    # Experimental features / mock SDK — passed through to an auto-started Backend
    parser.add_argument('--experimental', action='store_true', help='Enable experimental features (Agent Teams) on Backend')
    parser.add_argument(
        '--mock-sdk', action='store_true',
        help='Use MockClaudeSDK with fixture replay instead of real SDK (auto-started Backend only)'
    )
    parser.add_argument(
        '--fixtures-dir', type=str, default=None,
        help='Directory containing named fixture subdirectories (required with --mock-sdk)'
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

    # Debug flags (Frontend-relevant subset run here; the rest are forwarded to an
    # auto-started Backend verbatim — see _BACKEND_DEBUG_FLAGS)
    debug_group = parser.add_argument_group("Debug Flags")
    debug_group.add_argument('--debug-polling', action='store_true', help='Enable poll transport signal logging (events-returned lines)')
    debug_group.add_argument('--debug-all-polling', action='store_true', help='Enable full uvicorn access-log for /api/poll/* (high volume)')
    debug_group.add_argument('--debug-error-handler', action='store_true', help='Enable error handler debugging')
    debug_group.add_argument('--debug-all', action='store_true', help='Enable all debug logging (Frontend + forwarded to Backend)')
    for flag in _BACKEND_DEBUG_FLAGS:
        debug_group.add_argument(f'--debug-{flag}', action='store_true', help=f'Enable {flag} debugging on an auto-started Backend')

    args = parser.parse_args()

    # Ensure config file exists (creates with safe defaults on first run)
    config_file = ensure_config_file()
    frontend_config = load_frontend_config(config_file)

    # Validate network binding permission
    if not check_network_binding(args.host, frontend_config, config_file):
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
        debug_error_handler=args.debug_error_handler,
        debug_all=args.debug_all,
        log_dir=str(data_dir_path / "logs"),
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

    # Resolve Backend connection: explicit CLI flags (or config's backend_connection
    # section) skip auto-start entirely; otherwise auto-start a local Backend.
    backend_url = args.remote_backend_url or frontend_config.backend_connection.remote_backend_url
    backend_token = args.remote_backend_token or frontend_config.backend_connection.remote_backend_token

    backend_supervisor = None
    if backend_url and backend_token:
        print(f"Using manually-configured Backend at {backend_url}")
    elif backend_url or backend_token:
        print(
            "ERROR: --remote-backend-url and --remote-backend-token must be given together.",
            file=sys.stderr,
        )
        sys.exit(1)
    else:
        if args.mock_sdk and not args.fixtures_dir:
            parser.error("--fixtures-dir is required when --mock-sdk is specified")
        extra_backend_args = [
            f'--debug-{flag}' for flag in _BACKEND_DEBUG_FLAGS
            if getattr(args, f'debug_{flag.replace("-", "_")}')
        ]
        if args.debug_all:
            extra_backend_args.append('--debug-all')
        backend_supervisor = BackendSupervisor(
            data_dir=data_dir_path,
            experimental=args.experimental,
            mock_sdk=args.mock_sdk,
            fixtures_dir=Path(args.fixtures_dir).resolve() if args.fixtures_dir else None,
            extra_backend_args=extra_backend_args,
            log_dir=data_dir_path / "logs" / "backend",
        )
        print(f"Auto-starting Backend on 127.0.0.1:{backend_supervisor.port}")

    # Create FastAPI app
    app = create_app(
        backend_url=backend_url,
        backend_token=backend_token,
        backend_supervisor=backend_supervisor,
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
