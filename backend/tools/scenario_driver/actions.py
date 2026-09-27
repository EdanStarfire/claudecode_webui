"""Action primitives (issue #2038) — one small async function per action
type, each a thin HTTP call against the Frontend API. Actions that need data
from a just-observed event (e.g. a permission `request_id`) take the matched
event(s) from the preceding `wait_for()` call as an argument, rather than
re-deriving it, keeping each action a pure, testable function of
`(ctx, matched_events, **params)`.
"""

from __future__ import annotations

from pathlib import Path

from .context import DriverContext
from .poll_consumer import TaggedEvent, wait_for_ready


def _tool_call_data(matched: list[TaggedEvent]) -> dict:
    """Permission-lifecycle events are unified into the `tool_call` message
    type (no distinct `permission_request` event exists) — the fields live
    under `event["data"]`, e.g. `{"type": "message", "data": {"type":
    "tool_call", "request_id": ..., "name": ..., "input": {...}, ...}}`.
    See `backend/permission_service.py` for where this shape is broadcast.
    """
    return matched[0].event["data"]


def _permission_request_id(matched: list[TaggedEvent]) -> str:
    request_id = _tool_call_data(matched).get("request_id")
    if not request_id:
        raise ValueError(f"Matched event has no request_id: {matched[0].event!r}")
    return request_id


async def send_prompt(ctx: DriverContext, _matched: list[TaggedEvent] | None, *, session_id: str, message: str) -> dict:
    return await ctx.post_json(f"/api/sessions/{session_id}/messages", json={"message": message})


async def set_permission_mode(ctx: DriverContext, _matched: list[TaggedEvent] | None, *, session_id: str, mode: str) -> dict:
    return await ctx.post_json(f"/api/sessions/{session_id}/permission-mode", json={"mode": mode})


async def set_model(ctx: DriverContext, _matched: list[TaggedEvent] | None, *, session_id: str, model: str) -> dict:
    return await ctx.post_json(f"/api/sessions/{session_id}/model", json={"model": model})


async def answer_permission(
    ctx: DriverContext,
    matched: list[TaggedEvent],
    *,
    session_id: str,
    decision: str,
    apply_suggestions: bool = False,
    updated_input: dict | None = None,
) -> dict:
    """`decision` is `"allow"` or `"deny"`. Also used for ExitPlanMode's
    permission prompt, which uses this same endpoint."""
    request_id = _permission_request_id(matched)
    body = {
        "decision": decision,
        "apply_suggestions": apply_suggestions,
        "updated_input": updated_input,
    }
    return await ctx.post_json(
        f"/api/sessions/{session_id}/permission/{request_id}", json=body
    )


async def answer_ask_user_question(
    ctx: DriverContext,
    matched: list[TaggedEvent],
    *,
    session_id: str,
    option_label: str | None = None,
    custom_text: str | None = None,
) -> dict:
    """AskUserQuestion is just the same `tool_call`/permission flow as any
    other tool (`data.name == "AskUserQuestion"`, no distinct event type or
    endpoint). The answer is sent as `updated_input`: the original
    `input.questions` array copied back, plus an `answers` map keyed by
    question text (matching `PermissionPrompt.vue`'s own answer-building
    logic, consumed server-side as `PermissionResponseMessage.updated_input`
    in `permission_service.py`). Exactly one of `option_label`/`custom_text`
    should be given per question; skipping/denying uses
    `answer_permission(decision="deny")` directly instead.
    """
    data = _tool_call_data(matched)
    questions = data.get("input", {}).get("questions", [])
    answer_text = option_label if option_label is not None else custom_text
    answers = {q["question"]: answer_text for q in questions}
    body = {"decision": "allow", "updated_input": {"questions": questions, "answers": answers}}
    request_id = data.get("request_id")
    if not request_id:
        raise ValueError(f"Matched event has no request_id: {matched[0].event!r}")
    return await ctx.post_json(f"/api/sessions/{session_id}/permission/{request_id}", json=body)


async def interrupt(ctx: DriverContext, _matched: list[TaggedEvent] | None, *, session_id: str) -> dict:
    return await ctx.post_empty(f"/api/sessions/{session_id}/interrupt")


async def restart_session(ctx: DriverContext, _matched: list[TaggedEvent] | None, *, session_id: str) -> dict:
    return await ctx.post_empty(f"/api/sessions/{session_id}/restart")


async def restart_server(ctx: DriverContext, _matched: list[TaggedEvent] | None) -> dict:
    return await ctx.post_json("/api/system/restart", json={})


async def wait_for_server_ready(
    ctx: DriverContext, _matched: list[TaggedEvent] | None, *, timeout: float = 120.0
) -> None:
    """`restart_server`'s response arrives before the re-exec that tears the
    process down (`_finish_restart()` in `src/routers/system.py` fires
    ~0.5s later, fire-and-forget), so any action immediately after
    `restart_server` needs this gate first to avoid racing that teardown
    window."""
    await wait_for_ready(ctx.base_url, timeout=timeout)


async def upload_file(ctx: DriverContext, _matched: list[TaggedEvent] | None, *, session_id: str, file_path: Path) -> dict:
    path = Path(file_path)
    with path.open("rb") as fh:
        resp = await ctx.client.post(
            f"/api/sessions/{session_id}/files",
            files={"file": (path.name, fh, "application/octet-stream")},
            timeout=ctx.http_timeout,
        )
    resp.raise_for_status()
    return resp.json()


async def send_comm(
    ctx: DriverContext,
    _matched: list[TaggedEvent] | None,
    *,
    legion_id: str,
    to_minion_id: str,
    content: str,
    comm_type: str = "task",
) -> dict:
    return await ctx.post_json(
        f"/api/legions/{legion_id}/comms",
        json={"to_minion_id": to_minion_id, "to_user": False, "content": content, "comm_type": comm_type},
    )
