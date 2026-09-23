"""Fixture export for issue #1998 (stage 1a of epic #1990).

Bundles a recording session's raw_log.jsonl + state.json + messages.jsonl +
a live call to the REST history endpoint's own coordinator method +
provenance into backend/tests/fixtures/raw/{name}/, after checking the raw
log's coverage against the 9 named scenario markers (see
backend/tests/fixtures/SCENARIO_CHECKLIST.md). Consumed by both the REST
export endpoint (backend/routers/sessions.py) and the CLI wrapper
(backend/tools/export_fixture_cli.py) — the CLI translates
FixtureExportError into a non-zero exit; the REST handler catches it and
returns a 200 with {success: false, missing_markers: [...]} so the UI can
render the failure panel inline instead of treating it as a request error.
"""

import asyncio
import importlib.metadata
import json
import shutil
import subprocess
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from shared.git_restart import run_git_command

if TYPE_CHECKING:
    from backend.session_coordinator import SessionCoordinator

_REPO_ROOT = Path(__file__).parent.parent
_DEFAULT_FIXTURES_ROOT = _REPO_ROOT / "backend" / "tests" / "fixtures" / "raw"

# The 9 scenario markers from the issue's coverage-inventory check (AC4), in the
# order the export UI's checklist (see mockup Tab 3) displays them.
REQUIRED_MARKERS: tuple[str, ...] = (
    "streaming deltas",
    "tool call with permission prompt",
    "denied permission",
    "AskUserQuestion",
    "subagent task with progress",
    "interrupt mid-tool",
    "session restart",
    "compaction",
    "inter-minion comm",
)

_TASK_MESSAGE_TYPES = {"TaskStartedMessage", "TaskProgressMessage", "TaskNotificationMessage"}


class FixtureExportError(Exception):
    """Raised when the raw log is missing one or more required scenario markers.

    AC4: named missing markers, non-zero CLI exit. The REST handler catches this
    and returns HTTP 200 with {success: false, missing_markers: [...]} instead of
    letting it become a 500 — a coverage gap is an expected, actionable outcome of
    export, not a server error.
    """

    def __init__(self, missing_markers: list[str]):
        self.missing_markers = missing_markers
        super().__init__(
            f"{len(missing_markers)} of {len(REQUIRED_MARKERS)} required scenario "
            f"markers are missing from the raw log: {', '.join(missing_markers)}"
        )


@dataclass
class FixtureExportResult:
    success: bool = True
    fixture_dir: str = ""
    markers: dict[str, bool] = field(default_factory=dict)
    provenance: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "success": self.success,
            "fixture_dir": self.fixture_dir,
            "markers": self.markers,
            "provenance": self.provenance,
        }


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    records = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return records


def _check_markers(raw_records: list[dict[str, Any]]) -> dict[str, bool]:
    """Coverage-inventory pass over the raw log for the 9 named markers.

    Inherently heuristic (e.g. correlating a permission record with a subsequent
    tool_use to recognize "tool call with permission prompt") — kept isolated here,
    away from capture code, specifically so it's unit-testable and can iterate
    independently (see plan's Risks & Considerations).
    """
    found = dict.fromkeys(REQUIRED_MARKERS, False)

    for record in raw_records:
        kind = record.get("kind")

        if kind == "sdk_message":
            _type = record.get("_type")
            if _type == "StreamEvent":
                found["streaming deltas"] = True
            elif _type in _TASK_MESSAGE_TYPES:
                found["subagent task with progress"] = True
            elif _type == "SystemMessage":
                # Exact match only: 'microcompact_boundary' is a distinct, smaller-scope
                # event the codebase explicitly treats as NOT a real compaction (see
                # SessionCoordinator._handle_compact_boundary's docstring) — a substring
                # match here would false-positive on it and let a fixture through that
                # never actually exercised full-compaction SDK output shapes.
                subtype = (record.get("data") or {}).get("subtype")
                if subtype == "compact_boundary":
                    found["compaction"] = True
            elif _type == "AssistantMessage":
                content = (record.get("data") or {}).get("content") or []
                for block in content:
                    if isinstance(block, dict) and block.get("name") == "AskUserQuestion":
                        found["AskUserQuestion"] = True

        elif kind == "permission_invocation":
            found["tool call with permission prompt"] = True
            if record.get("tool_name") == "AskUserQuestion":
                found["AskUserQuestion"] = True

        elif kind == "permission_response":
            if record.get("decision") == "deny":
                found["denied permission"] = True
            if record.get("tool_name") == "AskUserQuestion":
                found["AskUserQuestion"] = True

        elif kind == "interrupt":
            found["interrupt mid-tool"] = True

        elif kind == "lifecycle":
            if record.get("action") == "restart":
                found["session restart"] = True

        elif kind == "queue_event":
            # An inter-minion comm delivered to this session arrives as an ordinary
            # outbound user message (CommRouter._send_to_minion -> SessionCoordinator.
            # send_message()) — it never reaches _process_sdk_message's inbound SDK-message
            # hook, and its queue-event type is "user", not "comm". The captured event is
            # the poll-queue envelope BackendApp._create_message_callback() builds
            # (web_server.py: {"type": "message", "session_id": ..., "data": websocket_data,
            # "timestamp": ...}) — the actual per-message type/metadata live one level down,
            # at event.data.type / event.data.metadata, not directly on event. Confirmed
            # against a real captured raw_log (2026-09-23): event.metadata is never populated
            # at the top level; event.data.metadata.comm is where a real comm delivery shows
            # up (comm_router.py's `comm_metadata = {"comm": {...}}`, carried through by
            # MessageProcessor.prepare_for_websocket()).
            event_data = (record.get("event") or {}).get("data") or {}
            event_metadata = event_data.get("metadata") or {}
            if "comm" in event_metadata:
                found["inter-minion comm"] = True

    return found


def _resolve_cli_version(cli_path: str | None) -> str | None:
    """Best-effort `<cli> --version` capture. Opaque string, no semantic parsing —
    no existing precedent in this codebase for CLI version detection.

    Synchronous/blocking by design (subprocess.run) — always run this via
    asyncio.to_thread from an async caller. A single-threaded Backend process also
    runs every other active session's SDK loop and poll responses on the same event
    loop, so calling this directly from async code would stall all of them for up
    to the full timeout if the CLI is slow to start or hung.
    """
    resolved = cli_path or shutil.which("claude")
    if not resolved:
        return None
    try:
        result = subprocess.run(
            [resolved, "--version"], capture_output=True, text=True, timeout=10, check=False
        )
        return (result.stdout or result.stderr or "").strip() or None
    except Exception:
        return None


async def _build_provenance(session_config: dict[str, Any]) -> dict[str, Any]:
    try:
        sdk_version = importlib.metadata.version("claude-agent-sdk")
    except importlib.metadata.PackageNotFoundError:
        sdk_version = None

    # Independent lookups — run concurrently, and keep the blocking CLI-version
    # subprocess off the event loop (see _resolve_cli_version's docstring).
    git_sha, cli_version = await asyncio.gather(
        run_git_command(["git", "rev-parse", "HEAD"], str(_REPO_ROOT)),
        asyncio.to_thread(_resolve_cli_version, session_config.get("cli_path")),
    )

    return {
        "sdk_version": sdk_version,
        "cli_version": cli_version,
        "model": session_config.get("model"),
        "git_sha": git_sha,
        "capture_date": datetime.now(UTC).isoformat(),
    }


async def export_fixture(
    coordinator: "SessionCoordinator",
    session_id: str,
    name: str,
    fixtures_root: Path | None = None,
) -> FixtureExportResult:
    """Bundle a recording session into a named fixture directory.

    Raises FixtureExportError if the raw log is missing required markers —
    checked BEFORE anything is written, so a failed export never leaves a
    partial fixture directory behind. Raises ValueError if session_id doesn't
    exist — the REST route checks existence first, but the CLI wrapper calls
    this directly with a user-supplied session_id and has no such pre-check.
    """
    session_dir = await coordinator.session_manager.get_session_directory(session_id)
    if session_dir is None:
        raise ValueError(f"Session not found: {session_id}")
    raw_log_path = session_dir / "raw_log.jsonl"
    raw_records = _read_jsonl(raw_log_path)

    markers = _check_markers(raw_records)
    missing = [m for m in REQUIRED_MARKERS if not markers[m]]
    if missing:
        raise FixtureExportError(missing)

    session_info = await coordinator.session_manager.get_session_info(session_id)
    session_config = session_info.config if session_info else {}
    provenance = await _build_provenance(session_config)

    fixtures_root = fixtures_root or _DEFAULT_FIXTURES_ROOT
    fixture_dir = fixtures_root / name
    fixture_dir.mkdir(parents=True, exist_ok=True)

    if raw_log_path.exists():
        shutil.copy2(raw_log_path, fixture_dir / "raw_log.jsonl")
    state_path = session_dir / "state.json"
    if state_path.exists():
        shutil.copy2(state_path, fixture_dir / "state.json")
    messages_path = session_dir / "messages.jsonl"
    if messages_path.exists():
        shutil.copy2(messages_path, fixture_dir / "messages.jsonl")

    # Issue #1998: deliberately not a raw re-serve of messages.jsonl — the REST response
    # is a materially different synthesized shape (reconstructed tool_call records) that
    # the frontend actually consumes on reload, so stage 1b's equivalence tests need it
    # captured separately from the raw storage-layer messages.jsonl above.
    rest_history = await coordinator.get_session_messages(session_id, limit=None)
    (fixture_dir / "rest_history.json").write_text(
        json.dumps(rest_history, indent=2, default=str), encoding="utf-8"
    )

    (fixture_dir / "provenance.json").write_text(
        json.dumps(provenance, indent=2), encoding="utf-8"
    )

    return FixtureExportResult(
        success=True,
        fixture_dir=str(fixture_dir),
        markers=markers,
        provenance=provenance,
    )
