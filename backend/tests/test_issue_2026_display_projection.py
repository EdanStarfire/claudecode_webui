"""Direct unit tests for DisplayProjection's delta-only DisplayMetadata (issue #2026,
Part B1). Each `_process_*` method used to return the full cumulative tool_states/
orphaned_tools/linked_permissions snapshot on every call; it now returns only the
entries that specific call touched.
"""

from backend.models.messages import (
    DisplayProjection,
    PermissionRequestMessage,
    PermissionResponseMessage,
    StoredMessage,
)


def _assistant_message(tool_id: str, tool_name: str = "Read") -> StoredMessage:
    return StoredMessage(
        _type="AssistantMessage",
        timestamp=1.0,
        session_id="sess-1",
        data={"content": [{"id": tool_id, "name": tool_name, "input": {}}]},
    )


def _user_message(tool_id: str, is_error: bool = False) -> StoredMessage:
    return StoredMessage(
        _type="UserMessage",
        timestamp=2.0,
        session_id="sess-1",
        data={"content": [{"tool_use_id": tool_id, "content": "result", "is_error": is_error}]},
    )


class TestDisplayProjectionDeltaOnly:
    def test_assistant_message_delta_contains_only_its_own_tool(self):
        projection = DisplayProjection()
        display_a = projection.process_message(_assistant_message("tool_a"))
        assert set(display_a.tool_states.keys()) == {"tool_a"}

        display_b = projection.process_message(_assistant_message("tool_b"))
        # tool_a must NOT reappear in tool_b's delta — that would be the old
        # full-snapshot behavior this issue removes.
        assert set(display_b.tool_states.keys()) == {"tool_b"}

    def test_user_message_delta_contains_only_the_completed_tool(self):
        projection = DisplayProjection()
        projection.process_message(_assistant_message("tool_a"))
        projection.process_message(_assistant_message("tool_b"))

        display = projection.process_message(_user_message("tool_a"))
        assert set(display.tool_states.keys()) == {"tool_a"}
        assert display.tool_states["tool_a"].state.value == "completed"

    def test_user_message_error_result_delta(self):
        projection = DisplayProjection()
        projection.process_message(_assistant_message("tool_a"))

        display = projection.process_message(_user_message("tool_a", is_error=True))
        assert set(display.tool_states.keys()) == {"tool_a"}
        assert display.tool_states["tool_a"].state.value == "failed"

    def test_user_message_with_no_matching_tool_produces_empty_delta(self):
        projection = DisplayProjection()
        display = projection.process_message(_user_message("never_seen"))
        assert display.tool_states == {}
        assert display.orphaned_tools == []
        assert display.linked_permissions == {}

    def test_unhandled_message_type_produces_empty_delta_not_full_snapshot(self):
        """The default branch (SystemMessage/ResultMessage/etc.) must return an
        EMPTY delta, not the accumulated tool_states snapshot from prior
        messages — this is the exact case that used to leak the full,
        unbounded, ever-growing snapshot onto every unrelated message."""
        projection = DisplayProjection()
        projection.process_message(_assistant_message("tool_a"))
        projection.process_message(_assistant_message("tool_b"))

        unrelated = StoredMessage(
            _type="ResultMessage", timestamp=3.0, session_id="sess-1", data={"subtype": "success"}
        )
        display = projection.process_message(unrelated)
        assert display.tool_states == {}
        assert display.orphaned_tools == []
        assert display.linked_permissions == {}

    def test_permission_request_delta_contains_only_linked_tool_and_permission(self):
        projection = DisplayProjection()
        projection.process_message(_assistant_message("tool_a", tool_name="Bash"))
        projection.process_message(_assistant_message("tool_b", tool_name="Bash"))

        request = StoredMessage(
            _type="PermissionRequestMessage",
            timestamp=3.0,
            session_id="sess-1",
            data=PermissionRequestMessage(
                request_id="req_1", tool_name="Bash", input_params={}, tool_use_id="tool_a"
            ).to_dict(),
        )
        display = projection.process_message(request)
        assert set(display.tool_states.keys()) == {"tool_a"}
        assert display.tool_states["tool_a"].state.value == "permission_required"
        assert display.linked_permissions == {"req_1": "tool_a"}

    def test_permission_response_delta_contains_only_its_own_tool(self):
        projection = DisplayProjection()
        projection.process_message(_assistant_message("tool_a", tool_name="Bash"))
        request = StoredMessage(
            _type="PermissionRequestMessage",
            timestamp=3.0,
            session_id="sess-1",
            data=PermissionRequestMessage(
                request_id="req_1", tool_name="Bash", input_params={}, tool_use_id="tool_a"
            ).to_dict(),
        )
        projection.process_message(request)

        response = StoredMessage(
            _type="PermissionResponseMessage",
            timestamp=4.0,
            session_id="sess-1",
            data=PermissionResponseMessage(
                request_id="req_1", decision="allow", tool_name="Bash"
            ).to_dict(),
        )
        display = projection.process_message(response)
        assert set(display.tool_states.keys()) == {"tool_a"}
        assert display.tool_states["tool_a"].state.value == "executing"
        # No new linked_permissions in a response delta — that link was already
        # sent with the request.
        assert display.linked_permissions == {}

    def test_a_stream_of_deltas_reconstructs_full_cumulative_state(self):
        """The frontend's cumulative cache (applyDisplayMetadata) merges each
        delta in; verify the underlying internal projection state after a
        sequence of deltas matches what a full-snapshot design would have
        reported at the end — i.e. deltas lose no information, they just spread
        it across messages instead of repeating it on every one."""
        projection = DisplayProjection()
        projection.process_message(_assistant_message("tool_a"))
        projection.process_message(_assistant_message("tool_b"))
        projection.process_message(_user_message("tool_a"))

        # _tool_states is the internal cumulative source of truth every delta is
        # drawn from — confirm both tools' final states landed there correctly
        # even though no single delta ever mentioned both at once.
        assert projection._tool_states["tool_a"].state.value == "completed"
        assert projection._tool_states["tool_b"].state.value == "pending"


class TestDisplayProjectionOrphanedDelta:
    """mark_tools_orphaned() mutates projection state directly, bypassing
    process_message() — under the delta-only design this would otherwise never
    reach any future DisplayMetadata this projection produces. build_orphaned_delta()
    is the seam callers use to surface that change on whatever message they emit
    next (see SessionCoordinator._mark_tools_orphaned())."""

    def test_build_orphaned_delta_contains_only_the_orphaned_tools(self):
        projection = DisplayProjection()
        projection.process_message(_assistant_message("tool_a"))
        projection.process_message(_assistant_message("tool_b"))

        orphaned_ids = projection.mark_tools_orphaned()
        assert set(orphaned_ids) == {"tool_a", "tool_b"}

        delta = projection.build_orphaned_delta(orphaned_ids)
        assert set(delta.tool_states.keys()) == {"tool_a", "tool_b"}
        assert delta.tool_states["tool_a"].state.value == "orphaned"
        assert delta.tool_states["tool_b"].state.value == "orphaned"
        assert set(delta.orphaned_tools) == {"tool_a", "tool_b"}

    def test_mark_tools_orphaned_after_a_completed_tool_only_orphans_active_ones(self):
        projection = DisplayProjection()
        projection.process_message(_assistant_message("tool_a"))
        projection.process_message(_assistant_message("tool_b"))
        projection.process_message(_user_message("tool_a"))  # tool_a completes

        orphaned_ids = projection.mark_tools_orphaned()
        # Only tool_b was still active (tool_a already completed, no longer tracked
        # in _active_tools) — completed tools must not be reported as orphaned.
        assert orphaned_ids == ["tool_b"]

        delta = projection.build_orphaned_delta(orphaned_ids)
        assert set(delta.tool_states.keys()) == {"tool_b"}
        assert delta.tool_states["tool_b"].state.value == "orphaned"
