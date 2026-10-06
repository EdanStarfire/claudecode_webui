"""Unit tests for ToolLifecycleReconstructor (issue #2084 stage 3-C, §1/§9).

Each of issue #491's 6 trigger conditions is exercised directly against the
extracted state machine, independent of get_session_messages()/migration.
"""

from backend.models.messages import ToolState
from backend.session_manager import SessionState
from backend.tool_lifecycle_reconstruction import (
    ToolLifecycleReconstructor,
    parse_send_comm_sender_attachments,
    prescan_stored_tool_calls,
)

SESSION_ID = "sess-1"


def _assistant_with_tool_uses(tool_use_id="tu-1", name="Edit", timestamp=100.0, parent=None):
    return {
        "type": "assistant",
        "timestamp": timestamp,
        "metadata": {
            "has_tool_uses": True,
            "tool_uses": [{"id": tool_use_id, "name": name, "input": {"path": "a.py"}}],
            "parent_tool_use_id": parent,
        },
    }


def _permission_request(tool_name="Edit", request_id="req-1", suggestions=None):
    return {
        "type": "permission_request",
        "content": "Allow Edit?",
        "metadata": {
            "tool_name": tool_name,
            "request_id": request_id,
            "suggestions": suggestions or [],
        },
    }


def _permission_response(decision="allow", tool_name="Edit", request_id="req-1"):
    return {
        "type": "permission_response",
        "metadata": {
            "decision": decision,
            "tool_name": tool_name,
            "request_id": request_id,
        },
    }


def _tool_result(tool_use_id="tu-1", is_error=False, content="ok"):
    return {
        "type": "user",
        "metadata": {
            "has_tool_results": True,
            "tool_results": [{"tool_use_id": tool_use_id, "is_error": is_error, "content": content}],
        },
    }


def _system(subtype):
    return {"type": "system", "metadata": {"subtype": subtype}}


class TestTrigger1PendingCreation:
    def test_creates_pending_tool_call(self):
        r = ToolLifecycleReconstructor(SESSION_ID)
        out = r.feed(_assistant_with_tool_uses())
        assert len(out) == 1
        assert out[0]["type"] == "tool_call"
        assert out[0]["tool_use_id"] == "tu-1"
        assert out[0]["status"] == ToolState.PENDING.value
        assert "tu-1" in r.active_history_tools

    def test_multiple_tool_uses_emit_multiple_records(self):
        r = ToolLifecycleReconstructor(SESSION_ID)
        record = {
            "type": "assistant",
            "timestamp": 1.0,
            "metadata": {
                "has_tool_uses": True,
                "tool_uses": [
                    {"id": "tu-a", "name": "Read", "input": {}},
                    {"id": "tu-b", "name": "Write", "input": {}},
                ],
            },
        }
        out = r.feed(record)
        assert {tc["tool_use_id"] for tc in out} == {"tu-a", "tu-b"}

    def test_suppressed_for_observed_tool_use_id(self):
        r = ToolLifecycleReconstructor(SESSION_ID)
        r.observe_stored_tool_call("tu-1")
        out = r.feed(_assistant_with_tool_uses())
        assert out == []
        assert "tu-1" not in r.active_history_tools

    def test_propagates_parent_tool_use_id(self):
        r = ToolLifecycleReconstructor(SESSION_ID)
        out = r.feed(_assistant_with_tool_uses(parent="parent-1"))
        assert out[0]["parent_tool_use_id"] == "parent-1"


class TestTrigger2PermissionRequest:
    def test_updates_matching_pending_tool(self):
        r = ToolLifecycleReconstructor(SESSION_ID)
        r.feed(_assistant_with_tool_uses())
        out = r.feed(_permission_request())
        assert len(out) == 1
        assert out[0]["status"] == ToolState.AWAITING_PERMISSION.value
        assert out[0]["request_id"] == "req-1"
        assert r.active_history_tools["tu-1"].status == ToolState.AWAITING_PERMISSION

    def test_no_match_emits_nothing(self):
        r = ToolLifecycleReconstructor(SESSION_ID)
        out = r.feed(_permission_request(tool_name="Nonexistent"))
        assert out == []


class TestTrigger3PermissionResponse:
    def test_allow_moves_to_running(self):
        r = ToolLifecycleReconstructor(SESSION_ID)
        r.feed(_assistant_with_tool_uses())
        r.feed(_permission_request())
        out = r.feed(_permission_response(decision="allow"))
        assert out[0]["status"] == ToolState.RUNNING.value
        assert r.active_history_tools["tu-1"].status == ToolState.RUNNING

    def test_deny_moves_to_denied_and_drops_tracking(self):
        r = ToolLifecycleReconstructor(SESSION_ID)
        r.feed(_assistant_with_tool_uses())
        r.feed(_permission_request())
        out = r.feed(_permission_response(decision="deny"))
        assert out[0]["status"] == ToolState.DENIED.value
        assert "tu-1" not in r.active_history_tools


class TestTrigger4ToolResults:
    def test_success_marks_completed(self):
        r = ToolLifecycleReconstructor(SESSION_ID)
        r.feed(_assistant_with_tool_uses())
        out = r.feed(_tool_result(content="done"))
        assert out[0]["status"] == ToolState.COMPLETED.value
        assert "tu-1" not in r.active_history_tools

    def test_error_marks_failed(self):
        r = ToolLifecycleReconstructor(SESSION_ID)
        r.feed(_assistant_with_tool_uses())
        out = r.feed(_tool_result(is_error=True, content="boom"))
        assert out[0]["status"] == ToolState.FAILED.value
        assert out[0]["error"] == "boom"

    def test_send_comm_sender_attachments_parsed(self):
        r = ToolLifecycleReconstructor(SESSION_ID)
        r.feed(_assistant_with_tool_uses(tool_use_id="tu-sc", name="mcp__legion__send_comm"))
        content = (
            "sent\n<!-- sender_attachments: "
            '[{"name": "f.png", "resource_id": "r1", "size": 10, "mime_type": "image/png"}] -->'
        )
        out = r.feed(_tool_result(tool_use_id="tu-sc", content=content))
        assert out[0]["sender_attachments"] == [
            {"name": "f.png", "resource_id": "r1", "size": 10, "mime_type": "image/png"}
        ]

    def test_no_match_emits_nothing(self):
        r = ToolLifecycleReconstructor(SESSION_ID)
        out = r.feed(_tool_result(tool_use_id="unknown"))
        assert out == []


class TestTrigger5SystemInterrupt:
    def test_client_launched_interrupts_open_tools(self):
        r = ToolLifecycleReconstructor(SESSION_ID)
        r.feed(_assistant_with_tool_uses())
        out = r.feed(_system("client_launched"))
        assert out[0]["status"] == ToolState.INTERRUPTED.value
        assert r.active_history_tools == {}

    def test_interrupt_subtype_interrupts_open_tools(self):
        r = ToolLifecycleReconstructor(SESSION_ID)
        r.feed(_assistant_with_tool_uses())
        out = r.feed(_system("interrupt"))
        assert len(out) == 1
        assert out[0]["status"] == ToolState.INTERRUPTED.value

    def test_unrelated_subtype_is_noop(self):
        r = ToolLifecycleReconstructor(SESSION_ID)
        r.feed(_assistant_with_tool_uses())
        out = r.feed(_system("some_other_subtype"))
        assert out == []
        assert "tu-1" in r.active_history_tools


class TestTrigger6FinalizeSweep:
    def test_sweeps_remaining_open_tools_when_terminated(self):
        r = ToolLifecycleReconstructor(SESSION_ID)
        r.feed(_assistant_with_tool_uses())
        out = r.finalize(SessionState.TERMINATED)
        assert len(out) == 1
        assert out[0]["status"] == ToolState.INTERRUPTED.value
        assert r.active_history_tools == {}

    def test_no_sweep_while_active(self):
        r = ToolLifecycleReconstructor(SESSION_ID)
        r.feed(_assistant_with_tool_uses())
        out = r.finalize(SessionState.ACTIVE)
        assert out == []
        assert "tu-1" in r.active_history_tools

    def test_no_sweep_while_paused_or_starting(self):
        r = ToolLifecycleReconstructor(SESSION_ID)
        r.feed(_assistant_with_tool_uses())
        assert r.finalize(SessionState.PAUSED) == []
        assert r.finalize(SessionState.STARTING) == []


class TestExplicitToolCallRecordBypassesSynthesis:
    def test_explicit_tool_call_updates_tracking_without_synthesis(self):
        r = ToolLifecycleReconstructor(SESSION_ID)
        explicit = {
            "type": "tool_call",
            "tool_use_id": "tu-explicit",
            "session_id": SESSION_ID,
            "name": "Bash",
            "input": {},
            "status": "running",
            "created_at": 1.0,
            "requires_permission": False,
        }
        out = r.feed(explicit)
        assert out == []
        assert "tu-explicit" in r.active_history_tools
        assert "tu-explicit" in r.stored_tool_update_ids

    def test_terminal_explicit_tool_call_is_not_tracked_as_open(self):
        r = ToolLifecycleReconstructor(SESSION_ID)
        explicit = {
            "type": "tool_call",
            "tool_use_id": "tu-done",
            "session_id": SESSION_ID,
            "name": "Bash",
            "input": {},
            "status": "completed",
            "created_at": 1.0,
            "requires_permission": False,
        }
        r.feed(explicit)
        assert "tu-done" not in r.active_history_tools
        # Finalize must not re-interrupt an already-terminal explicit record.
        assert r.finalize(SessionState.TERMINATED) == []


class TestExplicitRecordsSuppressAllSynthesisTriggers:
    """Issue #2084 (stage 3-C): a tool_use_id already covered by an explicit
    stored/canonical tool_call record must never be re-synthesized by triggers
    2-4 either — only trigger 1 originally checked stored_tool_update_ids.

    Found via 3-C's own fidelity testing: a canonical session that stores a
    full multi-transition lifecycle (pending/awaiting_permission/running/
    completed ToolCallUpdate-equivalent records, exactly what 3-B's live write
    path always does) also still contains the regular triggering messages
    (AssistantMessage, PermissionRequestMessage, UserMessage) with their own
    has_tool_uses/has_permission_requests/has_tool_results metadata. Without
    this exclusion, re-reading such a session would re-derive and emit a
    redundant tool_call entry alongside the already-explicit one for every
    transition — latent since 3-B shipped (no existing test exercised a
    canonical multi-transition round trip), surfaced by 3-C's migration
    fidelity tests, which require a byte-exact zero-divergence comparison.
    """

    def _explicit_tool_call(self, tool_use_id, status, **extra):
        return {
            "type": "tool_call",
            "tool_use_id": tool_use_id,
            "session_id": SESSION_ID,
            "name": "Edit",
            "input": {},
            "status": status,
            "created_at": 1.0,
            "requires_permission": False,
            **extra,
        }

    def test_tool_results_not_resynthesized_when_explicit_record_exists(self):
        r = ToolLifecycleReconstructor(SESSION_ID)
        r.feed(self._explicit_tool_call("tu-1", "pending"))
        out = r.feed(_tool_result(tool_use_id="tu-1", content="done"))
        assert out == []
        # The explicit pending record's tracking is untouched by the no-op trigger.
        assert r.active_history_tools["tu-1"].status == ToolState.PENDING

    def test_permission_request_not_resynthesized_when_explicit_record_exists(self):
        r = ToolLifecycleReconstructor(SESSION_ID)
        r.feed(self._explicit_tool_call("tu-1", "pending"))
        out = r.feed(_permission_request())
        assert out == []

    def test_permission_response_not_resynthesized_when_explicit_record_exists(self):
        r = ToolLifecycleReconstructor(SESSION_ID)
        r.feed(self._explicit_tool_call("tu-1", "awaiting_permission"))
        out = r.feed(_permission_response(decision="allow"))
        assert out == []

    def test_finalize_still_sweeps_explicit_nonterminal_records(self):
        """Trigger 6 is deliberately NOT excluded (issue #2052): a tool whose
        last *explicit* stored record is non-terminal must still be swept."""
        r = ToolLifecycleReconstructor(SESSION_ID)
        r.feed(self._explicit_tool_call("tu-1", "running"))
        out = r.finalize(SessionState.TERMINATED)
        assert len(out) == 1
        assert out[0]["status"] == ToolState.INTERRUPTED.value


class TestTerminalRecordGuardAgainstOutOfOrderDuplicates:
    """Issue #2093: two genuine stored explicit records for the same tool_use_id
    (one terminal, one non-terminal) landing out of chronological order in the
    file must not resurrect tracking for an already-concluded tool — otherwise
    finalize()'s end-of-stream sweep synthesizes a spurious duplicate record."""

    def _explicit_tool_call(self, tool_use_id, status, **extra):
        return {
            "type": "tool_call",
            "tool_use_id": tool_use_id,
            "session_id": SESSION_ID,
            "name": "Bash",
            "input": {},
            "status": status,
            "created_at": 1.0,
            "requires_permission": False,
            **extra,
        }

    def test_terminal_then_out_of_order_nonterminal_is_not_resurrected(self):
        r = ToolLifecycleReconstructor(SESSION_ID)
        r.feed(self._explicit_tool_call("tu-1", "interrupted"))
        out = r.feed(self._explicit_tool_call("tu-1", "running"))

        assert out == []
        assert "tu-1" not in r.active_history_tools

        # The finalize sweep must not synthesize a spurious duplicate.
        assert r.finalize(SessionState.TERMINATED) == []

    def test_normal_chronological_order_still_tracks_as_open(self):
        """Sanity check: a genuinely non-terminal record with no prior terminal
        record for the same id must still be tracked and swept normally."""
        r = ToolLifecycleReconstructor(SESSION_ID)
        r.feed(self._explicit_tool_call("tu-1", "running"))
        assert "tu-1" in r.active_history_tools

        out = r.finalize(SessionState.TERMINATED)
        assert len(out) == 1
        assert out[0]["status"] == ToolState.INTERRUPTED.value


class TestPrescanTerminalGuardForTrigger5AndFinalize:
    """Issue #2093 (reopened): a sibling gap to the one
    TestTerminalRecordGuardAgainstOutOfOrderDuplicates above fixed. That fix
    only covered the explicit-record branch (feed()'s own reactive population
    of terminal_tool_use_ids). Trigger 5 (client_launched/interrupt) and
    finalize() never consulted terminal_tool_use_ids at all — so either could
    synthesize a bogus duplicate "interrupted" record for a tool still sitting
    in active_history_tools when a genuine terminal explicit record for that
    same tool appears LATER in the stream than they do. prescan_stored_tool_calls()
    now populates terminal_tool_use_ids proactively from each raw record's own
    status field, regardless of file position, so the explicit branch's existing
    `elif tc_id not in self.terminal_tool_use_ids` check (see above) already
    excludes such a tool from active_history_tools before trigger 5/finalize
    ever run — and both also gained the same guard triggers 1-4 already have,
    as defense in depth.
    """

    def _explicit_tool_call(self, tool_use_id, status, **extra):
        return {
            "type": "tool_call",
            "tool_use_id": tool_use_id,
            "session_id": SESSION_ID,
            "name": "Bash",
            "input": {},
            "status": status,
            "created_at": 1.0,
            "requires_permission": False,
            **extra,
        }

    def test_trigger5_scenario_produces_zero_synthesized_records(self):
        """The exact 3-record scenario verified empirically in the plan: a
        pending explicit record, a client_launched system message, and the
        genuine interrupted explicit record later in the stream."""
        raw_records = [
            self._explicit_tool_call("tu-1", "pending"),
            _system("client_launched"),
            self._explicit_tool_call("tu-1", "interrupted"),
        ]
        r = ToolLifecycleReconstructor(SESSION_ID)
        prescan_stored_tool_calls(raw_records, r)

        synthesized = []
        for rec in raw_records:
            synthesized.extend(r.feed(rec))

        assert synthesized == []
        assert "tu-1" not in r.active_history_tools

    def test_finalize_scenario_produces_zero_synthesized_records(self):
        """Symmetric scenario: same pending-then-interrupted records, but no
        client_launched/interrupt message at all — finalize()'s end-of-stream
        sweep is the only thing that could fire for a still-open tool."""
        raw_records = [
            self._explicit_tool_call("tu-1", "pending"),
            self._explicit_tool_call("tu-1", "interrupted"),
        ]
        r = ToolLifecycleReconstructor(SESSION_ID)
        prescan_stored_tool_calls(raw_records, r)

        synthesized = []
        for rec in raw_records:
            synthesized.extend(r.feed(rec))
        synthesized.extend(r.finalize(SessionState.TERMINATED))

        assert synthesized == []

    def test_trigger5_guard_fires_directly_for_a_known_terminal_tracked_tool(self):
        """Exercises trigger 5's new guard line directly, independent of
        prescan/explicit-record plumbing: a tool_use_id sitting in
        active_history_tools (synthesized via trigger 1) that is already
        known-terminal must not be popped/re-interrupted a second time."""
        r = ToolLifecycleReconstructor(SESSION_ID)
        r.feed(_assistant_with_tool_uses(tool_use_id="tu-1"))
        assert "tu-1" in r.active_history_tools
        r.terminal_tool_use_ids.add("tu-1")

        out = r.feed(_system("client_launched"))

        assert out == []
        assert "tu-1" in r.active_history_tools

    def test_finalize_guard_fires_directly_for_a_known_terminal_tracked_tool(self):
        """Exercises finalize()'s new guard line directly, same setup as above."""
        r = ToolLifecycleReconstructor(SESSION_ID)
        r.feed(_assistant_with_tool_uses(tool_use_id="tu-1"))
        r.terminal_tool_use_ids.add("tu-1")

        out = r.finalize(SessionState.TERMINATED)

        assert out == []
        assert "tu-1" in r.active_history_tools


class TestIdempotency:
    def test_two_separate_feed_sequences_on_same_records_produce_identical_output(self):
        records = [
            _assistant_with_tool_uses(),
            _permission_request(),
            _permission_response(decision="allow"),
            _tool_result(content="done"),
        ]

        def run():
            r = ToolLifecycleReconstructor(SESSION_ID)
            out = []
            for rec in records:
                out.extend(r.feed(rec))
            return out

        assert run() == run()


class TestParseSendCommSenderAttachments:
    def test_returns_none_for_non_string(self):
        assert parse_send_comm_sender_attachments(None) is None
        assert parse_send_comm_sender_attachments(123) is None

    def test_returns_none_when_no_footer(self):
        assert parse_send_comm_sender_attachments("plain text result") is None

    def test_parses_valid_footer(self):
        content = 'result\n<!-- sender_attachments: [{"name": "x"}] -->'
        assert parse_send_comm_sender_attachments(content) == [{"name": "x"}]

    def test_returns_none_for_malformed_json(self):
        content = "result\n<!-- sender_attachments: not-json -->"
        assert parse_send_comm_sender_attachments(content) is None

    def test_returns_none_for_empty_list(self):
        content = "result\n<!-- sender_attachments: [] -->"
        assert parse_send_comm_sender_attachments(content) is None
