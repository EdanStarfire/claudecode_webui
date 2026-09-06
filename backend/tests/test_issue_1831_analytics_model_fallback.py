"""
Tests for issue #1831: analytics records NULL model when no configured override
resolves, even though the SDK reported a real model for that turn.

The fix tracks the most recent assistant-reported model for the current
in-flight turn (`SessionCoordinator._last_turn_model_by_session`) and uses it as
a fallback exactly when `_resolve_analytics_model_label()` yields None. These
tests drive `coordinator._create_message_callback(session_id)` end-to-end,
following the `TestIssue1838...` precedent in test_session_coordinator.py.
"""

import tempfile
import uuid
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from backend.session_config import SessionConfig
from backend.session_coordinator import SessionCoordinator


@pytest.fixture
async def temp_coordinator():
    with tempfile.TemporaryDirectory() as temp_dir:
        coordinator = SessionCoordinator(Path(temp_dir))
        await coordinator.initialize()
        yield coordinator
        await coordinator.cleanup()


async def _make_session(coordinator, config: SessionConfig) -> str:
    """Create a session (with a backing project) and return its session_id."""
    project = await coordinator.project_manager.create_project(
        name="Test Project", working_directory="/test/project"
    )
    session_id = str(uuid.uuid4())
    await coordinator.create_session(
        session_id=session_id, project_id=project.project_id, config=config
    )
    return session_id


def _mock_analytics(coordinator):
    coordinator.analytics_store = AsyncMock()
    coordinator.analytics_store.get_turn_count.return_value = 0
    coordinator.analytics_store.get_session_usage.return_value = None
    coordinator.analytics_store.record_turn = AsyncMock(return_value=True)
    return coordinator.analytics_store


class TestIssue1831AnalyticsModelFallback:
    @pytest.mark.asyncio
    async def test_no_override_falls_back_to_sdk_reported_model(self, temp_coordinator):
        """T1: no configured override → analytics uses the assistant message's model."""
        coordinator = temp_coordinator
        session_id = await _make_session(coordinator, SessionConfig())
        analytics_store = _mock_analytics(coordinator)

        message_callback = coordinator._create_message_callback(session_id)

        await message_callback({
            "type": "assistant",
            "model": "claude-sonnet-4-6",
            "session_id": session_id,
        })
        await message_callback({
            "type": "result",
            "session_id": session_id,
            "usage": {"input_tokens": 10, "output_tokens": 5},
            "total_cost_usd": 1.0,
        })

        analytics_store.record_turn.assert_awaited_once()
        _, _, model_arg, _, _ = analytics_store.record_turn.call_args.args
        assert model_arg == "claude-sonnet-4-6"

    @pytest.mark.asyncio
    async def test_configured_override_wins_over_sdk_reported_model(self, temp_coordinator):
        """T2: configured override still wins, unaffected by the fallback."""
        coordinator = temp_coordinator
        session_id = await _make_session(
            coordinator, SessionConfig(model="claude-opus-4-configured")
        )
        analytics_store = _mock_analytics(coordinator)

        message_callback = coordinator._create_message_callback(session_id)

        await message_callback({
            "type": "assistant",
            "model": "claude-sonnet-4-6",
            "session_id": session_id,
        })
        await message_callback({
            "type": "result",
            "session_id": session_id,
            "usage": {"input_tokens": 10, "output_tokens": 5},
            "total_cost_usd": 1.0,
        })

        analytics_store.record_turn.assert_awaited_once()
        _, _, model_arg, _, _ = analytics_store.record_turn.call_args.args
        assert model_arg == "claude-opus-4-configured"

    @pytest.mark.asyncio
    async def test_genuinely_modelless_turn_records_none_no_cross_turn_leak(
        self, temp_coordinator
    ):
        """T3: a result with no preceding assistant message in that turn records
        None — both standalone, and as a second turn after a T1-style turn (to
        prove the fallback cache doesn't leak across turns)."""
        coordinator = temp_coordinator
        session_id = await _make_session(coordinator, SessionConfig())
        analytics_store = _mock_analytics(coordinator)

        message_callback = coordinator._create_message_callback(session_id)

        # Turn 1: assistant + result → fallback consumed and cleared.
        await message_callback({
            "type": "assistant",
            "model": "claude-sonnet-4-6",
            "session_id": session_id,
        })
        await message_callback({
            "type": "result",
            "session_id": session_id,
            "usage": {"input_tokens": 10, "output_tokens": 5},
            "total_cost_usd": 1.0,
        })

        # Turn 2: immediate result, no assistant message at all this turn.
        await message_callback({
            "type": "result",
            "session_id": session_id,
            "usage": {"input_tokens": 1, "output_tokens": 1},
            "total_cost_usd": 0.1,
        })

        assert analytics_store.record_turn.await_count == 2
        second_call_args = analytics_store.record_turn.call_args_list[1].args
        _, _, model_arg, _, _ = second_call_args
        assert model_arg is None

    @pytest.mark.asyncio
    async def test_subagent_assistant_message_does_not_clobber_fallback(self, temp_coordinator):
        """A sub-agent (Task tool) assistant message carries parent_tool_use_id and
        must not overwrite the top-level turn's tracked model — it belongs to a
        nested sub-conversation, not "this turn" from the top-level result's
        perspective."""
        coordinator = temp_coordinator
        session_id = await _make_session(coordinator, SessionConfig())
        analytics_store = _mock_analytics(coordinator)

        message_callback = coordinator._create_message_callback(session_id)

        await message_callback({
            "type": "assistant",
            "model": "claude-sonnet-4-6",
            "session_id": session_id,
        })
        # Sub-agent message for a Task tool call — different model, tagged as a sidechain.
        await message_callback({
            "type": "assistant",
            "model": "claude-haiku-4-5",
            "parent_tool_use_id": "toolu_subagent_1",
            "session_id": session_id,
        })
        await message_callback({
            "type": "result",
            "session_id": session_id,
            "usage": {"input_tokens": 10, "output_tokens": 5},
            "total_cost_usd": 1.0,
        })

        analytics_store.record_turn.assert_awaited_once()
        _, _, model_arg, _, _ = analytics_store.record_turn.call_args.args
        assert model_arg == "claude-sonnet-4-6"

    @pytest.mark.asyncio
    async def test_cleanup_on_session_deletion(self, temp_coordinator):
        """T4: deleting a session clears its tracked in-flight-turn model."""
        coordinator = temp_coordinator
        session_id = await _make_session(coordinator, SessionConfig())
        coordinator.legion_system = None  # skip schedule/legion cleanup, unrelated here

        coordinator._last_turn_model_by_session[session_id] = "claude-sonnet-4-6"

        result = await coordinator.delete_session(session_id)

        assert result["success"] is True
        assert session_id not in coordinator._last_turn_model_by_session
