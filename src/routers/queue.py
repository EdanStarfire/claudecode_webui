"""Queue endpoints: /api/sessions/{session_id}/queue*"""

from fastapi import APIRouter, HTTPException, Request

from ..exception_handlers import handle_exceptions


def build_router(webui) -> APIRouter:
    router = APIRouter()

    @router.post("/api/sessions/{session_id}/queue-message")
    @handle_exceptions("enqueue message", value_error_status=400)
    async def enqueue_message(session_id: str, request: Request):
        """Enqueue a message for a session."""
        data = await request.json()
        content = data.get("content")
        if not content:
            raise HTTPException(status_code=400, detail="content is required")

        item = await webui.coordinator.enqueue_message(
            session_id=session_id,
            content=content,
            reset_session=data.get("reset_session"),
            metadata=data.get("metadata"),
        )

        return {
            "queue_id": item["queue_id"],
            "position": item["position"],
            "item": item,
        }

    @router.get("/api/sessions/{session_id}/queue")
    @handle_exceptions("get queue")
    async def get_queue(session_id: str, limit: int = 100, offset: int = 0):
        """List queue items for a session, paginated."""
        if not await webui.service.get_session_exists(session_id):
            raise HTTPException(status_code=404, detail="Session not found")
        result = await webui.coordinator.get_queue(session_id, limit=limit, offset=offset)
        return result

    @router.delete("/api/sessions/{session_id}/queue/{queue_id}")
    @handle_exceptions("cancel queue item")
    async def cancel_queue_item(session_id: str, queue_id: str):
        """Cancel a pending queue item."""
        item = await webui.coordinator.cancel_queue_item(session_id, queue_id)
        if not item:
            raise HTTPException(status_code=404, detail="Queue item not found or not pending")

        await webui._broadcast_queue_update(session_id, "cancelled", item)
        return {"item": item}

    @router.post("/api/sessions/{session_id}/queue/{queue_id}/requeue")
    @handle_exceptions("requeue item")
    async def requeue_item(session_id: str, queue_id: str):
        """Re-queue a sent or failed item at the front."""
        item = await webui.coordinator.requeue_item(session_id, queue_id)
        if not item:
            raise HTTPException(status_code=404, detail="Queue item not found or not in sent/failed state")

        await webui._broadcast_queue_update(session_id, "enqueued", item)
        return {"item": item}

    @router.delete("/api/sessions/{session_id}/queue")
    @handle_exceptions("clear queue")
    async def clear_queue(session_id: str):
        """Clear all pending queue items."""
        if not await webui.service.get_session_exists(session_id):
            raise HTTPException(status_code=404, detail="Session not found")
        count = await webui.coordinator.clear_queue(session_id)
        await webui._broadcast_queue_update(session_id, "cleared", {"count": count})
        return {"cancelled_count": count}

    @router.put("/api/sessions/{session_id}/queue/pause")
    @handle_exceptions("pause queue")
    async def pause_queue(session_id: str, request: Request):
        """Pause or resume the queue."""
        data = await request.json()
        paused = data.get("paused", True)
        success = await webui.coordinator.pause_queue(session_id, paused)
        if not success:
            raise HTTPException(status_code=400, detail="Failed to update queue pause state")

        action = "paused" if paused else "resumed"
        await webui._broadcast_queue_update(session_id, action, {"paused": paused})
        return {"paused": paused}

    @router.put("/api/sessions/{session_id}/queue/config")
    @handle_exceptions("update queue config")
    async def update_queue_config(session_id: str, request: Request):
        """Update queue configuration."""
        data = await request.json()
        success = await webui.coordinator.update_queue_config(session_id, data)
        if not success:
            raise HTTPException(status_code=400, detail="Failed to update queue config")
        return {"config": data}

    return router
