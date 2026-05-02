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
]
