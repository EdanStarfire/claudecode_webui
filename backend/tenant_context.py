"""TenantContext: per-process tenant identity for the Backend (issue #2268).

Epic #1990 stage 8 — replaces the scattered data_dir/config_file fallback
expressions and import-time Path.home() module constants that used to stand in
for "this process's tenant" with one explicit object, threaded through
construction. A seam for #1818's eventual multi-backend model, not a retrofit
of one: one TenantContext is constructed once per Backend process (matching
today's one-Backend-process-per-user model), never resolved per-request and
never multi-tenant-within-one-process.
"""

from dataclasses import dataclass, field
from pathlib import Path

from shared.event_envelope import DEFAULT_BACKEND_ID

from .config_manager import AppConfig
from .config_manager import load_config as _load_config


@dataclass
class TenantContext:
    """This process's tenant identity. `data_dir`/`config_file` are always the
    final, already-resolved paths — callers resolve any "no explicit value
    given" fallback once, before constructing this, so every other call site
    can read `data_dir`/`config_file` (or call `load_config()`) unconditionally.
    """

    data_dir: Path
    config_file: Path
    host: str
    # Mirrors the data_dir/config_file override pattern above — nothing sets this
    # differently yet, but the field exists now so a future per-tenant home
    # override (#1818) doesn't need a signature change to land.
    home_dir: Path = field(default_factory=Path.home)
    backend_id: str = DEFAULT_BACKEND_ID

    def load_config(self) -> AppConfig:
        return _load_config(self.config_file)
