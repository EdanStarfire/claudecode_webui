"""
Tests for issue #1840: capture subagent (Agent/Task tool) token usage in analytics.

Subagent `AssistantMessage`s carry `parent_tool_use_id` and their own per-call
`usage`. Before this fix, `AssistantMessageHandler` never extracted `usage` at
all, so every subagent call's tokens were silently dropped from analytics.

These tests drive `coordinator._create_message_callback(session_id)` end-to-end,
following the `TestIssue1831...` precedent in test_issue_1831_analytics_model_fallback.py.
"""

import tempfile
import uuid
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from backend.analytics.database import AnalyticsDB
from backend.analytics_store import AnalyticsStore
from backend.session_config import SessionConfig
from backend.session_coordinator import SessionCoordinator, _accumulate_subagent_usage


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


def _subagent_usage(input_tokens, output_tokens, cache_5m=0, cache_1h=0):
    return {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cache_read_input_tokens": 0,
        "cache_creation": {
            "ephemeral_5m_input_tokens": cache_5m,
            "ephemeral_1h_input_tokens": cache_1h,
        },
    }


class TestIssue1840SubagentUsageCapture:
    @pytest.mark.asyncio
    async def test_t1_single_subagent_call_merged_into_turn(self, temp_coordinator):
        """AC1/AC2: one subagent AssistantMessage's usage is added to the turn's
        recorded totals, including the TTL-tier breakdown."""
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
            "type": "assistant",
            "model": "claude-haiku-4-5",
            "parent_tool_use_id": "toolu_subagent_1",
            "usage": _subagent_usage(100, 20, cache_5m=15, cache_1h=3),
            "session_id": session_id,
        })
        await message_callback({
            "type": "result",
            "session_id": session_id,
            "usage": {"input_tokens": 10, "output_tokens": 5},
            "total_cost_usd": 1.0,
        })

        analytics_store.record_turn.assert_awaited_once()
        call_args = analytics_store.record_turn.call_args.args
        usage_delta = call_args[3]
        assert usage_delta["input_tokens"] == 10 + 100
        assert usage_delta["output_tokens"] == 5 + 20
        assert usage_delta["cache_write_tokens_5m"] == 15
        assert usage_delta["cache_write_tokens_1h"] == 3

    @pytest.mark.asyncio
    async def test_t3_no_double_counting_against_top_level_result(self, temp_coordinator):
        """AC3: the top-level ResultMessage's own usage and the subagent's usage
        are structurally disjoint inputs merged by addition, never derived from
        each other."""
        coordinator = temp_coordinator
        session_id = await _make_session(coordinator, SessionConfig())
        analytics_store = _mock_analytics(coordinator)

        message_callback = coordinator._create_message_callback(session_id)

        await message_callback({
            "type": "assistant",
            "parent_tool_use_id": "toolu_subagent_1",
            "usage": _subagent_usage(50, 10),
            "session_id": session_id,
        })
        await message_callback({
            "type": "result",
            "session_id": session_id,
            "usage": {"input_tokens": 200, "output_tokens": 80},
            "total_cost_usd": 2.0,
        })

        usage_delta = analytics_store.record_turn.call_args.args[3]
        # Both contributions present, neither overwritten by the other.
        assert usage_delta["input_tokens"] == 250
        assert usage_delta["output_tokens"] == 90

    @pytest.mark.asyncio
    async def test_multiple_subagent_calls_sum_not_diff(self, temp_coordinator):
        """Simulates the issue's real scenario (many subagent calls in one turn) —
        each call's usage is a per-call delta and must be summed, not baselined."""
        coordinator = temp_coordinator
        session_id = await _make_session(coordinator, SessionConfig())
        analytics_store = _mock_analytics(coordinator)

        message_callback = coordinator._create_message_callback(session_id)

        for _ in range(5):
            await message_callback({
                "type": "assistant",
                "parent_tool_use_id": "toolu_subagent_1",
                "usage": _subagent_usage(10, 2, cache_5m=1),
                "session_id": session_id,
            })
        await message_callback({
            "type": "result",
            "session_id": session_id,
            "usage": {"input_tokens": 0, "output_tokens": 0},
            "total_cost_usd": 0.1,
        })

        usage_delta = analytics_store.record_turn.call_args.args[3]
        assert usage_delta["input_tokens"] == 50
        assert usage_delta["output_tokens"] == 10
        assert usage_delta["cache_write_tokens_5m"] == 5

    @pytest.mark.asyncio
    async def test_ac4_session_without_subagents_unaffected(self, temp_coordinator):
        """AC4: a turn with no subagent messages must be byte-identical to
        pre-#1840 behaviour."""
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

        usage_delta = analytics_store.record_turn.call_args.args[3]
        assert usage_delta == {
            "input_tokens": 10.0,
            "output_tokens": 5.0,
            "cache_creation_input_tokens": 0.0,
            "cache_read_input_tokens": 0.0,
        }
        assert session_id not in coordinator._subagent_usage_by_session

    @pytest.mark.asyncio
    async def test_subagent_cache_write_folds_into_flat_total_for_cost_estimation(
        self, temp_coordinator
    ):
        """Regression: the subagent's TTL-tier cache-write usage must also fold into
        the pre-existing flat cache_creation_input_tokens field that
        record_turn()/compute_cost() key off of — otherwise cost estimation and the
        plain cache_write_tokens total would silently undercount whenever a turn
        used subagents, even though the dedicated TTL columns look correct."""
        coordinator = temp_coordinator
        session_id = await _make_session(coordinator, SessionConfig())
        analytics_store = _mock_analytics(coordinator)

        message_callback = coordinator._create_message_callback(session_id)

        await message_callback({
            "type": "assistant",
            "parent_tool_use_id": "toolu_subagent_1",
            "usage": _subagent_usage(1, 1, cache_5m=15, cache_1h=3),
            "session_id": session_id,
        })
        await message_callback({
            "type": "result",
            "session_id": session_id,
            "usage": {"input_tokens": 0, "output_tokens": 0},
            "total_cost_usd": 0.0,
        })

        usage_delta = analytics_store.record_turn.call_args.args[3]
        assert usage_delta["cache_write_tokens_5m"] == 15
        assert usage_delta["cache_write_tokens_1h"] == 3
        assert usage_delta["cache_creation_input_tokens"] == 18

    @pytest.mark.asyncio
    async def test_subagent_cache_write_adds_to_nonzero_main_thread_value(
        self, temp_coordinator
    ):
        """The fold-in must add to the main thread's own cache_creation_input_tokens,
        not overwrite it — both contributions are real and distinct."""
        coordinator = temp_coordinator
        session_id = await _make_session(coordinator, SessionConfig())
        analytics_store = _mock_analytics(coordinator)

        message_callback = coordinator._create_message_callback(session_id)

        await message_callback({
            "type": "assistant",
            "parent_tool_use_id": "toolu_subagent_1",
            "usage": _subagent_usage(1, 1, cache_5m=10, cache_1h=0),
            "session_id": session_id,
        })
        await message_callback({
            "type": "result",
            "session_id": session_id,
            "usage": {"input_tokens": 0, "output_tokens": 0, "cache_creation_input_tokens": 100},
            "total_cost_usd": 0.0,
        })

        usage_delta = analytics_store.record_turn.call_args.args[3]
        assert usage_delta["cache_creation_input_tokens"] == 110

    @pytest.mark.asyncio
    async def test_subagent_message_does_not_clobber_model_fallback(self, temp_coordinator):
        """Regression guard: #1840's new elif branch must not break #1831's
        existing model-fallback skip guard for subagent messages."""
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
            "type": "assistant",
            "model": "claude-haiku-4-5",
            "parent_tool_use_id": "toolu_subagent_1",
            "usage": _subagent_usage(1, 1),
            "session_id": session_id,
        })
        await message_callback({
            "type": "result",
            "session_id": session_id,
            "usage": {"input_tokens": 10, "output_tokens": 5},
            "total_cost_usd": 1.0,
        })

        model_arg = analytics_store.record_turn.call_args.args[2]
        assert model_arg == "claude-sonnet-4-6"


class TestIssue1840TerminationAndDeletionLifecycle:
    @pytest.mark.asyncio
    async def test_termination_flushes_leftover_subagent_usage(self, temp_coordinator):
        """§2c.2: a run_in_background subagent whose usage arrives after the
        session's last 'result' must be flushed as a catch-up row on
        terminate_session(), not silently discarded."""
        coordinator = temp_coordinator
        session_id = await _make_session(coordinator, SessionConfig())
        coordinator.legion_system = None  # skip schedule/legion cleanup, unrelated here
        analytics_store = _mock_analytics(coordinator)

        # Simulate a still-running background subagent's leftover usage, built via
        # the real accumulator helper (production code never hand-rolls this dict).
        coordinator._subagent_usage_by_session[session_id] = _accumulate_subagent_usage(
            None,
            {
                "input_tokens": 42,
                "cache_creation": {"ephemeral_5m_input_tokens": 3, "ephemeral_1h_input_tokens": 0},
            },
        )

        result = await coordinator.terminate_session(session_id)

        assert result is True
        analytics_store.record_turn.assert_awaited_once()
        call = analytics_store.record_turn.call_args
        assert call.args[0] == session_id
        assert call.args[3] == {
            "input_tokens": 42,
            "output_tokens": 0,
            "cache_read_input_tokens": 0,
            "cache_write_tokens_5m": 3,
            "cache_write_tokens_1h": 0,
            "cache_write_tokens": 3,
        }
        assert call.kwargs.get("is_subagent") is True
        assert session_id not in coordinator._subagent_usage_by_session

    @pytest.mark.asyncio
    async def test_termination_without_leftover_usage_does_not_write_extra_row(
        self, temp_coordinator
    ):
        coordinator = temp_coordinator
        session_id = await _make_session(coordinator, SessionConfig())
        coordinator.legion_system = None
        analytics_store = _mock_analytics(coordinator)

        result = await coordinator.terminate_session(session_id)

        assert result is True
        analytics_store.record_turn.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_deletion_drops_leftover_usage_without_persisting(self, temp_coordinator):
        """§2c.2: delete_session() wipes all analytics rows immediately after, so
        it must drop any leftover accumulator without writing a catch-up row —
        persisting first would accomplish nothing."""
        coordinator = temp_coordinator
        session_id = await _make_session(coordinator, SessionConfig())
        coordinator.legion_system = None
        analytics_store = _mock_analytics(coordinator)

        coordinator._subagent_usage_by_session[session_id] = {"input_tokens": 99}

        result = await coordinator.delete_session(session_id)

        assert result["success"] is True
        analytics_store.record_turn.assert_not_awaited()
        assert session_id not in coordinator._subagent_usage_by_session


class TestIssue1840UsageEndpointExposesTtlTiers:
    """Regression: GET /api/sessions/{id}/usage explicitly reshapes the aggregate
    dict field-by-field rather than passing it through verbatim — the new TTL-tier
    columns must be added to that reshaping or they silently never reach the API."""

    @pytest.fixture
    async def app_and_db(self, tmp_path):
        from fastapi import FastAPI

        from backend.routers.sessions import build_router

        db = AnalyticsDB(tmp_path / "analytics.db")
        await db.initialize()
        store = AnalyticsStore(db)

        webui = MagicMock()
        webui.coordinator.analytics_store = store
        webui.config_file = tmp_path / "nonexistent_config.json"

        app = FastAPI()
        app.include_router(build_router(webui))

        yield app, store
        await db.close()

    @pytest.mark.asyncio
    async def test_usage_endpoint_returns_ttl_tier_fields(self, app_and_db):
        from httpx import ASGITransport, AsyncClient

        app, store = app_and_db
        await store.record_turn(
            "sid-endpoint-1", 1, "claude-sonnet-4-6",
            {"input_tokens": 10, "cache_write_tokens_5m": 20, "cache_write_tokens_1h": 5},
            None,
        )

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/sessions/sid-endpoint-1/usage")

        assert resp.status_code == 200
        body = resp.json()
        assert body["cache_write_tokens_5m"] == 20
        assert body["cache_write_tokens_1h"] == 5


class TestIssue1840RecordTurnFailureSafetyNet:
    """Regression: a failed record_turn() write must not silently drop the
    subagent's contribution — the accumulator must be restored so a later turn
    or flush can retry, mirroring how _usage_baseline_by_session is left
    un-advanced on failure."""

    @pytest.mark.asyncio
    async def test_result_branch_restores_accumulator_on_write_failure(
        self, temp_coordinator
    ):
        coordinator = temp_coordinator
        session_id = await _make_session(coordinator, SessionConfig())
        analytics_store = _mock_analytics(coordinator)
        analytics_store.record_turn = AsyncMock(return_value=False)

        message_callback = coordinator._create_message_callback(session_id)

        await message_callback({
            "type": "assistant",
            "parent_tool_use_id": "toolu_subagent_1",
            "usage": _subagent_usage(10, 5, cache_5m=2),
            "session_id": session_id,
        })
        await message_callback({
            "type": "result",
            "session_id": session_id,
            "usage": {"input_tokens": 1, "output_tokens": 1},
            "total_cost_usd": 0.0,
        })

        restored = coordinator._subagent_usage_by_session.get(session_id)
        assert restored is not None
        assert restored["input_tokens"] == 10
        assert restored["cache_write_tokens_5m"] == 2

    @pytest.mark.asyncio
    async def test_termination_flush_restores_accumulator_on_write_failure(
        self, temp_coordinator
    ):
        coordinator = temp_coordinator
        session_id = await _make_session(coordinator, SessionConfig())
        coordinator.legion_system = None
        analytics_store = _mock_analytics(coordinator)
        analytics_store.record_turn = AsyncMock(return_value=False)

        expected = _accumulate_subagent_usage(None, {"input_tokens": 7})
        coordinator._subagent_usage_by_session[session_id] = expected

        result = await coordinator.terminate_session(session_id)

        assert result is True
        assert coordinator._subagent_usage_by_session.get(session_id) == expected


class TestIssue1840SessionUsageModelNotClobbered:
    """Regression: record_turn()'s session_usage UPSERT must not overwrite a
    session's known model with NULL just because a later row (e.g. a
    termination-time subagent catch-up flush) doesn't know the model."""

    @pytest.fixture
    async def store(self, tmp_path):
        db = AnalyticsDB(tmp_path / "analytics.db")
        await db.initialize()
        s = AnalyticsStore(db)
        yield s
        await db.close()

    @pytest.mark.asyncio
    async def test_null_model_turn_does_not_clobber_known_model(self, store):
        await store.record_turn("sid-model-1", 1, "claude-sonnet-4-6", {"input_tokens": 1}, None)
        await store.record_turn(
            "sid-model-1", 2, None, {"input_tokens": 1}, None, is_subagent=True
        )

        agg = await store.get_session_usage("sid-model-1")
        assert agg["model"] == "claude-sonnet-4-6"

    @pytest.mark.asyncio
    async def test_non_null_model_turn_still_updates_model(self, store):
        await store.record_turn("sid-model-2", 1, "claude-sonnet-4-6", {"input_tokens": 1}, None)
        await store.record_turn("sid-model-2", 2, "claude-haiku-4-5", {"input_tokens": 1}, None)

        agg = await store.get_session_usage("sid-model-2")
        assert agg["model"] == "claude-haiku-4-5"


class TestIssue1840TurnCountExcludesSubagentCatchUpRows:
    """Regression: turn_count must reflect real conversation turns, not be
    inflated by termination-time subagent catch-up rows (is_subagent=1)."""

    @pytest.fixture
    async def store(self, tmp_path):
        db = AnalyticsDB(tmp_path / "analytics.db")
        await db.initialize()
        s = AnalyticsStore(db)
        yield s
        await db.close()

    @pytest.mark.asyncio
    async def test_subagent_catch_up_row_not_counted_as_a_turn(self, store):
        await store.record_turn("sid-count-1", 1, "claude-sonnet-4-6", {"input_tokens": 1}, None)
        await store.record_turn("sid-count-1", 2, "claude-sonnet-4-6", {"input_tokens": 1}, None)
        await store.record_turn(
            "sid-count-1", 3, None, {"input_tokens": 1}, None, is_subagent=True
        )

        agg = await store.get_session_usage("sid-count-1")
        assert agg["turn_count"] == 2
        # Token totals still include the catch-up row's contribution.
        assert agg["input_tokens"] == 3
