"""Keeps shared/event_registry.py honest against the real producers (issue #2063).

Source-scans the 10 producer modules audited for #2063 for `"type": "<literal>"` dict-literal
keys AND `emit(queue, family, "<literal>", ...)` call sites (stage 2a-B migrated most direct
`.append({"type": X, ...})` call sites into the latter shape — a literal-dict-key-only scan
would silently stop seeing most of them), and asserts every literal found is a registered
`TOP_LEVEL_EVENT_TYPES` key. Also scans the 3 modules that set a `system`-message `subtype` and
`backend/message_parser.py`'s `MessageType(Enum)` body, keeping `SYSTEM_SUBTYPES`/
`MESSAGE_DATA_TYPES` honest the same way. These are regression guards, not one-time audits: if
a future change appends a new, unregistered literal, the relevant test fails until
`shared/event_registry.py` is updated.

The one verified false-positive source is `"type": "text"` — MCP tool-response content blocks
(`{"type": "text", "text": ...}`) in `legion_mcp_tools.py`, which have nothing to do with
EventQueue events and are excluded by name.
"""

import re
from pathlib import Path

from shared.event_envelope import QUEUE_SESSION
from shared.event_registry import MESSAGE_DATA_TYPES, SYSTEM_SUBTYPES, TOP_LEVEL_EVENT_TYPES

REPO_ROOT = Path(__file__).resolve().parents[2]

PRODUCER_MODULES = [
    "backend/web_server.py",
    "backend/permission_service.py",
    "backend/session_watchdog.py",
    "backend/legion_system.py",
    "backend/legion/scheduler_service.py",
    "backend/legion/overseer_controller.py",
    "backend/legion/mcp/legion_mcp_tools.py",
    "backend/routers/secrets.py",
    "backend/routers/files.py",
    "src/routers/system.py",
]

# Modules that set `metadata["subtype"]` / `"subtype": "..."` on real `system`-typed messages.
# Deliberately excludes backend/claude_sdk.py's `_send_control_request({"subtype": ...})` —
# that's the SDK's own control-request protocol, unrelated to our `system`-message subtype.
SUBTYPE_PRODUCER_MODULES = [
    "backend/message_parser.py",
    "backend/session_coordinator.py",
    "backend/mock_sdk.py",
]

# MCP tool-response content blocks (`{"type": "text", "text": ...}`) — not EventQueue events.
KNOWN_NON_EVENT_LITERALS = frozenset({"text"})

TYPE_LITERAL_RE = re.compile(r'"type":\s*"(\w+)"')

# shared/event_emitter.py's emit(queue, family, event_type, payload) call sites where
# event_type is a literal string (as opposed to a forwarded/dynamic value like
# `event["type"]` or a variable) — the shape stage 2a-B migrated most producer call
# sites into, moving the literal out of a dict key and into a positional argument.
EMIT_CALL_LITERAL_RE = re.compile(r'\bemit\(\s*[^,\n]+,\s*\w+,\s*"(\w+)"')

# routers/secrets.py's proxy endpoint defaults to this when the caller supplies no `type`
# (`body.get("type", "proxy_event")`) — the one legitimately dynamic (non-literal) producer.
DYNAMIC_DEFAULT_RE = re.compile(r'get\(\s*"type",\s*"(\w+)"\s*\)')

# backend/docker/proxy/addon.py's `self._emit_ui_event("<literal>", ...)` call(s) — the addon
# runs out-of-process (in the Docker image) and can't import the Python registry directly, so
# this pins its literal event type(s) by static scan instead (issue #2063 AC4/T3).
ADDON_EMIT_LITERAL_RE = re.compile(r'_emit_ui_event\(\s*"(\w+)"')

# addon.py's secret_refresh_failed call's nested dict field names — pins the fixed
# "secret_name" key (was "name", a pre-existing bug no consumer matched) so a future
# revert can't silently reintroduce the unmatched field name (issue #2063 §3c/3d).
ADDON_SECRET_REFRESH_FAILED_FIELDS_RE = re.compile(
    r'_emit_ui_event\(\s*"secret_refresh_failed"\s*,\s*\{([^}]*)\}', re.DOTALL
)

# Matches both `"subtype": "x"` (dict-literal construction) and `["subtype"] = "x"`
# (item-assignment) forms — both are used across the 3 subtype-producer modules.
SUBTYPE_LITERAL_RE = re.compile(r'(?:"subtype":|\["subtype"\]\s*=)\s*"(\w+)"')

# backend/message_parser.py's MessageType(Enum) members: `    NAME = "value"` at 4-space
# indent — the only place in that file matching this shape.
MESSAGE_TYPE_ENUM_MEMBER_RE = re.compile(r'^\s{4}[A-Z_]+\s*=\s*"(\w+)"$', re.MULTILINE)


def _scan_literal_event_types(relative_path: str) -> set[str]:
    source = (REPO_ROOT / relative_path).read_text()
    found = {m.group(1) for m in TYPE_LITERAL_RE.finditer(source)}
    found |= {m.group(1) for m in EMIT_CALL_LITERAL_RE.finditer(source)}
    return found - KNOWN_NON_EVENT_LITERALS


def _scan_literal_subtypes(relative_path: str) -> set[str]:
    source = (REPO_ROOT / relative_path).read_text()
    return {m.group(1) for m in SUBTYPE_LITERAL_RE.finditer(source)}


def test_every_literal_event_type_is_registered():
    unregistered: dict[str, set[str]] = {}
    for relative_path in PRODUCER_MODULES:
        literals = _scan_literal_event_types(relative_path)
        missing = literals - TOP_LEVEL_EVENT_TYPES.keys()
        if missing:
            unregistered[relative_path] = missing

    assert not unregistered, (
        "Found event type(s) not registered in shared/event_registry.py's "
        f"TOP_LEVEL_EVENT_TYPES: {unregistered}"
    )


def test_dynamic_proxy_event_default_is_registered():
    source = (REPO_ROOT / "backend/routers/secrets.py").read_text()
    matches = DYNAMIC_DEFAULT_RE.findall(source)
    assert matches, (
        "Expected routers/secrets.py's dynamic-type proxy endpoint "
        '(body.get("type", <default>)) — update this test if that call site moved.'
    )
    for default_type in matches:
        assert default_type in TOP_LEVEL_EVENT_TYPES, (
            f"Dynamic default type {default_type!r} is not registered in TOP_LEVEL_EVENT_TYPES"
        )


def test_addon_literal_event_types_are_registered():
    """T3: pins backend/docker/proxy/addon.py's `_emit_ui_event("<literal>", ...)` event
    type(s) against the registry, and specifically against QUEUE_SESSION — the stream the
    addon actually reaches via routers/secrets.py's `emit_session_event`, not QUEUE_UI."""
    source = (REPO_ROOT / "backend/docker/proxy/addon.py").read_text()
    matches = ADDON_EMIT_LITERAL_RE.findall(source)
    assert matches, (
        "Expected backend/docker/proxy/addon.py's _emit_ui_event(<literal>, ...) call — "
        "update this test if that call site moved."
    )
    for event_type in matches:
        spec = TOP_LEVEL_EVENT_TYPES.get(event_type)
        assert spec is not None, (
            f"addon.py's literal event type {event_type!r} is not registered in "
            "TOP_LEVEL_EVENT_TYPES"
        )
        assert QUEUE_SESSION in spec.queues, (
            f"addon.py's literal event type {event_type!r} must be registered for "
            f"QUEUE_SESSION (the stream routers/secrets.py relays it onto), got {spec.queues}"
        )


def test_addon_secret_refresh_failed_uses_secret_name_field():
    """Pins the fixed field name (issue #2063 §3c): addon.py's secret_refresh_failed call
    must send "secret_name", not "name" — the original, pre-existing bug that no consumer
    (the eventual 2b session-stream handler, nor QUEUE_UI's secret_refresh_failed consumer)
    matched. Fails loudly if a future revert silently reintroduces the unmatched key."""
    source = (REPO_ROOT / "backend/docker/proxy/addon.py").read_text()
    match = ADDON_SECRET_REFRESH_FAILED_FIELDS_RE.search(source)
    assert match, (
        "Expected backend/docker/proxy/addon.py's _emit_ui_event(\"secret_refresh_failed\", "
        "{...}) call — update this test if that call site moved."
    )
    body = match.group(1)
    assert '"secret_name":' in body, (
        "addon.py's secret_refresh_failed payload must use \"secret_name\" "
        '(found no such key) — "name" is the pre-existing bug this test guards against.'
    )
    assert '"name":' not in body, (
        'addon.py\'s secret_refresh_failed payload must not use the unmatched "name" key.'
    )


def test_registry_is_not_missing_any_producer_module():
    for relative_path in PRODUCER_MODULES:
        assert (REPO_ROOT / relative_path).is_file(), f"Producer module moved/renamed: {relative_path}"


def test_every_literal_system_subtype_is_registered():
    unregistered: dict[str, set[str]] = {}
    for relative_path in SUBTYPE_PRODUCER_MODULES:
        literals = _scan_literal_subtypes(relative_path)
        missing = literals - SYSTEM_SUBTYPES
        if missing:
            unregistered[relative_path] = missing

    assert not unregistered, (
        "Found system-message subtype(s) not registered in shared/event_registry.py's "
        f"SYSTEM_SUBTYPES: {unregistered}"
    )


def test_registry_is_not_missing_any_subtype_producer_module():
    for relative_path in SUBTYPE_PRODUCER_MODULES:
        assert (REPO_ROOT / relative_path).is_file(), f"Producer module moved/renamed: {relative_path}"


def test_every_message_type_enum_member_is_registered():
    source = (REPO_ROOT / "backend/message_parser.py").read_text()
    members = set(MESSAGE_TYPE_ENUM_MEMBER_RE.findall(source))

    assert members, "Expected to find MessageType(Enum) members in backend/message_parser.py"
    missing = members - MESSAGE_DATA_TYPES
    assert not missing, (
        f"MessageType enum member(s) not registered in MESSAGE_DATA_TYPES: {missing}"
    )
