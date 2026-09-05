"""Config dataclasses shared verbatim by Frontend and Backend (issue #498 review finding).

NetworkingConfig used to be independently defined in both src/frontend_config.py and
backend/config_manager.py — same fields, same property, drifting by definition
whenever one copy was edited without the other.
"""

from dataclasses import dataclass


@dataclass
class NetworkingConfig:
    allow_network_binding: bool = False
    acknowledged_risk: bool = False

    @property
    def network_binding_allowed(self) -> bool:
        return self.allow_network_binding and self.acknowledged_risk
