"""Synthetic fixture freshness check (issue #2037 Stage C, AC3).

Fails loudly if the committed backend/tests/fixtures/raw/mock-sdk-synthetic/ fixture
has drifted from what generate_synthetic_fixture.py's generate() currently produces —
e.g. a scenario/marker change landed without regenerating the committed copy. Run
`uv run python -m backend.tests.fixtures.generate_synthetic_fixture` to regenerate.
"""

import json
from typing import Any

from backend.fixture_export import REQUIRED_MARKERS
from backend.tests.fixtures.generate_synthetic_fixture import _FIXTURE_DIR, _read_jsonl, generate

_REGENERATE_HINT = (
    "Run `uv run python -m backend.tests.fixtures.generate_synthetic_fixture` to "
    "regenerate the committed fixture."
)

# Fields expected to differ between any two independently-generated copies of this
# fixture — wall-clock timestamps, plus message_id (stamped from a fresh uuid4() per
# run by ClaudeSDK._process_sdk_message()'s converted_message.setdefault('message_id',
# ...) and by the outgoing-user-message path in its conversation loop; not derived
# from anything content-based). Stripped recursively before comparison, mirroring
# frontend/src/stores/__tests__/helpers/fixtureEquivalence.js's narrow, named-field
# TIMING_ONLY_KEYS approach rather than a blanket diff-suppression. Confirmed
# empirically (2026-09-30): stripping exactly these three keys is sufficient to make
# two independent generate() runs compare byte-identical.
_NONDETERMINISTIC_KEYS = {"timestamp", "processed_at", "message_id"}

# Issue #2052: get_session_messages() (the real REST endpoint's own reconstruction
# path — distinct from this fixture's own _reconstruct_rest_history_messages()) has a
# tool_call-synthesis pass that stamps a nondeterministic created_at on synthesized
# type: "tool_call" entries. Empirically confirmed (2026-09-30, both before and after
# this stage's expansion to 9/9 markers): mock-sdk-synthetic's rest_history.json never
# contains a type: "tool_call" entry at all — _reconstruct_rest_history_messages()
# explicitly skips stored ToolCallUpdate records, and none of the 9 marker scenarios
# produce one (every scenario goes through _process_sdk_message() with plain SDK
# dataclasses, stored as AssistantMessage/UserMessage/SystemMessage — never
# ToolCallUpdate). This normalization is therefore a documented no-op guard today,
# kept per instruction since it's cheap and correct if a future scenario changes that.
_TOOL_CALL_TIMING_KEY = "created_at"


def _strip_nondeterministic(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: _strip_nondeterministic(val)
            for key, val in value.items()
            if key not in _NONDETERMINISTIC_KEYS
        }
    if isinstance(value, list):
        return [_strip_nondeterministic(val) for val in value]
    return value


def _normalize_rest_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    normalized = []
    for message in messages:
        message = _strip_nondeterministic(message)
        if message.get("type") == "tool_call":
            message.pop(_TOOL_CALL_TIMING_KEY, None)
        normalized.append(message)
    return normalized


async def test_synthetic_fixture_matches_committed_copy(tmp_path):
    """Regenerates mock-sdk-synthetic into a scratch dir (generate() never mutates the
    committed copy when given output_dir) and diffs it against the committed copy —
    excluding provenance.json entirely (capture-run metadata — capture_date/git_sha
    are expected to differ run-to-run, not fixture content) and normalizing known
    nondeterministic fields. A real drift (a scenario/marker change landed without
    regenerating the committed fixture) fails loudly and names the file/record.
    """
    marker_results = await generate(output_dir=tmp_path)
    missing = [marker for marker in REQUIRED_MARKERS if not marker_results.get(marker)]
    assert not missing, (
        f"A fresh generate() no longer covers all of fixture_export.REQUIRED_MARKERS "
        f"(missing: {missing}) — this is a coverage regression in generate_synthetic_"
        f"fixture.py's scenarios, not just a committed-copy drift."
    )

    committed_raw_log = _strip_nondeterministic(_read_jsonl(_FIXTURE_DIR / "raw_log.jsonl"))
    fresh_raw_log = _strip_nondeterministic(_read_jsonl(tmp_path / "raw_log.jsonl"))

    assert len(committed_raw_log) == len(fresh_raw_log), (
        f"mock-sdk-synthetic/raw_log.jsonl record count drifted: committed fixture has "
        f"{len(committed_raw_log)} records, a fresh generate() currently produces "
        f"{len(fresh_raw_log)}. {_REGENERATE_HINT}"
    )
    for i, (committed, fresh) in enumerate(zip(committed_raw_log, fresh_raw_log, strict=True)):
        assert committed == fresh, (
            f"mock-sdk-synthetic/raw_log.jsonl record #{i} (kind={committed.get('kind')!r}) "
            f"drifted from the committed fixture.\nCommitted: {committed}\nFresh:     {fresh}\n"
            f"{_REGENERATE_HINT}"
        )

    committed_rest = json.loads((_FIXTURE_DIR / "rest_history.json").read_text(encoding="utf-8"))
    fresh_rest = json.loads((tmp_path / "rest_history.json").read_text(encoding="utf-8"))
    committed_messages = _normalize_rest_messages(committed_rest["messages"])
    fresh_messages = _normalize_rest_messages(fresh_rest["messages"])

    assert len(committed_messages) == len(fresh_messages), (
        f"mock-sdk-synthetic/rest_history.json messages count drifted: committed "
        f"fixture has {len(committed_messages)}, a fresh generate() currently produces "
        f"{len(fresh_messages)}. {_REGENERATE_HINT}"
    )
    for i, (committed, fresh) in enumerate(zip(committed_messages, fresh_messages, strict=True)):
        assert committed == fresh, (
            f"mock-sdk-synthetic/rest_history.json messages[{i}] (type="
            f"{committed.get('type')!r}) drifted from the committed fixture.\n"
            f"Committed: {committed}\nFresh:     {fresh}\n{_REGENERATE_HINT}"
        )
