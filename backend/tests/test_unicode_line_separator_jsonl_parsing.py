"""Issue #2096 regression tests.

Six real JSONL-parsing sites used str.splitlines() to split a file into
discrete records — the exact hazard #2032 already found and fixed in
data_storage.py. splitlines() also breaks on U+2028/U+2029/U+0085/etc, so a
literal one of those characters inside a stored message's content (every real
write path uses json.dump(..., ensure_ascii=False), so this genuinely occurs)
fragments that single JSON record into multiple invalid-JSON pieces instead
of being treated as ordinary content. Each test below writes a record with an
embedded U+2028 through the real write path (json.dumps(..., ensure_ascii=False))
and asserts the fixed read site still parses it as exactly one intact record.
"""

import json

from backend.fixture_export import _read_jsonl as fixture_export_read_jsonl
from backend.mock_sdk import RawFixtureReplay, SessionRecording
from backend.tests.fixtures.generate_synthetic_fixture import (
    _read_jsonl as synthetic_fixture_read_jsonl,
)
from backend.tests.fixtures.scale_fixture import _load_source_queue_events
from backend.tools.repair_duplicate_tool_calls import find_and_repair_duplicate_tool_calls

_SEPARATOR = "\u2028"  # U+2028 LINE SEPARATOR
_CONTENT = f"line one{_SEPARATOR}line two"


def test_fixture_export_read_jsonl_survives_embedded_separator(tmp_path):
    path = tmp_path / "messages.jsonl"
    path.write_text(
        json.dumps({"type": "user", "content": _CONTENT}, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    records = fixture_export_read_jsonl(path)

    assert len(records) == 1
    assert records[0]["content"] == _CONTENT


def test_generate_synthetic_fixture_read_jsonl_survives_embedded_separator(tmp_path):
    path = tmp_path / "messages.jsonl"
    path.write_text(
        json.dumps({"type": "user", "content": _CONTENT}, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    records = synthetic_fixture_read_jsonl(path)

    assert len(records) == 1
    assert records[0]["content"] == _CONTENT


def test_session_recording_load_messages_survives_embedded_separator(tmp_path):
    (tmp_path / "state.json").write_text(json.dumps({"session_id": "s1"}), encoding="utf-8")
    record = {"type": "user", "content": _CONTENT, "session_id": "s1", "timestamp": 1.0}
    (tmp_path / "messages.jsonl").write_text(
        json.dumps(record, ensure_ascii=False) + "\n", encoding="utf-8"
    )

    recording = SessionRecording(tmp_path)

    assert len(recording.messages) == 1
    assert recording.messages[0]["content"] == _CONTENT


def test_raw_fixture_replay_parse_survives_embedded_separator(tmp_path):
    # Written directly with ensure_ascii=False (not via SessionRecorder, which
    # defaults to ensure_ascii=True and would escape the separator away) to
    # exercise _parse()'s read-side robustness independent of today's writer.
    record = {
        "kind": "sdk_message",
        "_type": "UserMessage",
        "data": {
            "content": _CONTENT,
            "uuid": None,
            "parent_tool_use_id": None,
            "tool_use_result": None,
            "origin": None,
        },
    }
    (tmp_path / "raw_log.jsonl").write_text(
        json.dumps(record, ensure_ascii=False) + "\n", encoding="utf-8"
    )

    replay = RawFixtureReplay(tmp_path)

    assert len(replay.messages) == 1
    assert replay.messages[0].content == _CONTENT


def test_scale_fixture_load_source_queue_events_survives_embedded_separator(tmp_path, monkeypatch):
    import backend.tests.fixtures.scale_fixture as scale_fixture

    fixture_dir = tmp_path / "raw" / "my-fixture"
    fixture_dir.mkdir(parents=True)
    event_record = {"kind": "queue_event", "event": {"queue_id": "q1", "content": _CONTENT}}
    (fixture_dir / "raw_log.jsonl").write_text(
        json.dumps(event_record, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    monkeypatch.setattr(scale_fixture, "_RAW_FIXTURES_ROOT", tmp_path / "raw")

    events = _load_source_queue_events("my-fixture")

    assert len(events) == 1
    assert events[0]["content"] == _CONTENT


def test_repair_duplicate_tool_calls_survives_embedded_separator(tmp_path):
    record = {
        "type": "tool_call",
        "tool_use_id": "tu-1",
        "status": "completed",
        "result": _CONTENT,
    }
    messages_path = tmp_path / "messages.jsonl"
    messages_path.write_text(json.dumps(record, ensure_ascii=False) + "\n", encoding="utf-8")

    result = find_and_repair_duplicate_tool_calls(messages_path, "sess-1", apply=False)

    # A single record is never a duplicate — the point here is that reading
    # it (previously via splitlines()) does not raise/crash and does not
    # silently fragment/drop it.
    assert result.clean
