"""Background-task snapshot helpers (issue #2145).

The CLI emits ``system/background_tasks_changed`` whenever the set of running
background tasks (bash / agent / workflow) changes. Each event carries the FULL
current list at ``metadata.init_data.tasks``, so only the latest snapshot matters.
A process death emits no empty list, so a snapshot is only valid if it follows
the most recent process-launch boundary (client_launched / session_failed).
"""

from typing import Any

BACKGROUND_TASKS_SUBTYPE = "background_tasks_changed"
# Records after which any earlier snapshot refers to a dead process.
PROCESS_BOUNDARY_SUBTYPES = frozenset({"client_launched", "session_failed"})


def record_subtype(record: dict) -> str | None:
    """Subtype of a stored/live system record (top-level or metadata-only layouts)."""
    metadata = record.get("metadata")
    if isinstance(metadata, dict) and metadata.get("subtype"):
        return metadata["subtype"]
    return record.get("subtype")


def parse_background_tasks(metadata: Any) -> list[dict[str, Any]]:
    """Normalize ``metadata.init_data.tasks`` to ``[{task_id, task_type, description}]``.

    Tolerant by design: missing/non-list tasks -> [], entries without a task_id skipped.
    """
    init_data = metadata.get("init_data") if isinstance(metadata, dict) else None
    tasks = init_data.get("tasks") if isinstance(init_data, dict) else None
    if not isinstance(tasks, list):
        return []
    result = []
    for task in tasks:
        if not isinstance(task, dict) or not task.get("task_id"):
            continue
        result.append(
            {
                "task_id": str(task["task_id"]),
                "task_type": str(task.get("task_type") or ""),
                "description": str(task.get("description") or ""),
            }
        )
    return result


def latest_background_tasks(raw_messages: list[dict]) -> list[dict[str, Any]]:
    """Last snapshot occurring after the latest process boundary (none -> [])."""
    tasks: list[dict[str, Any]] = []
    for record in raw_messages:
        if record.get("type") != "system":
            continue
        subtype = record_subtype(record)
        if subtype in PROCESS_BOUNDARY_SUBTYPES:
            tasks = []
        elif subtype == BACKGROUND_TASKS_SUBTYPE:
            tasks = parse_background_tasks(record.get("metadata"))
    return tasks
