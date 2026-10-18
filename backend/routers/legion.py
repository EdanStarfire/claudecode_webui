"""Legion endpoints: timeline, hierarchy, comms, minions"""

import json
import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException

from shared.exception_handlers import handle_exceptions

from ..timestamp_utils import normalize_timestamp
from ._models import CommSendRequest, MinionCreateRequest, ReparentRequest


def build_router(webui) -> APIRouter:
    router = APIRouter()

    @router.get("/api/legions/{legion_id}/timeline")
    @handle_exceptions("get legion timeline")
    async def get_legion_timeline(legion_id: str, limit: int = 100, offset: int = 0):
        """Get Comms for legion timeline (all communications in the legion)"""
        # Read all comms from the main legion timeline
        legion_dir = webui.service.get_data_dir() / "legions" / legion_id
        if not legion_dir.exists():
            return {
                "comms": [],
                "total": 0,
                "limit": limit,
                "offset": offset
            }

        all_comms = []

        # Read from main timeline.jsonl (contains ALL comms)
        timeline_file = legion_dir / "timeline.jsonl"
        if timeline_file.exists():
            with open(timeline_file, encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    if line:
                        try:
                            comm_data = json.loads(line)
                            all_comms.append(comm_data)
                        except json.JSONDecodeError:
                            continue

        # Normalize timestamps to handle mixed string/float formats (backwards compatibility)
        for comm in all_comms:
            if 'timestamp' in comm:
                try:
                    comm['timestamp'] = normalize_timestamp(comm['timestamp'])
                except (ValueError, TypeError) as e:
                    import logging
                    logging.getLogger(__name__).warning(
                        f"Invalid timestamp in comm {comm.get('comm_id', 'unknown')}: {e}, using current time"
                    )
                    comm['timestamp'] = datetime.now(UTC).timestamp()

        # Sort by timestamp (newest first) and deduplicate
        all_comms.sort(key=lambda x: x.get('timestamp', 0.0), reverse=True)

        # Deduplicate by comm_id (since comms appear in both sender and receiver logs)
        seen_ids = set()
        unique_comms = []
        for comm in all_comms:
            comm_id = comm.get('comm_id')
            if comm_id and comm_id not in seen_ids:
                seen_ids.add(comm_id)
                unique_comms.append(comm)

        # Paginate
        total = len(unique_comms)
        paginated_comms = unique_comms[offset:offset + limit]

        return {
            "comms": paginated_comms,
            "total": total,
            "limit": limit,
            "offset": offset
        }

    @router.get("/api/legions/{legion_id}/hierarchy")
    @handle_exceptions("get legion hierarchy")
    async def get_legion_hierarchy(legion_id: str):
        """Get complete minion hierarchy with user at root (issue #313: universal Legion)"""
        # Issue #313: All projects support hierarchy - verify project exists
        if not await webui.service.validate_project_exists(legion_id):
            raise HTTPException(status_code=404, detail="Project not found")

        # Assemble hierarchy (returns empty children if no minions)
        hierarchy = await webui.service.assemble_minion_hierarchy(legion_id)
        if hierarchy is None:
            raise HTTPException(status_code=500, detail="Legion coordinator not available")

        return hierarchy

    @router.post("/api/legions/{legion_id}/comms")
    @handle_exceptions("send comm to legion")
    async def send_comm_to_legion(legion_id: str, request: CommSendRequest):
        """Send a Comm in the legion"""
        from backend.models.legion_models import Comm, CommType

        legion = await webui.service.get_legion(legion_id)
        if not legion:
            raise HTTPException(status_code=404, detail="Legion not found")

        # Look up minion name if targeting a minion (for historical display)
        to_minion_name = None
        if request.to_minion_id:
            to_minion_name = await webui.service.get_minion_name(request.to_minion_id)

        # Create Comm from user. auto_start=True (issue #2193 PR5 AC8): preserves
        # this endpoint's pre-AC8-enforcement observed behavior of auto-starting
        # a dormant target minion — deliver()'s _ensure_session_ready() now fails
        # fast on a dormant target unless auto_start is explicitly set.
        comm = Comm(
            comm_id=str(uuid.uuid4()),
            from_user=True,
            to_minion_id=request.to_minion_id,
            to_user=request.to_user,
            to_minion_name=to_minion_name,
            content=request.content,
            comm_type=CommType(request.comm_type),
            auto_start=True,
        )

        # Route the comm
        success = await webui.service.route_comm(comm)

        if success:
            return {"comm": comm.to_dict(), "success": True}
        else:
            raise HTTPException(status_code=500, detail="Failed to route comm")

    @router.post("/api/legions/{legion_id}/minions")
    @handle_exceptions("create minion", value_error_status=400)
    async def create_minion(legion_id: str, request: MinionCreateRequest):
        """Create a new minion in the project (issue #313: universal Legion)"""
        # Verify project exists (all projects support minions - issue #313)
        project_dict = await webui.service.get_legion_project(legion_id)
        if not project_dict:
            raise HTTPException(status_code=404, detail="Project not found")

        # Validate and normalize working directory
        from ..session_config import validate_and_normalize_working_directory
        working_dir = validate_and_normalize_working_directory(
            request.working_directory,
            str(project_dict.get("working_directory", ""))
        )

        # Create minion via OverseerController
        config = request.model_copy(update={"working_directory": str(working_dir)})
        minion_id = await webui.service.create_minion_for_user(
            legion_id=legion_id,
            name=request.name,
            config=config,
            role=request.role,
            capabilities=request.capabilities,
        )

        # Get the created minion info
        minion_info = await webui.service.get_minion_session(minion_id)

        return {
            "success": True,
            "minion_id": minion_id,
            "minion": minion_info,
        }

    @router.post("/api/legions/{legion_id}/minions/{minion_id}/reparent")
    @handle_exceptions("reparent minion", value_error_status=400)
    async def reparent_minion(legion_id: str, minion_id: str, request: ReparentRequest):
        """Move a minion to a new parent within the same legion (user-initiated)."""
        if not await webui.service.validate_project_exists(legion_id):
            raise HTTPException(status_code=404, detail="Project not found")

        result = await webui.service.reparent_minion(
            subject_id=minion_id,
            new_parent_id=request.new_parent_id,
            caller_id=None,
        )
        return result

    return router
