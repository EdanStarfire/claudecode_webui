"""
Tests for schedule status migration (Issue #1416).

Covers:
- Migration of on-disk "cancelled" records to "paused" on load
- delete_schedules_for_minion removes records outright (not status-flip)
- MCP delete_schedule tool (renamed from cancel_schedule)
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.legion.scheduler_service import SchedulerService
from src.models.schedule_models import Schedule, ScheduleStatus


def _make_system(tmp_path: Path):
    coordinator = MagicMock()
    coordinator.data_dir = tmp_path
    system = MagicMock()
    system.session_coordinator = coordinator
    system.history_rotator = None
    return system


def _make_scheduler(system) -> SchedulerService:
    scheduler = SchedulerService(system)
    scheduler._schedule_broadcast_callback = None
    return scheduler


def _make_schedule_dict(
    schedule_id: str,
    legion_id: str,
    minion_id: str,
    status: str = "active",
) -> dict:
    return {
        "schedule_id": schedule_id,
        "legion_id": legion_id,
        "minion_id": minion_id,
        "minion_name": "TestMinion",
        "name": "Test Schedule",
        "cron_expression": "0 * * * *",
        "prompt": "hello",
        "reset_session": False,
        "status": status,
        "max_retries": 3,
        "timeout_seconds": 3600,
        "created_at": 1000000.0,
        "updated_at": 1000000.0,
        "next_run": 1000060.0,
        "last_run": None,
        "last_status": None,
        "execution_count": 0,
        "failure_count": 0,
        "schedule_type": "prompt",
        "script_command": None,
        "script_timeout_seconds": 60,
        "last_stdout": None,
        "last_stderr": None,
        "last_exit_code": None,
    }


class TestLoadMigratesCancelledToPaused:
    @pytest.mark.asyncio
    async def test_cancelled_record_loaded_as_paused(self, tmp_path):
        """On-disk 'cancelled' status is coerced to 'paused' on load."""
        system = _make_system(tmp_path)
        scheduler = _make_scheduler(system)
        legion_id = "leg-migrate-1"
        schedule_id = str(uuid.uuid4())

        # Write a schedules.json with a cancelled record
        legion_dir = tmp_path / "legions" / legion_id
        legion_dir.mkdir(parents=True)
        schedules_file = legion_dir / "schedules.json"
        schedules_file.write_text(
            json.dumps([_make_schedule_dict(schedule_id, legion_id, "minion-1", status="cancelled")])
        )

        await scheduler._load_schedules(legion_id)

        assert schedule_id in scheduler._schedules
        loaded = scheduler._schedules[schedule_id]
        assert loaded.status == ScheduleStatus.PAUSED

    @pytest.mark.asyncio
    async def test_cancelled_record_persisted_as_paused(self, tmp_path):
        """After migration, schedules.json is rewritten with status='paused'."""
        system = _make_system(tmp_path)
        scheduler = _make_scheduler(system)
        legion_id = "leg-migrate-2"
        schedule_id = str(uuid.uuid4())

        legion_dir = tmp_path / "legions" / legion_id
        legion_dir.mkdir(parents=True)
        schedules_file = legion_dir / "schedules.json"
        schedules_file.write_text(
            json.dumps([_make_schedule_dict(schedule_id, legion_id, "minion-1", status="cancelled")])
        )

        await scheduler._load_schedules(legion_id)

        persisted = json.loads(schedules_file.read_text())
        assert len(persisted) == 1
        assert persisted[0]["status"] == "paused"

    @pytest.mark.asyncio
    async def test_active_record_unchanged(self, tmp_path):
        """Active records are not affected by migration logic."""
        system = _make_system(tmp_path)
        scheduler = _make_scheduler(system)
        legion_id = "leg-migrate-3"
        schedule_id = str(uuid.uuid4())

        legion_dir = tmp_path / "legions" / legion_id
        legion_dir.mkdir(parents=True)
        schedules_file = legion_dir / "schedules.json"
        schedules_file.write_text(
            json.dumps([_make_schedule_dict(schedule_id, legion_id, "minion-1", status="active")])
        )

        await scheduler._load_schedules(legion_id)

        loaded = scheduler._schedules[schedule_id]
        assert loaded.status == ScheduleStatus.ACTIVE

    @pytest.mark.asyncio
    async def test_from_dict_handles_cancelled_string(self):
        """Schedule.from_dict coerces 'cancelled' without raising ValueError."""
        data = _make_schedule_dict("s1", "leg-1", "m1", status="cancelled")
        schedule = Schedule.from_dict(data)
        assert schedule.status == ScheduleStatus.PAUSED


class TestDeleteSchedulesForMinion:
    @pytest.mark.asyncio
    async def test_delete_removes_records(self, tmp_path):
        """delete_schedules_for_minion removes matching schedules from memory and disk."""
        system = _make_system(tmp_path)
        scheduler = _make_scheduler(system)
        legion_id = "leg-del-1"
        minion_id = "minion-del-1"
        sched_id_1 = str(uuid.uuid4())
        sched_id_2 = str(uuid.uuid4())
        sched_id_other = str(uuid.uuid4())

        legion_dir = tmp_path / "legions" / legion_id
        legion_dir.mkdir(parents=True)

        # Manually inject schedules into in-memory dict
        scheduler._schedules[sched_id_1] = Schedule(
            schedule_id=sched_id_1, legion_id=legion_id, name="S1",
            cron_expression="0 * * * *", prompt="p", minion_id=minion_id,
        )
        scheduler._schedules[sched_id_2] = Schedule(
            schedule_id=sched_id_2, legion_id=legion_id, name="S2",
            cron_expression="0 * * * *", prompt="p", minion_id=minion_id,
        )
        scheduler._schedules[sched_id_other] = Schedule(
            schedule_id=sched_id_other, legion_id=legion_id, name="S3",
            cron_expression="0 * * * *", prompt="p", minion_id="other-minion",
        )

        deleted = await scheduler.delete_schedules_for_minion(minion_id)

        assert deleted == 2
        assert sched_id_1 not in scheduler._schedules
        assert sched_id_2 not in scheduler._schedules
        assert sched_id_other in scheduler._schedules

    @pytest.mark.asyncio
    async def test_delete_zero_when_no_match(self, tmp_path):
        """delete_schedules_for_minion returns 0 when no schedules match."""
        system = _make_system(tmp_path)
        scheduler = _make_scheduler(system)
        legion_id = "leg-del-2"
        scheduler._schedules["s-other"] = Schedule(
            schedule_id="s-other", legion_id=legion_id, name="S",
            cron_expression="0 * * * *", prompt="p", minion_id="other-minion",
        )
        legion_dir = tmp_path / "legions" / legion_id
        legion_dir.mkdir(parents=True)

        deleted = await scheduler.delete_schedules_for_minion("nonexistent-minion")
        assert deleted == 0

    @pytest.mark.asyncio
    async def test_delete_persists_reduced_array(self, tmp_path):
        """After delete, schedules.json no longer contains the deleted record."""
        system = _make_system(tmp_path)
        scheduler = _make_scheduler(system)
        legion_id = "leg-del-3"
        minion_id = "minion-del-3"
        sched_id = str(uuid.uuid4())

        legion_dir = tmp_path / "legions" / legion_id
        legion_dir.mkdir(parents=True)

        scheduler._schedules[sched_id] = Schedule(
            schedule_id=sched_id, legion_id=legion_id, name="ToDelete",
            cron_expression="0 * * * *", prompt="p", minion_id=minion_id,
        )

        await scheduler.delete_schedules_for_minion(minion_id)

        schedules_file = tmp_path / "legions" / legion_id / "schedules.json"
        persisted = json.loads(schedules_file.read_text())
        assert persisted == []


class TestDeleteScheduleMCPTool:
    @pytest.mark.asyncio
    async def test_delete_schedule_removes_schedule(self, tmp_path):
        """_handle_delete_schedule deletes the schedule for the owning minion."""
        from src.legion.mcp.legion_mcp_tools import LegionMCPTools

        system = _make_system(tmp_path)
        scheduler = AsyncMock()
        schedule = Schedule(
            schedule_id="sched-mcp-1", legion_id="leg-1", name="MySchedule",
            cron_expression="0 * * * *", prompt="p", minion_id="minion-mcp-1",
        )
        scheduler.get_schedule = AsyncMock(return_value=schedule)
        scheduler.delete_schedule = AsyncMock(return_value=True)
        system.scheduler_service = scheduler

        mcp_tools = LegionMCPTools(system)

        result = await mcp_tools._handle_delete_schedule({
            "_from_minion_id": "minion-mcp-1",
            "schedule_id": "sched-mcp-1",
        })

        assert not result.get("is_error", False)
        scheduler.delete_schedule.assert_awaited_once_with("sched-mcp-1")

    @pytest.mark.asyncio
    async def test_delete_schedule_rejects_non_owned(self, tmp_path):
        """_handle_delete_schedule rejects schedules owned by another minion."""
        from src.legion.mcp.legion_mcp_tools import LegionMCPTools

        system = _make_system(tmp_path)
        scheduler = AsyncMock()
        schedule = Schedule(
            schedule_id="sched-mcp-2", legion_id="leg-1", name="OtherSchedule",
            cron_expression="0 * * * *", prompt="p", minion_id="other-minion",
        )
        scheduler.get_schedule = AsyncMock(return_value=schedule)
        scheduler.delete_schedule = AsyncMock()
        system.scheduler_service = scheduler

        mcp_tools = LegionMCPTools(system)

        result = await mcp_tools._handle_delete_schedule({
            "_from_minion_id": "minion-mcp-2",
            "schedule_id": "sched-mcp-2",
        })

        assert result.get("is_error") is True
        scheduler.delete_schedule.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_delete_schedule_missing_id(self, tmp_path):
        """_handle_delete_schedule returns error when schedule_id is missing."""
        from src.legion.mcp.legion_mcp_tools import LegionMCPTools

        system = _make_system(tmp_path)
        mcp_tools = LegionMCPTools(system)

        result = await mcp_tools._handle_delete_schedule({
            "_from_minion_id": "minion-mcp-3",
            "schedule_id": "",
        })

        assert result.get("is_error") is True
