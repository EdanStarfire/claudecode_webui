#!/usr/bin/env python3
"""Read-only scan-all migration status report (issue #2084 stage 3-D-prep, §6) —
the literal go/no-go signal for 3-D-cutover: "how many sessions, live or
archived, are still non-canonical."

Strictly read-only — no --apply, no mutation capability at all, by design,
distinct from migrate_session_verify_cli.py's --apply (which targets one
session/archive at a time and does perform a real migration). Reads each
state.json directly rather than going through SessionManager.initialize()
(whose startup self-heal persists changes as a side effect of loading) — this
tool must never write anything, even incidentally, so it's safe to run freely
and repeatedly against a live data dir without any coordination.

Usage:
    uv run python -m backend.tools.migration_status_report_cli [--data-dir DIR]
"""

import argparse
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path

from backend.models.messages import CURRENT_MESSAGE_SCHEMA_VERSION


@dataclass
class PopulationReport:
    canonical: int = 0
    never_attempted: int = 0
    in_progress: int = 0
    quarantined: int = 0
    quarantined_ids: list[str] = field(default_factory=list)
    unreadable: list[str] = field(default_factory=list)

    @property
    def total(self) -> int:
        return self.canonical + self.never_attempted + self.in_progress + self.quarantined

    @property
    def fully_canonical(self) -> bool:
        # Issue #2084 (stage 3-D-prep, §6, found in review): an unreadable
        # state.json must never be silently treated as equivalent to "verified
        # canonical" — it's exactly the kind of record a human needs to look at
        # before the 3-D-cutover decision is safe, so it blocks readiness the
        # same way a quarantined record does, even though every readable record
        # happens to be canonical.
        return self.total == self.canonical and not self.unreadable


def _bucket(report: PopulationReport, record_id: str, data: dict) -> None:
    # Inlined rather than importing session_coordinator._is_canonical_schema_version
    # (the documented single source of this criterion) — that module pulls in the
    # full SDK/ClaudeSDK/SessionCoordinator dependency graph, which this tool
    # deliberately avoids so it stays cheap and dependency-light to run freely and
    # repeatedly. Keep this comparison identical to that function's if the
    # criterion ever changes.
    schema_version = data.get("message_schema_version", 0)
    if schema_version >= CURRENT_MESSAGE_SCHEMA_VERSION:
        report.canonical += 1
        return
    status = data.get("message_migration_status")
    state = status.get("state") if isinstance(status, dict) else None
    if state == "in_progress":
        report.in_progress += 1
    elif state == "quarantined":
        report.quarantined += 1
        report.quarantined_ids.append(record_id)
    else:
        # None (never attempted) or "completed" with a stale schema_version
        # (shouldn't happen — complete_message_migration() sets both together —
        # but bucketed as never_attempted rather than silently miscounted as OK).
        report.never_attempted += 1


def _scan_live(sessions_dir: Path) -> PopulationReport:
    report = PopulationReport()
    if not sessions_dir.is_dir():
        return report
    for session_dir in sorted(sessions_dir.iterdir()):
        if not session_dir.is_dir():
            continue
        state_file = session_dir / "state.json"
        if not state_file.exists():
            continue
        try:
            data = json.loads(state_file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            report.unreadable.append(session_dir.name)
            continue
        _bucket(report, data.get("session_id", session_dir.name), data)
    return report


def _scan_archives(archives_dir: Path) -> PopulationReport:
    report = PopulationReport()
    if not archives_dir.is_dir():
        return report
    for minion_dir in sorted(archives_dir.iterdir()):
        if not minion_dir.is_dir():
            continue
        for archive_dir in sorted(minion_dir.iterdir()):
            if not archive_dir.is_dir():
                continue
            archive_id = f"{minion_dir.name}/{archive_dir.name}"
            state_file = archive_dir / "state.json"
            if not state_file.exists():
                continue
            try:
                data = json.loads(state_file.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                report.unreadable.append(archive_id)
                continue
            _bucket(report, data.get("session_id", archive_id), data)
    return report


def _print_population(name: str, report: PopulationReport) -> None:
    print(f"{name}:")
    print(f"  total: {report.total}")
    print(f"  canonical: {report.canonical}")
    print(f"  never attempted: {report.never_attempted}")
    print(f"  in_progress: {report.in_progress}")
    print(f"  quarantined: {report.quarantined}")
    if report.quarantined_ids:
        print("  quarantined ids:")
        for qid in report.quarantined_ids:
            print(f"    - {qid}")
    if report.unreadable:
        print(f"  unreadable state.json: {len(report.unreadable)}")
        for uid in report.unreadable:
            print(f"    - {uid}")


def run(data_dir: Path) -> int:
    """Scan both populations and print the report. Returns the process exit code
    (0 only when both populations are 100% canonical), so this doubles as a
    scriptable CI/operational gate, not just a human-read report."""
    live = _scan_live(data_dir / "sessions")
    archived = _scan_archives(data_dir / "archives" / "minions")

    _print_population("Live sessions", live)
    _print_population("Archived sessions", archived)

    if live.fully_canonical and archived.fully_canonical:
        print("\nResult: OK — 100% canonical across both live and archived sessions")
        return 0
    print("\nResult: NOT READY — non-canonical sessions remain in at least one population")
    return 1


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Read-only scan of live + archived sessions' migration status "
                     "(issue #2084 stage 3-D-prep, §6) — the go/no-go signal for 3-D-cutover"
    )
    parser.add_argument(
        "--data-dir", default="./data",
        help="Data directory location (default: ./data)",
    )
    args = parser.parse_args()
    data_dir = Path(args.data_dir).resolve()
    return run(data_dir)


if __name__ == "__main__":
    sys.exit(main())
