"""Filesystem browser endpoint: /api/filesystem/browse"""

import os
import platform
from pathlib import Path

from fastapi import APIRouter, HTTPException

from ..exception_handlers import handle_exceptions


def build_router(webui) -> APIRouter:
    router = APIRouter()

    @router.get("/api/filesystem/browse")
    @handle_exceptions("browse filesystem")
    async def browse_filesystem(path: str = None):
        """Browse filesystem directories"""
        # Default to user home directory if no path provided
        if not path:
            path = str(Path.home())

        # Resolve and validate path
        browse_path = Path(path).resolve()

        # Check if path exists and is a directory
        if not browse_path.exists():
            raise HTTPException(status_code=404, detail="Path does not exist")
        if not browse_path.is_dir():
            raise HTTPException(status_code=400, detail="Path is not a directory")

        # Get parent path (None if at root)
        parent_path = str(browse_path.parent) if browse_path.parent != browse_path else None

        # List directories only
        directories = []
        try:
            for entry in sorted(browse_path.iterdir()):
                if entry.is_dir():
                    # Skip hidden directories on Unix-like systems (optional)
                    if platform.system() != 'Windows' and entry.name.startswith('.'):
                        continue
                    directories.append({
                        "name": entry.name,
                        "path": str(entry.resolve())
                    })
        except PermissionError:
            # If we can't list the directory, return what we can
            pass

        return {
            "current_path": str(browse_path),
            "parent_path": parent_path,
            "directories": directories,
            "separator": os.sep
        }

    return router
