"""
Minion Template Data Model

Defines reusable configuration templates for minion creation with
pre-configured permissions, tools, and default settings.
"""

from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from typing import List, Optional, Dict, Any


@dataclass
class MinionTemplate:
    """
    Reusable configuration template for minion creation.

    Stores permission mode, allowed tools, and default settings
    that can be applied when creating new minions.
    """
    template_id: str
    name: str
    permission_mode: str  # default, acceptEdits, plan, bypassPermissions
    allowed_tools: Optional[List[str]] = None
    default_role: Optional[str] = None
    default_system_prompt: Optional[str] = None
    description: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    def __post_init__(self):
        """Initialize timestamps and ensure allowed_tools is a list."""
        if self.created_at is None:
            self.created_at = datetime.now(timezone.utc)
        if self.updated_at is None:
            self.updated_at = datetime.now(timezone.utc)
        if self.allowed_tools is None:
            self.allowed_tools = []

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        data = asdict(self)
        data['created_at'] = self.created_at.isoformat()
        data['updated_at'] = self.updated_at.isoformat()
        return data

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'MinionTemplate':
        """Create from dictionary loaded from JSON."""
        data['created_at'] = datetime.fromisoformat(data['created_at'])
        data['updated_at'] = datetime.fromisoformat(data['updated_at'])
        return cls(**data)
