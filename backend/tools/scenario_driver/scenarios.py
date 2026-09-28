"""Scenario data (issue #2038) — all 22 equivalence-fixture recording
scenarios, transcribed as data: each `Step` is (optionally) a wait followed
by (optionally) an action, referencing the fixed action/predicate libraries
in `actions.py`/`predicates.py`. Adding a new scenario or reordering existing
ones is a pure data edit to `build_scenarios()` — no new control flow.

`coverage_markers` cross-references `backend.fixture_export.REQUIRED_MARKERS`
by name; `backend/tests/test_scenario_driver_dry_run.py` asserts every
required marker is claimed by at least one scenario here, catching drift
between the two files early. Each scenario that needs a specific permission
mode sets it explicitly as its own first step (not inherited from a prior
scenario) so scenarios stay reorderable.

Prompts are directive — naming the exact tool and call count — to keep tool
usage predictable for a real, credentialed run against a live model. The
mock SDK's raw-log replay never re-decides anything from a prompt (it
replays pre-recorded output regardless of what's sent), so prompt wording
only matters there for readability.

## Adding a new scenario

1. Pick an unused `id` (list order is run order, not `id` order — ids don't
   need to stay contiguous, though keeping them so makes `--from-scenario`
   and checkpoints easier to reason about) and append a new
   `scenarios.append(Scenario(id=..., title=..., steps=[...]))` block
   anywhere in `build_scenarios()`.
2. Each `Step` does, in order: wait (optional), then act (optional) — the
   wait's matched events are passed to the action, or `None` if there was no
   wait. Three common shapes:
   - Pure action (nothing to wait for first):
     `Step(action=partial(actions.send_prompt, session_id=m, message="..."))`
   - Pure wait (no response needed): `Step(wait=_wait_result(m, "<id>"))`
   - Wait for an event, then respond to it:
     `Step(wait=WaitSpec(predicate=..., description="scenario <id>: ...",
     timeout_s=...), action=partial(actions.answer_permission, session_id=m,
     decision="allow"))`
3. Reuse an existing `predicates.py` matcher (`tool_call_awaiting_permission`,
   `result_message`, `system_subtype`, `task_event`, `state_change`,
   `resource_registered`, `minion_comm_notification`, ...) rather than
   writing a new one inline. Only add a new predicate function when none of
   the existing shapes fit — and verify the real event shape against a live
   session first, not just from reading `message_parser.py`/`web_server.py`:
   most session-stream events are wrapped as `{"type": "message", "data":
   {...}}` (see `predicates._message_data()`), but a few — `assistant_delta`,
   `usage_updated`, `context_update`, `resource_registered`, and everything
   on the UI stream (`state_change`, `notification`) — are bare top-level
   dicts instead. Getting this distinction wrong is the single most common
   way a new wait silently times out — verify against a real session's
   actual poll response, not just by reading the message-construction code.
4. Reuse an existing `actions.py` function the same way; add a new one only
   for a genuinely new HTTP call, keeping the `(ctx, matched, **kwargs)`
   signature so it slots into `functools.partial(...)` like the others.
5. If the new scenario should count toward one of
   `backend.fixture_export.REQUIRED_MARKERS`, set `coverage_markers=(...)`
   — otherwise leave the default empty tuple.
   `test_required_markers_all_covered_by_scenarios` only requires each
   marker be claimed once across the whole list, not by every scenario that
   happens to touch it.
6. Give every `description` a `"scenario <id>: ..."` prefix — that string is
   exactly what `ScenarioError`'s message surfaces on failure, so a vague
   description makes a real failure harder to diagnose.
7. If the scenario needs a specific permission mode, set it explicitly as
   the scenario's own first step (`partial(actions.set_permission_mode,
   session_id=m, mode="default")`) rather than relying on a prior scenario
   having left it in the right state — keeps scenarios reorderable.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from functools import partial
from pathlib import Path
from typing import Any

from . import actions, predicates
from .context import DriverContext
from .poll_consumer import Stream, TaggedEvent
from .setup import TEST_MINION_NAME

Action = Callable[[DriverContext, list[TaggedEvent] | None], Awaitable[Any]]


@dataclass
class WaitSpec:
    predicate: predicates.Predicate
    description: str
    count: int = 1
    timeout_s: float = 30.0
    stream: Stream | None = None
    # False: the next wait rescans from where this one started, so events this
    # wait scanned past (but didn't match) stay visible to it. Needed when two
    # waits' events can interleave, e.g. a background task finishing before a
    # sibling task has started.
    consume: bool = True


@dataclass
class Step:
    wait: WaitSpec | None = None
    action: Action | None = None


@dataclass
class Scenario:
    id: int
    title: str
    steps: list[Step]
    coverage_markers: tuple[str, ...] = field(default_factory=tuple)


def _wait_result(session_id: str, scenario: str, timeout_s: float = 60.0) -> WaitSpec:
    return WaitSpec(
        predicate=predicates.result_message(session_id),
        description=f"scenario {scenario}: result message",
        timeout_s=timeout_s,
    )


def _wait_delta(session_id: str, scenario: str, timeout_s: float = 60.0) -> WaitSpec:
    return WaitSpec(
        predicate=predicates.assistant_delta(session_id),
        description=f"scenario {scenario}: streaming delta",
        timeout_s=timeout_s,
    )


def _permission_flow(
    session_id: str,
    scenario: str,
    tool_name: str,
    *,
    decision: str,
    timeout_s: float = 60.0,
) -> Step:
    """Wait for `tool_name`'s permission prompt, then allow/deny it."""
    return Step(
        wait=WaitSpec(
            predicate=predicates.tool_call_awaiting_permission(session_id, tool_name),
            description=f"scenario {scenario}: {tool_name} permission prompt",
            timeout_s=timeout_s,
        ),
        action=partial(actions.answer_permission, session_id=session_id, decision=decision),
    )


def build_scenarios(
    *, main_session_id: str, minion_id: str, legion_id: str, scratch_repo: Path
) -> list[Scenario]:
    """Scenario definitions are parameterized by the IDs setup.py creates at
    run time (unknown until the session/legion/minion actually exist) — still
    plain data, just resolved once instead of hardcoded. `scratch_repo` is
    the `Path` setup.py resets/populates — needed for scenario 20's upload,
    which reads a local file from it."""
    m = main_session_id

    scenarios: list[Scenario] = []

    # 1: Setup prompt — send; wait for result.
    scenarios.append(
        Scenario(
            id=1,
            title="Setup prompt",
            coverage_markers=("streaming deltas",),
            steps=[
                Step(action=partial(
                    actions.send_prompt, session_id=m,
                    message="Reply with the single word 'ready' and nothing else.",
                )),
                Step(wait=_wait_delta(m, "1")),
                Step(wait=_wait_result(m, "1")),
            ],
        )
    )

    # 2: Permission mode changes — change mode via API several times; wait for each.
    mode_cycle = ["plan", "acceptEdits", "default"]
    mode_steps: list[Step] = []
    for mode in mode_cycle:
        mode_steps.append(Step(action=partial(actions.set_permission_mode, session_id=m, mode=mode)))
        mode_steps.append(Step(wait=WaitSpec(
            predicate=predicates.permission_mode_state_change(m, mode),
            description=f"scenario 2: permission mode changed to {mode}",
            timeout_s=30.0,
        )))
    scenarios.append(Scenario(id=2, title="Permission mode changes", steps=mode_steps))

    # 3: Write file, approved.
    scenarios.append(
        Scenario(
            id=3,
            title="Write file, approved",
            coverage_markers=("tool call with permission prompt",),
            steps=[
                Step(action=partial(actions.set_permission_mode, session_id=m, mode="default")),
                Step(action=partial(
                    actions.send_prompt, session_id=m,
                    message="Call the Write tool exactly once to create test_file.md "
                            "containing a four-line poem.",
                )),
                _permission_flow(m, "3", "Write", decision="allow"),
                Step(wait=_wait_result(m, "3")),
            ],
        )
    )

    # 4: Write file, denied.
    scenarios.append(
        Scenario(
            id=4,
            title="Write file, denied",
            coverage_markers=("denied permission",),
            steps=[
                Step(action=partial(actions.set_permission_mode, session_id=m, mode="default")),
                Step(action=partial(
                    actions.send_prompt, session_id=m,
                    message="Call the Write tool exactly once to create test_file_denied.md "
                            "containing the text 'denied'.",
                )),
                _permission_flow(m, "4", "Write", decision="deny"),
                Step(wait=_wait_result(m, "4")),
            ],
        )
    )

    # 5: AskUserQuestion answered.
    scenarios.append(
        Scenario(
            id=5,
            title="AskUserQuestion answered",
            coverage_markers=("AskUserQuestion",),
            steps=[
                Step(action=partial(
                    actions.send_prompt, session_id=m,
                    message="Call the AskUserQuestion tool exactly once, asking me to choose "
                            "between 'Option A' and 'Option B'.",
                )),
                Step(
                    wait=WaitSpec(
                        predicate=predicates.tool_call_awaiting_permission(m, "AskUserQuestion"),
                        description="scenario 5: AskUserQuestion prompt",
                        timeout_s=60.0,
                    ),
                    action=partial(actions.answer_ask_user_question, session_id=m, option_label="Option A"),
                ),
                Step(wait=_wait_result(m, "5")),
            ],
        )
    )

    # 6: AskUserQuestion skipped.
    scenarios.append(
        Scenario(
            id=6,
            title="AskUserQuestion skipped",
            steps=[
                Step(action=partial(
                    actions.send_prompt, session_id=m,
                    message="Call the AskUserQuestion tool exactly once, asking me to choose "
                            "between 'Option A' and 'Option B'.",
                )),
                Step(
                    wait=WaitSpec(
                        predicate=predicates.tool_call_awaiting_permission(m, "AskUserQuestion"),
                        description="scenario 6: AskUserQuestion prompt",
                        timeout_s=60.0,
                    ),
                    action=partial(actions.answer_permission, session_id=m, decision="deny"),
                ),
                Step(wait=_wait_result(m, "6")),
            ],
        )
    )

    # 7: AskUserQuestion custom answer.
    scenarios.append(
        Scenario(
            id=7,
            title="AskUserQuestion custom answer",
            steps=[
                Step(action=partial(
                    actions.send_prompt, session_id=m,
                    message="Call the AskUserQuestion tool exactly once, asking me to choose "
                            "between 'Option A' and 'Option B'.",
                )),
                Step(
                    wait=WaitSpec(
                        predicate=predicates.tool_call_awaiting_permission(m, "AskUserQuestion"),
                        description="scenario 7: AskUserQuestion prompt",
                        timeout_s=60.0,
                    ),
                    action=partial(
                        actions.answer_ask_user_question, session_id=m,
                        custom_text="I'd like a third option instead.",
                    ),
                ),
                Step(wait=_wait_result(m, "7")),
            ],
        )
    )

    # 8: Foreground subagent reads folder and writes a file. Runs in acceptEdits:
    # the subagent's choice of listing tool (Read/Glob/Bash ls) isn't predictable,
    # and read-only tools don't prompt anyway, so waiting on specific permission
    # prompts here is flaky. Permission prompts are covered by other scenarios.
    scenarios.append(
        Scenario(
            id=8,
            title="Foreground subagent reads folder and writes a file",
            coverage_markers=("subagent task with progress",),
            steps=[
                Step(action=partial(
                    actions.set_permission_mode, session_id=m, mode="acceptEdits",
                )),
                Step(action=partial(
                    actions.send_prompt, session_id=m,
                    message="Use the Task tool to spawn a foreground subagent that reads the "
                            "current directory listing and writes a one-paragraph summary to "
                            "subagent_summary.md, then reports back.",
                )),
                Step(
                    wait=WaitSpec(
                        predicate=predicates.task_event(m, ("task_started",)),
                        description="scenario 8: subagent task started",
                        timeout_s=60.0,
                    ),
                ),
                Step(
                    wait=WaitSpec(
                        predicate=predicates.task_event(m, ("task_notification",)),
                        description="scenario 8: subagent task completion notification",
                        timeout_s=90.0,
                    ),
                ),
                Step(wait=_wait_result(m, "8")),
            ],
        )
    )

    # 9: Session restart via MCP tool.
    scenarios.append(
        Scenario(
            id=9,
            title="Session restart via MCP tool",
            coverage_markers=("session restart",),
            steps=[
                Step(action=partial(
                    actions.send_prompt, session_id=m,
                    message="Call the restart_session Legion MCP tool on your own session_id, "
                            "then continue.",
                )),
                Step(wait=WaitSpec(
                    predicate=predicates.state_change(m, "terminated"),
                    description="scenario 9: restart begins",
                    timeout_s=60.0,
                )),
                Step(wait=WaitSpec(
                    predicate=predicates.state_change(m, "active"),
                    description="scenario 9: session resumed after restart",
                    timeout_s=60.0,
                )),
                Step(wait=_wait_result(m, "9")),
            ],
        )
    )

    # 10: Three parallel background subagents plus own work.
    scenarios.append(
        Scenario(
            id=10,
            title="Three parallel background subagents plus own work",
            steps=[
                Step(action=partial(actions.set_permission_mode, session_id=m, mode="acceptEdits")),
                Step(action=partial(
                    actions.send_prompt, session_id=m,
                    message="Spawn exactly three background Task subagents in parallel, each "
                            "counting from 1 to 5 with no tool use, while you also reply "
                            "yourself with a short status update.",
                )),
                Step(wait=WaitSpec(
                    predicate=predicates.task_event(m, ("task_started",)),
                    description="scenario 10: three background tasks started",
                    count=3,
                    timeout_s=60.0,
                    consume=False,
                )),
                Step(wait=WaitSpec(
                    predicate=predicates.task_event(m, ("task_notification",)),
                    description="scenario 10: three background tasks notified",
                    count=3,
                    timeout_s=90.0,
                )),
                Step(wait=_wait_result(m, "10")),
            ],
        )
    )

    # 11: Bash sleep, interrupted.
    scenarios.append(
        Scenario(
            id=11,
            title="Bash sleep, interrupted",
            coverage_markers=("interrupt mid-tool",),
            steps=[
                Step(action=partial(actions.set_permission_mode, session_id=m, mode="acceptEdits")),
                Step(action=partial(
                    actions.send_prompt, session_id=m,
                    message="Call the Bash tool exactly once to run 'sleep 30'.",
                )),
                Step(wait=WaitSpec(
                    predicate=predicates.tool_call_status(m, "pending", "Bash"),
                    description="scenario 11: Bash sleep pending",
                    timeout_s=30.0,
                )),
                Step(action=partial(actions.interrupt, session_id=m)),
                Step(wait=WaitSpec(
                    predicate=predicates.system_subtype(m, "interrupt"),
                    description="scenario 11: interrupt landed",
                    timeout_s=30.0,
                )),
            ],
        )
    )

    # 12: Bash sleep, session restarted mid-tool.
    scenarios.append(
        Scenario(
            id=12,
            title="Bash sleep, session restarted mid-tool",
            steps=[
                Step(action=partial(
                    actions.send_prompt, session_id=m,
                    message="Call the Bash tool exactly once to run 'sleep 30'.",
                )),
                Step(wait=WaitSpec(
                    predicate=predicates.tool_call_status(m, "pending", "Bash"),
                    description="scenario 12: Bash sleep pending",
                    timeout_s=30.0,
                )),
                Step(action=partial(actions.restart_session, session_id=m)),
                # The restart endpoint returns with the session already STARTING, so
                # polling its REST state is reliable here; a `state_change` "active"
                # wait isn't — one from before the restart can match immediately.
                Step(action=partial(actions.ensure_session_active, session_id=m)),
            ],
        )
    )

    # 13: Comm to Test Minion and reply.
    scenarios.append(
        Scenario(
            id=13,
            title="Comm to Test Minion and reply",
            coverage_markers=("inter-minion comm",),
            # The main session itself sends the comm (not the driver via REST), so the
            # Test Minion's reply is delivered back INTO the main session — the only
            # session whose raw log gets exported, and so the only place the
            # "inter-minion comm" marker can be observed.
            steps=[
                Step(action=partial(
                    actions.send_prompt, session_id=m,
                    message=f"Use the send_comm Legion MCP tool exactly once to send the "
                            f"message 'Hello Test Minion.' to the minion named "
                            f"'{TEST_MINION_NAME}', then stop and wait for its reply.",
                )),
                # consume=False: this UI-stream notification and the session-stream
                # delivery below land in the shared buffer in either order.
                Step(wait=WaitSpec(
                    predicate=predicates.minion_comm_notification(TEST_MINION_NAME),
                    description="scenario 13: comm reply from Test Minion",
                    timeout_s=90.0,
                    consume=False,
                )),
                Step(wait=WaitSpec(
                    predicate=predicates.comm_delivered(),
                    description="scenario 13: Test Minion's reply delivered to main session",
                    stream="session",
                    timeout_s=60.0,
                )),
                Step(wait=_wait_result(m, "13")),
            ],
        )
    )

    # 14: Large markdown with two mermaid diagrams.
    scenarios.append(
        Scenario(
            id=14,
            title="Large markdown with two mermaid diagrams",
            steps=[
                Step(action=partial(
                    actions.send_prompt, session_id=m,
                    message="Write a markdown report of at least 500 words containing exactly "
                            "two mermaid diagrams, and reply with it directly (no file tools).",
                )),
                Step(wait=_wait_result(m, "14", timeout_s=120.0)),
            ],
        )
    )

    # 15: Compaction.
    scenarios.append(
        Scenario(
            id=15,
            title="Compaction",
            coverage_markers=("compaction",),
            steps=[
                Step(action=partial(actions.send_prompt, session_id=m, message="/compact")),
                Step(wait=WaitSpec(
                    predicate=predicates.system_subtype(m, "compact_boundary"),
                    description="scenario 15: compaction boundary",
                    timeout_s=90.0,
                )),
                Step(wait=_wait_result(m, "15")),
            ],
        )
    )

    # 16: Model change.
    scenarios.append(
        Scenario(
            id=16,
            title="Model change",
            steps=[
                Step(action=partial(actions.set_model, session_id=m, model="sonnet")),
                Step(wait=WaitSpec(
                    predicate=predicates.model_state_change(m, "sonnet"),
                    description="scenario 16: model change confirmed",
                    timeout_s=30.0,
                )),
            ],
        )
    )

    # 17: Write approved.
    scenarios.append(
        Scenario(
            id=17,
            title="Write approved (first of a follow-up pair)",
            steps=[
                Step(action=partial(actions.set_permission_mode, session_id=m, mode="default")),
                Step(action=partial(
                    actions.send_prompt, session_id=m,
                    message="Call the Write tool exactly once to create followup_a.md "
                            "containing the text 'first'.",
                )),
                _permission_flow(m, "17", "Write", decision="allow"),
                Step(wait=_wait_result(m, "17")),
            ],
        )
    )

    # 18: Follow-up write (Edit), approved.
    scenarios.append(
        Scenario(
            id=18,
            title="Follow-up write (Edit), approved",
            steps=[
                Step(action=partial(actions.set_permission_mode, session_id=m, mode="default")),
                Step(action=partial(
                    actions.send_prompt, session_id=m,
                    message="Call the Edit tool exactly once to change followup_a.md's "
                            "content to 'second'.",
                )),
                _permission_flow(m, "18", "Edit", decision="allow"),
                Step(wait=_wait_result(m, "18")),
            ],
        )
    )

    # 19: Comm with attachment, reply as attachment.
    scenarios.append(
        Scenario(
            id=19,
            title="Comm with attachment, reply as attachment",
            steps=[
                Step(action=partial(
                    actions.send_comm, legion_id=legion_id, to_minion_id=minion_id,
                    content="Please write a haiku and send it back as an attachment.",
                )),
                Step(wait=WaitSpec(
                    predicate=predicates.resource_registered(minion_id),
                    description="scenario 19: haiku attachment registered as a resource",
                    timeout_s=60.0,
                )),
                Step(wait=WaitSpec(
                    predicate=predicates.minion_comm_notification(TEST_MINION_NAME),
                    description="scenario 19: comm reply with attachment from Test Minion",
                    timeout_s=60.0,
                )),
            ],
        )
    )

    # 20: User file upload, read it.
    scenarios.append(
        Scenario(
            id=20,
            title="User file upload, read it",
            steps=[
                # Read is auto-allowed for the attachment path even in default mode, so
                # there's no permission prompt to wait for.
                Step(action=partial(actions.set_permission_mode, session_id=m, mode="acceptEdits")),
                Step(action=partial(
                    actions.send_prompt_with_upload, session_id=m,
                    file_path=scratch_repo / "upload_test.txt",
                    message="Use the Read tool exactly once to read the attached file "
                            "and reply with its contents.",
                )),
                Step(wait=_wait_result(m, "20")),
            ],
        )
    )

    # 21: Full app restart mid-session.
    scenarios.append(
        Scenario(
            id=21,
            title="Full app restart mid-session",
            steps=[
                Step(action=actions.restart_server),
                Step(action=partial(actions.wait_for_server_ready, timeout=120.0)),
                # Sessions come back in `created` after an app restart (the frontend
                # auto-starts one when it's selected); prompting before it's active 409s.
                Step(action=partial(actions.ensure_session_active, session_id=m)),
                Step(action=partial(
                    actions.send_prompt, session_id=m,
                    message="Reply with the single word 'resumed' and nothing else.",
                )),
                Step(wait=_wait_result(m, "21", timeout_s=120.0)),
            ],
        )
    )

    # 22 (new): Plan mode.
    scenarios.append(
        Scenario(
            id=22,
            title="Plan mode",
            steps=[
                Step(action=partial(actions.set_permission_mode, session_id=m, mode="plan")),
                Step(wait=WaitSpec(
                    predicate=predicates.permission_mode_state_change(m, "plan"),
                    description="scenario 22: plan mode engaged",
                    timeout_s=30.0,
                )),
                Step(action=partial(
                    actions.send_prompt, session_id=m,
                    message="Propose a one-step plan to create a file named plan_test.md, "
                            "then call ExitPlanMode to present it.",
                )),
                Step(
                    wait=WaitSpec(
                        predicate=predicates.tool_call_awaiting_permission(m, "ExitPlanMode"),
                        description="scenario 22: ExitPlanMode prompt",
                        timeout_s=60.0,
                    ),
                    action=partial(actions.answer_permission, session_id=m, decision="allow"),
                ),
                Step(wait=WaitSpec(
                    predicate=predicates.system_subtype(m, "permission_mode_change"),
                    description="scenario 22: permission mode auto-reset after plan approval",
                    timeout_s=30.0,
                )),
                Step(wait=_wait_result(m, "22")),
            ],
        )
    )

    return scenarios
