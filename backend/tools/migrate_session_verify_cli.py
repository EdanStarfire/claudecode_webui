#!/usr/bin/env python3
"""Verify (and optionally apply) message migration for one session (issue #2084
stage 3-C, §8) — the hard requirement for the 3-C -> 3-D gate.

Default (no --apply) is a safe, non-destructive dry run — no files are written
under --data-dir at all (issue #2094 AC1: an earlier version of this tool wrote
a full backup copy into the live data directory on every dry run, which was
never used for anything but populating a printed path):
1. Migrate a disposable copy of the session (never the real messages.jsonl).
2. Reconstruct what the frontend would see two ways: (a) today's legacy read of
   the original data via SessionCoordinator.get_session_messages(), and (b) a
   canonical read of the migrated copy via the exact same method — the real
   production code path both times, not a reimplementation. Diff them per
   logical tool/message (field-content-aware), not as a blind line diff —
   materialized records legitimately change line count/order for a pre-#494
   session, which is the whole point of this stage.

With --apply: after a clean dry-run-equivalent pass, write a pre-migration
backup to a dedicated location outside the directories the scan-all tools walk
(issue #2094 AC2), then actually perform the real migration (the atomic swap +
message_schema_version/message_migration_status flip) via the same
migrate_session_messages() the background/on-demand paths use.

Usage:
    uv run python -m backend.tools.migrate_session_verify_cli <session_id> [--data-dir DIR] [--apply]

Archive-targeting mode (issue #2084 stage 3-D-prep, §3) — same dry-run/--apply
semantics, applied to a disposed session's frozen archive snapshot instead of a
live session directory (needed since 3-C's background/on-demand migration only
ever reaches live sessions; archives are otherwise permanently stuck at whatever
message_schema_version they had at disposal time):
    uv run python -m backend.tools.migrate_session_verify_cli --archive-dir <path> [--apply]
"""

import argparse
import asyncio
import json
import os
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
from backend.session_coordinator import SessionCoordinator, _is_canonical_schema_version
from backend.session_manager import SessionInfo, SessionState

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
    # Issue #2094: dry-run no longer writes any backup (it was unused for
    # anything but populating this field — see module docstring). Only
    # --apply ever sets this now, to the dedicated backup location (AC2).
    backup_dir: Path | None = None
    backup_skipped: bool = False

    @property
    def ok(self) -> bool:
        return not self.divergences


def _write_apply_backup(backup_dir: Path, messages_path: Path, state_path: Path) -> bool:
    """Idempotent pre-migration backup for --apply (issue #2094 AC2). Returns
    True if a new backup was written, False if one already existed from a
    prior attempt (skipped, not an error — the backup's mere presence on disk
    is itself the idempotency marker, AC2).

    Stages into a sibling tmp directory and atomically renames it into place
    (found in review) — mirroring message_migration.py's own tmp-file +
    os.replace() swap for the same reason: `backup_dir.exists()` is the sole
    completeness signal a retry trusts, so a crash between mkdir and the two
    copy2 calls must never leave a PARTIAL backup_dir behind for a retry to
    mistake for a complete one.
    """
    if backup_dir.exists():
        return False
    staging_dir = backup_dir.parent / f".{backup_dir.name}.migration-backup-tmp-{uuid.uuid4().hex}"
    staging_dir.mkdir(parents=True, exist_ok=True)
    try:
        shutil.copy2(messages_path, staging_dir / "messages.jsonl")
        if state_path.exists():
            shutil.copy2(state_path, staging_dir / "state.json")
        os.replace(staging_dir, backup_dir)
    except Exception:
        shutil.rmtree(staging_dir, ignore_errors=True)
        raise
    return True


def _normalize(msg: dict[str, Any], *, ignore_tool_call_session_id: bool = False) -> dict[str, Any]:
    """Strip fields that are expected to legitimately differ between an ephemeral
    live read and a persisted canonical read (see module docstring).

    message_id is only stripped for tool_call records — a synthesized tool_call
    never had a stored id before migration (gets a fresh random one on every
    legacy read, vs. migration's deterministic materialized id), so comparing it
    would always false-positive. Every other message type's message_id is a real,
    already-stable identifier through the legacy read's straight passthrough
    (_convert_legacy_record_to_websocket()) — stripping it there too would make
    this comparison blind to migration corrupting or reassigning a real record's
    identity, exactly the kind of regression this tool is the final gate against
    (found in review).

    ignore_tool_call_session_id (issue #2084 stage 3-D-prep, §3, found in review):
    a synthesized tool_call's session_id is stamped from whatever accessor id
    get_session_messages() was called with, not a stored identity. The
    archive-targeting mode's "before"/"after" reads necessarily go through scratch
    sessions registered under disposable ids (reusing the archive's own real
    session_id for a scratch directory would risk colliding with a live session of
    the same id in a real data dir), so those synthesized ids legitimately differ
    from each other there. Scoped to a per-call opt-in rather than a module-global
    ignore so the live-session path (verify_session_migration(), the actual 3-C ->
    3-D gate) keeps comparing session_id and stays sensitive to a real migration
    bug that mis-stamps it.
    """
    out = dict(msg)
    if out.get("type") == "tool_call":
        out.pop("message_id", None)
        for f in _IGNORED_TOOL_CALL_FIELDS:
            out.pop(f, None)
        if ignore_tool_call_session_id:
            out.pop("session_id", None)
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
    coordinator: SessionCoordinator, source_dir: Path, session_state: SessionState
) -> tuple[str, Path]:
    """Register a throwaway session pointing at a private copy of the source
    directory's messages.jsonl, so get_session_messages() can be reused verbatim
    for the canonical "after" read — the real production code, not a
    reimplementation — without mutating or depending on the real session unless
    --apply was passed. source_dir is a plain Path (a live session directory or
    an archive directory — the archive-targeting mode (§3) has no live SessionInfo
    to pull a state from, hence taking session_state directly)."""
    scratch_id = f"verify-{uuid.uuid4()}"
    scratch_dir = coordinator.session_manager.sessions_dir / scratch_id
    scratch_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source_dir / "messages.jsonl", scratch_dir / "messages.jsonl")

    now = datetime.now(UTC)
    info = SessionInfo(
        session_id=scratch_id,
        state=session_state,
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

    # (a) today's legacy read, before any migration.
    before = await coordinator.get_session_messages(session_id)
    before_messages = [_normalize(m) for m in before["messages"]]

    session_info = await coordinator.session_manager.get_session_info(session_id)
    if session_info is None:
        raise ValueError(f"Session {session_id} not found")

    backup_dir: Path | None = None
    backup_skipped = False
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
        # Issue #2094 (AC2): apply mode previously had no backup mechanism at
        # all — migration ran directly against the real session_dir with only
        # the atomic swap as protection. Dedicated location, outside
        # data/sessions/ proper, so migration_status_report_cli.py's scan
        # never counts it as a real session.
        backup_dir = session_dir.parent / "migration-backups" / session_id
        backup_written = _write_apply_backup(
            backup_dir, session_dir / "messages.jsonl", session_dir / "state.json"
        )
        backup_skipped = not backup_written
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
        scratch_id, scratch_dir = await _build_scratch_session(coordinator, session_dir, session_info.state)
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
        backup_dir=backup_dir,
        backup_skipped=backup_skipped,
    )


async def verify_archive_migration(
    coordinator: SessionCoordinator,
    archive_dir: Path,
    *,
    apply: bool = False,
) -> VerificationReport:
    """Archive-targeting mode (§3): same dry-run/--apply semantics as
    verify_session_migration(), applied to a disposed session's frozen archive
    snapshot instead of a live session directory.

    There's no live SessionInfo/SessionManager tracking an archived session, so
    --apply read-modify-writes the archive's own state.json directly — mirroring
    what SessionManager.complete_message_migration() does for a live session —
    instead of going through the session-state machinery. The archive is treated
    as TERMINATED for finalize()'s end-of-stream sweep: a disposed session is
    never ACTIVE/PAUSED/STARTING by definition.
    """
    state_path = archive_dir / "state.json"
    messages_path = archive_dir / "messages.jsonl"
    if not state_path.exists() or not messages_path.exists():
        raise ValueError(f"Archive {archive_dir} is missing state.json or messages.jsonl")

    state_data = json.loads(state_path.read_text(encoding="utf-8"))
    session_id = state_data.get("session_id")
    if not session_id:
        raise ValueError(f"Archive {archive_dir}'s state.json has no session_id")
    if _is_canonical_schema_version(state_data.get("message_schema_version", 0)):
        raise RuntimeError(f"Archive {archive_dir} is already canonical — nothing to migrate")

    start_time = time.monotonic()
    session_state = SessionState.TERMINATED

    backup_dir: Path | None = None
    backup_skipped = False
    if apply:
        # Issue #2094 (AC2): dedicated location outside data/archives/minions/,
        # so _scan_archives() (which only walks archives_dir / "minions")
        # never sees it. minion_id/archive_id mirror AC2's own example path.
        # Structure is validated (found in review) because --archive-dir is
        # arbitrary user input — unlike the other scan tools, which only ever
        # walk DOWN from a known root, this climbs UP from it, so a
        # non-standard path must fail loudly rather than silently writing the
        # safety backup to an unintended location outside data_dir.
        minion_id = archive_dir.parent.name
        archive_id = archive_dir.name
        if archive_dir.parent.parent.name != "minions":
            raise ValueError(
                f"Archive {archive_dir} is not nested as <data_dir>/archives/minions/"
                "<minion>/<archive> — refusing to guess a backup location."
            )
        backup_dir = archive_dir.parent.parent.parent / "migration-backups" / minion_id / archive_id
        backup_written = _write_apply_backup(backup_dir, messages_path, state_path)
        backup_skipped = not backup_written

    before_id, before_dir = await _build_scratch_session(coordinator, archive_dir, session_state)
    try:
        before = await coordinator.get_session_messages(before_id)
        before_messages = [
            _normalize(m, ignore_tool_call_session_id=True) for m in before["messages"]
        ]
    finally:
        await _teardown_scratch_session(coordinator, before_id, before_dir)

    if apply:
        # Real migration, run directly against the archive's own files — a fresh
        # throwaway asyncio.Lock() is safe here (no concurrent writer can ever
        # touch a disposed session's archive, unlike a live session's messages.jsonl).
        result = await migrate_session_messages(
            archive_dir, session_id, session_state,
            coordinator._convert_legacy_record_to_websocket, asyncio.Lock(),
        )
        existing_status = state_data.get("message_migration_status")
        started_at = existing_status.get("started_at") if isinstance(existing_status, dict) else None
        state_data["message_schema_version"] = CURRENT_MESSAGE_SCHEMA_VERSION
        state_data["message_migration_status"] = {
            "state": "completed",
            "started_at": started_at,
            "completed_at": datetime.now(UTC).isoformat(),
            "error": None,
            "materialized_tool_calls": result.materialized_tool_calls,
        }
        state_path.write_text(
            json.dumps(state_data, indent=2, ensure_ascii=False), encoding="utf-8"
        )

        after_id, after_dir = await _build_scratch_session(coordinator, archive_dir, session_state)
        coordinator.session_manager._active_sessions[after_id].message_schema_version = (
            CURRENT_MESSAGE_SCHEMA_VERSION
        )
        try:
            after = await coordinator.get_session_messages(after_id)
            after_messages = [
                _normalize(m, ignore_tool_call_session_id=True) for m in after["messages"]
            ]
        finally:
            await _teardown_scratch_session(coordinator, after_id, after_dir)
    else:
        scratch_id, scratch_dir = await _build_scratch_session(coordinator, archive_dir, session_state)
        try:
            scratch_storage = coordinator._storage_managers[scratch_id]
            result = await migrate_session_messages(
                scratch_dir, session_id, session_state,
                coordinator._convert_legacy_record_to_websocket, scratch_storage._write_lock,
            )
            coordinator.session_manager._active_sessions[scratch_id].message_schema_version = (
                CURRENT_MESSAGE_SCHEMA_VERSION
            )
            after = await coordinator.get_session_messages(scratch_id)
            after_messages = [
                _normalize(m, ignore_tool_call_session_id=True) for m in after["messages"]
            ]
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
        backup_dir=backup_dir,
        backup_skipped=backup_skipped,
    )


def _print_report(report: VerificationReport) -> None:
    print(f"Session: {report.session_id}")
    print(f"Records before: {report.total_records_before}")
    print(f"Materialized tool_call records: {report.materialized_tool_calls}")
    print(f"Mode: {'APPLIED' if report.applied else 'dry run'}")
    # Issue #2094 (AC4): one line reporting exactly what was (or wasn't) written.
    if not report.applied:
        print("Dry run: no files written under --data-dir")
    elif report.backup_skipped:
        print(f"Backup already present at: {report.backup_dir} (skipped)")
    else:
        print(f"Backup written to: {report.backup_dir}")
    print(f"Duration: {report.duration_seconds:.3f}s")
    if report.ok:
        print("Result: OK — zero field-level divergences")
    else:
        print(f"Result: FAILED — {len(report.divergences)} divergence(s):")
        for d in report.divergences:
            print(f"  {d['key']}")
            print(f"    before: {d['before']}")
            print(f"    after:  {d['after']}")


async def _run(
    session_id: str | None, archive_dir: Path | None, data_dir: Path, apply: bool
) -> int:
    coordinator = SessionCoordinator(data_dir)
    await coordinator.initialize()
    try:
        if archive_dir is not None:
            report = await verify_archive_migration(coordinator, archive_dir, apply=apply)
        else:
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
        description="Verify (and optionally apply) message migration for one session or archive"
    )
    parser.add_argument(
        "session_id", nargs="?", default=None,
        help="Session ID to verify/migrate (mutually exclusive with --archive-dir)",
    )
    parser.add_argument(
        "--archive-dir", default=None,
        help="Path to a disposed session's archive directory to verify/migrate "
             "instead of a live session (mutually exclusive with session_id)",
    )
    parser.add_argument(
        "--data-dir", default="./data",
        help="Data directory location (default: ./data)",
    )
    parser.add_argument(
        "--apply", action="store_true",
        help="Actually perform the migration (default: safe, non-destructive dry run)",
    )
    args = parser.parse_args()

    if bool(args.session_id) == bool(args.archive_dir):
        parser.error("Specify exactly one of session_id or --archive-dir")

    data_dir = Path(args.data_dir).resolve()
    archive_dir = Path(args.archive_dir).resolve() if args.archive_dir else None
    return asyncio.run(_run(args.session_id, archive_dir, data_dir, args.apply))


if __name__ == "__main__":
    sys.exit(main())
