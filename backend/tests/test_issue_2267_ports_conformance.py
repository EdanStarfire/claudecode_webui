"""AC4 (issue #2267): structural conformance test between
backend/legion/ports.py's 4 Protocols and the real classes Legion wires into
those slots at runtime.

Correction to the issue's own wording: the issue names
`backend/legion/legion_coordinator.py::LegionCoordinator` as the 4th
Protocol's (`LegionCoordinatorPort`) satisfier. It is not — the Protocol's
own docstring says it is a "narrow view of SessionCoordinator", and
`backend/legion_system.py` wires `session_port=session_coordinator` (a
SessionCoordinator instance) into that slot. `LegionCoordinator` itself
takes a `LegionSystem` in its own constructor, making it a *consumer* of
the port, not a satisfier. This test checks `SessionCoordinator` instead.

Uses manual `inspect.signature()` comparison rather than
`@runtime_checkable` + `isinstance()` — no `@runtime_checkable` Protocol
exists anywhere in this repo today, so this avoids changing `ports.py`'s
public contract on zero existing precedent either way.
"""

from __future__ import annotations

import inspect

import pytest

from backend.legion.ports import (
    LegionCoordinatorPort,
    ProjectManagerPort,
    QueueManagerPort,
    SessionManagerPort,
)
from backend.project_manager import ProjectManager
from backend.queue_manager import QueueManager
from backend.session_coordinator import SessionCoordinator
from backend.session_manager import SessionManager

# Protocol -> concrete class -> method names to check. Hand-listed rather than
# derived via generic Protocol-introspection machinery — there are only 4
# pairs and ~26 methods total, so an explicit list is simpler to read and
# maintain than generic reflection for a one-off conformance test.
_METHOD_PAIRS: list[tuple[type, type, list[str]]] = [
    (
        SessionManagerPort, SessionManager,
        ["get_session_info", "list_sessions", "get_session_directory", "update_session"],
    ),
    (
        ProjectManagerPort, ProjectManager,
        [
            "get_project", "list_projects", "record_fleet_stopped",
            "clear_fleet_stopped", "get_fleet_snapshot",
        ],
    ),
    (QueueManagerPort, QueueManager, ["get_queue"]),
    (
        LegionCoordinatorPort, SessionCoordinator,
        [
            "enqueue_message", "start_session", "terminate_session", "create_session",
            "delete_session", "create_ephemeral_session", "send_message",
            "interrupt_session", "wait_for_session_ready", "get_all_descendants",
            "archive_and_clear_session", "resolve_orphaned_ephemeral_session",
            "restart_session", "register_uploaded_file", "register_uploaded_resource",
            "pause_queue",
        ],
    ),
]

# Protocol -> instance attribute names declared on it (not methods).
_ATTRIBUTE_PAIRS: list[tuple[type, list[str]]] = [
    (SessionManagerPort, ["sessions_dir", "data_dir"]),
    (
        LegionCoordinatorPort,
        [
            "data_dir", "template_manager", "profile_manager", "credential_vault",
            "session_manager", "project_manager", "queue_manager", "delivery_gate",
        ],
    ),
]


def _param_names(fn) -> list[str]:
    """Positional/keyword parameter names, excluding `self`."""
    return [p for p in inspect.signature(fn).parameters if p != "self"]


@pytest.fixture
def session_manager(tmp_path):
    return SessionManager(tmp_path)


@pytest.fixture
def project_manager(tmp_path):
    return ProjectManager(tmp_path)


@pytest.fixture
def queue_manager():
    return QueueManager()


@pytest.fixture
def coordinator(tmp_path):
    return SessionCoordinator(data_dir=tmp_path)


@pytest.fixture
def instances(session_manager, project_manager, queue_manager, coordinator):
    return {
        SessionManager: session_manager,
        ProjectManager: project_manager,
        QueueManager: queue_manager,
        SessionCoordinator: coordinator,
    }


class TestAC4PortsConformance:
    def test_concrete_classes_define_every_protocol_method(self, instances):
        for protocol, concrete, method_names in _METHOD_PAIRS:
            instance = instances[concrete]
            for name in method_names:
                attr = getattr(instance, name, None)
                assert callable(attr), (
                    f"{concrete.__name__}.{name} must exist and be callable to satisfy "
                    f"{protocol.__name__}"
                )

    def test_concrete_method_signatures_match_protocol_param_names(self, instances):
        violations: list[str] = []
        for protocol, concrete, method_names in _METHOD_PAIRS:
            instance = instances[concrete]
            for name in method_names:
                protocol_params = _param_names(getattr(protocol, name))
                concrete_params = _param_names(getattr(instance, name))
                if protocol_params != concrete_params:
                    violations.append(
                        f"{concrete.__name__}.{name}: {concrete_params} != "
                        f"{protocol.__name__}.{name}: {protocol_params}"
                    )
        assert not violations, (
            "Parameter name drift between a Protocol and its satisfier — a renamed/"
            "reordered parameter would break every caller going through the port:\n"
            + "\n".join(violations)
        )

    def test_concrete_instances_carry_every_protocol_attribute(self, instances):
        concrete_by_protocol = {
            SessionManagerPort: instances[SessionManager],
            LegionCoordinatorPort: instances[SessionCoordinator],
        }
        violations: list[str] = []
        for protocol, attr_names in _ATTRIBUTE_PAIRS:
            instance = concrete_by_protocol[protocol]
            for attr in attr_names:
                if not hasattr(instance, attr):
                    violations.append(f"{type(instance).__name__} is missing attribute {attr!r}")
        assert not violations, (
            "Protocol-declared instance attribute missing on its satisfier:\n"
            + "\n".join(violations)
        )
