"""Issue #2333: "restart required" tracking for model-related config changes on a running session.

A running session's launch-bound settings (provider routing mode, tier models, Docker isolation,
Auto Mode classifier model, the CLI's auto-compact window) are fixed when the CLI process starts.
The Provider Model itself IS live-switchable (see SessionCoordinator.set_provider_model), so a
change there is not a restart reason on its own.

The launcher records a ``LaunchSnapshot`` when a session starts; ``compute_restart_reasons``
diffs the current effective config against it and returns the full, human-readable reason list
(empty when nothing is pending). Reverting a change therefore clears its reason. The list is
runtime state: it is cleared on (re)start and is not persisted across a backend restart.
"""

from __future__ import annotations

from dataclasses import dataclass

from .litellm_proxy_manager import make_model_alias

TIER_FIELDS: tuple[str, ...] = (
    "provider_haiku_catalog_id", "provider_haiku_model_id",
    "provider_sonnet_catalog_id", "provider_sonnet_model_id",
    "provider_opus_catalog_id", "provider_opus_model_id",
    "provider_default_tier",
)

RELAY_NOT_ATTACHED_REASON = (
    "Relay attached: its tools are added when the session next starts. "
    "Footer, handle and policy edits apply immediately."
)

ROUTING_SINGLE = "single"
ROUTING_TIER = "tier"


def routing_kind(cfg) -> str | None:
    """How a config routes through the LiteLLM catalog: tier, single, or not at all (direct)."""
    if all(getattr(cfg, f, None) for f in TIER_FIELDS):
        return ROUTING_TIER
    if getattr(cfg, "provider_catalog_id", None) and getattr(cfg, "provider_model_id", None):
        return ROUTING_SINGLE
    return None


def single_alias(cfg) -> str | None:
    """The single-model catalog alias a config selects, or None when it is not single-routed."""
    if routing_kind(cfg) != ROUTING_SINGLE:
        return None
    return make_model_alias(cfg.provider_catalog_id, cfg.provider_model_id)


@dataclass
class LaunchSnapshot:
    """Launch-bound facts of the CLI process currently serving a session."""

    routing: str | None            # ROUTING_SINGLE / ROUTING_TIER / None (not LiteLLM-routed)
    docker_enabled: bool
    classifier_model: str | None   # raw (pre-vet) auto_mode_classifier_model
    tier_values: tuple             # TIER_FIELDS values at launch
    live_alias: str | None         # alias the session is serving NOW (single routing); updated on a live switch
    wants_compact_window: bool     # launcher derives CLAUDE_CODE_AUTO_COMPACT_WINDOW (no user extra_env override)
    compact_window: int | None     # the derived window emitted at launch (None = nothing emitted)
    relay_wanted: bool = False     # the relay MCP server was attached at launch (issue #2103)


def build_launch_snapshot(
    cfg, *, litellm_routed: bool, live_alias: str | None,
    wants_compact_window: bool, compact_window: int | None, relay_wanted: bool = False,
) -> LaunchSnapshot:
    return LaunchSnapshot(
        routing=routing_kind(cfg) if litellm_routed else None,
        docker_enabled=bool(cfg.docker_enabled),
        classifier_model=cfg.auto_mode_classifier_model,
        tier_values=tuple(getattr(cfg, f, None) for f in TIER_FIELDS),
        live_alias=live_alias,
        wants_compact_window=wants_compact_window,
        compact_window=compact_window,
        relay_wanted=relay_wanted,
    )


def compute_restart_reasons(
    snap: LaunchSnapshot,
    cfg,
    *,
    new_compact_window: int | None,
    alias_in_live_router: bool,
) -> list[str]:
    """Diff the current effective config ``cfg`` against the launch snapshot.

    ``new_compact_window`` is the auto-compact window the launcher WOULD derive for the config's
    current single alias; ``alias_in_live_router`` whether the running LiteLLM router serves it.
    """
    reasons: list[str] = []
    kind = routing_kind(cfg)

    # Issue #2103: the relay MCP server is attached once at launch. Everything else about the
    # relay (footer, handle, policies, switching or clearing relay_id) is read live per call.
    if getattr(cfg, "relay_id", None) and not snap.relay_wanted:
        reasons.append(RELAY_NOT_ATTACHED_REASON)

    if kind != snap.routing and not (snap.routing is None and kind is None):
        reasons.append(
            "Provider routing changed (provider/direct or per-tier routing): "
            "restart the session to apply."
        )
    elif tuple(getattr(cfg, f, None) for f in TIER_FIELDS) != snap.tier_values and (
        kind == ROUTING_TIER or snap.routing == ROUTING_TIER
    ):
        reasons.append("Per-tier provider models changed: restart the session to apply.")

    if bool(cfg.docker_enabled) != snap.docker_enabled:
        reasons.append("Docker isolation changed: restart the session to apply.")

    if cfg.auto_mode_classifier_model != snap.classifier_model:
        reasons.append("Auto Mode classifier model changed: restart the session to apply.")

    if snap.routing == ROUTING_SINGLE and kind == ROUTING_SINGLE:
        alias = single_alias(cfg)
        if alias != snap.live_alias:
            if not alias_in_live_router:
                reasons.append(
                    "Catalog change pending: restart the LiteLLM proxy, then restart the session."
                )
            else:
                # Saved but not switched live (e.g. config edited without the live-switch endpoint).
                reasons.append(
                    f"Provider model '{alias}' is saved but the session is still using "
                    f"'{snap.live_alias}': restart the session to apply."
                )
        elif snap.wants_compact_window and new_compact_window != snap.compact_window:
            reasons.append(
                "The new model's context window needs a different auto-compact window "
                f"({snap.compact_window} -> {new_compact_window}): restart the session to apply."
            )
    return reasons
