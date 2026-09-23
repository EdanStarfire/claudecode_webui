#!/usr/bin/env python3
"""CLI wrapper around backend.fixture_export.export_fixture (issue #1998).

Thin terminal entry point for exporting a recording session's raw log into a
named fixture directory, for scripted/terminal use alongside the REST/UI
export path (session manage modal). Translates FixtureExportError into a
real non-zero process exit, satisfying AC4's literal "exits non-zero"
wording without making the UI path depend on this CLI.

Usage:
    uv run python -m backend.tools.export_fixture_cli <session_id> [--name NAME] [--data-dir DIR]
"""

import argparse
import asyncio
import sys
from pathlib import Path

from backend.fixture_export import FixtureExportError, export_fixture
from backend.session_coordinator import SessionCoordinator


async def _run(session_id: str, name: str, data_dir: Path) -> int:
    coordinator = SessionCoordinator(data_dir)
    await coordinator.initialize()
    try:
        result = await export_fixture(coordinator, session_id, name)
    except FixtureExportError as e:
        print(f"Export failed: {e}", file=sys.stderr)
        for marker in e.missing_markers:
            print(f"  missing: {marker}", file=sys.stderr)
        return 1
    except ValueError as e:
        print(f"Export failed: {e}", file=sys.stderr)
        return 1
    else:
        print(f"Export succeeded: {result.fixture_dir}")
        for marker, present in result.markers.items():
            print(f"  {'✔' if present else '✘'} {marker}")
        return 0
    finally:
        await coordinator.cleanup()


def main() -> int:
    parser = argparse.ArgumentParser(description="Export a recording session as a test fixture")
    parser.add_argument("session_id", help="Session ID to export")
    parser.add_argument(
        "--name", default=None,
        help="Fixture directory name (default: the session_id)",
    )
    parser.add_argument(
        "--data-dir", default="./data",
        help="Data directory location (default: ./data)",
    )
    args = parser.parse_args()

    data_dir = Path(args.data_dir).resolve()
    name = args.name or args.session_id
    return asyncio.run(_run(args.session_id, name, data_dir))


if __name__ == "__main__":
    sys.exit(main())
