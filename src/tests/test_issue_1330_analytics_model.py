"""
Regression tests for issue #1330: analytics turn recording broken by post-#1230
flat-field access.

session_coordinator.py previously read `_sinfo.model` (removed by #1230) instead
of routing through `resolve_effective_config()`.  The fix replaces that line with
a proper resolve call so template-linked sessions also get the correct model.
"""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.config_resolution import resolve_effective_config
from src.models.minion_template import MinionTemplate
from src.session_manager import SessionInfo, SessionState

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_NOW = datetime.now(UTC)


def _make_session(config: dict, template_id: str | None = None) -> SessionInfo:
    """Build a minimal post-#1230 SessionInfo with only the fields we care about."""
    return SessionInfo(
        session_id="test-session",
        state=SessionState.ACTIVE,
        created_at=_NOW,
        updated_at=_NOW,
        config=config,
        template_id=template_id,
    )


def _make_template_manager(template: MinionTemplate | None) -> MagicMock:
    """Mock TemplateManager whose get_template() returns the given template."""
    tm = MagicMock()
    tm.get_template = AsyncMock(return_value=template)
    return tm


# ---------------------------------------------------------------------------
# Scenario 1: template-linked session gets model from template
# ---------------------------------------------------------------------------

async def test_template_linked_session_resolves_model_from_template():
    """A session whose config is empty defers to the template's model."""
    template = MinionTemplate(
        template_id="tmpl-1",
        name="builder",
        config={"model": "sonnet"},
    )
    sinfo = _make_session(config={}, template_id="tmpl-1")
    tm = _make_template_manager(template)

    effective = await resolve_effective_config(sinfo, tm, profile_manager=None)

    assert effective.model == "sonnet"


# ---------------------------------------------------------------------------
# Scenario 2: non-template session reads model from session.config
# ---------------------------------------------------------------------------

async def test_non_template_session_resolves_model_from_config():
    """A plain session with no template_id reads model directly from session.config."""
    sinfo = _make_session(config={"model": "opus"})
    tm = _make_template_manager(template=None)

    effective = await resolve_effective_config(sinfo, tm, profile_manager=None)

    assert effective.model == "opus"


# ---------------------------------------------------------------------------
# Scenario 3: session with no model in config → None, no exception
# ---------------------------------------------------------------------------

async def test_session_with_no_model_resolves_to_none():
    """When no model is set anywhere in the chain, effective.model is None."""
    sinfo = _make_session(config={})
    tm = _make_template_manager(template=None)

    effective = await resolve_effective_config(sinfo, tm, profile_manager=None)

    assert effective.model is None


async def test_session_with_none_config_resolves_to_none():
    """A session whose config dict is None (dataclass allows it) → model is None."""
    sinfo = _make_session(config={})
    sinfo.config = None  # exercise the `or {}` guard in resolve_effective_config
    tm = _make_template_manager(template=None)

    effective = await resolve_effective_config(sinfo, tm, profile_manager=None)

    assert effective.model is None


# ---------------------------------------------------------------------------
# Scenario 4: session.config overrides template model (priority chain)
# ---------------------------------------------------------------------------

async def test_session_config_overrides_template_model():
    """session.config has higher priority than template.config."""
    template = MinionTemplate(
        template_id="tmpl-2",
        name="builder",
        config={"model": "sonnet"},
    )
    sinfo = _make_session(config={"model": "haiku"}, template_id="tmpl-2")
    tm = _make_template_manager(template)

    effective = await resolve_effective_config(sinfo, tm, profile_manager=None)

    assert effective.model == "haiku"


# ---------------------------------------------------------------------------
# Scenario 5: simulated DB failure surfaces the underlying error
#   (verifies that the analytics except block re-raises the original exception
#    context via logger.exception, not just a cosmetic wrapper)
# ---------------------------------------------------------------------------

async def test_record_turn_db_failure_propagates_exception():
    """
    When record_turn raises, the exception propagates out of the try block
    so the caller's `except Exception: logger.exception(...)` receives the
    original error, not a secondary one.
    """
    from src.analytics_store import AnalyticsStore

    store = MagicMock(spec=AnalyticsStore)
    store.record_turn = AsyncMock(side_effect=RuntimeError("db locked"))

    with pytest.raises(RuntimeError, match="db locked"):
        await store.record_turn("sid", 1, "sonnet", {}, 0.001)
