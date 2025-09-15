#!/usr/bin/env python3
"""
Main entry point for Claude Code WebUI server.
"""

import asyncio
import sys
from pathlib import Path

import uvicorn

# Add src to path
sys.path.insert(0, str(Path(__file__).parent / "src"))

from src.web_server import create_app, startup_event, shutdown_event
from src.logging_config import setup_logging


def main():
    """Main function to start the Claude Code WebUI server."""

    # Setup logging
    setup_logging(log_level="INFO", enable_console=True)

    # Create FastAPI app
    app = create_app()

    # Add startup/shutdown events
    app.add_event_handler("startup", startup_event)
    app.add_event_handler("shutdown", shutdown_event)

    # Run the server
    uvicorn.run(
        app,
        host="127.0.0.1",
        port=8000,
        log_level="info",
        access_log=True
    )


if __name__ == "__main__":
    main()