"""Scratch-repo reset and project/session/minion bootstrap (AC1, AC2).

`guard_target()` (context.py) is the production guard; this module handles
the rest of AC1/AC2's deterministic setup: a fixed scratch repository, a
recording-enabled main session, and a Test Minion whose fixed system prompt
makes its replies deterministic.
"""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from . import predicates
from .context import DriverContext
from .poll_consumer import PollConsumer, SharedEventBuffer, wait_for

TEST_MINION_NAME = "Test Minion"

TEST_MINION_SYSTEM_PROMPT = (
    "You are Test Minion. When you receive any comm, reply with exactly the "
    "text '42' and nothing else. If the comm asks you to write a haiku, write "
    "it to a file named attachment.txt with exactly that content, register it "
    "as a resource, then reply 'done'."
)

UPLOAD_FILE_CONTENT = "This is the scenario driver's fixed upload-test file (issue #2038).\n"


@dataclass
class DriverSetup:
    project_id: str
    main_session_id: str
    minion_id: str
    scratch_repo: Path
    buffer: SharedEventBuffer
    consumers: list[PollConsumer]

    async def stop_consumers(self) -> None:
        for consumer in self.consumers:
            await consumer.stop()


def reset_scratch_repo(scratch_repo: Path) -> None:
    """Resets the scratch repository to fixed contents (AC2). A git repo is
    reset via `git clean`/`git checkout`; anything else is wiped and rebuilt
    from scratch — either way, ends with exactly the fixed files the 22
    scenarios need to exist up front (currently just scenario 20's upload
    fixture; every other scenario creates its own files as it runs).
    """
    scratch_repo.mkdir(parents=True, exist_ok=True)
    if (scratch_repo / ".git").is_dir():
        subprocess.run(["git", "checkout", "--", "."], cwd=scratch_repo, check=False)
        subprocess.run(["git", "clean", "-fdx"], cwd=scratch_repo, check=True)
    else:
        for child in scratch_repo.iterdir():
            if child.is_dir():
                shutil.rmtree(child)
            else:
                child.unlink()

    (scratch_repo / "upload_test.txt").write_text(UPLOAD_FILE_CONTENT)


async def bootstrap(ctx: DriverContext, *, scratch_repo: Path, project_name: str = "scenario-driver") -> DriverSetup:
    """Creates the project, main recording-enabled session, and Test Minion
    (AC2), starting each stream's `PollConsumer` as early as possible — the
    Test Minion's specifically right after its own creation, before it's
    started, per AC4's edge case ("must be followed from creation, not only
    after the comm is sent").
    """
    reset_scratch_repo(scratch_repo)

    project = await ctx.post_json(
        "/api/projects", json={"name": project_name, "working_directory": str(scratch_repo)}
    )
    project_id = project["project"]["project_id"]

    buffer = SharedEventBuffer()
    consumers: list[PollConsumer] = []
    resume_index = 0

    ui_consumer = PollConsumer(stream="ui", base_url=ctx.base_url, token=ctx.token, buffer=buffer)
    ui_consumer.start()
    consumers.append(ui_consumer)

    main_session = await ctx.post_json(
        "/api/sessions",
        json={
            "project_id": project_id,
            "name": "scenario-driver-main",
            "recording_enabled": True,
            "enable_streaming_text": True,
            "permission_mode": "acceptEdits",
        },
    )
    main_session_id = main_session["session_id"]

    session_consumer = PollConsumer(
        stream="session", base_url=ctx.base_url, token=ctx.token, buffer=buffer, session_id=main_session_id
    )
    session_consumer.start()
    consumers.append(session_consumer)

    await ctx.post_empty(f"/api/sessions/{main_session_id}/start")
    # start_session() sets STARTING and returns immediately — the real SDK
    # calls mark_session_active() asynchronously once it's actually ready,
    # which is what flips this to ACTIVE (backend/session_manager.py). A
    # scenario's first send_prompt would otherwise race this and 409.
    _, resume_index = await wait_for(
        buffer, predicates.state_change(main_session_id, "active"),
        resume_index=resume_index, timeout=60.0,
        description="setup: main session became active",
    )

    minion_result = await ctx.post_json(
        f"/api/legions/{project_id}/minions",
        json={
            "name": TEST_MINION_NAME,
            "system_prompt": TEST_MINION_SYSTEM_PROMPT,
            "permission_mode": "acceptEdits",
        },
    )
    minion_id = minion_result["minion_id"]

    # AC4: attach the minion's own poll consumer immediately after creation,
    # before starting it — its own init/system messages must not be missed.
    minion_consumer = PollConsumer(
        stream="minion", base_url=ctx.base_url, token=ctx.token, buffer=buffer, session_id=minion_id
    )
    minion_consumer.start()
    consumers.append(minion_consumer)

    await ctx.post_empty(f"/api/sessions/{minion_id}/start")
    _, resume_index = await wait_for(
        buffer, predicates.state_change(minion_id, "active"),
        resume_index=resume_index, timeout=60.0,
        description="setup: Test Minion session became active",
    )

    return DriverSetup(
        project_id=project_id,
        main_session_id=main_session_id,
        minion_id=minion_id,
        scratch_repo=scratch_repo,
        buffer=buffer,
        consumers=consumers,
    )
