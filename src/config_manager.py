"""
Configuration manager for Claude Code WebUI.

Handles application config file creation, loading, and validation.
Config file location: ~/.config/cc_webui/config.json
"""

import json
from dataclasses import dataclass, field
from pathlib import Path

CONFIG_DIR = Path.home() / ".config" / "cc_webui"
CONFIG_FILE = CONFIG_DIR / "config.json"

LOCALHOST_ADDRESSES = {"127.0.0.1", "localhost", "::1"}


@dataclass
class NetworkingConfig:
    allow_network_binding: bool = False
    acknowledged_risk: bool = False

    @property
    def network_binding_allowed(self) -> bool:
        return self.allow_network_binding and self.acknowledged_risk


@dataclass
class FeaturesConfig:
    skill_sync_enabled: bool = True


@dataclass
class ProxyConfig:
    proxy_image: str = "claude-proxy:local"  # Default proxy image for sessions that don't override


@dataclass
class LegionConfig:
    max_concurrent_minions: int = 20  # Global default max concurrent minions per Legion project


@dataclass
class BackgroundCallsConfig:
    """Suppression flags for ambient/background SDK calls.

    Defaults match a self-hosted/fleet deployment posture: minimize
    incidental API traffic. Per-session config can opt back in.
    """

    disable_auto_memory: bool = True
    disable_claudeai_mcp_servers: bool = True
    disable_background_tasks: bool = True
    disable_nonessential_traffic: bool = True
    disable_cron: bool = True
    disable_feedback_survey: bool = True
    disable_telemetry: bool = True
    subprocess_env_scrub: bool = False
    skip_version_check: bool = True
    dont_inherit_env: bool = False  # leaving off — breaks Docker/proxy flows


@dataclass
class AppConfig:
    networking: NetworkingConfig = field(default_factory=NetworkingConfig)
    features: FeaturesConfig = field(default_factory=FeaturesConfig)
    proxy: ProxyConfig = field(default_factory=ProxyConfig)
    legion: LegionConfig = field(default_factory=LegionConfig)
    background_calls: BackgroundCallsConfig = field(default_factory=BackgroundCallsConfig)

    @classmethod
    def from_dict(cls, data: dict) -> "AppConfig":
        networking_data = data.get("networking", {})
        networking = NetworkingConfig(
            allow_network_binding=networking_data.get("allow_network_binding", False),
            acknowledged_risk=networking_data.get("acknowledged_risk", False),
        )
        features_data = data.get("features", {})
        features = FeaturesConfig(
            skill_sync_enabled=features_data.get("skill_sync_enabled", True),
        )
        proxy_data = data.get("proxy", {})
        proxy = ProxyConfig(
            proxy_image=proxy_data.get("proxy_image", "claude-proxy:local"),
        )
        legion_data = data.get("legion", {})
        legion = LegionConfig(
            max_concurrent_minions=legion_data.get("max_concurrent_minions", 20),
        )
        bg_data = data.get("background_calls", {})
        background_calls = BackgroundCallsConfig(
            disable_auto_memory=bg_data.get("disable_auto_memory", True),
            disable_claudeai_mcp_servers=bg_data.get("disable_claudeai_mcp_servers", True),
            disable_background_tasks=bg_data.get("disable_background_tasks", True),
            disable_nonessential_traffic=bg_data.get("disable_nonessential_traffic", True),
            disable_cron=bg_data.get("disable_cron", True),
            disable_feedback_survey=bg_data.get("disable_feedback_survey", True),
            disable_telemetry=bg_data.get("disable_telemetry", True),
            subprocess_env_scrub=bg_data.get("subprocess_env_scrub", False),
            skip_version_check=bg_data.get("skip_version_check", True),
            dont_inherit_env=bg_data.get("dont_inherit_env", False),
        )
        return cls(
            networking=networking,
            features=features,
            proxy=proxy,
            legion=legion,
            background_calls=background_calls,
        )

    def to_dict(self) -> dict:
        return {
            "networking": {
                "_comment": (
                    "CAUTION: This exposes Claude Code to your local network,"
                    " potentially enabling remote access to untrusted users"
                ),
                "allow_network_binding": self.networking.allow_network_binding,
                "acknowledged_risk": self.networking.acknowledged_risk,
            },
            "features": {
                "skill_sync_enabled": self.features.skill_sync_enabled,
            },
            "proxy": {
                "proxy_image": self.proxy.proxy_image,
            },
            "legion": {
                "max_concurrent_minions": self.legion.max_concurrent_minions,
            },
            "background_calls": {
                "_comment": (
                    "Suppress ambient/background SDK API calls. All flags default ON"
                    " (suppression enabled) except dont_inherit_env (breaks Docker/proxy)."
                    " Per-session config can opt back in via auto_memory_mode, enable_claudeai_mcp_servers,"
                    " or extra_env."
                ),
                "disable_auto_memory": self.background_calls.disable_auto_memory,
                "disable_claudeai_mcp_servers": self.background_calls.disable_claudeai_mcp_servers,
                "disable_background_tasks": self.background_calls.disable_background_tasks,
                "disable_nonessential_traffic": self.background_calls.disable_nonessential_traffic,
                "disable_cron": self.background_calls.disable_cron,
                "disable_feedback_survey": self.background_calls.disable_feedback_survey,
                "disable_telemetry": self.background_calls.disable_telemetry,
                "subprocess_env_scrub": self.background_calls.subprocess_env_scrub,
                "skip_version_check": self.background_calls.skip_version_check,
                "dont_inherit_env": self.background_calls.dont_inherit_env,
            },
        }


def ensure_config_file(config_file: Path = CONFIG_FILE) -> Path:
    """Create config directory and file with safe defaults if missing.

    Returns the path to the config file.
    """
    if config_file.exists():
        return config_file

    config_file.parent.mkdir(parents=True, exist_ok=True)

    default_config = AppConfig()
    config_file.write_text(json.dumps(default_config.to_dict(), indent=2) + "\n")
    print(f"Created default config file: {config_file}")

    return config_file


def load_config(config_file: Path = CONFIG_FILE) -> AppConfig:
    """Load config from file. Returns safe defaults on malformed JSON."""
    try:
        data = json.loads(config_file.read_text())
        return AppConfig.from_dict(data)
    except (json.JSONDecodeError, ValueError) as e:
        print(f"Warning: Malformed config file at {config_file}: {e}")
        print("Using safe defaults (network binding disabled).")
        return AppConfig()
    except FileNotFoundError:
        return AppConfig()


def save_config(config: AppConfig, config_file: Path = CONFIG_FILE) -> None:
    """Save config to file."""
    config_file.parent.mkdir(parents=True, exist_ok=True)
    config_file.write_text(json.dumps(config.to_dict(), indent=2) + "\n")


def check_network_binding(host: str, config: AppConfig, config_file: Path = CONFIG_FILE) -> bool:
    """Validate that the host binding is permitted by config.

    Returns True if binding is allowed, False if blocked.
    Prints an error message when blocked.
    """
    if host in LOCALHOST_ADDRESSES:
        return True

    if config.networking.network_binding_allowed:
        return True

    print(f"""
ERROR: Network binding requires explicit configuration.

You attempted to start the server on interface: {host}

WARNING: This exposes Claude WebUI to your local network, potentially granting
anyone with network access full command execution privileges on your machine
with your credentials.

To enable network binding:

  1. Edit: ~/.config/cc_webui/config.json
  2. Set: "allow_network_binding": true
  3. Set: "acknowledged_risk": true
  4. Restart the server

File location: {config_file}
""")
    return False
