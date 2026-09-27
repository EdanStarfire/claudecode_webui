#!/usr/bin/env python3
"""CLI entrypoint for the scripted scenario driver (issue #2038).

Runs all 22 scenarios in `scenario_driver/scenarios.py` against a live
Frontend API instance, then exports the resulting session's raw log as a
named fixture. See `scenarios.py`'s module docstring for how to add or
change a scenario.

## Prerequisites

- The Backend the target Frontend talks to must have been started with
  `--enable-session-recording` — the final export step 403s otherwise (a
  dev-only capability, not an end-user feature; see `backend/fixture_export.py`).
- The target host must be loopback/private, or pass `--allow-remote`
  explicitly (AC1's production guard — there's no other "is this prod"
  signal available from the API itself).
- For a real, non-mock run: the Backend process's own environment needs live
  Claude credentials already available to it (e.g. `claude auth login`
  already completed there) — this CLI never handles credentials itself, it's
  a plain HTTP client of the Frontend API.

## Usage

    uv run python -m backend.tools.scenario_driver_cli \\
        --url http://127.0.0.1:8001 --token <frontend-token> \\
        --scratch-repo /tmp/scenario-scratch \\
        --fixture-name 2026-09-23-primary

The exported fixture lands under `backend/tests/fixtures/raw/<fixture-name>/`
on whichever machine the *Backend* is running on (not necessarily the machine
this CLI runs on) — export happens server-side via
`POST /api/sessions/{id}/export-fixture`; this CLI never writes fixture files
itself.

## Resuming a failed run

Every step failure raises a message naming the scenario, the awaited event,
and the last events seen (AC5), and this CLI exits non-zero. Pass
`--checkpoint <path>` on the original run to have it write progress after
each completed scenario; on a rerun, pass `--resume <path>` (instead of
`--scratch-repo`) to reattach to the still-live session/minion by ID and
continue from `last_completed_scenario_id + 1` — see `runner.reattach()`'s
docstring for the "this assumes the instance is still alive between
invocations" tradeoff. `--from-scenario N` alone (no checkpoint) starts a
*fresh* run at scenario N, for skipping ahead manually without resuming state.
"""

import argparse
import asyncio
import sys
from pathlib import Path

from backend.tools.scenario_driver.context import DriverContext, UnsafeTargetError, guard_target
from backend.tools.scenario_driver.runner import ScenarioError, load_checkpoint, reattach, run
from backend.tools.scenario_driver.scenarios import build_scenarios
from backend.tools.scenario_driver.setup import bootstrap


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", required=True, help="Frontend API base URL")
    parser.add_argument("--token", default=None, help="Frontend API bearer token")
    parser.add_argument("--scratch-repo", required=True, type=Path, help="Scratch repository path")
    parser.add_argument("--fixture-name", required=True, help="Output fixture directory name")
    parser.add_argument("--from-scenario", type=int, default=1, help="Resume from this scenario id")
    parser.add_argument("--checkpoint", type=Path, default=None, help="Checkpoint file path")
    parser.add_argument("--resume", type=Path, default=None, help="Resume from this checkpoint file")
    parser.add_argument("--allow-remote", action="store_true", help="Bypass the loopback/private-host guard")
    return parser.parse_args(argv)


async def _main(argv: list[str]) -> int:
    args = _parse_args(argv)

    try:
        guard_target(args.url, allow_remote=args.allow_remote)
    except UnsafeTargetError as exc:
        print(f"Refusing to run: {exc}", file=sys.stderr)
        return 1

    ctx = DriverContext(base_url=args.url, token=args.token)
    setup = None
    try:
        if args.resume is not None:
            checkpoint = load_checkpoint(args.resume)
            setup = await reattach(ctx, checkpoint)
            from_scenario = checkpoint.last_completed_scenario_id + 1
        else:
            setup = await bootstrap(ctx, scratch_repo=args.scratch_repo)
            from_scenario = args.from_scenario

        scenarios = build_scenarios(
            main_session_id=setup.main_session_id,
            minion_id=setup.minion_id,
            legion_id=setup.project_id,
            scratch_repo=setup.scratch_repo,
        )

        try:
            await run(ctx, setup, scenarios, from_scenario=from_scenario, checkpoint_path=args.checkpoint)
        except ScenarioError as exc:
            print(str(exc), file=sys.stderr)
            return 1

        result = await ctx.post_json(
            f"/api/sessions/{setup.main_session_id}/export-fixture",
            json={"name": args.fixture_name},
        )
        if not result.get("success"):
            print("Export failed:", file=sys.stderr)
            for marker in result.get("missing_markers", []):
                print(f"  missing: {marker}", file=sys.stderr)
            return 1

        print(f"Export succeeded: {result.get('fixture_dir')}")
        for marker, present in result.get("markers", {}).items():
            print(f"  {'✔' if present else '✘'} {marker}")
        return 0
    finally:
        if setup is not None:
            await setup.stop_consumers()
        await ctx.aclose()


def main() -> None:
    sys.exit(asyncio.run(_main(sys.argv[1:])))


if __name__ == "__main__":
    main()
