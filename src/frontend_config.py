"""Frontend-owned config sections (issue #498): networking + backend_connection.

Frontend owns only these two sections of the shared ~/.config/cc_webui/config.json
— Backend owns everything else (features, legion, watchdog, pricing,
background-calls, proxy, secrets). Reads/writes only its own keys on save,
leaving the rest of the file (Backend's sections) untouched, so both processes
can safely share one file without clobbering each other's writes.
"""

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

CONFIG_FILE = Path.home() / ".config" / "cc_webui" / "config.json"
LOCALHOST_ADDRESSES = {"127.0.0.1", "localhost", "::1"}


@dataclass
class NetworkingConfig:
    allow_network_binding: bool = False
    acknowledged_risk: bool = False

    @property
    def network_binding_allowed(self) -> bool:
        return self.allow_network_binding and self.acknowledged_risk


@dataclass
class BackendConnectionConfig:
    """Manual remote-Backend override, if set via config instead of CLI (Phase 3)."""
    remote_backend_url: str | None = None
    remote_backend_token: str | None = None


@dataclass
class FrontendConfig:
    networking: NetworkingConfig = field(default_factory=NetworkingConfig)
    backend_connection: BackendConnectionConfig = field(default_factory=BackendConnectionConfig)

    def to_dict(self) -> dict:
        return {
            "networking": asdict(self.networking),
            "backend_connection": asdict(self.backend_connection),
        }


def ensure_config_file(config_file: Path = CONFIG_FILE) -> Path:
    """Create config directory and file with safe defaults if missing."""
    if config_file.exists():
        return config_file
    config_file.parent.mkdir(parents=True, exist_ok=True)
    config_file.write_text(json.dumps({}, indent=2) + "\n")
    print(f"Created default config file: {config_file}")
    return config_file


def load_frontend_config(config_file: Path = CONFIG_FILE) -> FrontendConfig:
    """Load just the networking/backend_connection sections. Safe defaults on malformed JSON."""
    try:
        data = json.loads(config_file.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return FrontendConfig()
    net = data.get("networking", {})
    bc = data.get("backend_connection", {})
    return FrontendConfig(
        networking=NetworkingConfig(
            allow_network_binding=net.get("allow_network_binding", False),
            acknowledged_risk=net.get("acknowledged_risk", False),
        ),
        backend_connection=BackendConnectionConfig(
            remote_backend_url=bc.get("remote_backend_url"),
            remote_backend_token=bc.get("remote_backend_token"),
        ),
    )


def save_frontend_config(config: FrontendConfig, config_file: Path = CONFIG_FILE) -> None:
    """Write only networking/backend_connection, preserving Backend's sections untouched."""
    try:
        data = json.loads(config_file.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        data = {}
    data["networking"] = asdict(config.networking)
    data["backend_connection"] = asdict(config.backend_connection)
    config_file.parent.mkdir(parents=True, exist_ok=True)
    config_file.write_text(json.dumps(data, indent=2) + "\n")


def check_network_binding(host: str, config: FrontendConfig, config_file: Path = CONFIG_FILE) -> bool:
    """Validate that the Frontend's host binding is permitted by config.

    Returns True if binding is allowed, False if blocked. Prints an error when blocked.
    """
    if host in LOCALHOST_ADDRESSES:
        return True

    if config.networking.network_binding_allowed:
        return True

    print(f"""
ERROR: Network binding requires explicit configuration.

You attempted to start the Frontend API on interface: {host}

For security, binding to non-localhost addresses requires explicit opt-in.
Edit {config_file} and set both:
  "networking": {{
    "allow_network_binding": true,
    "acknowledged_risk": true
  }}
""")
    return False
