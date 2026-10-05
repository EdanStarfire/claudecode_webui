"""
Streaming background migration driver (issue #2084 stage 3-C, §3).

Converts a legacy (message_schema_version == 0) session's messages.jsonl into
canonical MessageRecord shape in two bounded-memory streaming passes, materializing
any tool_call records that issue #491's synthetic-reconstruction pass would otherwise
only ever generate transiently at read time (the pre-#494 case: a legacy session with
zero stored ToolCallUpdate records for some tool calls).

Two passes, not one — issue #2052 already proved a single forward pass is unsafe for
this exact state (storage append order does not guarantee a ToolCallUpdate precedes its
triggering AssistantMessage). Pass 1 pre-scans for already-covered tool_use_ids; pass 2
converts and synthesizes, now safe to run forward-only.

Any exception here (malformed JSON, a conversion failure) propagates to the caller
uncaught — callers (MessageMigrationService / the on-demand trigger) catch it and
quarantine the session (§7). The original `source` file is never mutated before the
final atomic replace, so a raised exception always leaves it untouched.
"""

import asyncio
import json
import os
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .session_manager import SessionState
from .tool_lifecycle_reconstruction import ToolLifecycleReconstructor, prescan_stored_tool_calls

# Fixed constant (issue #2084 stage 3-C, §2): never change — every materialized
# record's identity depends on this namespace staying stable across runs/restarts.
_MIGRATION_NAMESPACE = uuid.UUID("f3b9b6a0-6b1d-4e2a-9b0d-2e9f9b7e6c41")


def _materialized_record_id(session_id: str, tool_use_id: str | None, transition: str) -> str:
    """Deterministic id for a synthesized record — same on every run (§2 resumability)."""
    return str(uuid.uuid5(_MIGRATION_NAMESPACE, f"{session_id}:{tool_use_id}:{transition}"))


def _stamp_materialized_record(record: dict[str, Any], session_id: str, timestamp: Any) -> dict[str, Any]:
    """Mint a stable message_id and anchor timestamp for a newly synthesized record.

    Issue #2084 (stage 3-C, §2): the live reload path never persists these (ephemeral,
    regenerated per-request), so ToolLifecycleReconstructor.feed()/finalize() never set
    either field. Migration persists them, so both must be set here — once, at the
    exact point get_session_messages() would have inserted this entry — not inside the
    shared reconstructor, which the live path must keep using unmodified.
    """
    record["message_id"] = _materialized_record_id(
        session_id, record.get("tool_use_id"), record.get("status", "unknown")
    )
    record.setdefault("timestamp", timestamp)
    return record


@dataclass
class MigrationResult:
    session_id: str
    line_count: int
    materialized_tool_calls: int
    output_path: Path
    applied: bool


def _prescan_pass(source: Path, reconstructor: ToolLifecycleReconstructor) -> None:
    """Pass 1 (issue #2052's own fix, reused as-is): collect every tool_use_id that
    already has an explicit stored record, BEFORE any conversion/synthesis runs, via
    the exact same matching logic get_session_messages() uses for its own pre-scan
    (prescan_stored_tool_calls() — shared, not reimplemented, to avoid silent drift).
    Holds only a small id set in memory, never record contents (the generator below
    yields one parsed record at a time). Plain synchronous function — run via
    asyncio.to_thread so a large file never blocks the event loop (issue #2026's own
    hazard, now applying equally to a streaming migration pass)."""
    with source.open(encoding="utf-8") as inp:
        prescan_stored_tool_calls(
            (json.loads(line) for line in inp if line.strip()), reconstructor
        )


def _conversion_pass(
    source: Path,
    tmp: Path,
    session_id: str,
    session_state: SessionState,
    convert_fn: Callable[[dict[str, Any]], dict[str, Any] | None],
    reconstructor: ToolLifecycleReconstructor,
) -> tuple[int, int]:
    """Pass 2: the real streaming conversion + synthesis, now safe to run
    forward-only since every already-covered tool_use_id is already known. Plain
    synchronous function for the same asyncio.to_thread reason as _prescan_pass.
    Returns (line_count, materialized_tool_calls)."""
    line_count = 0
    materialized_tool_calls = 0
    last_timestamp: Any = None
    with source.open(encoding="utf-8") as inp, tmp.open("w", encoding="utf-8") as out:
        for line in inp:
            line = line.strip()
            if not line:
                continue
            raw = json.loads(line)
            line_count += 1

            canonical = convert_fn(raw)
            if canonical is None:
                # Matches get_session_messages()'s `if not websocket_data: continue`
                # — no output, no synthesis, for a record that fails to convert.
                continue

            last_timestamp = canonical.get("timestamp", last_timestamp)
            out.write(json.dumps(canonical, ensure_ascii=False) + "\n")

            for synthesized in reconstructor.feed(canonical):
                _stamp_materialized_record(synthesized, session_id, canonical.get("timestamp"))
                out.write(json.dumps(synthesized, ensure_ascii=False) + "\n")
                materialized_tool_calls += 1

        for synthesized in reconstructor.finalize(session_state):
            _stamp_materialized_record(synthesized, session_id, last_timestamp)
            out.write(json.dumps(synthesized, ensure_ascii=False) + "\n")
            materialized_tool_calls += 1

    return line_count, materialized_tool_calls


async def migrate_session_messages(
    session_dir: Path,
    session_id: str,
    session_state: SessionState,
    convert_fn: Callable[[dict[str, Any]], dict[str, Any] | None],
    write_lock: asyncio.Lock,
    *,
    apply: bool = True,
    output_filename: str = "messages.jsonl.migrating",
) -> MigrationResult:
    """Migrate one session's messages.jsonl to canonical shape.

    Holds `write_lock` for the *entire* duration (both passes + the atomic swap when
    `apply=True`) — not just at the swap — so a live `append_message()` call can never
    land between pass 1's pre-scan and pass 2's conversion (§3 correction: this would
    reintroduce a narrower version of #2052's hazard, moved to a race between migration
    and live writes instead of within migration's own passes).

    Both passes run via `asyncio.to_thread` — the same hazard issue #2026 fixed for
    `DataStorageManager.read_messages()` applies equally here: a large messages.jsonl
    would otherwise block the entire event loop (every other session's long-poll
    responses, SDK message delivery, incoming requests) for the full O(file size)
    duration of a synchronous read/write loop. `write_lock` is an asyncio.Lock, held by
    this coroutine across both `await`s, so the lock's hold duration is unaffected by
    running the actual I/O off-thread.

    `apply=False` (the verification CLI's dry-run mode, §8) writes the full migrated
    output to `output_filename` without ever touching the real `source` file — safe to
    run against a live session directory without risk, though the CLI normally runs it
    against a timestamped copy instead.
    """
    source = session_dir / "messages.jsonl"
    tmp = session_dir / output_filename
    reconstructor = ToolLifecycleReconstructor(session_id)

    async with write_lock:
        await asyncio.to_thread(_prescan_pass, source, reconstructor)
        line_count, materialized_tool_calls = await asyncio.to_thread(
            _conversion_pass, source, tmp, session_id, session_state, convert_fn, reconstructor
        )

        if apply:
            os.replace(tmp, source)
            output_path = source
        else:
            output_path = tmp

    return MigrationResult(
        session_id=session_id,
        line_count=line_count,
        materialized_tool_calls=materialized_tool_calls,
        output_path=output_path,
        applied=apply,
    )
