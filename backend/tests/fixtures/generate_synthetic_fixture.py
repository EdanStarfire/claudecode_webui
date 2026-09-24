#!/usr/bin/env python3
"""Generate the `mock-sdk-synthetic` raw fixture (issue #1999, stage 1b of epic #1990).

Unlike `backend/tools/export_fixture_cli.py` (which exports a fixture from a real,
already-recorded live session via `backend.fixture_export.export_fixture`), this
script builds a fixture from scratch with no live SDK subprocess and no Anthropic
credentials: it constructs synthetic `claude_agent_sdk` dataclass instances by hand
and drives them through a real (unconnected) `backend.claude_sdk.ClaudeSDK` instance
wired to a real `backend.session_recorder.SessionRecorder`, exactly mirroring
`backend/mock_sdk.py`'s `MockClaudeSDK._start_raw_replay()` pattern.

This fixture is intentionally scoped to just 5 of the real 9 scenario markers
`backend.fixture_export.REQUIRED_MARKERS` checks for (see module docstring there):
  - streaming deltas
  - tool call with permission prompt
  - subagent task with progress
  - session restart

A future builder without Anthropic credentials can re-run this script to
regenerate `backend/tests/fixtures/raw/mock-sdk-synthetic/` from scratch — that's
the whole point of it living in the repo as a runnable script rather than being a
one-off throwaway.

`rest_history.json` is built independently from the live path (see
`_reconstruct_rest_history_messages()`): it reprocesses the real, stored
`messages.jsonl` rows through `SessionCoordinator._convert_stored_message_to_
websocket()` — the actual REST-reload reconstruction method, called on a
minimally-constructed instance (`object.__new__`) since that method needs no
other coordinator state. This is deliberate, not incidental: an earlier version
of this script built `rest_history.json` from the same accumulator the live
path used, which made the equivalence harness's check against this fixture
tautological. Before issue #2002, this fixture independently reproduced #2002's
divergence too (see `frontend/src/stores/__tests__/equivalence.test.js`'s git
history) — confirmatory evidence the bug was systemic, not an artifact of one
real recording. Since #2002 fixed `_convert_stored_message_to_websocket()`,
this fixture now converges and is no longer listed in
`KNOWN_DIVERGENT_FIXTURES` — note this script still doesn't attach `display`
(DisplayProjection) to any message, since it bypasses `get_session_messages()`
entirely, so it doesn't exercise #2002's Gap 2/2b at all, only Gap 1.

Usage:
    uv run python -m backend.tests.fixtures.generate_synthetic_fixture
"""

import asyncio
import dataclasses
import importlib.metadata
import json
import subprocess
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from claude_agent_sdk import (
    AssistantMessage,
    PermissionResultAllow,
    ResultMessage,
    StreamEvent,
    TaskNotificationMessage,
    TaskProgressMessage,
    TaskStartedMessage,
    TextBlock,
    ToolPermissionContext,
    ToolResultBlock,
    ToolUseBlock,
    UserMessage,
)

from backend.claude_sdk import ClaudeSDK
from backend.data_storage import DataStorageManager
from backend.message_parser import MessageParser, MessageProcessor
from backend.session_coordinator import SessionCoordinator
from backend.session_recorder import SessionRecorder
from shared.event_queue import EventQueue

_REPO_ROOT = Path(__file__).resolve().parents[3]
_FIXTURE_NAME = "mock-sdk-synthetic"
_FIXTURE_DIR = Path(__file__).resolve().parent / "raw" / _FIXTURE_NAME
_SESSION_ID = "mock-sdk-synthetic-session"

_SELF_CHECK_MARKERS = (
    "stream_event",
    "assistant_message_with_tool_use",
    "permission_invocation_response_pair",
    "task_started_or_progress",
    "lifecycle_restart",
)


def _json_default(obj: Any) -> Any:
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        return dataclasses.asdict(obj)
    return str(obj)


def _json_safe(value: Any) -> Any:
    """Round-trip `value` through json.dumps/loads so nested SDK dataclass
    objects (e.g. the raw `sdk_message` key ClaudeSDK._convert_sdk_message()
    embeds in every converted message dict) become plain, JSON-serializable
    structures. Mirrors SessionRecorder._write()'s `default=str` idiom, but
    resolves dataclasses via dataclasses.asdict() first so nested content
    blocks (TextBlock/ToolUseBlock/...) come through as real dicts instead of
    opaque repr strings.
    """
    return json.loads(json.dumps(value, default=_json_default))


async def _run_scenario(shadow_sdk: ClaudeSDK, recorder: SessionRecorder) -> None:
    """Drive the synthetic scenario dataclasses through the shadow SDK instance,
    in order, with the lifecycle restart marker interleaved partway through.
    """
    # --- Marker 1: streaming deltas ---
    stream_event = StreamEvent(
        uuid="synthetic-se-1",
        session_id=_SESSION_ID,
        event={
            "type": "content_block_delta",
            "index": 0,
            "delta": {"type": "text_delta", "text": "I'll read that file for you."},
        },
    )
    await shadow_sdk._process_sdk_message(stream_event)

    # --- Marker 2: tool call with permission prompt ---
    tool_use_id = "synthetic-tu-1"
    assistant_msg = AssistantMessage(
        content=[
            TextBlock(text="I'll read that file for you."),
            ToolUseBlock(id=tool_use_id, name="Read", input={"file_path": "/tmp/example.txt"}),
        ],
        model="claude-sonnet-4-5",
        session_id=_SESSION_ID,
        uuid="synthetic-am-1",
    )
    await shadow_sdk._process_sdk_message(assistant_msg)

    # Real permission round trip: this mirrors _get_sdk_options()'s
    # can_use_tool_wrapper closure exactly (record invocation, call the real
    # decision method, record response) — that closure itself is only reachable
    # via a live subprocess connection, so it's replicated here by hand rather
    # than called directly. _can_use_tool_callback() IS called for real (not
    # hand-rolled) — with no permission_handler configured and a
    # permission_callback that returns True, it resolves via the external
    # callback branch to PermissionResultAllow without any prompting/hanging.
    tool_input = {"file_path": "/tmp/example.txt"}
    context = ToolPermissionContext(tool_use_id=tool_use_id, suggestions=[])
    recorder.record_permission_invocation("Read", tool_input, suggestions=None)
    permission_result = await shadow_sdk._can_use_tool_callback("Read", tool_input, context)
    decision = "allow" if isinstance(permission_result, PermissionResultAllow) else "deny"
    recorder.record_permission_response(
        "Read", decision, getattr(permission_result, "message", None)
    )

    user_result_msg = UserMessage(
        content=[
            ToolResultBlock(
                tool_use_id=tool_use_id,
                content="Line 1: hello\nLine 2: world\n",
                is_error=False,
            )
        ],
        uuid="synthetic-um-1",
    )
    await shadow_sdk._process_sdk_message(user_result_msg)

    # --- Marker 3: subagent task with progress ---
    task_id = "synthetic-task-1"
    task_started = TaskStartedMessage(
        subtype="task_started",
        data={"task_id": task_id, "description": "Explore the repository structure"},
        task_id=task_id,
        description="Explore the repository structure",
        uuid="synthetic-ts-1",
        session_id=_SESSION_ID,
        task_type="general-purpose",
    )
    await shadow_sdk._process_sdk_message(task_started)

    task_progress = TaskProgressMessage(
        subtype="task_progress",
        data={"task_id": task_id},
        task_id=task_id,
        description="Reading files in src/",
        usage={"total_tokens": 1200, "tool_uses": 3, "duration_ms": 4500},
        uuid="synthetic-tp-1",
        session_id=_SESSION_ID,
        last_tool_name="Read",
    )
    await shadow_sdk._process_sdk_message(task_progress)

    task_notification = TaskNotificationMessage(
        subtype="task_notification",
        data={"task_id": task_id, "status": "completed"},
        task_id=task_id,
        status="completed",
        output_file="/tmp/synthetic-task-output.txt",
        summary="Explored the repository structure and summarized key directories.",
        uuid="synthetic-tn-1",
        session_id=_SESSION_ID,
        usage={"total_tokens": 3400, "tool_uses": 5, "duration_ms": 9800},
    )
    await shadow_sdk._process_sdk_message(task_notification)

    # --- Marker 4: session restart (mid-stream) ---
    # The real call site (SessionCoordinator.restart_session -> recorder.record_lifecycle)
    # requires the full SessionCoordinator/SessionManager object graph, which is too heavy
    # to spin up here — calling SessionRecorder.record_lifecycle("restart") directly is the
    # exact same method the real code calls, just not routed through the coordinator.
    recorder.record_lifecycle("restart")

    # A few more messages after the restart marker, so the fixture has genuine
    # activity on both sides of the restart boundary (relevant for #1999's
    # equivalence/fault-simulation harnesses).
    post_restart_stream = StreamEvent(
        uuid="synthetic-se-2",
        session_id=_SESSION_ID,
        event={
            "type": "content_block_delta",
            "index": 0,
            "delta": {"type": "text_delta", "text": "Welcome back — resuming where we left off."},
        },
    )
    await shadow_sdk._process_sdk_message(post_restart_stream)

    post_restart_assistant = AssistantMessage(
        content=[TextBlock(text="Welcome back — resuming where we left off.")],
        model="claude-sonnet-4-5",
        session_id=_SESSION_ID,
        uuid="synthetic-am-2",
    )
    await shadow_sdk._process_sdk_message(post_restart_assistant)

    result_msg = ResultMessage(
        subtype="success",
        duration_ms=12345,
        duration_api_ms=9800,
        is_error=False,
        num_turns=2,
        session_id=_SESSION_ID,
        result="Done.",
    )
    await shadow_sdk._process_sdk_message(result_msg)


def _reconstruct_rest_history_messages(stored_records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Rebuilds rest_history.json's message list via the REAL REST-reload
    reconstruction method — backend.session_coordinator.SessionCoordinator.
    _convert_stored_message_to_websocket() — instead of reusing anything the
    live path (message_callback, above) computed. Reviewed (2026-09-23): that
    method never reads `self` beyond calling two @staticmethods on the same
    class (_extract_agent_name, _parse_agent_notification_label) — no
    SessionManager, storage, or other coordinator state — so the smallest
    viable real dependency is a completely uninitialized instance
    (`object.__new__`, bypassing `__init__` and its full manager graph
    entirely), not a hand-rolled approximation of the method's logic.

    This is deliberately independent of the live path's own accumulator: an
    earlier version of this script built rest_history.json from the exact
    same list the live path's queue events were drawn from, making the
    equivalence check tautological (see issue #1999 PR discussion) — it could
    prove the replay mechanics ran without crashing, never that the harness
    catches a genuine live-vs-reload divergence. Reprocessing the real stored
    JSONL rows through the real reconstruction method closes that gap — before
    issue #2002 fixed it, this independently reproduced that same tracked bug
    on synthetic data too, confirming it was a systemic backend gap, not an
    artifact of one real recording. Calling _convert_stored_message_to_
    websocket() directly here (bypassing get_session_messages()) also means
    this fixture never exercises #2002's DisplayProjection reconstruction
    (Gap 2/2b) — only its field-parity fixes (Gap 1).
    """
    coordinator = object.__new__(SessionCoordinator)
    messages = []
    for stored in stored_records:
        if stored.get("_type") == "ToolCallUpdate":
            continue  # not produced by this fixture's scenario; nothing to reconstruct
        websocket_data = coordinator._convert_stored_message_to_websocket(stored)
        if websocket_data is not None:
            messages.append(_json_safe(websocket_data))
    return messages


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    records = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        records.append(json.loads(line))
    return records


def _self_verify(raw_records: list[dict[str, Any]]) -> dict[str, bool]:
    """Mirrors (but does not call) backend.fixture_export._check_markers()'s
    heuristics — checks this fixture's own narrower 5-check set (per the issue's
    section 6), not the real 9-marker gate.
    """
    found = dict.fromkeys(_SELF_CHECK_MARKERS, False)
    task_types = {"TaskStartedMessage", "TaskProgressMessage"}

    pending_invocation_tool: str | None = None

    for record in raw_records:
        kind = record.get("kind")

        if kind == "sdk_message":
            _type = record.get("_type")
            if _type == "StreamEvent":
                found["stream_event"] = True
            elif _type in task_types:
                found["task_started_or_progress"] = True
            elif _type == "AssistantMessage":
                content = (record.get("data") or {}).get("content") or []
                for block in content:
                    if isinstance(block, dict) and "id" in block and "name" in block:
                        # ToolUseBlock-shaped: {id, name, input} — TextBlock only has {text}
                        found["assistant_message_with_tool_use"] = True

        elif kind == "permission_invocation":
            pending_invocation_tool = record.get("tool_name")
        elif kind == "permission_response":
            if pending_invocation_tool is not None and record.get("tool_name") == pending_invocation_tool:
                found["permission_invocation_response_pair"] = True
            pending_invocation_tool = None

        elif kind == "lifecycle":
            if record.get("action") == "restart":
                found["lifecycle_restart"] = True

    return found


def _build_synthetic_state() -> dict[str, Any]:
    """DataStorageManager doesn't produce state.json on its own (that's
    SessionManager's job, which requires the full coordinator object graph) —
    so this is a scrubbed-down synthetic state.json with the same top-level
    keys as backend/tests/fixtures/raw/2026-09-23-primary/state.json, good
    enough for a synthetic fixture without needing to be realistic beyond that.
    """
    now = datetime.now(UTC).isoformat()
    return {
        "can_spawn_minions": False,
        "capabilities": [],
        "child_minion_ids": [],
        "config": {
            "auto_memory_mode": "session",
            "enable_streaming_text": True,
            "max_subagent_spawn_depth": 3,
            "model": "synthetic",
            "permission_mode": "acceptEdits",
            "recording_enabled": True,
        },
        "created_at": now,
        "current_model": "synthetic",
        "current_permission_mode": "acceptEdits",
        "error_api_error_status": None,
        "error_list": None,
        "error_subtype": None,
        "error_terminal_reason": None,
        "expertise_score": 0.5,
        "initial_model": None,
        "initial_permission_mode": "acceptEdits",
        "is_ephemeral": False,
        "is_overseer": False,
        "last_activity_at": now,
        "last_completion_at": now,
        "last_timestamp_injection_date": None,
        "last_viewed_at": now,
        "links": [],
        "name": "mock_sdk_synthetic",
        "order": 0,
        "overseer_level": 0,
        "parent_overseer_id": None,
        "project_id": None,
        "queue_config": None,
        "queue_paused": False,
        "role": "assistant",
        "sdk_generated_name": None,
        "secret_placeholders": {},
        "session_id": _SESSION_ID,
        "slug": "mock-sdk-synthetic",
        "state": "active",
        "template_id": None,
        "updated_at": now,
        "working_directory": None,
    }


def _resolve_git_sha() -> str | None:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(_REPO_ROOT),
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        sha = (result.stdout or "").strip()
        return sha or None
    except Exception:
        return None


def _build_provenance() -> dict[str, Any]:
    try:
        sdk_version = importlib.metadata.version("claude-agent-sdk")
    except importlib.metadata.PackageNotFoundError:
        sdk_version = None

    return {
        "sdk_version": sdk_version,
        "cli_version": None,
        "model": "synthetic",
        "git_sha": _resolve_git_sha(),
        "capture_date": datetime.now(UTC).isoformat(),
    }


async def generate() -> dict[str, bool]:
    """Build the mock-sdk-synthetic fixture and write it to
    backend/tests/fixtures/raw/mock-sdk-synthetic/. Returns the self-verification
    marker results (see _self_verify()).
    """
    with tempfile.TemporaryDirectory(prefix="mock_sdk_synthetic_") as tmp:
        session_dir = Path(tmp) / _SESSION_ID
        session_dir.mkdir(parents=True, exist_ok=True)

        recorder = SessionRecorder(_SESSION_ID, session_dir)
        storage_manager = DataStorageManager(session_dir)
        await storage_manager.initialize()

        # Issue #1999: a real EventQueue, with the SAME on_append hook
        # backend/web_server.py's _queue_append_hook() wires up in production, so
        # every appended envelope is captured to raw_log.jsonl as a genuine
        # `queue_event` record — the "live path ground truth" US1's equivalence
        # harness reads (queue_event, not sdk_message, records). Without this, the
        # earlier version of this script produced a raw_log.jsonl with no
        # queue_event records at all, which the equivalence harness can't consume.
        queue = EventQueue(on_append=recorder.record_queue_event)
        message_processor = MessageProcessor(MessageParser())

        async def message_callback(msg: dict[str, Any]) -> None:
            # Mirrors backend/web_server.py's BackendApp._create_message_callback()
            # exactly — the real code that turns a ClaudeSDK message_callback
            # invocation into a poll-queue envelope. That method lives on a
            # BackendApp instance with a live self.session_queues/self._message_processor
            # too heavy to construct standalone here; replicated by hand against the
            # SAME MessageProcessor and EventQueue primitives it actually uses. This is
            # the LIVE path only — rest_history.json is built completely independently,
            # below, by re-processing stored messages.jsonl through the real REST
            # reconstruction method (see the tautology note there).
            if isinstance(msg, dict) and msg.get("type") == "assistant_delta":
                if msg.get("parent_tool_use_id") is not None:
                    return  # subagent deltas are dropped in production too
                queue.append({
                    "type": "assistant_delta",
                    "session_id": _SESSION_ID,
                    "data": {
                        "uuid": msg["uuid"],
                        "event": msg["event"],
                        "turn_id": msg.get("turn_id"),
                        "tool_use_id": msg.get("tool_use_id"),
                    },
                    "timestamp": datetime.now(UTC).isoformat(),
                })
                return

            parsed_message = message_processor.process_message(msg, source="websocket")
            websocket_data = message_processor.prepare_for_websocket(parsed_message)
            if parsed_message.record_id:
                websocket_data["message_id"] = parsed_message.record_id

            queue.append({
                "type": "message",
                "session_id": _SESSION_ID,
                "data": websocket_data,
                "timestamp": datetime.now(UTC).isoformat(),
            })

        shadow_sdk = ClaudeSDK(
            session_id=_SESSION_ID,
            working_directory=str(session_dir),
            storage_manager=storage_manager,
            message_callback=message_callback,
            error_callback=None,
            permission_callback=lambda tool_name, input_params, context: True,
            recorder=recorder,
        )

        await _run_scenario(shadow_sdk, recorder)
        recorder.close()

        _FIXTURE_DIR.mkdir(parents=True, exist_ok=True)

        raw_log_src = session_dir / "raw_log.jsonl"
        raw_log_dst = _FIXTURE_DIR / "raw_log.jsonl"
        raw_log_dst.write_text(
            raw_log_src.read_text(encoding="utf-8") if raw_log_src.exists() else "",
            encoding="utf-8",
        )

        messages_src = session_dir / "messages.jsonl"
        messages_dst = _FIXTURE_DIR / "messages.jsonl"
        messages_dst.write_text(
            messages_src.read_text(encoding="utf-8") if messages_src.exists() else "",
            encoding="utf-8",
        )

        (_FIXTURE_DIR / "state.json").write_text(
            json.dumps(_build_synthetic_state(), indent=2, default=str), encoding="utf-8"
        )

        # rest_history.json is built from the REAL stored messages.jsonl rows,
        # reprocessed through the real REST reconstruction method — genuinely
        # independent of the live path above. See
        # _reconstruct_rest_history_messages()'s docstring for why this is the
        # correct fix (not an approximation) and what it's expected to surface.
        stored_records = _read_jsonl(messages_dst)
        rest_messages = _reconstruct_rest_history_messages(stored_records)
        rest_history = {
            "messages": rest_messages,
            "total_count": len(rest_messages),
            "limit": 50,
            "offset": 0,
            "has_more": False,
        }
        (_FIXTURE_DIR / "rest_history.json").write_text(
            json.dumps(rest_history, indent=2, default=str), encoding="utf-8"
        )

        (_FIXTURE_DIR / "provenance.json").write_text(
            json.dumps(_build_provenance(), indent=2), encoding="utf-8"
        )

        raw_records = _read_jsonl(raw_log_dst)
        return _self_verify(raw_records)


def main() -> int:
    marker_results = asyncio.run(generate())

    print(f"Fixture written to: {_FIXTURE_DIR}")
    all_ok = True
    for marker, present in marker_results.items():
        status = "✔" if present else "✘"
        print(f"  {status} {marker}")
        if not present:
            all_ok = False

    if not all_ok:
        print("Self-verification FAILED: one or more required markers missing.", file=sys.stderr)
        return 1

    print("Self-verification passed: all required markers present.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
