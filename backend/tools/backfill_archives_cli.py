#!/usr/bin/env python3
"""Batch backfill for existing (pre-3-D-prep) archives (issue #2084, 3-D-cutover prep).

migrate_session_verify_cli.py's --archive-dir mode operates on one archive at a
time by design (it's a verification tool, not a batch tool). This wrapper drives
it across every archive under <data-dir>/archives/minions/*/*/ in one process —
reusing the exact same verify_archive_migration() engine (same atomic-swap
migration, same state.json update), just iterated, with one SessionCoordinator
for the whole run instead of 441 separate process startups.

Safe to interrupt and re-run: already-canonical archives are skipped (the
underlying function raises and this wrapper catches it), and any archive whose
migration raises is reported and skipped, not retried in the same run.

Usage:
    uv run python -m backend.tools.backfill_archives_cli [--data-dir DIR] [--apply]

Default (no --apply) is a dry run: reports what WOULD happen (canonical-already /
divergence-free / would-fail counts) without writing anything, same semantics as
migrate_session_verify_cli.py's own --apply flag.
"""

import argparse
import asyncio
import json
import sys
from pathlib import Path

from backend.session_coordinator import SessionCoordinator, _is_canonical_schema_version
from backend.tools.migrate_session_verify_cli import verify_archive_migration
from backend.tools.migration_status_report_cli import _is_ignored_population_dir


async def _run(data_dir: Path, apply: bool) -> int:
    archives_root = data_dir / "archives" / "minions"
    if not archives_root.is_dir():
        print(f"No archives directory at {archives_root}")
        return 0

    targets = []
    for minion_dir in sorted(archives_root.iterdir()):
        if not minion_dir.is_dir() or _is_ignored_population_dir(minion_dir.name):
            continue
        for archive_dir in sorted(minion_dir.iterdir()):
            if not archive_dir.is_dir() or _is_ignored_population_dir(archive_dir.name):
                continue
            state_path = archive_dir / "state.json"
            if not state_path.exists():
                continue
            try:
                state_data = json.loads(state_path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError) as e:
                print(f"SKIP (unreadable state.json): {archive_dir} — {e}")
                continue
            if _is_canonical_schema_version(state_data.get("message_schema_version", 0)):
                continue
            targets.append(archive_dir)

    print(f"Found {len(targets)} non-canonical archive(s) to process "
          f"({'APPLY' if apply else 'DRY RUN'}).\n")

    coordinator = SessionCoordinator(data_dir)
    await coordinator.initialize()

    migrated, clean_dry_run, failed = [], [], []
    try:
        for i, archive_dir in enumerate(targets, 1):
            label = f"[{i}/{len(targets)}] {archive_dir.parent.name}/{archive_dir.name}"
            try:
                report = await verify_archive_migration(coordinator, archive_dir, apply=apply)
            except Exception as e:
                print(f"{label}: FAILED — {e}")
                failed.append((archive_dir, str(e)))
                continue

            if not report.ok:
                print(f"{label}: DIVERGENCE FOUND ({len(report.divergences)}) — not applied cleanly")
                failed.append((archive_dir, f"{len(report.divergences)} divergence(s)"))
                continue

            if apply:
                print(f"{label}: migrated ({report.materialized_tool_calls} materialized tool_call records)")
                migrated.append(archive_dir)
            else:
                print(f"{label}: OK — would migrate cleanly ({report.materialized_tool_calls} materialized tool_call records)")
                clean_dry_run.append(archive_dir)
    finally:
        await coordinator.cleanup()

    print("\n--- Summary ---")
    print(f"Total non-canonical found: {len(targets)}")
    if apply:
        print(f"Migrated successfully: {len(migrated)}")
    else:
        print(f"Would migrate cleanly: {len(clean_dry_run)}")
    print(f"Failed/diverged: {len(failed)}")
    for archive_dir, reason in failed:
        print(f"  - {archive_dir}: {reason}")

    return 0 if not failed else 1


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Batch-backfill every non-canonical archive under <data-dir>/archives/minions/"
    )
    parser.add_argument("--data-dir", default="./data", help="Data directory location (default: ./data)")
    parser.add_argument("--apply", action="store_true", help="Actually perform the migrations (default: dry run)")
    args = parser.parse_args()
    return asyncio.run(_run(Path(args.data_dir).resolve(), args.apply))


if __name__ == "__main__":
    sys.exit(main())
