#!/usr/bin/env python3
"""One-off cleanup tool for already-bloated thinking_tokens records (issue #2136,
carries AC3/"stage B" of the now-closed #2132).

#2132 stage A (commit 2c5960ef) stopped *new* thinking_tokens SystemMessage frames
from being persisted — they're forwarded live-only now. That fix does nothing for
sessions/archives that are already bloated with these records on disk. This tool
scans every live session and disposed archive, and (with --apply) rewrites
messages.jsonl to drop lines matching the bloated shape:
{"type": "system", "subtype": "thinking_tokens", ...}.

Modeled on migration_status_report_cli.py: plain functions over argparse, no
SessionCoordinator/SDK dependency, dependency-light so it's safe to run freely.

Default (no --apply) is a dry run — reports per-session/archive removed count and
bytes reclaimed, writes nothing. --apply performs the rewrite, after backing up the
original files. A live session that looks like it's actually running
(RUNNABLE_STATES or is_processing) is skipped by --apply unless --force is also
passed — see _process_live_session for why this guard exists instead of a lock.

Usage:
    uv run python -m backend.tools.cleanup_thinking_tokens_cli [--data-dir DIR] [--apply] [--force]
"""

import argparse
import json
import os
import shutil
import sys
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from backend.session_manager import RUNNABLE_STATES, SessionState

# This tool's own backup namespace — deliberately NOT "migration-backups". The
# now-deleted migrate_session_verify_cli.py (retired in #2099) used exactly
# <data_dir>/sessions/migration-backups/<session_id>/ and
# <data_dir>/archives/migration-backups/<minion_id>/<archive_id>/ for its own,
# unrelated schema-migration backups, and production sessions that went through
# that migration still have those directories on disk. _write_backup()'s only
# idempotency signal is "does backup_dir already exist" — reusing that same path
# here would make this tool see the old tool's leftover backup, conclude "already
# backed up", and skip ever saving the real pre-cleanup messages.jsonl before
# overwriting it (found in review, reproduced live). A dedicated name sidesteps
# the collision entirely.
_BACKUP_DIR_NAME = "thinking-tokens-backups"

# Own copy of migration_status_report_cli.py's ignore-dir convention (issue #2094
# AC3's precedent) — no cross-import, these are two independent tools. Applied at
# every directory level walked so a prior run's own backup directory (ours or the
# retired schema-migration tool's) is never mistaken for a session/archive to
# clean on a later run. The substring marker catches any pre-#2094 legacy
# "<id>-migration-verify-<timestamp>" dry-run leftovers that may still exist in
# production, exactly as migration_status_report_cli.py also guards against.
_IGNORED_DIR_NAMES = frozenset({"migration-backups", _BACKUP_DIR_NAME})
_LEGACY_VERIFY_SUFFIX_MARKER = "-migration-verify-"


def _is_ignored_dir_name(name: str) -> bool:
    return name in _IGNORED_DIR_NAMES or _LEGACY_VERIFY_SUFFIX_MARKER in name


@dataclass
class CleanupResult:
    removed_count: int = 0
    bytes_reclaimed: int = 0
    applied: bool = False
    skipped_active: bool = False  # live session was running, apply skipped unless --force
    backup_skipped: bool = False  # backup dir already existed (idempotent re-run)
    error: str | None = None


@dataclass
class CleanupReport:
    per_target: dict[str, CleanupResult] = field(default_factory=dict)
    total_removed: int = 0
    total_bytes: int = 0


def _filter_thinking_tokens(original: bytes) -> tuple[bytes, int, int]:
    """Split on b'\\n' only (mirrors data_storage.py's own read_messages()
    precedent — NOT str.splitlines(), which also breaks on U+2028/U+2029/etc and
    would corrupt a content field containing one verbatim). Returns
    (new_content_bytes, removed_count, bytes_reclaimed). Lines that fail to parse
    as JSON, or don't match type=="system"/subtype=="thinking_tokens", are kept
    byte-for-byte unchanged — never round-tripped through json.dumps — so every
    surviving record stays byte-identical to its pre-cleanup original.

    Takes already-read bytes rather than a path so callers that also need the
    original bytes for a backup (see _process_live_session/_process_archive) only
    read the file off disk once.
    """
    lines = original.split(b"\n")
    kept: list[bytes] = []
    removed_count = 0
    for line in lines:
        stripped = line.strip()
        if stripped:
            try:
                data = json.loads(stripped)
            except (json.JSONDecodeError, UnicodeDecodeError):
                data = None
            if (
                isinstance(data, dict)
                and data.get("type") == "system"
                and data.get("subtype") == "thinking_tokens"
            ):
                removed_count += 1
                continue
        kept.append(line)
    new_content = b"\n".join(kept)
    bytes_reclaimed = len(original) - len(new_content)
    return new_content, removed_count, bytes_reclaimed


def _write_backup(backup_dir: Path, messages_bytes: bytes, state_path: Path) -> bool:
    """Idempotent pre-rewrite backup. Returns True if a new backup was written,
    False if one already existed from a prior run (skipped, not an error — the
    backup's presence is itself the idempotency marker).

    Takes the already-in-memory pre-filter bytes for messages.jsonl (rather than
    re-reading the path) so the file is only read off disk once per run.

    Stages into a sibling tmp directory and atomically renames it into place
    (mirrors the deleted migrate_session_verify_cli.py's _write_apply_backup()) so
    a crash mid-backup never leaves a partial backup_dir a retry mistakes for
    complete.
    """
    if backup_dir.exists():
        return False
    staging_dir = backup_dir.parent / f".{backup_dir.name}.cleanup-backup-tmp-{uuid.uuid4().hex}"
    staging_dir.mkdir(parents=True, exist_ok=True)
    try:
        (staging_dir / "messages.jsonl").write_bytes(messages_bytes)
        if state_path.exists():
            shutil.copy2(state_path, staging_dir / "state.json")
        os.replace(staging_dir, backup_dir)
    except Exception:
        shutil.rmtree(staging_dir, ignore_errors=True)
        raise
    return True


def _atomic_rewrite(path: Path, content: bytes) -> None:
    tmp_path = path.with_name(f".{path.name}.cleanup-tmp-{uuid.uuid4().hex}")
    try:
        tmp_path.write_bytes(content)
        os.replace(tmp_path, path)
    except Exception:
        tmp_path.unlink(missing_ok=True)
        raise


def _is_active_session(state_path: Path) -> bool:
    """RUNNABLE_STATES/is_processing check (imported from backend.session_manager,
    not reinvented) — the active-session guard. See _process_live_session for why
    this replaces a DataStorageManager._write_lock-based approach entirely.

    A state.json that can't be read, isn't valid JSON, or doesn't even parse to a
    dict is treated as "can't confirm inactive" and reported active (the safe
    default for a tool about to overwrite someone's messages.jsonl) rather than
    raising or silently defaulting to "not active" — both found in review to be
    the wrong failure mode for a maintenance tool whose whole point is not
    corrupting live data.
    """
    if not state_path.exists():
        return False
    try:
        state_data = json.loads(state_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError, UnicodeDecodeError):
        return True
    if not isinstance(state_data, dict):
        return True
    try:
        state_enum = SessionState(state_data.get("state"))
    except ValueError:
        state_enum = None
    return state_enum in RUNNABLE_STATES or bool(state_data.get("is_processing"))


def _finalize_apply(
    *,
    messages_path: Path,
    state_path: Path,
    backup_dir: Path,
    original: bytes,
    new_content: bytes,
    removed_count: int,
    result: CleanupResult,
) -> None:
    """Shared backup-then-atomic-rewrite step for both the live-session and
    archive paths — the only things that differ between them are the active-
    session guard (live only) and the backup_dir formula, both handled by the
    caller before this is invoked.
    """
    if removed_count == 0:
        # Nothing to do — this is what makes a second --apply run a no-op,
        # not an explicit backup-dir check.
        return
    try:
        backup_written = _write_backup(backup_dir, original, state_path)
    except OSError as e:
        result.error = str(e)
        return
    result.backup_skipped = not backup_written

    try:
        _atomic_rewrite(messages_path, new_content)
    except OSError as e:
        result.error = str(e)
        return
    result.applied = True


def _process_live_session(session_dir: Path, *, apply: bool, force: bool) -> CleanupResult:
    """DataStorageManager._write_lock is a plain asyncio.Lock() — meaningful only
    for coroutines sharing one process/event loop. append_message() never acquires
    any OS-level lock, so there is no mechanism on the write side this CLI could
    ever synchronize against from a separate process. Instead: refuse to touch a
    live session's messages.jsonl at all while it's RUNNABLE_STATES/is_processing,
    unless --force overrides it. Residual TOCTOU risk between this check and the
    rewrite below is accepted — fine for a one-off maintenance tool run
    interactively, not a background service.
    """
    result = CleanupResult()
    messages_path = session_dir / "messages.jsonl"
    if not messages_path.exists():
        return result

    result.skipped_active = _is_active_session(session_dir / "state.json")

    try:
        original = messages_path.read_bytes()
    except OSError as e:
        result.error = str(e)
        return result
    new_content, removed_count, bytes_reclaimed = _filter_thinking_tokens(original)
    result.removed_count = removed_count
    result.bytes_reclaimed = bytes_reclaimed

    if not apply or (result.skipped_active and not force):
        return result

    backup_dir = session_dir.parent / _BACKUP_DIR_NAME / session_dir.name
    _finalize_apply(
        messages_path=messages_path,
        state_path=session_dir / "state.json",
        backup_dir=backup_dir,
        original=original,
        new_content=new_content,
        removed_count=removed_count,
        result=result,
    )
    return result


def _process_archive(archive_dir: Path, minion_id: str, apply: bool) -> CleanupResult:
    """Same _filter_thinking_tokens() core as the live path. No active-session
    guard — a disposed archive has no live writer (the deleted
    migrate_session_verify_cli.py used a fresh throwaway asyncio.Lock() for
    archive rewrites, i.e. "no real concurrent writer exists" was already the
    established assumption there).

    Backup path is <data_dir>/archives/<_BACKUP_DIR_NAME>/<minion_id>/<archive_id>/
    — archive_dir is <data_dir>/archives/minions/<minion_id>/<archive_id>, so
    archive_dir.parent.parent.parent is <data_dir>/archives.
    """
    result = CleanupResult()
    messages_path = archive_dir / "messages.jsonl"
    if not messages_path.exists():
        return result

    try:
        original = messages_path.read_bytes()
    except OSError as e:
        result.error = str(e)
        return result
    new_content, removed_count, bytes_reclaimed = _filter_thinking_tokens(original)
    result.removed_count = removed_count
    result.bytes_reclaimed = bytes_reclaimed

    if not apply:
        return result

    backup_dir = archive_dir.parent.parent.parent / _BACKUP_DIR_NAME / minion_id / archive_dir.name
    _finalize_apply(
        messages_path=messages_path,
        state_path=archive_dir / "state.json",
        backup_dir=backup_dir,
        original=original,
        new_content=new_content,
        removed_count=removed_count,
        result=result,
    )
    return result


def _scan_live(sessions_dir: Path, *, apply: bool, force: bool) -> CleanupReport:
    report = CleanupReport()
    if not sessions_dir.is_dir():
        return report
    for session_dir in sorted(sessions_dir.iterdir()):
        if not session_dir.is_dir() or _is_ignored_dir_name(session_dir.name):
            continue
        try:
            result = _process_live_session(session_dir, apply=apply, force=force)
        except Exception as e:
            result = CleanupResult(error=str(e))
        report.per_target[session_dir.name] = result
        report.total_removed += result.removed_count
        report.total_bytes += result.bytes_reclaimed
    return report


def _scan_archives(archives_dir: Path, *, apply: bool) -> CleanupReport:
    report = CleanupReport()
    if not archives_dir.is_dir():
        return report
    for minion_dir in sorted(archives_dir.iterdir()):
        if not minion_dir.is_dir() or _is_ignored_dir_name(minion_dir.name):
            continue
        for archive_dir in sorted(minion_dir.iterdir()):
            if not archive_dir.is_dir() or _is_ignored_dir_name(archive_dir.name):
                continue
            target_id = f"{minion_dir.name}/{archive_dir.name}"
            try:
                result = _process_archive(archive_dir, minion_dir.name, apply)
            except Exception as e:
                result = CleanupResult(error=str(e))
            report.per_target[target_id] = result
            report.total_removed += result.removed_count
            report.total_bytes += result.bytes_reclaimed
    return report


def _print_report(name: str, report: CleanupReport) -> None:
    print(f"{name}:")
    if not report.per_target:
        print("  (none found)")
        return
    for target_id, result in report.per_target.items():
        if result.error:
            print(f"  {target_id}: ERROR - {result.error}")
            continue
        flags = []
        if result.skipped_active:
            flags.append("active — apply skipped unless --force" if not result.applied else "active, forced")
        if result.applied:
            flags.append("applied")
        if result.backup_skipped:
            flags.append("backup already present")
        suffix = f" [{', '.join(flags)}]" if flags else ""
        print(
            f"  {target_id}: {result.removed_count} record(s), "
            f"{result.bytes_reclaimed} bytes{suffix}"
        )


def run(data_dir: Path, *, apply: bool, force: bool) -> int:
    """Scan both populations, print the report, and (with --apply) perform the
    rewrites. Returns the process exit code: 0 on a clean run (regardless of
    whether anything was found to clean — this is a cleanup utility, not a
    go/no-go gate), 1 only if any individual session/archive hit an unexpected
    error while being processed.
    """
    live = _scan_live(data_dir / "sessions", apply=apply, force=force)
    archived = _scan_archives(data_dir / "archives" / "minions", apply=apply)

    _print_report("Live sessions", live)
    _print_report("Archived sessions", archived)

    total_removed = live.total_removed + archived.total_removed
    total_bytes = live.total_bytes + archived.total_bytes
    mode = "applied" if apply else "dry run"
    print(f"\nGrand total ({mode}): {total_removed} record(s), {total_bytes} bytes reclaimed")

    has_error = any(r.error for r in live.per_target.values()) or any(
        r.error for r in archived.per_target.values()
    )
    return 1 if has_error else 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Clean up already-persisted thinking_tokens bloat from "
        "messages.jsonl across live sessions and disposed archives (issue #2136)"
    )
    parser.add_argument(
        "--data-dir", default="./data",
        help="Data directory location (default: ./data)",
    )
    parser.add_argument(
        "--apply", action="store_true",
        help="Actually perform the rewrites (default: safe, non-destructive dry run)",
    )
    parser.add_argument(
        "--force", action="store_true",
        help="Only meaningful with --apply; bypasses the active-session guard for "
        "live sessions",
    )
    args = parser.parse_args()
    if args.force and not args.apply:
        parser.error("--force is only meaningful with --apply")

    data_dir = Path(args.data_dir).resolve()
    return run(data_dir, apply=args.apply, force=args.force)


if __name__ == "__main__":
    sys.exit(main())
