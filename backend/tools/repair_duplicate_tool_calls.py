#!/usr/bin/env python3
"""Repair tool for already-migrated duplicate tool_call records (issue #2093,
AC5) — a one-off operational tool, not part of the regular migration path.

#2093's root cause (fixed in tool_lifecycle_reconstruction.py) let two genuine
stored tool_call records for the same tool_use_id (one terminal, one
non-terminal) landing out of chronological order in the file resurrect
tracking and cause finalize() to synthesize a spurious duplicate record.
Since migration overwrites messages.jsonl in place, there is no original to
re-derive from for sessions/archives this bug already affected — this tool
operates on the *current* (already-duplicated) canonical file directly.

A tool_use_id legitimately has multiple `type == "tool_call"` records in a
canonical file — one per real lifecycle transition (pending, running,
completed, ...) — so grouping by (tool_use_id, status) alone is NOT how a
duplicate is detected here (found in review: #2093's finalize() sweep always
forces the SYNTHESIZED record's status to "interrupted" regardless of what
status the genuine terminal record already had, so a genuine "completed" +
synthesized "interrupted" pair would never collide on a (tool_use_id, status)
key). Instead: a tool_use_id is only ever meant to reach ONE terminal status
once — so 2+ `type == "tool_call"` records in a TERMINAL state for the same
tool_use_id is itself the duplicate signal, independent of whether their
statuses match. Among those, the specific record finalize() would have
synthesized is identified unambiguously: status == "interrupted" AND
message_id == `_materialized_record_id(session_id, tool_use_id, "interrupted")`
(the only transition finalize() ever mints). A record is only ever removed
when exactly one candidate matches that signature AND at least one other
terminal record for the same tool_use_id does not — i.e. there is an
unambiguous genuine/synthesized split. Zero or 2+ matching candidates is
reported as ambiguous and left alone; guessing which copy to delete would
risk destroying a genuine record.

Usage:
    uv run python -m backend.tools.repair_duplicate_tool_calls --session-id <id> [--data-dir DIR] [--apply]
    uv run python -m backend.tools.repair_duplicate_tool_calls --archive-dir <path> [--apply]
    uv run python -m backend.tools.repair_duplicate_tool_calls --scan-all [--data-dir DIR] [--apply]
"""

import argparse
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path

from backend.message_migration import _materialized_record_id
from backend.tool_lifecycle_reconstruction import _TERMINAL_STATUS_VALUES
from backend.tools.migration_status_report_cli import _is_canonical, _is_ignored_population_dir


@dataclass
class DuplicateCandidate:
    status: str
    message_id: str | None


@dataclass
class DuplicateGroup:
    tool_use_id: str
    candidates: list[DuplicateCandidate]


@dataclass
class RepairResult:
    target: str
    messages_path: Path
    repaired: list[DuplicateGroup] = field(default_factory=list)
    ambiguous: list[DuplicateGroup] = field(default_factory=list)
    applied: bool = False

    @property
    def clean(self) -> bool:
        return not self.repaired and not self.ambiguous


def find_and_repair_duplicate_tool_calls(
    messages_path: Path, session_id: str, apply: bool
) -> RepairResult:
    """Scan a canonical messages.jsonl for tool_use_ids with more than one
    `type == "tool_call"` record in a TERMINAL state (a tool_use_id is only
    ever meant to reach one terminal state once, so 2+ is itself the
    duplicate signal — see module docstring for why this does NOT group by
    (tool_use_id, status)). Among a group, the record finalize() would have
    synthesized is identified by status == "interrupted" and message_id
    matching `_materialized_record_id(session_id, tool_use_id, "interrupted")`
    — removed only if exactly one candidate matches AND at least one other
    terminal record for the same tool_use_id does not (an unambiguous
    genuine/synthesized split). Zero or 2+ matches is ambiguous — report,
    don't guess.
    """
    # Split on '\n' only — NOT str.splitlines(), which also breaks on
    # U+2028/U+2029/U+0085/etc. (issue #2096, same hazard as #2032).
    non_blank_lines = [
        line for line in messages_path.read_text(encoding="utf-8").split("\n") if line.strip()
    ]
    records = [json.loads(line) for line in non_blank_lines]

    groups: dict[str, list[int]] = {}
    for idx, record in enumerate(records):
        if record.get("type") != "tool_call":
            continue
        tool_use_id = record.get("tool_use_id")
        status = record.get("status")
        if not tool_use_id or status not in _TERMINAL_STATUS_VALUES:
            continue
        groups.setdefault(tool_use_id, []).append(idx)

    result = RepairResult(target=session_id, messages_path=messages_path, applied=apply)
    remove_indices: set[int] = set()

    for tool_use_id, indices in groups.items():
        if len(indices) < 2:
            continue
        synthesized_id = _materialized_record_id(session_id, tool_use_id, "interrupted")
        synthesized_candidates = [
            idx for idx in indices
            if records[idx].get("status") == "interrupted"
            and records[idx].get("message_id") == synthesized_id
        ]
        candidates = [
            DuplicateCandidate(records[idx].get("status"), records[idx].get("message_id"))
            for idx in indices
        ]

        if len(synthesized_candidates) == 1:
            remove_indices.add(synthesized_candidates[0])
            result.repaired.append(DuplicateGroup(tool_use_id, candidates))
        else:
            result.ambiguous.append(DuplicateGroup(tool_use_id, candidates))

    if apply and remove_indices:
        kept_lines = [
            line for idx, line in enumerate(non_blank_lines) if idx not in remove_indices
        ]
        messages_path.write_text(
            "\n".join(kept_lines) + ("\n" if kept_lines else ""), encoding="utf-8"
        )

    return result


def _scan_all_targets(data_dir: Path) -> list[tuple[str, Path]]:
    """Every already-canonical live session and archive under data_dir,
    reusing migration_status_report_cli.py's same directory-walking
    structure (sessions_dir.iterdir(); archives_dir/minions/*/*)."""
    targets: list[tuple[str, Path]] = []

    sessions_dir = data_dir / "sessions"
    if sessions_dir.is_dir():
        for session_dir in sorted(sessions_dir.iterdir()):
            if not session_dir.is_dir() or _is_ignored_population_dir(session_dir.name):
                continue
            state_file = session_dir / "state.json"
            messages_file = session_dir / "messages.jsonl"
            if not state_file.exists() or not messages_file.exists():
                continue
            try:
                state_data = json.loads(state_file.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                continue
            if _is_canonical(state_data):
                targets.append((state_data.get("session_id", session_dir.name), messages_file))

    archives_dir = data_dir / "archives" / "minions"
    if archives_dir.is_dir():
        for minion_dir in sorted(archives_dir.iterdir()):
            if not minion_dir.is_dir() or _is_ignored_population_dir(minion_dir.name):
                continue
            for archive_dir in sorted(minion_dir.iterdir()):
                if not archive_dir.is_dir() or _is_ignored_population_dir(archive_dir.name):
                    continue
                state_file = archive_dir / "state.json"
                messages_file = archive_dir / "messages.jsonl"
                if not state_file.exists() or not messages_file.exists():
                    continue
                try:
                    state_data = json.loads(state_file.read_text(encoding="utf-8"))
                except (json.JSONDecodeError, OSError):
                    continue
                if _is_canonical(state_data):
                    session_id = state_data.get("session_id", archive_dir.name)
                    targets.append((session_id, messages_file))

    return targets


def _print_result(result: RepairResult) -> None:
    print(f"Target: {result.target} ({result.messages_path})")
    print(f"Mode: {'APPLIED' if result.applied else 'dry run'}")
    if not result.repaired and not result.ambiguous:
        print("  no duplicates found")
        return
    for group in result.repaired:
        verb = "removed" if result.applied else "would remove"
        candidates = [(c.status, c.message_id) for c in group.candidates]
        print(
            f"  {verb} duplicate tool_use_id={group.tool_use_id} "
            f"(status, message_id) candidates={candidates}"
        )
    for group in result.ambiguous:
        candidates = [(c.status, c.message_id) for c in group.candidates]
        print(
            f"  AMBIGUOUS (not touched) tool_use_id={group.tool_use_id} "
            f"(status, message_id) candidates={candidates}"
        )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Repair already-migrated duplicate tool_call records (issue #2093 AC5)"
    )
    parser.add_argument("--session-id", default=None, help="Live session id to repair")
    parser.add_argument("--archive-dir", default=None, help="Archive directory to repair")
    parser.add_argument(
        "--scan-all", action="store_true",
        help="Check every already-canonical live session and archive under --data-dir",
    )
    parser.add_argument("--data-dir", default="./data", help="Data directory location (default: ./data)")
    parser.add_argument(
        "--apply", action="store_true",
        help="Actually remove the identified duplicates (default: dry-run report only)",
    )
    args = parser.parse_args()

    modes_selected = sum(bool(x) for x in (args.session_id, args.archive_dir, args.scan_all))
    if modes_selected != 1:
        parser.error("Specify exactly one of --session-id, --archive-dir, or --scan-all")

    data_dir = Path(args.data_dir).resolve()

    if args.scan_all:
        targets = _scan_all_targets(data_dir)
        if not targets:
            print("No already-canonical sessions/archives found.")
            return 0
        any_ambiguous = False
        for session_id, messages_path in targets:
            result = find_and_repair_duplicate_tool_calls(messages_path, session_id, args.apply)
            if not result.clean:
                _print_result(result)
                if result.ambiguous:
                    any_ambiguous = True
        return 1 if any_ambiguous else 0

    if args.archive_dir:
        archive_dir = Path(args.archive_dir).resolve()
        state_path = archive_dir / "state.json"
        messages_path = archive_dir / "messages.jsonl"
        if not state_path.exists() or not messages_path.exists():
            print(f"Archive {archive_dir} is missing state.json or messages.jsonl", file=sys.stderr)
            return 1
        session_id = json.loads(state_path.read_text(encoding="utf-8")).get("session_id")
        if not session_id:
            print(f"Archive {archive_dir}'s state.json has no session_id", file=sys.stderr)
            return 1
    else:
        session_id = args.session_id
        messages_path = data_dir / "sessions" / session_id / "messages.jsonl"
        if not messages_path.exists():
            print(f"Session {session_id} has no messages.jsonl under {data_dir}", file=sys.stderr)
            return 1

    result = find_and_repair_duplicate_tool_calls(messages_path, session_id, args.apply)
    _print_result(result)
    return 1 if result.ambiguous else 0


if __name__ == "__main__":
    sys.exit(main())
