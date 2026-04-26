"""
Configuration manager for Claude Code WebUI.

Handles application config file creation, loading, and validation.
Config file location: ~/.config/cc_webui/config.json
"""

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

CONFIG_DIR = Path.home() / ".config" / "cc_webui"
CONFIG_FILE = CONFIG_DIR / "config.json"

LOCALHOST_ADDRESSES = {"127.0.0.1", "localhost", "::1"}

# ---------------------------------------------------------------------------
# Pricing configuration
# ---------------------------------------------------------------------------

# Canonical model IDs for built-in defaults
_SONNET_4_6 = "claude-sonnet-4-6"
_OPUS_4_7 = "claude-opus-4-7"
_HAIKU_4_5 = "claude-haiku-4-5"

# Short-name aliases → canonical model ID
_MODEL_ALIASES: dict[str, str] = {
    "sonnet": _SONNET_4_6,
    "sonnet-4-6": _SONNET_4_6,
    "opus": _OPUS_4_7,
    "opus-4-7": _OPUS_4_7,
    "opusplan": _OPUS_4_7,
    "haiku": _HAIKU_4_5,
    "haiku-4-5": _HAIKU_4_5,
}


def normalize_model_id(model: str | None) -> str | None:
    """Resolve a model alias or partial name to its canonical form."""
    if model is None:
        return None
    key = model.lower().strip()
    return _MODEL_ALIASES.get(key, model)


def _default_rates() -> "dict[str, ModelRates]":
    """Return built-in pricing defaults (USD per 1 M tokens, as of 2026-04)."""
    return {
        _SONNET_4_6: ModelRates(input=3.0, output=15.0, cache_write=3.75, cache_read=0.30),
        _OPUS_4_7: ModelRates(input=15.0, output=75.0, cache_write=18.75, cache_read=1.50),
        _HAIKU_4_5: ModelRates(input=0.80, output=4.0, cache_write=1.0, cache_read=0.08),
    }


@dataclass
class ModelRates:
    """USD per 1 M tokens for a single model."""

    input: float = 0.0
    output: float = 0.0
    cache_write: float = 0.0
    cache_read: float = 0.0

    @classmethod
    def from_dict(cls, data: dict) -> "ModelRates":
        return cls(
            input=float(data.get("input", 0.0)),
            output=float(data.get("output", 0.0)),
            cache_write=float(data.get("cache_write", 0.0)),
            cache_read=float(data.get("cache_read", 0.0)),
        )

    def to_dict(self) -> dict:
        return {
            "input": self.input,
            "output": self.output,
            "cache_write": self.cache_write,
            "cache_read": self.cache_read,
        }


@dataclass
class PricingConfig:
    """Per-model pricing table and default model fallback."""

    rates: dict[str, ModelRates] = field(default_factory=_default_rates)
    default_model: str = _SONNET_4_6

    @classmethod
    def from_dict(cls, data: dict) -> "PricingConfig":
        raw_rates = data.get("rates", {})
        rates = {
            model_id: ModelRates.from_dict(r)
            for model_id, r in raw_rates.items()
            if isinstance(r, dict)
        }
        # Merge with defaults so unknown models in config don't lose built-in entries
        merged = _default_rates()
        merged.update(rates)
        return cls(
            rates=merged,
            default_model=data.get("default_model", _SONNET_4_6),
        )

    def to_dict(self) -> dict:
        return {
            "_comment": (
                "Pricing rates in USD per 1 M tokens. "
                "Edit to override without restarting the server. "
                "Cost is recomputed at query time from the current file."
            ),
            "default_model": self.default_model,
            "rates": {model_id: r.to_dict() for model_id, r in self.rates.items()},
        }

    def get_rates(self, model: str | None) -> tuple[ModelRates | None, bool]:
        """Return (ModelRates, rates_known).

        Tries the given model, then its normalized form, then the default_model.
        Returns (None, False) when no match exists at all.
        """
        if model:
            if model in self.rates:
                return self.rates[model], True
            canonical = normalize_model_id(model)
            if canonical and canonical in self.rates:
                return self.rates[canonical], True
        # Try default
        if self.default_model in self.rates:
            return self.rates[self.default_model], False
        return None, False


def compute_cost(rates: ModelRates, counts: dict[str, Any]) -> float:
    """Compute estimated cost in USD given ModelRates and token counts dict."""
    m = 1_000_000.0
    return (
        rates.input * int(counts.get("input_tokens") or 0) / m
        + rates.output * int(counts.get("output_tokens") or 0) / m
        + rates.cache_write * int(counts.get("cache_write_tokens") or 0) / m
        + rates.cache_read * int(counts.get("cache_read_tokens") or 0) / m
    )


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
class IdleWatchdogConfig:
    enabled: bool = False
    timeout_seconds: int = 300


@dataclass
class ErrorRateWatchdogConfig:
    enabled: bool = False
    min_calls: int = 10
    threshold: float = 0.6


@dataclass
class WatchdogConfig:
    """Watchdog service configuration. All defaults OFF — templates opt in."""
    enabled: bool = False
    poll_interval_seconds: int = 60
    idle: IdleWatchdogConfig = field(default_factory=IdleWatchdogConfig)
    error_rate: ErrorRateWatchdogConfig = field(default_factory=ErrorRateWatchdogConfig)


@dataclass
class SecretsConfig:
    """Configuration for the secrets keyring backend (issue #827)."""
    backend_override: str | None = None  # Force a specific backend (e.g., "CryptFileKeyring")
    keyring_service_name: str = "cc_webui"  # Service name used in keyring calls


@dataclass
class AppConfig:
    networking: NetworkingConfig = field(default_factory=NetworkingConfig)
    features: FeaturesConfig = field(default_factory=FeaturesConfig)
    proxy: ProxyConfig = field(default_factory=ProxyConfig)
    legion: LegionConfig = field(default_factory=LegionConfig)
    background_calls: BackgroundCallsConfig = field(default_factory=BackgroundCallsConfig)
    watchdog: WatchdogConfig = field(default_factory=WatchdogConfig)
    secrets: SecretsConfig = field(default_factory=SecretsConfig)
    pricing: PricingConfig = field(default_factory=PricingConfig)

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
        watchdog_data = data.get("watchdog", {})
        idle_data = watchdog_data.get("idle", {})
        idle = IdleWatchdogConfig(
            enabled=idle_data.get("enabled", False),
            timeout_seconds=idle_data.get("timeout_seconds", 300),
        )
        error_rate_data = watchdog_data.get("error_rate", {})
        error_rate = ErrorRateWatchdogConfig(
            enabled=error_rate_data.get("enabled", False),
            min_calls=error_rate_data.get("min_calls", 10),
            threshold=error_rate_data.get("threshold", 0.6),
        )
        watchdog = WatchdogConfig(
            enabled=watchdog_data.get("enabled", False),
            poll_interval_seconds=watchdog_data.get("poll_interval_seconds", 60),
            idle=idle,
            error_rate=error_rate,
        )
        secrets_data = data.get("secrets", {})
        secrets = SecretsConfig(
            backend_override=secrets_data.get("backend_override", None),
            keyring_service_name=secrets_data.get("keyring_service_name", "cc_webui"),
        )
        pricing_data = data.get("pricing", {})
        pricing = PricingConfig.from_dict(pricing_data) if pricing_data else PricingConfig()
        return cls(
            networking=networking,
            features=features,
            proxy=proxy,
            legion=legion,
            background_calls=background_calls,
            watchdog=watchdog,
            secrets=secrets,
            pricing=pricing,
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
            "watchdog": {
                "_comment": "Session watchdog service. All defaults OFF — templates opt in.",
                "enabled": self.watchdog.enabled,
                "poll_interval_seconds": self.watchdog.poll_interval_seconds,
                "idle": {
                    "enabled": self.watchdog.idle.enabled,
                    "timeout_seconds": self.watchdog.idle.timeout_seconds,
                },
                "error_rate": {
                    "enabled": self.watchdog.error_rate.enabled,
                    "min_calls": self.watchdog.error_rate.min_calls,
                    "threshold": self.watchdog.error_rate.threshold,
                },
            },
            "secrets": {
                "backend_override": self.secrets.backend_override,
                "keyring_service_name": self.secrets.keyring_service_name,
            },
            "pricing": self.pricing.to_dict(),
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
