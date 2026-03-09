"""
Shared fixtures for Legion MCP and REST API integration tests.

Provides:
- legion_test_env: Complete test environment with real LegionSystem
- api_integration_env: Full FastAPI app with TestClient for REST API testing
- --retain-test-data flag for debugging
"""

import shutil
import uuid
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from src.session_config import SessionConfig
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

        from src.session_manager import SessionState

        # Generate session_id
        session_id = str(uuid.uuid4())

        # Create session (issue #349: is_minion removed - all sessions are minions)
        config = SessionConfig(working_directory=str(Path.cwd()))
        await session_coordinator.session_manager.create_session(
            session_id=session_id,
            config=config,
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
        max_wait = 15.0
        poll_interval = 0.1
        start_time = time.time()

        while True:
            elapsed = time.time() - start_time

            if elapsed >= max_wait:
                session_info = await session_coordinator.session_manager.get_session_info(session_id)
                raise TimeoutError(
                    f"Session {name} ({session_id}) did not become ACTIVE within {max_wait}s. "
                    f"Current state: {session_info.state}"
                )

            session_info = await session_coordinator.session_manager.get_session_info(session_id)
            if session_info.state == SessionState.ACTIVE:
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


@pytest.fixture
async def api_integration_env(request, tmp_path):
    """
    Full FastAPI app with httpx.AsyncClient for REST API integration testing.

    Provides:
    - app: FastAPI application instance (ClaudeWebUI)
    - client: httpx.AsyncClient for HTTP requests
    - coordinator: Real SessionCoordinator with MockClaudeSDK factory
    - project_manager: Real ProjectManager
    - data_dir: Path to isolated temp data directory
    - create_test_project: Helper to create a project
    - create_test_session: Helper to create a session in a project
    - create_test_legion_project: Helper to create a multi-agent project
    - create_test_minion: Helper to create a minion in a legion project
    """
    from src.mock_sdk import MockClaudeSDK
    from src.web_server import ClaudeWebUI

    data_dir = tmp_path / "test_data_api"
    data_dir.mkdir()

    fixtures_dir = Path(__file__).parent.parent / "fixtures"

    webui = ClaudeWebUI(data_dir=data_dir)
    await webui.initialize()

    # Inject a lenient mock SDK factory: resolves fixture names when available,
    # falls back to a no-recording mock for CRUD-only sessions.
    def _lenient_mock_factory(session_id, working_directory, **kwargs):
        session_name = kwargs.pop("session_name", None)
        if session_name:
            candidate = fixtures_dir / session_name
            if candidate.is_dir():
                kwargs["session_dir"] = str(candidate)
        return MockClaudeSDK(
            session_id=session_id,
            working_directory=working_directory,
            **kwargs,
        )

    webui.coordinator.set_sdk_factory(_lenient_mock_factory)

    coordinator = webui.coordinator
    project_manager = coordinator.project_manager

    transport = ASGITransport(app=webui.app)
    client = AsyncClient(transport=transport, base_url="http://testserver")

    # --- Helper functions ---

    async def create_test_project(name="Test Project", **kwargs):
        """Create a project via the API and return the response dict."""
        payload = {
            "name": name,
            "working_directory": str(tmp_path),
            **kwargs,
        }
        resp = await client.post("/api/projects", json=payload)
        assert resp.status_code == 200, f"create_test_project failed: {resp.text}"
        return resp.json()["project"]

    async def create_test_session(project_id, name="Test Session", **kwargs):
        """Create a session in a project via the API and return the session info dict."""
        payload = {
            "project_id": project_id,
            "name": name,
            **kwargs,
        }
        resp = await client.post("/api/sessions", json=payload)
        assert resp.status_code == 200, f"create_test_session failed: {resp.text}"
        session_id = resp.json()["session_id"]
        # Fetch full session info
        resp2 = await client.get(f"/api/sessions/{session_id}")
        assert resp2.status_code == 200
        return resp2.json()["session"]

    async def create_test_legion_project(name="Test Legion", **kwargs):
        """Create a multi-agent project via the API and return the response dict."""
        return await create_test_project(name=name, **kwargs)

    async def create_test_minion(legion_id, name="Test Minion", role="Worker", **kwargs):
        """Create a minion in a legion project via the API and return the response dict."""
        payload = {
            "name": name,
            "role": role,
            **kwargs,
        }
        resp = await client.post(f"/api/legions/{legion_id}/minions", json=payload)
        assert resp.status_code == 200, f"create_test_minion failed: {resp.text}"
        return resp.json()["session"]

    env = {
        "app": webui.app,
        "webui": webui,
        "client": client,
        "coordinator": coordinator,
        "project_manager": project_manager,
        "data_dir": data_dir,
        "fixtures_dir": fixtures_dir,
        "create_test_project": create_test_project,
        "create_test_session": create_test_session,
        "create_test_legion_project": create_test_legion_project,
        "create_test_minion": create_test_minion,
    }

    yield env

    # Cleanup
    await client.aclose()
    try:
        for session_id in list(coordinator._active_sdks.keys()):
            try:
                await coordinator.terminate_session(session_id)
            except Exception:
                pass
    except Exception:
        pass


@pytest.fixture
def ws_integration_env(tmp_path):
    """
    WebSocket integration test environment with Starlette TestClient.

    Provides both REST and WebSocket access to the FastAPI app
    with MockClaudeSDK for deterministic message replay.
    """
    import time

    from starlette.testclient import TestClient

    from src.mock_sdk import MockClaudeSDK
    from src.web_server import ClaudeWebUI

    data_dir = tmp_path / "test_data_ws"
    data_dir.mkdir()
    fixtures_dir = Path(__file__).parent.parent / "fixtures"

    import asyncio

    webui = ClaudeWebUI(data_dir=data_dir)

    # Initialize app (creates data directories, loads templates, etc.)
    # Must happen before TestClient starts, since there's no lifespan event.
    asyncio.run(webui.initialize())

    test_client = TestClient(webui.app)
    test_client.__enter__()

    # Inject mock SDK factory (same pattern as api_integration_env)
    def _lenient_mock_factory(session_id, working_directory, **kwargs):
        session_name = kwargs.pop("session_name", None)
        if session_name:
            candidate = fixtures_dir / session_name
            if candidate.is_dir():
                kwargs["session_dir"] = str(candidate)
        return MockClaudeSDK(
            session_id=session_id,
            working_directory=working_directory,
            **kwargs,
        )

    webui.coordinator.set_sdk_factory(_lenient_mock_factory)

    def create_project(name="Test Project"):
        resp = test_client.post("/api/projects", json={
            "name": name,
            "working_directory": str(tmp_path),
        })
        assert resp.status_code == 200
        return resp.json()["project"]

    def create_session(project_id, name="Test Session", fixture_name=None):
        # When fixture_name is provided, use it as the session name so the
        # mock SDK factory can resolve it to the fixtures directory.
        session_name = fixture_name if fixture_name else name
        payload = {"project_id": project_id, "name": session_name}
        resp = test_client.post("/api/sessions", json=payload)
        assert resp.status_code == 200
        session_id = resp.json()["session_id"]
        resp2 = test_client.get(f"/api/sessions/{session_id}")
        return resp2.json()["session"]

    def start_session(session_id):
        resp = test_client.post(f"/api/sessions/{session_id}/start")
        assert resp.status_code == 200
        return resp

    def wait_for_state(session_id, target_state, timeout=10):
        for _ in range(int(timeout / 0.2)):
            resp = test_client.get(f"/api/sessions/{session_id}")
            if resp.json()["session"]["state"] == target_state:
                return True
            time.sleep(0.2)
        return False

    env = {
        "test_client": test_client,
        "webui": webui,
        "coordinator": webui.coordinator,
        "data_dir": data_dir,
        "fixtures_dir": fixtures_dir,
        "create_project": create_project,
        "create_session": create_session,
        "start_session": start_session,
        "wait_for_state": wait_for_state,
    }

    yield env

    # Cleanup: close TestClient first (stops background thread), then terminate SDKs
    test_client.__exit__(None, None, None)
    try:
        async def _cleanup():
            for sid in list(webui.coordinator._active_sdks.keys()):
                try:
                    await webui.coordinator.terminate_session(sid)
                except Exception:
                    pass
        asyncio.run(_cleanup())
    except Exception:
        pass


def pytest_addoption(parser):
    """Add --retain-test-data flag for debugging."""
    parser.addoption(
        "--retain-test-data",
        action="store_true",
        default=False,
        help="Retain test_data_mcp/ directory after tests for inspection"
    )
