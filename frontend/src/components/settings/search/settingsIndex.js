// Static index of Application settings sections and their field labels.
// Used by SettingsSidebarSearch to filter which sections are shown.
// When adding a new field to a section tab, add a corresponding entry here.
export const settingsIndex = [
  // Features section (FeaturesTab)
  { section: 'features', fieldKey: 'skill_sync_enabled', label: 'Enable Skill Syncing' },
  { section: 'features', fieldKey: 'skill_sync',         label: 'Workflow Skill Sync' },
  { section: 'features', fieldKey: 'docker_proxy',       label: 'Docker Proxy' },
  { section: 'features', fieldKey: 'proxy_image',        label: 'Default Proxy Image' },
  { section: 'features', fieldKey: 'templates',          label: 'Templates' },

  // Notifications section (NotificationsTab)
  { section: 'notifications', fieldKey: 'soundEnabled',         label: 'Enable Notification Sounds' },
  { section: 'notifications', fieldKey: 'volume',               label: 'Volume' },
  { section: 'notifications', fieldKey: 'permissionSound',      label: 'Permission Sound' },
  { section: 'notifications', fieldKey: 'completionSound',      label: 'Completion Sound' },
  { section: 'notifications', fieldKey: 'errorSound',           label: 'Error Sound' },
  { section: 'notifications', fieldKey: 'browserNotifications', label: 'Browser Notifications' },
  { section: 'notifications', fieldKey: 'notifyOnCompletion',   label: 'Notify on Completion' },
  { section: 'notifications', fieldKey: 'notifyOnPermission',   label: 'Notify on Permission' },
  { section: 'notifications', fieldKey: 'notifyOnError',        label: 'Notify on Error' },

  // Read Aloud section (ReadAloudTab)
  { section: 'read-aloud', fieldKey: 'tts_enabled', label: 'Read Aloud' },
  { section: 'read-aloud', fieldKey: 'voice',        label: 'Voice' },
  { section: 'read-aloud', fieldKey: 'rate',         label: 'Reading Rate' },
  { section: 'read-aloud', fieldKey: 'pitch',        label: 'Pitch' },
  { section: 'read-aloud', fieldKey: 'speech',       label: 'Speech Synthesis' },

  // Providers section (ProvidersTab)
  { section: 'providers', fieldKey: 'provider_entries',       label: 'Provider Catalog' },
  { section: 'providers', fieldKey: 'provider_id',            label: 'Provider ID' },
  { section: 'providers', fieldKey: 'provider_display_name',  label: 'Provider Display Name' },
  { section: 'providers', fieldKey: 'provider_type',          label: 'Provider Type' },
  { section: 'providers', fieldKey: 'provider_models',        label: 'Provider Models' },
  { section: 'providers', fieldKey: 'provider_status',        label: 'Proxy Status' },
  { section: 'providers', fieldKey: 'litellm_params',         label: 'LiteLLM Params' },
  { section: 'providers', fieldKey: 'secret_reference',       label: 'Secret Reference' },

  // Model Tuning provider fields
  { section: 'edit-model-tuning', fieldKey: 'provider_catalog_id', label: 'Provider' },
  { section: 'edit-model-tuning', fieldKey: 'provider_model_id',   label: 'Provider Model' },

  // MCP Servers section (McpConfigTab)
  { section: 'mcp-servers', fieldKey: 'mcp',         label: 'MCP Servers' },
  { section: 'mcp-servers', fieldKey: 'mcp_type',    label: 'Server Type' },
  { section: 'mcp-servers', fieldKey: 'mcp_name',    label: 'Server Name' },
  { section: 'mcp-servers', fieldKey: 'mcp_command', label: 'Command' },
  { section: 'mcp-servers', fieldKey: 'mcp_url',     label: 'URL' },
  { section: 'mcp-servers', fieldKey: 'oauth',       label: 'OAuth' },

  // Templates section (LibraryTemplatesSection)
  { section: 'templates', fieldKey: 'template_name',        label: 'Template Name' },
  { section: 'templates', fieldKey: 'template_description', label: 'Description' },
  { section: 'templates', fieldKey: 'template_model',       label: 'Model' },
  { section: 'templates', fieldKey: 'template_system_prompt', label: 'System Prompt' },
  { section: 'templates', fieldKey: 'template_permission_mode', label: 'Permission Mode' },
  { section: 'templates', fieldKey: 'template_allowed_tools',  label: 'Allowed Tools' },
  { section: 'templates', fieldKey: 'template_capabilities',   label: 'Capabilities' },
  { section: 'templates', fieldKey: 'template_role',           label: 'Role' },

  // Profiles section (LibraryProfilesSection)
  { section: 'profiles', fieldKey: 'profile_name',        label: 'Profile Name' },
  { section: 'profiles', fieldKey: 'profile_area',        label: 'Area' },
  { section: 'profiles', fieldKey: 'profile_model',       label: 'Model' },
  { section: 'profiles', fieldKey: 'profile_permission',  label: 'Permission Mode' },
  { section: 'profiles', fieldKey: 'profile_system_prompt', label: 'System Prompt' },
  { section: 'profiles', fieldKey: 'profile_features',    label: 'Features' },
  { section: 'profiles', fieldKey: 'profile_mcp',         label: 'MCP Servers' },
  { section: 'profiles', fieldKey: 'profile_isolation',   label: 'Isolation' },

  // Secrets section (LibrarySecretsSection + SecretGeneralSection)
  { section: 'secrets', fieldKey: 'secret_name',          label: 'Secret Name' },
  { section: 'secrets', fieldKey: 'secret_type',          label: 'Type' },
  { section: 'secrets', fieldKey: 'secret_api_key',       label: 'API Key' },
  { section: 'secrets', fieldKey: 'secret_token',         label: 'Token' },
  { section: 'secrets', fieldKey: 'secret_target_hosts',  label: 'Target Hosts' },
  { section: 'secrets', fieldKey: 'secret_inject_env',    label: 'Inject Env Var' },
  { section: 'secrets', fieldKey: 'secret_inject_file',   label: 'Inject File Path' },
  { section: 'secrets', fieldKey: 'secret_oauth2',        label: 'OAuth2 Refresh' },
  { section: 'secrets', fieldKey: 'secret_ssh_key',       label: 'SSH Key' },

  // Template / Profile edit sections (shared 7-section edit surface)
  // General section
  { section: 'edit-general', fieldKey: 'name',            label: 'Name' },
  { section: 'edit-general', fieldKey: 'description',     label: 'Description' },
  { section: 'edit-general', fieldKey: 'role',            label: 'Role' },
  { section: 'edit-general', fieldKey: 'permission_mode', label: 'Permission Mode' },
  { section: 'edit-general', fieldKey: 'profile_ids',     label: 'Profile Bindings' },

  // Model Tuning section
  { section: 'edit-model-tuning', fieldKey: 'model',                  label: 'Model' },
  { section: 'edit-model-tuning', fieldKey: 'thinking_mode',          label: 'Thinking Mode' },
  { section: 'edit-model-tuning', fieldKey: 'thinking_budget_tokens', label: 'Budget Tokens' },
  { section: 'edit-model-tuning', fieldKey: 'effort',                 label: 'Effort' },

  // Tools & Permissions section
  { section: 'edit-tools-permissions', fieldKey: 'allowed_tools',           label: 'Allowed Tools' },
  { section: 'edit-tools-permissions', fieldKey: 'disallowed_tools',        label: 'Disallowed Tools' },
  { section: 'edit-tools-permissions', fieldKey: 'setting_sources',         label: 'Settings Sources' },
  { section: 'edit-tools-permissions', fieldKey: 'additional_directories',  label: 'Additional Directories' },
  { section: 'edit-tools-permissions', fieldKey: 'capabilities',            label: 'Capabilities' },

  // MCP Servers section
  { section: 'edit-mcp-servers', fieldKey: 'mcp_server_ids',              label: 'MCP Servers' },
  { section: 'edit-mcp-servers', fieldKey: 'enable_claudeai_mcp_servers', label: 'Claude AI MCP Servers' },
  { section: 'edit-mcp-servers', fieldKey: 'strict_mcp_config',           label: 'Strict MCP Config' },

  // Features section
  { section: 'edit-features', fieldKey: 'history_distillation_enabled', label: 'History Distillation' },
  { section: 'edit-features', fieldKey: 'auto_memory_mode',             label: 'Auto-Memory Mode' },
  { section: 'edit-features', fieldKey: 'auto_memory_directory',        label: 'Memory Directory' },
  { section: 'edit-features', fieldKey: 'skill_creating_enabled',       label: 'Skill Creating' },

  // System Prompt section
  { section: 'edit-system-prompt', fieldKey: 'system_prompt',          label: 'System Prompt' },
  { section: 'edit-system-prompt', fieldKey: 'override_system_prompt', label: 'Override System Prompt' },

  // Isolation section
  { section: 'edit-isolation', fieldKey: 'cli_path',                       label: 'CLI Path' },
  { section: 'edit-isolation', fieldKey: 'sandbox_enabled',                label: 'Sandbox Mode' },
  { section: 'edit-isolation', fieldKey: 'docker_enabled',                 label: 'Docker Isolation' },
  { section: 'edit-isolation', fieldKey: 'docker_image',                   label: 'Docker Image' },
  { section: 'edit-isolation', fieldKey: 'docker_extra_mounts',            label: 'Mounts' },
  { section: 'edit-isolation', fieldKey: 'docker_home_directory',          label: 'Home Directory' },
  { section: 'edit-isolation', fieldKey: 'docker_proxy_enabled',           label: 'Network Proxy Sidecar' },
  { section: 'edit-isolation', fieldKey: 'docker_proxy_image',             label: 'Proxy Image' },
  { section: 'edit-isolation', fieldKey: 'docker_proxy_allowlist_domains', label: 'Extra Allowed Domains' },
  { section: 'edit-isolation', fieldKey: 'assigned_secrets',               label: 'Assigned Secrets' },
  { section: 'edit-isolation', fieldKey: 'bare_mode',                      label: 'Bare Mode' },
  { section: 'edit-isolation', fieldKey: 'env_scrub_enabled',              label: 'Scrub Subprocess Credentials' },
]
