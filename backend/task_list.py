"""SDK task-list snapshot reducer (issue #2030, stage 1a, §3.6).

A pure reducer over stored ``tool_call`` records for ``TaskCreate`` / ``TaskUpdate`` that
mirrors the browser's live path — ``frontend/src/stores/task.js::handleTaskToolResult``
(``parseResultContent``, ``extractTaskId``, ``createTask``, ``updateTask``) — exactly,
including its quirks (JS truthiness, ``undefined`` vs ``null`` input fields, a ``TaskUpdate``
for an id that was never created materialising a subject-less task once any task exists,
and a ``TaskUpdate`` being ignored while the session has never had a task).

Two reducers exist (this one for the snapshot, the browser's for the live path); the shared
case file ``backend/tests/fixtures/task_reducer_cases.json`` is run by both a pytest and a
vitest test so they cannot drift.
"""

import json
import math
import re
from typing import Any

TASK_TOOL_NAMES = frozenset({"TaskCreate", "TaskUpdate"})
_TERMINAL_STATUSES = frozenset({"completed", "failed"})

# JS \s (no `u` flag): WhiteSpace + LineTerminator code points.
_JS_WS = "\t\n\u000b\u000c\r \u00a0\u1680\u2000-\u200a\u2028\u2029\u202f\u205f\u3000\ufeff"
_TASK_ID_RE = re.compile(rf"[Tt]ask[{_JS_WS}]*#([0-9]+)", re.ASCII | re.IGNORECASE)
_SIMPLE_ID_RE = re.compile(r"#([0-9]+)", re.ASCII)

_MISSING = object()  # JS `undefined`


class _JsTypeError(Exception):
    """A JS TypeError the browser's reducer would throw (and its caller would swallow)."""


def _truthy(value: Any) -> bool:
    """JS truthiness (note: ``[]`` and ``{}`` are truthy)."""
    if value is _MISSING or value is None or value is False:
        return False
    if isinstance(value, (int, float)):
        return not (value == 0 or (isinstance(value, float) and math.isnan(value)))
    if isinstance(value, str):
        return value != ""
    return True


def _or(value: Any, default: Any) -> Any:
    return value if _truthy(value) else default


def _get(obj: Any, key: str) -> Any:
    """``obj?.key`` — ``_MISSING`` for non-objects and absent keys."""
    if isinstance(obj, dict):
        return obj.get(key, _MISSING)
    return _MISSING


def _json_stringify(value: Any) -> str:
    return json.dumps(value, separators=(",", ":"), ensure_ascii=False)


def _parse_result_content(error: bool, content: Any) -> str:
    """``parseResultContent({error, content})`` — ``content`` may be ``_MISSING``."""
    if _truthy(content):
        return content if isinstance(content, str) else _json_stringify(content)
    result: dict[str, Any] = {"error": error}
    if content is not _MISSING:
        result["content"] = content
    return _json_stringify(result)


def _extract_task_id(text: str) -> str | None:
    match = _TASK_ID_RE.search(text)
    if match:
        return match.group(1)
    simple = _SIMPLE_ID_RE.search(text)
    return simple.group(1) if simple else None


def _spread_items(value: Any) -> list[Any]:
    """``[...value]`` for the values a truthy ``addBlocks`` can take."""
    if isinstance(value, (list, str)):
        return list(value)
    raise _JsTypeError("not iterable")


def _spread_object(value: Any) -> dict[Any, Any]:
    """``{...value}``."""
    if isinstance(value, dict):
        return dict(value)
    if isinstance(value, (list, str)):
        return {str(i): v for i, v in enumerate(value)}
    return {}


def _set_key(item: Any) -> Any:
    """SameValueZero-ish identity for ``new Set([...])`` over JSON values."""
    if isinstance(item, bool):
        return ("b", item)
    if isinstance(item, (int, float)):
        return ("n", float(item))
    if isinstance(item, str):
        return ("s", item)
    if item is None:
        return ("z", None)
    return ("o", id(item))  # objects/arrays never compare equal by value


def _union(first: list[Any], second: list[Any]) -> list[Any]:
    seen: set[Any] = set()
    out = []
    for item in [*first, *second]:
        key = _set_key(item)
        if key not in seen:
            seen.add(key)
            out.append(item)
    return out


def _id_sort_key(task_id: Any) -> int:
    digits = re.sub(r"[^0-9]", "", str(task_id))
    return int(digits) if digits else 0


class TaskListState:
    """Per-session task list. ``apply_tool_call`` mirrors ``handleTaskToolResult``."""

    def __init__(self) -> None:
        self._tasks: dict[Any, dict[str, Any]] = {}
        # Browser: tasksBySession.get(sessionId) is undefined until the first createTask.
        self._has_map = False
        self._applied_ids: set[str] = set()

    # ------------------------------------------------------------------ public

    def apply_tool_call(self, record: dict[str, Any]) -> bool:
        """Apply one stored/live ``tool_call`` record. Returns True when it was a
        completed/failed Task* record that had not been applied before (de-duplicated by
        ``tool_use_id``)."""
        if record.get("type") != "tool_call":
            return False
        if record.get("name") not in TASK_TOOL_NAMES:
            return False
        if record.get("status") not in _TERMINAL_STATUSES:
            return False
        tool_use_id = record.get("tool_use_id")
        if tool_use_id is not None:
            if tool_use_id in self._applied_ids:
                return False
            self._applied_ids.add(tool_use_id)

        if record.get("status") == "failed":
            return True  # browser: result.error -> skipped
        content = record.get("result", _MISSING)
        text = _parse_result_content(False, content)
        task_input = record.get("input", _MISSING)
        try:
            if record["name"] == "TaskCreate":
                self._task_create(text, task_input)
            else:
                self._task_update(task_input)
        except (_JsTypeError, TypeError):
            pass  # caller swallows (message.js try/catch); partial effects stay applied
        return True

    def snapshot(self) -> list[dict[str, Any]]:
        tasks = [t for t in self._tasks.values() if t["status"] != "deleted"]
        tasks.sort(key=lambda t: _id_sort_key(t["id"]))
        return [dict(t) for t in tasks]

    # ------------------------------------------------------------------ reducer

    def _task_create(self, text: str, task_input: Any) -> None:
        task_id = _or(_extract_task_id(text), _get(task_input, "id"))
        if not _truthy(task_id):
            return
        self._put(
            task_id,
            subject=_get(task_input, "subject"),
            description=_get(task_input, "description"),
            status="pending",
            active_form=_get(task_input, "activeForm"),
            owner=_get(task_input, "owner"),
            blocks=[],
            blocked_by=[],
            metadata=_get(task_input, "metadata"),
        )

    def _put(
        self, task_id: Any, *, subject: Any, description: Any, status: Any, active_form: Any,
        owner: Any, blocks: Any, blocked_by: Any, metadata: Any,
    ) -> None:
        self._has_map = True
        self._tasks[task_id] = {
            "id": task_id,
            "subject": _or(subject, ""),
            "description": _or(description, ""),
            "status": _or(status, "pending"),
            "activeForm": _or(active_form, None),
            "owner": _or(owner, None),
            "blocks": _or(blocks, []),
            "blockedBy": _or(blocked_by, []),
            "metadata": _or(metadata, {}),
        }

    def _task_update(self, task_input: Any) -> None:
        task_id = _get(task_input, "taskId")
        if not _truthy(task_id):
            return
        if not self._has_map:
            return  # updateTask: "No tasks found for session"
        updates = {
            key: _get(task_input, key)
            for key in (
                "status", "subject", "description", "activeForm", "owner", "metadata",
                "addBlocks", "addBlockedBy",
            )
        }
        task = self._tasks.get(task_id)
        if task is None:
            if updates["status"] == "deleted":
                return
            self._put(
                task_id,
                subject=updates["subject"],
                description=updates["description"],
                status=updates["status"],
                active_form=updates["activeForm"],
                owner=updates["owner"],
                blocks=None,
                blocked_by=None,
                metadata=updates["metadata"],
            )
            return

        if updates["subject"] is not _MISSING:
            task["subject"] = updates["subject"]
        if updates["description"] is not _MISSING:
            task["description"] = updates["description"]
        if updates["status"] is not _MISSING:
            task["status"] = updates["status"]
        if updates["activeForm"] is not _MISSING:
            task["activeForm"] = updates["activeForm"]
        if updates["owner"] is not _MISSING:
            task["owner"] = updates["owner"]
        if updates["metadata"] is not _MISSING:
            task["metadata"] = {
                **_spread_object(task["metadata"]), **_spread_object(updates["metadata"])
            }
        if _truthy(updates["addBlocks"]):
            task["blocks"] = _union(_spread_items(task["blocks"]), _spread_items(updates["addBlocks"]))
        if _truthy(updates["addBlockedBy"]):
            task["blockedBy"] = _union(
                _spread_items(task["blockedBy"]), _spread_items(updates["addBlockedBy"])
            )
        if updates["status"] == "deleted":
            del self._tasks[task_id]
