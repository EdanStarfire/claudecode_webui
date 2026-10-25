"""Outbound host-path display handling for API payloads (issue #2279).

Payload fields that carry a host path the browser has no need for are rewritten
relative to the session working directory (`relativize_to`). Fields the browser
legitimately displays or round-trips as absolute paths are registered in
`DISPLAY_PATH_FIELDS`, each with the reason it stays absolute; the T1 payload scan
(`backend/tests/test_issue_2279_payload_paths.py`) allows exactly these.

Outbound-only: nothing here accepts or validates inbound paths.
"""

from __future__ import annotations

import os
from pathlib import Path

# payload field -> why it legitimately stays an absolute host path
DISPLAY_PATH_FIELDS: dict[str, str] = {
    "session.working_directory": "shown in the session info view; archived sessions keep the recorded value",
    "project.working_directory": "shown in project overview/pills and edited in create/edit dialogs",
    "filesystem.browse": "the folder browser selects an absolute directory for new sessions/projects/schedules",
    "resource.file_path": "resource registry path, fixture-bound; out of scope for #2279",
    "upload.stored_path": "upload attachment path, fixture-bound; out of scope for #2279",
    "tool_use/message payloads": "tool cards display the exact path the model used; fixture-bound",
}


def relativize_to(workdir: str | os.PathLike, path: str | os.PathLike) -> tuple[str, bool]:
    """Return `(display_path, outside_workdir)` for `path` relative to `workdir`.

    Symlinks (in either argument), `..` segments and trailing slashes are resolved
    before the containment check, so a path that escapes via a symlink or `..` counts as
    outside. Inside paths come back as a POSIX-style relative path (`"."` for the
    workdir itself); outside paths are returned unchanged with `True`. An already
    relative `path` is interpreted against `workdir`.
    """
    raw = os.fspath(path)
    base = os.path.realpath(os.fspath(workdir))
    target = os.path.realpath(raw if os.path.isabs(raw) else os.path.join(base, raw))
    try:
        if os.path.commonpath([base, target]) != base:
            return raw, True
    except ValueError:  # different drives (Windows) or mixed absolute/relative
        return raw, True
    return Path(os.path.relpath(target, base)).as_posix(), False
