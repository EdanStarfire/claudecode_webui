"""
Storage utilities for alphabetized JSON writes and pre-migration backup.

Used by template_manager, session_manager, and profile_manager to ensure
consistent, diff-friendly JSON output (issue #1230).
"""

import json
import shutil
from datetime import datetime
from pathlib import Path


def write_alphabetized_json(path: Path, data: dict, indent: int = 2) -> None:
    """Write dict to path as JSON with alphabetized keys (recursive via sort_keys=True)."""
    payload = json.dumps(data, indent=indent, sort_keys=True, ensure_ascii=False)
    path.write_text(payload, encoding="utf-8")


def _load_safe(path: Path) -> dict:
    """Load JSON from path, returning {} on any error."""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def backup_legacy_dir_once(
    source_dir: Path,
    marker_subdir: str = ".legacy_pre_1230",
) -> bool:
    """Copy all files from source_dir into <source_dir>/<marker>/<ts>/ once.

    No-op if marker_subdir already exists in source_dir (idempotent).
    Only backs up if there is at least one unmigrated JSON file (no 'config' key).
    Returns True if a backup was created.
    """
    marker = source_dir / marker_subdir
    if marker.exists():
        return False

    # Only back up if there is actually unmigrated data to protect
    has_legacy = any(
        "config" not in _load_safe(p)
        for p in source_dir.glob("*.json")
    )
    if not has_legacy:
        return False

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    target = marker / timestamp
    target.mkdir(parents=True, exist_ok=True)
    for path in source_dir.iterdir():
        if path.is_file():
            shutil.copy2(path, target / path.name)
    return True
