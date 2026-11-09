"""Atomic JSON persistence for the inbound stores (tmp file + fsync + os.replace)."""

from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def atomic_write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.tmp")
    payload = json.dumps(data, ensure_ascii=False)
    with tmp.open("w", encoding="utf-8") as f:
        f.write(payload)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def load_json(path: Path) -> Any | None:
    """Parsed JSON, or None when the file is missing. A corrupt file is moved aside to
    ``<name>.corrupt-<ts>`` (logged) and treated as missing."""
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        aside = path.with_name(f"{path.name}.corrupt-{int(time.time())}")
        try:
            os.replace(path, aside)
        except OSError:
            pass
        logger.warning("Corrupt inbound state file %s moved aside; starting empty", path.name)
        return None
