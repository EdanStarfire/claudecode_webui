#!/usr/bin/env python3
"""Regenerates the 5 hand-built mock-SDK fixtures (single_turn, multi_turn, tool_use,
permission_flow, hook_messages) into canonical MessageRecord shape (issue #2109, AC11).

These fixtures have no `raw_log.jsonl` (unlike the `raw/` fixtures, regenerated instead
via `test_equivalence_replay_generation.py::test_generate_fixture_from_real_pipeline`),
so they need direct per-record conversion. Every record is built through the real
production constructors (`reconstruct_sdk_message` + `MessageRecord.from_sdk_message`/
`from_user_input`/`from_tool_call`, `_message_dict_to_record`) — never hand-written JSON
— so the regenerated fixtures are byte-faithful to what the live code actually produces
today, and stay that way automatically if those constructors change in the future.

Does NOT delegate to `mock_sdk.py`'s `_converting_callback`: that function was built
for `raw_log.jsonl` replay, where `_type` always names a real `claude_agent_sdk` class.
`ToolCallUpdate`/`HookEventMessage` are the old *storage-layer* `_type` taxonomy (a
WebUI concept, never a real SDK type) and aren't in `raw_replay.py`'s `_MESSAGE_TYPES`
for the former — delegating risks the exact silent-degrade behavior this stage retires.

`permission_flow` is the one fixture needing actual domain reconstruction: its
`permission_request`/`permission_response` pair (a pre-#324 shape with no current
canonical equivalent) is replaced with the real pending/awaiting_permission/
running|denied `tool_call` records the live pipeline would have produced — one
`MessageRecord.from_tool_call(...).to_dict()` per transition, mirroring
`SessionCoordinator.create_tool_call()`/`update_tool_call_permission_request()`/
`update_tool_call_permission_response()` exactly (never collapsed into one record).

Usage:
    uv run python -m backend.tools.regenerate_fixtures_cli [--fixtures-dir DIR] [--apply]

Without --apply, prints a per-fixture dry-run summary (record count before/after,
any `_type`/legacy shapes remaining) and writes nothing.
"""

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from backend.models.messages import (
    MessageRecord,
    PermissionInfo,
    ToolCall,
    ToolDisplayInfo,
    ToolState,
    _message_dict_to_record,
)
from backend.raw_replay import reconstruct_sdk_message

DEFAULT_FIXTURES_DIR = Path(__file__).resolve().parents[2] / "backend" / "tests" / "fixtures"

FIXTURE_NAMES = ["single_turn", "multi_turn", "tool_use", "permission_flow", "hook_messages"]

# Confirmed exhaustively against the actual `_type` values present across all five
# fixtures today (issue #2109 plan) — nothing else appears.
_SDK_TAGGED_TYPES = {"AssistantMessage", "UserMessage", "SystemMessage", "ResultMessage"}


def _find_preceding_tool_use(
    records: list[dict], before_index: int, tool_use_id: str
) -> dict[str, Any] | None:
    """Scan backwards from `before_index` for the AssistantMessage tool_use block
    matching `tool_use_id`, to recover its name/input/timestamp for pending ToolCall
    reconstruction (the "pending" state is never its own top-level record — it's
    always derived from the tool_use block that introduced it)."""
    for i in range(before_index - 1, -1, -1):
        rec = records[i]
        if rec.get("_type") != "AssistantMessage":
            continue
        content = (rec.get("data") or {}).get("content") or []
        for block in content:
            if isinstance(block, dict) and block.get("id") == tool_use_id and "name" in block:
                return {
                    "name": block["name"],
                    "input": block.get("input", {}),
                    "timestamp": rec.get("timestamp"),
                }
    return None


def _reconstruct_permission_pair(
    records: list[dict], request_index: int, session_id: str
) -> list[dict]:
    """Issue #2109 (AC11): faithful ToolCall-transition reconstruction for one
    permission_request/permission_response pair, replacing them with the real
    pending/awaiting_permission/running|denied tool_call records the live pipeline
    would have produced. Each serialized independently via
    `MessageRecord.from_tool_call(...).to_dict()` — never collapsed into one record,
    matching how real tool-call storage has always worked."""
    request_rec = records[request_index]
    response_rec = records[request_index + 1]
    if response_rec.get("type") != "permission_response":
        raise ValueError(
            f"Expected permission_response immediately after permission_request at "
            f"index {request_index}, got {response_rec.get('type')!r}"
        )

    tool_use_id = request_rec["tool_use_id"]
    tool_name = request_rec["tool_name"]
    preceding = _find_preceding_tool_use(records, request_index, tool_use_id)
    if preceding is None:
        raise ValueError(
            f"No preceding AssistantMessage tool_use block found for tool_use_id "
            f"{tool_use_id!r} (permission_request at index {request_index})"
        )

    # 1. pending — construct a ToolCall from the preceding tool-use record's
    # tool_use_id/name/input, mirroring SessionCoordinator.create_tool_call().
    tool_call = ToolCall(
        tool_use_id=tool_use_id,
        session_id=session_id,
        name=tool_name,
        input=preceding["input"],
        status=ToolState.PENDING,
        created_at=preceding["timestamp"],
        requires_permission=False,
        display=ToolDisplayInfo(
            state=ToolState.PENDING, visible=True, collapsed=False, style="default"
        ),
    )
    pending_record = MessageRecord.from_tool_call(tool_call).to_dict()

    # 2. awaiting_permission — from the permission_request record's request_id,
    # mirroring update_tool_call_permission_request(). Note: created_at carries
    # forward unchanged here (no started_at/completed_at set yet), so this record's
    # timestamp (from_tool_call's completed_at-or-started_at-or-created_at fallback)
    # is identical to the pending record's — exactly how real production storage
    # behaves for this transition, not an artifact of this reconstruction.
    tool_call.status = ToolState.AWAITING_PERMISSION
    tool_call.requires_permission = True
    tool_call.permission = PermissionInfo(message=f"Allow {tool_name}?")
    tool_call.request_id = request_rec.get("request_id")
    tool_call.display.state = ToolState.AWAITING_PERMISSION
    tool_call.display.style = "warning"
    awaiting_record = MessageRecord.from_tool_call(
        tool_call, triggering_message=request_rec
    ).to_dict()

    # 3. running (allow) or denied (deny) — from the permission_response record's
    # decision, mirroring update_tool_call_permission_response().
    decision = response_rec.get("decision")
    response_ts = response_rec.get("timestamp")
    tool_call.permission_granted = decision == "allow"
    tool_call.permission_response_at = response_ts
    if decision == "allow":
        tool_call.status = ToolState.RUNNING
        tool_call.started_at = response_ts
        tool_call.display.state = ToolState.RUNNING
        tool_call.display.style = "default"
    else:
        tool_call.status = ToolState.DENIED
        tool_call.completed_at = response_ts
        tool_call.display.state = ToolState.DENIED
        tool_call.display.style = "error"
    response_record = MessageRecord.from_tool_call(
        tool_call, triggering_message=response_rec
    ).to_dict()

    return [pending_record, awaiting_record, response_record]


def regenerate_fixture_records(messages_jsonl_path: Path) -> list[dict]:
    """Convert one fixture's `messages.jsonl` content into canonical records."""
    records: list[dict] = []
    with open(messages_jsonl_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            records.append(json.loads(line))

    out: list[dict] = []
    i = 0
    while i < len(records):
        rec = records[i]
        stored_type = rec.get("_type")
        session_id = rec.get("session_id", "")

        if stored_type:
            if stored_type not in _SDK_TAGGED_TYPES:
                raise ValueError(
                    f"Unhandled _type {stored_type!r} at index {i} in {messages_jsonl_path}"
                )
            sdk_obj = reconstruct_sdk_message(stored_type, rec.get("data", {}))
            record = MessageRecord.from_sdk_message(sdk_obj, session_id=session_id).to_dict()
            # from_sdk_message always stamps "now" — restamp with the fixture's own
            # recorded timestamp so the regenerated fixture's chronological order and
            # content match the original (message_id/display are still freshly minted,
            # matching how every real stored record gets its own identity).
            record["timestamp"] = rec.get("timestamp", record["timestamp"])
            out.append(record)
            i += 1
        elif rec.get("type") == "user":
            record = MessageRecord.from_user_input(
                rec.get("content", ""), session_id, metadata=rec.get("metadata")
            ).to_dict()
            record["timestamp"] = rec.get("timestamp", record["timestamp"])
            out.append(record)
            i += 1
        elif rec.get("type") == "system":
            record = _message_dict_to_record(rec, session_id).to_dict()
            record["timestamp"] = rec.get("timestamp", record["timestamp"])
            out.append(record)
            i += 1
        elif rec.get("type") == "permission_request":
            out.extend(_reconstruct_permission_pair(records, i, session_id))
            i += 2
        else:
            raise ValueError(
                f"Unrecognized record shape at index {i} in {messages_jsonl_path}: {rec}"
            )

    return out


def _run(fixtures_dir: Path, apply: bool) -> int:
    exit_code = 0
    for name in FIXTURE_NAMES:
        messages_path = fixtures_dir / name / "messages.jsonl"
        if not messages_path.exists():
            print(f"{name}: SKIPPED (no messages.jsonl at {messages_path})", file=sys.stderr)
            exit_code = 1
            continue

        before_count = sum(1 for line in messages_path.read_text(encoding="utf-8").splitlines() if line.strip())
        try:
            records = regenerate_fixture_records(messages_path)
        except ValueError as e:
            print(f"{name}: FAILED ({e})", file=sys.stderr)
            exit_code = 1
            continue

        remaining_legacy = [
            r for r in records
            if "_type" in r or r.get("type") in ("permission_request", "permission_response")
        ]
        if remaining_legacy:
            print(
                f"{name}: FAILED ({len(remaining_legacy)} non-canonical record(s) remain "
                f"after regeneration)",
                file=sys.stderr,
            )
            exit_code = 1
            continue

        print(f"{name}: {before_count} -> {len(records)} record(s)")

        if apply:
            with open(messages_path, "w", encoding="utf-8") as f:
                for record in records:
                    f.write(json.dumps(record) + "\n")
            print(f"{name}: written to {messages_path}")

    if not apply:
        print("\nDry run only — pass --apply to write the regenerated fixtures.", file=sys.stderr)

    return exit_code


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Regenerate the 5 hand-built mock-SDK fixtures into canonical MessageRecord shape"
    )
    parser.add_argument(
        "--fixtures-dir", default=str(DEFAULT_FIXTURES_DIR),
        help=f"Fixtures directory (default: {DEFAULT_FIXTURES_DIR})",
    )
    parser.add_argument(
        "--apply", action="store_true",
        help="Write the regenerated messages.jsonl files (default: dry-run only)",
    )
    args = parser.parse_args()
    return _run(Path(args.fixtures_dir).resolve(), args.apply)


if __name__ == "__main__":
    sys.exit(main())
