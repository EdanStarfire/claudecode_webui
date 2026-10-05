#!/usr/bin/env python3
"""Verify (and optionally apply) message migration for one session (issue #2084
stage 3-C, §8) — the hard requirement for the 3-C -> 3-D gate.

Default (no --apply) is a safe, non-destructive dry run:
1. Copy messages.jsonl + state.json into a timestamped sibling directory
   (plain shutil.copy2 — never scrub_state_for_archive(), which drops fields and
   would corrupt a byte-level diff).
2. Migrate a disposable copy of the session (never the real messages.jsonl).
3. Reconstruct what the frontend would see two ways: (a) today's legacy read of
   the original data via SessionCoordinator.get_session_messages(), and (b) a
   canonical read of the migrated copy via the exact same method — the real
   production code path both times, not a reimplementation. Diff them per
   logical tool/message (field-content-aware), not as a blind line diff —
   materialized records legitimately change line count/order for a pre-#494
   session, which is the whole point of this stage.

With --apply: after a clean dry-run-equivalent pass, actually perform the real
migration (the atomic swap + message_schema_version/message_migration_status
flip) via the same migrate_session_messages() the background/on-demand paths use.

Usage:
    uv run python -m backend.tools.migrate_session_verify_cli <session_id> [--data-dir DIR] [--apply]
"""

import argparse
import asyncio
import shutil
import sys
import time
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from backend.data_storage import DataStorageManager
from backend.message_migration import migrate_session_messages
from backend.models.messages import CURRENT_MESSAGE_SCHEMA_VERSION
from backend.session_coordinator import SessionCoordinator
from backend.session_manager import SessionInfo

# Fields that are inherently conversion-time-variant in the existing (unmodified)
# MessageProcessor fallback path, or migration-minted bookkeeping the live/
# ephemeral synthesis never had — never real divergence signals for T1 purposes
# (see backend/tests/test_message_migration.py's _strip_volatile() for the same
# reasoning spelled out in more detail).
_IGNORED_METADATA_FIELDS = frozenset({"processed_at"})
_IGNORED_TOOL_CALL_FIELDS = frozenset({"timestamp"})


@dataclass
class VerificationReport:
    session_id: str
    total_records_before: int
    materialized_tool_calls: int
    divergences: list[dict[str, Any]] = field(default_factory=list)
    duration_seconds: float = 0.0
    applied: bool = False
    archive_dir: Path | None = None

    @property
    def ok(self) -> bool:
        return not self.divergences


def _normalize(msg: dict[str, Any]) -> dict[str, Any]:
    """Strip fields that are expected to legitimately differ between an ephemeral
    live read and a persisted canonical read (see module docstring)."""
    out = {k: v for k, v in msg.items() if k != "message_id"}
    if out.get("type") == "tool_call":
        for f in _IGNORED_TOOL_CALL_FIELDS:
            out.pop(f, None)
    metadata = out.get("metadata")
    if isinstance(metadata, dict):
        out["metadata"] = {k: v for k, v in metadata.items() if k not in _IGNORED_METADATA_FIELDS}
    return out


def _group_by_logical_identity(messages: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """Group messages for the field-content-aware diff — per logical tool/message,
    not per raw line.

    Migration only ever *inserts* materialized tool_call records at the point
    they'd have been synthesized; it never reorders or removes the original
    regular messages. So a tool_call record is identified by (tool_use_id,
    status) — stable regardless of how many other tool_call records surround
    it — while every other message type is identified by its position *among
    messages of that same non-tool_call sequence*, a counter that tool_call
    insertions never advance. A plain overall-list index would instead shift
    for every regular message following an inserted tool_call record, falsely
    flagging a pure insertion as a divergence.
    """
    grouped: dict[str, list[dict[str, Any]]] = {}
    counter = 0
    for m in messages:
        if m.get("type") == "tool_call":
            key = f"tool_call:{m.get('tool_use_id')}:{m.get('status')}"
        else:
            key = f"{m.get('type')}:{counter}"
            counter += 1
        grouped.setdefault(key, []).append(m)
    return grouped


def _diff(before: list[dict[str, Any]], after: list[dict[str, Any]]) -> list[dict[str, Any]]:
    before_by_key = _group_by_logical_identity(before)
    after_by_key = _group_by_logical_identity(after)

    divergences = []
    for key in sorted(set(before_by_key) | set(after_by_key)):
        b_list = before_by_key.get(key, [])
        a_list = after_by_key.get(key, [])
        if b_list != a_list:
            divergences.append({"key": key, "before": b_list, "after": a_list})
    return divergences


async def _build_scratch_session(
    coordinator: SessionCoordinator, source_dir: Path, session_info: SessionInfo
) -> tuple[str, Path]:
    """Register a throwaway session pointing at a private copy of the source
    session's messages.jsonl, so get_session_messages() can be reused verbatim
    for the canonical "after" read — the real production code, not a
    reimplementation — without mutating or depending on the real session unless
    --apply was passed."""
    scratch_id = f"verify-{uuid.uuid4()}"
    scratch_dir = coordinator.session_manager.sessions_dir / scratch_id
    scratch_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source_dir / "messages.jsonl", scratch_dir / "messages.jsonl")

    now = datetime.now(UTC)
    info = SessionInfo(
        session_id=scratch_id,
        state=session_info.state,
        created_at=now,
        updated_at=now,
        working_directory=str(scratch_dir),
        current_permission_mode="default",
        message_schema_version=0,
    )
    coordinator.session_manager._active_sessions[scratch_id] = info
    storage = DataStorageManager(scratch_dir)
    await storage.initialize()
    coordinator._storage_managers[scratch_id] = storage
    return scratch_id, scratch_dir


async def _teardown_scratch_session(
    coordinator: SessionCoordinator, scratch_id: str, scratch_dir: Path
) -> None:
    coordinator.session_manager._active_sessions.pop(scratch_id, None)
    coordinator._storage_managers.pop(scratch_id, None)
    shutil.rmtree(scratch_dir, ignore_errors=True)


async def verify_session_migration(
    coordinator: SessionCoordinator,
    session_id: str,
    *,
    apply: bool = False,
) -> VerificationReport:
    """Run the real verification (§8 step 4) for one session.

    Exposed as a plain importable function, mirroring scale_fixture.py's dual-
    purpose precedent, so this comparison logic is unit-testable under pytest
    independent of the manual CLI invocation.
    """
    start_time = time.monotonic()
    session_dir = await coordinator.session_manager.get_session_directory(session_id)
    if session_dir is None:
        raise ValueError(f"Session {session_id} not found")

    archive_dir: Path | None = None
    if not apply:
        timestamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S_%f")
        archive_dir = session_dir.parent / f"{session_id}-migration-verify-{timestamp}"
        archive_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(session_dir / "messages.jsonl", archive_dir / "messages.jsonl")
        state_path = session_dir / "state.json"
        if state_path.exists():
            shutil.copy2(state_path, archive_dir / "state.json")

    # (a) today's legacy read, before any migration.
    before = await coordinator.get_session_messages(session_id)
    before_messages = [_normalize(m) for m in before["messages"]]

    session_info = await coordinator.session_manager.get_session_info(session_id)
    if session_info is None:
        raise ValueError(f"Session {session_id} not found")

    if apply:
        # Mirror the production claim step so message_migration_status bookkeeping
        # (started_at, state transitions) looks identical to a background/on-demand
        # run, not just the end result. Must check the return value: if a live
        # Backend's background/on-demand migration already claimed or completed this
        # session concurrently, running migrate_session_messages() anyway would
        # convert an already-canonical file a second time and corrupt it.
        claimed = await coordinator.session_manager.try_claim_message_migration(session_id)
        if not claimed:
            raise RuntimeError(
                f"Session {session_id} is already migrated or has an in-progress "
                "migration elsewhere — refusing to run --apply concurrently."
            )
        storage = await coordinator.get_or_create_storage_manager(session_id)
        result = await migrate_session_messages(
            session_dir, session_id, session_info.state,
            coordinator._convert_legacy_record_to_websocket, storage._write_lock,
        )
        await coordinator.session_manager.complete_message_migration(
            session_id, result.materialized_tool_calls
        )
        after = await coordinator.get_session_messages(session_id)
        after_messages = [_normalize(m) for m in after["messages"]]
    else:
        scratch_id, scratch_dir = await _build_scratch_session(coordinator, session_dir, session_info)
        try:
            scratch_storage = coordinator._storage_managers[scratch_id]
            # Pass the REAL session_id (not scratch_id) so materialized records
            # carry the same identity a real migration would mint — only the
            # directory is a disposable copy; scratch_id exists solely as a lookup
            # key for get_or_create_storage_manager()/get_session_info() below.
            result = await migrate_session_messages(
                scratch_dir, session_id, session_info.state,
                coordinator._convert_legacy_record_to_websocket, scratch_storage._write_lock,
            )
            coordinator.session_manager._active_sessions[scratch_id].message_schema_version = (
                CURRENT_MESSAGE_SCHEMA_VERSION
            )
            after = await coordinator.get_session_messages(scratch_id)
            after_messages = [_normalize(m) for m in after["messages"]]
        finally:
            await _teardown_scratch_session(coordinator, scratch_id, scratch_dir)

    divergences = _diff(before_messages, after_messages)

    return VerificationReport(
        session_id=session_id,
        total_records_before=before["total_count"],
        materialized_tool_calls=result.materialized_tool_calls,
        divergences=divergences,
        duration_seconds=time.monotonic() - start_time,
        applied=apply,
        archive_dir=archive_dir,
    )


def _print_report(report: VerificationReport) -> None:
    print(f"Session: {report.session_id}")
    print(f"Records before: {report.total_records_before}")
    print(f"Materialized tool_call records: {report.materialized_tool_calls}")
    print(f"Mode: {'APPLIED' if report.applied else 'dry run'}")
    if report.archive_dir:
        print(f"Archive snapshot: {report.archive_dir}")
    print(f"Duration: {report.duration_seconds:.3f}s")
    if report.ok:
        print("Result: OK — zero field-level divergences")
    else:
        print(f"Result: FAILED — {len(report.divergences)} divergence(s):")
        for d in report.divergences:
            print(f"  {d['key']}")
            print(f"    before: {d['before']}")
            print(f"    after:  {d['after']}")


async def _run(session_id: str, data_dir: Path, apply: bool) -> int:
    coordinator = SessionCoordinator(data_dir)
    await coordinator.initialize()
    try:
        report = await verify_session_migration(coordinator, session_id, apply=apply)
    except Exception as e:
        print(f"Verification failed: {e}", file=sys.stderr)
        return 1
    finally:
        await coordinator.cleanup()

    _print_report(report)
    return 0 if report.ok else 1


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Verify (and optionally apply) message migration for one session"
    )
    parser.add_argument("session_id", help="Session ID to verify/migrate")
    parser.add_argument(
        "--data-dir", default="./data",
        help="Data directory location (default: ./data)",
    )
    parser.add_argument(
        "--apply", action="store_true",
        help="Actually perform the migration (default: safe, non-destructive dry run)",
    )
    args = parser.parse_args()

    data_dir = Path(args.data_dir).resolve()
    return asyncio.run(_run(args.session_id, data_dir, args.apply))


if __name__ == "__main__":
    sys.exit(main())
