"""
Tests for issue #1302: Enriched permission context fields (SDK 0.1.74+).

Verifies that decision_reason, blocked_path, title, display_name, and description
are correctly extracted from ToolPermissionContext and propagated through
PermissionRequestMessage, PermissionInfo, and the permission service.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.models.messages import PermissionInfo, PermissionRequestMessage

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_context(
    tool_use_id: str | None = None,
    agent_id: str | None = None,
    decision_reason: object = None,
    blocked_path: str | None = None,
    title: str | None = None,
    display_name: str | None = None,
    description: str | None = None,
) -> MagicMock:
    ctx = MagicMock()
    ctx.tool_use_id = tool_use_id
    ctx.agent_id = agent_id
    ctx.suggestions = []
    ctx.decision_reason = decision_reason
    ctx.blocked_path = blocked_path
    ctx.title = title
    ctx.display_name = display_name
    ctx.description = description
    return ctx


def _make_coordinator(session_id: str) -> MagicMock:
    coord = MagicMock()
    event = asyncio.Event()

    coord.get_tool_call_by_id.side_effect = lambda sid, tuid: None
    coord.find_tool_call_by_signature.side_effect = lambda sid, name, params: None
    coord.get_tool_call_event.side_effect = lambda sid: event
    coord.is_uploaded_file.side_effect = lambda sid, path: False
    coord.session_manager = MagicMock()
    coord.session_manager.pause_session = AsyncMock()
    coord.session_manager.get_session_info = AsyncMock(return_value=None)
    return coord


# ---------------------------------------------------------------------------
# PermissionRequestMessage – dataclass round-trip
# ---------------------------------------------------------------------------

class TestPermissionRequestMessageEnrichedFields:
    def test_defaults_are_none(self):
        msg = PermissionRequestMessage(request_id="r1", tool_name="Edit", session_id="s1")
        assert msg.decision_reason is None
        assert msg.blocked_path is None
        assert msg.title is None
        assert msg.display_name is None
        assert msg.description is None

    def test_fields_stored_correctly(self):
        msg = PermissionRequestMessage(
            request_id="r2",
            tool_name="Write",
            session_id="s2",
            decision_reason="policy-match",
            blocked_path="/etc/passwd",
            title="Write sensitive file",
            display_name="Write File",
            description="Claude wants to write to /etc/passwd",
        )
        assert msg.decision_reason == "policy-match"
        assert msg.blocked_path == "/etc/passwd"
        assert msg.title == "Write sensitive file"
        assert msg.display_name == "Write File"
        assert msg.description == "Claude wants to write to /etc/passwd"

    def test_to_dict_includes_non_none_fields(self):
        msg = PermissionRequestMessage(
            request_id="r3",
            tool_name="Bash",
            session_id="s3",
            title="Run command",
            display_name="Bash",
        )
        d = msg.to_dict()
        assert d["title"] == "Run command"
        assert d["display_name"] == "Bash"
        assert "decision_reason" not in d
        assert "blocked_path" not in d
        assert "description" not in d

    def test_to_dict_omits_none_fields(self):
        msg = PermissionRequestMessage(request_id="r4", tool_name="Read", session_id="s4")
        d = msg.to_dict()
        assert "decision_reason" not in d
        assert "blocked_path" not in d
        assert "title" not in d
        assert "display_name" not in d
        assert "description" not in d

    def test_to_dict_all_fields_set(self):
        msg = PermissionRequestMessage(
            request_id="r5",
            tool_name="Edit",
            session_id="s5",
            decision_reason={"reason": "deny-rule"},
            blocked_path="/secret",
            title="Edit secret",
            display_name="Edit",
            description="Long description here",
        )
        d = msg.to_dict()
        assert d["decision_reason"] == {"reason": "deny-rule"}
        assert d["blocked_path"] == "/secret"
        assert d["title"] == "Edit secret"
        assert d["display_name"] == "Edit"
        assert d["description"] == "Long description here"

    def test_from_dict_round_trip(self):
        original = PermissionRequestMessage(
            request_id="r6",
            tool_name="Write",
            session_id="s6",
            tool_use_id="tu-abc",
            agent_id="agent-1",
            decision_reason="deny-rule",
            blocked_path="/var/secret",
            title="Write to secret",
            display_name="Write File",
            description="Detail about the write",
        )
        restored = PermissionRequestMessage.from_dict(original.to_dict())
        assert restored.request_id == "r6"
        assert restored.tool_use_id == "tu-abc"
        assert restored.agent_id == "agent-1"
        assert restored.decision_reason == "deny-rule"
        assert restored.blocked_path == "/var/secret"
        assert restored.title == "Write to secret"
        assert restored.display_name == "Write File"
        assert restored.description == "Detail about the write"

    def test_from_dict_missing_fields_default_none(self):
        # Old sessions without enriched fields should deserialize cleanly
        d = {
            "request_id": "r7",
            "tool_name": "Bash",
            "session_id": "s7",
        }
        msg = PermissionRequestMessage.from_dict(d)
        assert msg.decision_reason is None
        assert msg.blocked_path is None
        assert msg.title is None
        assert msg.display_name is None
        assert msg.description is None


# ---------------------------------------------------------------------------
# PermissionInfo – dataclass round-trip
# ---------------------------------------------------------------------------

class TestPermissionInfoEnrichedFields:
    def test_defaults_are_none(self):
        info = PermissionInfo(message="Allow Edit?")
        assert info.decision_reason is None
        assert info.blocked_path is None
        assert info.title is None
        assert info.display_name is None
        assert info.description is None

    def test_fields_stored_correctly(self):
        info = PermissionInfo(
            message="Allow Write?",
            decision_reason="policy",
            blocked_path="/etc",
            title="Write to /etc",
            display_name="Write File",
            description="A longer description",
        )
        assert info.decision_reason == "policy"
        assert info.blocked_path == "/etc"
        assert info.title == "Write to /etc"
        assert info.display_name == "Write File"
        assert info.description == "A longer description"

    def test_to_dict_omits_none_enriched_fields(self):
        info = PermissionInfo(message="Allow?")
        d = info.to_dict()
        assert "decision_reason" not in d
        assert "blocked_path" not in d
        assert "title" not in d
        assert "display_name" not in d
        assert "description" not in d

    def test_to_dict_includes_non_none_enriched_fields(self):
        info = PermissionInfo(
            message="Allow?",
            title="My Title",
            display_name="ToolName",
        )
        d = info.to_dict()
        assert d["title"] == "My Title"
        assert d["display_name"] == "ToolName"

    def test_from_dict_round_trip(self):
        original = PermissionInfo(
            message="Allow Edit?",
            suggestions=[],
            risk_level="high",
            decision_reason="deny-rule",
            blocked_path="/root",
            title="Edit root file",
            display_name="Edit",
            description="Editing a root-owned file",
        )
        restored = PermissionInfo.from_dict(original.to_dict())
        assert restored.message == "Allow Edit?"
        assert restored.risk_level == "high"
        assert restored.decision_reason == "deny-rule"
        assert restored.blocked_path == "/root"
        assert restored.title == "Edit root file"
        assert restored.display_name == "Edit"
        assert restored.description == "Editing a root-owned file"

    def test_from_dict_old_session_compat(self):
        d = {"message": "Allow?", "suggestions": [], "risk_level": "medium"}
        info = PermissionInfo.from_dict(d)
        assert info.decision_reason is None
        assert info.blocked_path is None
        assert info.title is None
        assert info.display_name is None
        assert info.description is None


# ---------------------------------------------------------------------------
# Permission service extraction – mocked coordinator
# ---------------------------------------------------------------------------

class TestPermissionServiceExtraction:
    """Verify the permission_service extracts all 5 enriched fields from context."""

    def _make_service(self, session_id: str):
        from src.event_queue import EventQueue
        from src.permission_service import PermissionService

        coord = _make_coordinator(session_id)
        queues: dict = {session_id: EventQueue()}
        return PermissionService(coordinator=coord, session_queues=queues), coord

    @pytest.mark.asyncio
    async def test_all_enriched_fields_extracted_and_denied(self):
        """When no ToolCall is found, auto-deny fires — extraction still runs."""
        session_id = "sess-1302"
        service, _ = self._make_service(session_id)

        ctx = _make_context(
            tool_use_id="tu-001",
            decision_reason="policy-deny",
            blocked_path="/etc/shadow",
            title="Read /etc/shadow",
            display_name="Read File",
            description="Claude wants to read a sensitive system file",
        )

        captured_requests: list[PermissionRequestMessage] = []

        import src.permission_service as ps_module
        original_sm = ps_module.StoredMessage

        class CapturingStoredMessage(original_sm):
            @classmethod
            def from_permission_request(cls, request, display=None):
                captured_requests.append(request)
                return original_sm.from_permission_request(request, display)

        ps_module.StoredMessage = CapturingStoredMessage
        try:
            callback = service.create_permission_callback(session_id)
            result = await callback("Read", {"file_path": "/etc/shadow"}, ctx)
        finally:
            ps_module.StoredMessage = original_sm

        assert result.get("behavior") == "deny"
        assert len(captured_requests) == 1
        req = captured_requests[0]
        assert req.decision_reason == "policy-deny"
        assert req.blocked_path == "/etc/shadow"
        assert req.title == "Read /etc/shadow"
        assert req.display_name == "Read File"
        assert req.description == "Claude wants to read a sensitive system file"

    @pytest.mark.asyncio
    async def test_none_defaults_when_context_lacks_enriched_fields(self):
        """Fields absent from context (older SDK) default to None."""
        session_id = "sess-1302b"
        service, _ = self._make_service(session_id)

        # Context missing the enriched attributes entirely
        ctx = MagicMock(spec=["tool_use_id", "agent_id", "suggestions"])
        ctx.tool_use_id = None
        ctx.agent_id = None
        ctx.suggestions = []

        captured_requests: list[PermissionRequestMessage] = []

        import src.permission_service as ps_module
        original_sm = ps_module.StoredMessage

        class CapturingStoredMessage(original_sm):
            @classmethod
            def from_permission_request(cls, request, display=None):
                captured_requests.append(request)
                return original_sm.from_permission_request(request, display)

        ps_module.StoredMessage = CapturingStoredMessage
        try:
            callback = service.create_permission_callback(session_id)
            await callback("Write", {"file_path": "/tmp/out.txt"}, ctx)
        finally:
            ps_module.StoredMessage = original_sm

        assert len(captured_requests) == 1
        req = captured_requests[0]
        assert req.decision_reason is None
        assert req.blocked_path is None
        assert req.title is None
        assert req.display_name is None
        assert req.description is None

    @pytest.mark.asyncio
    async def test_none_when_context_is_none(self):
        """All enriched fields are None when context itself is None."""
        session_id = "sess-1302c"
        service, _ = self._make_service(session_id)

        captured_requests: list[PermissionRequestMessage] = []

        import src.permission_service as ps_module
        original_sm = ps_module.StoredMessage

        class CapturingStoredMessage(original_sm):
            @classmethod
            def from_permission_request(cls, request, display=None):
                captured_requests.append(request)
                return original_sm.from_permission_request(request, display)

        ps_module.StoredMessage = CapturingStoredMessage
        try:
            callback = service.create_permission_callback(session_id)
            await callback("Bash", {"command": "ls"}, None)
        finally:
            ps_module.StoredMessage = original_sm

        assert len(captured_requests) == 1
        req = captured_requests[0]
        assert req.decision_reason is None
        assert req.blocked_path is None
        assert req.title is None
        assert req.display_name is None
        assert req.description is None
