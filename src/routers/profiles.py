"""Profile CRUD endpoints: /api/profiles/*"""

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse

from ..exception_handlers import handle_exceptions
from ..profile_manager import ProfileInUseError
from ._models import ProfileCreateRequest, ProfileUpdateRequest


def build_router(webui) -> APIRouter:
    router = APIRouter()

    # ---- Profile CRUD endpoints (issue #1062) ----

    @router.get("/api/profiles")
    @handle_exceptions("list profiles")
    async def list_profiles(area: str | None = None):
        """List all profiles, optionally filtered by area."""
        return await webui.service.list_profiles(area=area)

    @router.get("/api/profiles/{profile_id}")
    @handle_exceptions("get profile")
    async def get_profile(profile_id: str):
        """Get a specific profile by ID."""
        profile = await webui.service.get_profile(profile_id)
        if not profile:
            raise HTTPException(status_code=404, detail="Profile not found")
        return profile

    @router.post("/api/profiles", status_code=201)
    @handle_exceptions("create profile", value_error_status=400)
    async def create_profile(request: ProfileCreateRequest):
        """Create a new configuration profile."""
        return await webui.service.create_profile(
            name=request.name,
            area=request.area,
            config=request.config,
        )

    @router.put("/api/profiles/{profile_id}")
    @handle_exceptions("update profile", value_error_status=400)
    async def update_profile(profile_id: str, request: ProfileUpdateRequest):
        """Update an existing profile."""
        return await webui.service.update_profile(
            profile_id=profile_id,
            name=request.name,
            config=request.config,
        )

    @router.delete("/api/profiles/{profile_id}")
    @handle_exceptions("delete profile")
    async def delete_profile(profile_id: str):
        """Delete a profile (blocked if templates reference it)."""
        try:
            success = await webui.service.delete_profile(profile_id)
        except ProfileInUseError as e:
            return JSONResponse(
                status_code=409,
                content={
                    "error": "profile_in_use",
                    "message": str(e),
                    "blocking_templates": [
                        {"template_id": tid, "name": name}
                        for tid, name in zip(e.template_ids, e.template_names, strict=True)
                    ],
                },
            )
        if not success:
            raise HTTPException(status_code=404, detail="Profile not found")
        return {"deleted": True}

    return router
