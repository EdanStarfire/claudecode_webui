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

  // Profiles section (LibraryProfilesSection / ProfileManagerTab)
  { section: 'profiles', fieldKey: 'profile_name',        label: 'Profile Name' },
  { section: 'profiles', fieldKey: 'profile_area',        label: 'Area' },
  { section: 'profiles', fieldKey: 'profile_model',       label: 'Model' },
  { section: 'profiles', fieldKey: 'profile_permission',  label: 'Permission Mode' },
  { section: 'profiles', fieldKey: 'profile_system_prompt', label: 'System Prompt' },
  { section: 'profiles', fieldKey: 'profile_features',    label: 'Features' },
  { section: 'profiles', fieldKey: 'profile_mcp',         label: 'MCP Servers' },
  { section: 'profiles', fieldKey: 'profile_isolation',   label: 'Isolation' },

  // Secrets section (LibrarySecretsSection / SecretsTab)
  { section: 'secrets', fieldKey: 'secret_name',          label: 'Secret Name' },
  { section: 'secrets', fieldKey: 'secret_type',          label: 'Type' },
  { section: 'secrets', fieldKey: 'secret_api_key',       label: 'API Key' },
  { section: 'secrets', fieldKey: 'secret_token',         label: 'Token' },
  { section: 'secrets', fieldKey: 'secret_target_hosts',  label: 'Target Hosts' },
  { section: 'secrets', fieldKey: 'secret_inject_env',    label: 'Inject Env Var' },
  { section: 'secrets', fieldKey: 'secret_inject_file',   label: 'Inject File Path' },
  { section: 'secrets', fieldKey: 'secret_oauth2',        label: 'OAuth2 Refresh' },
  { section: 'secrets', fieldKey: 'secret_ssh_key',       label: 'SSH Key' },
]
