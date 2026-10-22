"""
Pre-`self` construction helpers for SessionCoordinator.__init__ (issue #2267,
epic #1990 stage 7 residual).

`build_core_managers()` constructs the collaborators that depend only on
data_dir and/or each other (never on the coordinator instance itself), in
the same dependency order SessionCoordinator.__init__ built them in before
this extraction. `build_legion_system()` factors out LegionSystem's
AppConfig-loading and throwaway-storage-manager construction; it still
takes `coordinator` because LegionSystem's `session_port` is the
coordinator itself, so it can only run after `self` exists.

Collaborators that take `coordinator=self` at construction time (queue_processor,
delivery_gate, resource_mcp_tools, links_mcp_tools, tool_call_tracker,
message_pipeline, session_launcher, archive_coordinator, session_resources,
session_query) are NOT built here — a pre-`self` builder cannot construct
them, so they stay in SessionCoordinator.__init__ itself.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from backend.credential_vault import CredentialVault
from backend.hook_config_manager import HookConfigManager
from backend.mcp.oneshot_connection import McpOneshotConnector
from backend.mcp.shared_connection_manager import SharedMcpConnectionManager
from backend.mcp_config_manager import McpConfigManager
from backend.oauth_callback_listener_manager import OAuthCallbackListenerManager
from backend.oauth_manager import OAuthFlowManager
from backend.profile_manager import ProfileManager
from backend.provider_catalog import ProviderCatalogStore
from backend.template_manager import TemplateManager

from .data_storage import DataStorageManager
from .legion_system import LegionSystem
from .message_parser import MessageParser, MessageProcessor
from .oauth_refresh_manager import OAuthRefreshManager
from .oauth_secret_bundle_manager import OAuthSecretBundleManager
from .pending_store import PendingStore
from .project_manager import ProjectManager
from .queue_manager import QueueManager
from .secrets_service import SecretsService
from .session_manager import SessionManager
from .vault_refresh_manager import VaultRefreshManager

if TYPE_CHECKING:
    from .session_coordinator import SessionCoordinator
    from .tenant_context import TenantContext


@dataclass
class CoreManagers:
    """Collaborators SessionCoordinator.__init__ can construct before `self` exists."""

    session_manager: SessionManager
    template_manager: TemplateManager
    project_manager: ProjectManager
    message_parser: MessageParser
    message_processor: MessageProcessor
    credential_vault: CredentialVault
    profile_manager: ProfileManager
    mcp_config_manager: McpConfigManager
    hook_config_manager: HookConfigManager
    provider_catalog_store: ProviderCatalogStore
    oauth_manager: OAuthFlowManager
    oauth_refresh_manager: OAuthRefreshManager
    # Issue #1484: unwired — SessionCoordinator.__init__ still wires its
    # set_cfg_lookup() closure after `self` exists, since the closure
    # captures self.mcp_config_manager.
    shared_mcp_manager: SharedMcpConnectionManager
    mcp_oneshot_connector: McpOneshotConnector
    oauth_callback_listener_manager: OAuthCallbackListenerManager
    vault_refresh_manager: VaultRefreshManager
    secrets_service: SecretsService
    oauth_secret_bundle_manager: OAuthSecretBundleManager
    queue_manager: QueueManager
    pending_store: PendingStore


def build_core_managers(
    data_dir: Path,
    session_manager: SessionManager | None,
    template_manager: TemplateManager | None,
    host: str,
    tenant_context: TenantContext,
) -> CoreManagers:
    """Construct the ~18 pure/already-built-manager collaborators, in the
    same dependency order SessionCoordinator.__init__ built them in."""
    # Issue #2184: construct-if-not-given — see SessionCoordinator.__init__'s
    # own comment on this convention for session_manager/template_manager.
    session_manager = session_manager or SessionManager(data_dir)
    template_manager = template_manager or TemplateManager(data_dir)
    project_manager = ProjectManager(data_dir)
    message_parser = MessageParser()
    message_processor = MessageProcessor(message_parser)

    credential_vault = CredentialVault(data_dir)
    profile_manager = ProfileManager(data_dir)
    mcp_config_manager = McpConfigManager(data_dir)
    hook_config_manager = HookConfigManager(data_dir)
    provider_catalog_store = ProviderCatalogStore(data_dir, home_dir=tenant_context.home_dir)
    oauth_manager = OAuthFlowManager(data_dir)
    oauth_refresh_manager = OAuthRefreshManager(oauth_manager)

    shared_mcp_manager = SharedMcpConnectionManager(
        oauth_manager, oauth_refresh_manager, credential_vault,
    )
    mcp_oneshot_connector = McpOneshotConnector(oauth_manager, credential_vault)
    oauth_callback_listener_manager = OAuthCallbackListenerManager(oauth_manager, host=host)
    vault_refresh_manager = VaultRefreshManager(credential_vault)

    secrets_service = SecretsService(
        credential_vault,
        vault_refresh_manager,
        session_manager,
        template_manager,
        profile_manager,
        mcp_config_manager,
    )
    oauth_secret_bundle_manager = OAuthSecretBundleManager(
        credential_vault,
        oauth_manager,
        mcp_config_manager,
        vault_refresh_manager,
    )

    queue_manager = QueueManager()
    pending_store = PendingStore()

    return CoreManagers(
        session_manager=session_manager,
        template_manager=template_manager,
        project_manager=project_manager,
        message_parser=message_parser,
        message_processor=message_processor,
        credential_vault=credential_vault,
        profile_manager=profile_manager,
        mcp_config_manager=mcp_config_manager,
        hook_config_manager=hook_config_manager,
        provider_catalog_store=provider_catalog_store,
        oauth_manager=oauth_manager,
        oauth_refresh_manager=oauth_refresh_manager,
        shared_mcp_manager=shared_mcp_manager,
        mcp_oneshot_connector=mcp_oneshot_connector,
        oauth_callback_listener_manager=oauth_callback_listener_manager,
        vault_refresh_manager=vault_refresh_manager,
        secrets_service=secrets_service,
        oauth_secret_bundle_manager=oauth_secret_bundle_manager,
        queue_manager=queue_manager,
        pending_store=pending_store,
    )


def build_legion_system(
    coordinator: SessionCoordinator,
    data_dir: Path,
    template_manager: TemplateManager,
    tenant_context: TenantContext,
) -> LegionSystem:
    """Construct LegionSystem. Needs `coordinator` to exist first — its
    `session_port` is the coordinator itself — but the AppConfig-loading and
    throwaway-storage-manager construction around it carry no such
    dependency, so they're factored out here rather than left inline."""
    dummy_storage = DataStorageManager(data_dir / "legion_temp")
    app_config = tenant_context.load_config()
    return LegionSystem(
        session_port=coordinator,
        data_storage_manager=dummy_storage,
        template_manager=template_manager,
        default_max_minions=app_config.legion.max_concurrent_minions,
        history_retention=app_config.history_retention,
    )
