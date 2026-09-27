"""Orchestration, checkpointing, and error reporting (issue #2038).

Runs a scenario list in order against one shared event buffer, writing a
checkpoint after each scenario completes so `--resume` can reattach to the
still-live session/minion and continue rather than rerunning from scratch.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from .context import DriverContext
from .poll_consumer import PollConsumer, SharedEventBuffer, WaitTimeoutError, wait_for
from .scenarios import Scenario
from .setup import DriverSetup


class ScenarioError(Exception):
    """Names the scenario and the underlying cause — a `WaitTimeoutError`
    already carries the awaited event and the last events seen; any other
    exception (e.g. an action's HTTP call failing) is named just as clearly
    rather than crashing the whole run with no scenario context."""

    def __init__(self, scenario: Scenario, cause: Exception) -> None:
        self.scenario = scenario
        self.cause = cause
        super().__init__(f"Scenario {scenario.id} ({scenario.title!r}) failed: {cause}")


@dataclass
class Checkpoint:
    project_id: str
    main_session_id: str
    minion_id: str
    scratch_repo: str
    cursors: dict[str, int]
    last_completed_scenario_id: int

    def to_dict(self) -> dict:
        return {
            "project_id": self.project_id,
            "main_session_id": self.main_session_id,
            "minion_id": self.minion_id,
            "scratch_repo": self.scratch_repo,
            "cursors": self.cursors,
            "last_completed_scenario_id": self.last_completed_scenario_id,
        }

    @classmethod
    def from_dict(cls, data: dict) -> Checkpoint:
        return cls(
            project_id=data["project_id"],
            main_session_id=data["main_session_id"],
            minion_id=data["minion_id"],
            scratch_repo=data["scratch_repo"],
            cursors=data["cursors"],
            last_completed_scenario_id=data["last_completed_scenario_id"],
        )


def load_checkpoint(checkpoint_path: Path) -> Checkpoint:
    return Checkpoint.from_dict(json.loads(checkpoint_path.read_text()))


def _write_checkpoint(checkpoint_path: Path, setup: DriverSetup, last_completed_scenario_id: int) -> None:
    cursors = {consumer.stream: consumer.cursor for consumer in setup.consumers}
    checkpoint = Checkpoint(
        project_id=setup.project_id,
        main_session_id=setup.main_session_id,
        minion_id=setup.minion_id,
        scratch_repo=str(setup.scratch_repo),
        cursors=cursors,
        last_completed_scenario_id=last_completed_scenario_id,
    )
    checkpoint_path.write_text(json.dumps(checkpoint.to_dict(), indent=2))


async def reattach(ctx: DriverContext, checkpoint: Checkpoint) -> DriverSetup:
    """Resuming from a checkpoint reattaches to the still-live session/minion
    by ID rather than reconstructing full driver state — if the underlying
    instance was torn down, a full rerun from scenario 1 is required."""
    buffer = SharedEventBuffer()
    consumers = [
        PollConsumer(stream="ui", base_url=ctx.base_url, token=ctx.token, buffer=buffer),
        PollConsumer(
            stream="session", base_url=ctx.base_url, token=ctx.token, buffer=buffer,
            session_id=checkpoint.main_session_id,
        ),
        PollConsumer(
            stream="minion", base_url=ctx.base_url, token=ctx.token, buffer=buffer,
            session_id=checkpoint.minion_id,
        ),
    ]
    for consumer in consumers:
        consumer.cursor = checkpoint.cursors.get(consumer.stream, 0)
        consumer.start()

    return DriverSetup(
        project_id=checkpoint.project_id,
        main_session_id=checkpoint.main_session_id,
        minion_id=checkpoint.minion_id,
        scratch_repo=Path(checkpoint.scratch_repo),
        buffer=buffer,
        consumers=consumers,
    )


async def run(
    ctx: DriverContext,
    setup: DriverSetup,
    scenarios: list[Scenario],
    *,
    from_scenario: int = 1,
    checkpoint_path: Path | None = None,
) -> int:
    """Runs `scenarios` in order starting at `from_scenario`, returning the
    final buffer resume index. Writes a checkpoint after each scenario
    completes, if `checkpoint_path` is given."""
    resume_index = 0
    for scenario in scenarios:
        if scenario.id < from_scenario:
            continue
        for step in scenario.steps:
            matched = None
            if step.wait is not None:
                predicate = step.wait.predicate
                if step.wait.stream is not None:
                    stream, base_predicate = step.wait.stream, predicate
                    predicate = lambda tagged, s=stream, p=base_predicate: tagged.source == s and p(tagged)  # noqa: E731
                try:
                    matched, resume_index = await wait_for(
                        setup.buffer,
                        predicate,
                        resume_index=resume_index,
                        count=step.wait.count,
                        timeout=step.wait.timeout_s,
                        description=step.wait.description,
                    )
                except WaitTimeoutError as exc:
                    raise ScenarioError(scenario, exc) from exc
            if step.action is not None:
                try:
                    await step.action(ctx, matched)
                except Exception as exc:
                    raise ScenarioError(scenario, exc) from exc
        if checkpoint_path is not None:
            _write_checkpoint(checkpoint_path, setup, scenario.id)
    return resume_index
