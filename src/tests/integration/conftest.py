"""
Shared fixtures for Legion MCP integration tests.

Provides:
- legion_test_env: Complete test environment with real LegionSystem
- --retain-test-data flag for debugging
"""

import shutil
from pathlib import Path

import pytest

from src.session_coordinator import SessionCoordinator


@pytest.fixture
async def legion_test_env(request):
    """
    Create isolated legion testing environment with real components.

    Provides:
    - Clean test_data_mcp/ directory
    - Initialized SessionCoordinator with all components
    - Test legion project (multi-agent)
    - Helper function: create_minion()
    - Auto-cleanup (unless --retain-test-data flag)

    Returns:
        dict with keys:
        - session_coordinator: SessionCoordinator instance
        - project_manager: ProjectManager instance
        - template_manager: TemplateManager instance
        - legion_system: LegionSystem instance
        - legion_id: UUID of test legion project
        - project: ProjectInfo of test legion
        - data_dir: Path to test_data_mcp/
        - create_minion: Helper function to create test minions
    """
    # Setup: Create test data directory
    data_dir = Path("test_data_mcp")
    data_dir.mkdir(exist_ok=True)

    # Initialize real SessionCoordinator (which creates LegionSystem)
    session_coordinator = SessionCoordinator(data_dir=data_dir)
    await session_coordinator.initialize()

    # Access components
    project_manager = session_coordinator.project_manager
    template_manager = session_coordinator.template_manager
    legion_system = session_coordinator.legion_system

    # Create test legion project (all projects support minions - issue #313)
    project = await project_manager.create_project(
        name="Test Legion",
        working_directory=str(Path.cwd())
    )
    legion_id = project.project_id

    # Helper function: create minion in test legion
    async def create_test_minion(name, role="Test Minion", **kwargs):
        """
        Helper to create a minion in the test legion.

        Args:
            name: Minion name
            role: Minion role (default: "Test Minion")
            **kwargs: Additional session creation parameters

        Returns:
            SessionInfo of created minion (in ACTIVE state)
        """
        import asyncio
        import uuid

        from src.session_manager import SessionState

        # Generate session_id
        session_id = str(uuid.uuid4())

        # Create session (issue #349: is_minion removed - all sessions are minions)
        await session_coordinator.session_manager.create_session(
            session_id=session_id,
            working_directory=str(Path.cwd()),
            name=name,
            role=role,
            project_id=legion_id,
            **kwargs
        )

        # Add session to project's session list
        await project_manager.add_session_to_project(legion_id, session_id)

        # Start the minion session (required for active minions)
        await session_coordinator.start_session(session_id)

        # Wait for session to become ACTIVE (with timeout and timing)
        import time
        max_wait = 50.0  # 50 second timeout (generous for CI environments)
        poll_interval = 0.1  # Check every 100ms
        start_time = time.time()

        while True:
            elapsed = time.time() - start_time

            if elapsed >= max_wait:
                # Timeout - raise error
                session_info = await session_coordinator.session_manager.get_session_info(session_id)
                raise TimeoutError(
                    f"Session {name} ({session_id}) did not become ACTIVE within {max_wait}s. "
                    f"Current state: {session_info.state}"
                )

            session_info = await session_coordinator.session_manager.get_session_info(session_id)
            if session_info.state == SessionState.ACTIVE:
                # Session is active - check timing thresholds
                if elapsed > 45.0:
                    raise RuntimeError(
                        f"ERROR: Session {name} took {elapsed:.2f}s to become ACTIVE (>45s threshold). "
                        "This indicates a serious performance issue."
                    )
                elif elapsed > 20.0:
                    import warnings
                    warnings.warn(
                        f"WARNING: Session {name} took {elapsed:.2f}s to become ACTIVE (>20s threshold). "
                        "This is slower than expected.",
                        UserWarning
                    )

                # Log timing for all sessions (useful for performance tracking)
                print(f"  > Session {name} became ACTIVE in {elapsed:.2f}s")
                return session_info

            await asyncio.sleep(poll_interval)

    # Build environment dict
    env = {
        "session_coordinator": session_coordinator,
        "project_manager": project_manager,
        "template_manager": template_manager,
        "legion_system": legion_system,
        "legion_id": legion_id,
        "project": project,
        "data_dir": data_dir,
        "create_minion": create_test_minion,
    }

    # Yield to test
    yield env

    # Cleanup: Delete test_data_mcp/ unless --retain-test-data flag
    if not request.config.getoption("--retain-test-data", default=False):
        try:
            # Terminate all active SDK sessions first
            for session_id in list(session_coordinator._active_sdks.keys()):
                try:
                    await session_coordinator.terminate_session(session_id)
                except Exception:
                    pass  # Ignore errors during cleanup

            # Remove test data directory
            if data_dir.exists():
                shutil.rmtree(data_dir, ignore_errors=True)
        except Exception as e:
            # Log but don't fail cleanup
            print(f"Warning: Test cleanup failed: {e}")


def pytest_addoption(parser):
    """Add --retain-test-data flag for debugging."""
    parser.addoption(
        "--retain-test-data",
        action="store_true",
        default=False,
        help="Retain test_data_mcp/ directory after tests for inspection"
    )
