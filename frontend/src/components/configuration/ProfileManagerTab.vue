<template>
  <div class="profile-manager-tab">
    <!-- Header -->
    <div class="d-flex justify-content-between align-items-center mb-3">
      <h6 class="mb-0">Configuration Profiles</h6>
      <button class="btn btn-primary btn-sm" @click="openCreate">+ New Profile</button>
    </div>

    <!-- Loading / error -->
    <div v-if="profileStore.loading" class="text-muted small py-2">Loading profiles...</div>
    <div v-if="profileStore.error" class="alert alert-danger py-2 small">{{ profileStore.error }}</div>

    <!-- Empty state -->
    <div v-if="!profileStore.loading && profileStore.allProfiles.length === 0" class="alert alert-info py-2 small">
      No profiles yet. Create your first profile to define reusable configuration defaults.
    </div>

    <!-- Profiles grouped by area -->
    <div v-for="(area, areaKey) in AREA_META" :key="areaKey" class="mb-3">
      <div v-if="profileStore.profilesForArea(areaKey).length > 0">
        <div class="area-header small fw-semibold text-muted mb-1">{{ area.label }}</div>
        <div
          v-for="profile in profileStore.profilesForArea(areaKey)"
          :key="profile.profile_id"
          class="profile-card card mb-1"
        >
          <div class="card-body py-2 px-3">
            <div class="d-flex justify-content-between align-items-center">
              <div class="flex-grow-1 me-2">
                <span class="fw-medium small">{{ profile.name }}</span>
                <span class="ms-2 badge bg-secondary-subtle text-secondary-emphasis">{{ areaKey }}</span>
                <div class="text-muted small mt-1 font-monospace config-preview">
                  {{ configPreview(profile.config) }}
                </div>
              </div>
              <div class="btn-group btn-group-sm">
                <button class="btn btn-outline-primary" @click="openEdit(profile)" title="Edit">Edit</button>
                <button class="btn btn-outline-danger" @click="confirmDelete(profile)" title="Delete">×</button>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>

    <!-- Create / Edit Form -->
    <div v-if="showForm" class="profile-form card mt-3">
      <div class="card-header d-flex justify-content-between align-items-center py-2">
        <strong class="small">{{ editingProfile ? 'Edit Profile' : 'New Profile' }}</strong>
        <button type="button" class="btn-close btn-close-sm" @click="closeForm"></button>
      </div>
      <div class="card-body">
        <!-- Name -->
        <div class="mb-3">
          <label class="form-label small">Name <span class="text-danger">*</span></label>
          <input
            v-model="form.name"
            type="text"
            class="form-control form-control-sm"
            placeholder="e.g. Fast Model"
          />
        </div>

        <!-- Area (create only) -->
        <div v-if="!editingProfile" class="mb-3">
          <label class="form-label small">Area <span class="text-danger">*</span></label>
          <select v-model="form.area" class="form-select form-select-sm">
            <option value="">Select area...</option>
            <option v-for="(meta, key) in AREA_META" :key="key" :value="key">
              {{ meta.label }}
            </option>
          </select>
          <div class="form-text small">{{ form.area ? AREA_META[form.area]?.description : '' }}</div>
        </div>

        <!-- Field instructions -->
        <div v-if="currentArea" class="mb-3">
          <div class="d-flex justify-content-between align-items-center mb-2">
            <label class="form-label small mb-0">Config Values</label>
            <span class="form-text small text-muted">Check a field to include it in this profile</span>
          </div>

          <!-- MODEL AREA -->
          <div v-if="currentArea === 'model'" class="area-fields">
            <!-- model -->
            <div class="field-row">
              <div class="field-check">
                <input class="form-check-input" type="checkbox" id="f-model" v-model="included.model" />
                <label class="form-check-label small" for="f-model">model</label>
              </div>
              <div v-if="included.model" class="field-control mt-1">
                <div class="seg-btn-group">
                  <button type="button" class="seg-btn" :class="{ active: form.config.model === 'sonnet' }" @click="form.config.model = 'sonnet'">Sonnet</button>
                  <button type="button" class="seg-btn" :class="{ active: form.config.model === 'opus' }" @click="form.config.model = 'opus'">Opus</button>
                  <button type="button" class="seg-btn" :class="{ active: form.config.model === 'haiku' }" @click="form.config.model = 'haiku'">Haiku</button>
                  <button type="button" class="seg-btn" :class="{ active: form.config.model === 'opusplan' }" @click="form.config.model = 'opusplan'">OpusPlan</button>
                </div>
              </div>
            </div>
            <!-- thinking_mode -->
            <div class="field-row">
              <div class="field-check">
                <input class="form-check-input" type="checkbox" id="f-thinking-mode" v-model="included.thinking_mode" />
                <label class="form-check-label small" for="f-thinking-mode">thinking_mode</label>
              </div>
              <div v-if="included.thinking_mode" class="field-control mt-1">
                <div class="seg-btn-group">
                  <button type="button" class="seg-btn" :class="{ active: !form.config.thinking_mode }" @click="form.config.thinking_mode = ''">Default</button>
                  <button type="button" class="seg-btn" :class="{ active: form.config.thinking_mode === 'adaptive' }" @click="form.config.thinking_mode = 'adaptive'">Adaptive</button>
                  <button type="button" class="seg-btn" :class="{ active: form.config.thinking_mode === 'enabled' }" @click="form.config.thinking_mode = 'enabled'">Enabled</button>
                  <button type="button" class="seg-btn" :class="{ active: form.config.thinking_mode === 'disabled' }" @click="form.config.thinking_mode = 'disabled'">Disabled</button>
                </div>
              </div>
            </div>
            <!-- thinking_budget_tokens (only when thinking_mode=enabled) -->
            <div v-if="included.thinking_mode && form.config.thinking_mode === 'enabled'" class="field-row">
              <div class="field-check">
                <input class="form-check-input" type="checkbox" id="f-budget" v-model="included.thinking_budget_tokens" />
                <label class="form-check-label small" for="f-budget">thinking_budget_tokens</label>
              </div>
              <div v-if="included.thinking_budget_tokens" class="field-control mt-1">
                <div class="d-flex align-items-center gap-2">
                  <input
                    type="range" class="form-range flex-grow-1"
                    min="1024" max="32768" step="1024"
                    :value="form.config.thinking_budget_tokens || 10240"
                    @input="form.config.thinking_budget_tokens = parseInt($event.target.value)"
                  />
                  <span class="small text-muted">{{ (form.config.thinking_budget_tokens || 10240).toLocaleString() }}</span>
                </div>
              </div>
            </div>
            <!-- effort -->
            <div class="field-row">
              <div class="field-check">
                <input class="form-check-input" type="checkbox" id="f-effort" v-model="included.effort" />
                <label class="form-check-label small" for="f-effort">effort</label>
              </div>
              <div v-if="included.effort" class="field-control mt-1">
                <div class="seg-btn-group">
                  <button type="button" class="seg-btn" :class="{ active: !form.config.effort }" @click="form.config.effort = ''">Default</button>
                  <button type="button" class="seg-btn" :class="{ active: form.config.effort === 'low' }" @click="form.config.effort = 'low'">Low</button>
                  <button type="button" class="seg-btn" :class="{ active: form.config.effort === 'medium' }" @click="form.config.effort = 'medium'">Med</button>
                  <button type="button" class="seg-btn" :class="{ active: form.config.effort === 'high' }" @click="form.config.effort = 'high'">High</button>
                </div>
              </div>
            </div>
          </div>

          <!-- PERMISSIONS AREA -->
          <div v-else-if="currentArea === 'permissions'" class="area-fields">
            <!-- permission_mode -->
            <div class="field-row">
              <div class="field-check">
                <input class="form-check-input" type="checkbox" id="f-perm-mode" v-model="included.permission_mode" />
                <label class="form-check-label small" for="f-perm-mode">permission_mode</label>
              </div>
              <div v-if="included.permission_mode" class="field-control mt-1">
                <div class="seg-btn-group">
                  <button type="button" class="seg-btn" :class="{ active: form.config.permission_mode === 'default' }" @click="form.config.permission_mode = 'default'">Default</button>
                  <button type="button" class="seg-btn" :class="{ active: form.config.permission_mode === 'acceptEdits' }" @click="form.config.permission_mode = 'acceptEdits'">Accept Edits</button>
                  <button type="button" class="seg-btn" :class="{ active: form.config.permission_mode === 'dontAsk' }" @click="form.config.permission_mode = 'dontAsk'">Don't Ask</button>
                  <button type="button" class="seg-btn" :class="{ active: form.config.permission_mode === 'bypassPermissions' }" @click="form.config.permission_mode = 'bypassPermissions'">Bypass</button>
                </div>
              </div>
            </div>
            <!-- allowed_tools -->
            <div class="field-row">
              <div class="field-check">
                <input class="form-check-input" type="checkbox" id="f-allowed-tools" v-model="included.allowed_tools" />
                <label class="form-check-label small" for="f-allowed-tools">allowed_tools</label>
              </div>
              <div v-if="included.allowed_tools" class="field-control mt-1">
                <div class="tag-editor" @click="$refs.allowedInput?.focus()">
                  <span v-for="(t, i) in allowedToolsList" :key="i" class="tag tag-allowed">
                    {{ t }}<span class="tag-remove" @click.stop="removeAllowedTool(i)">&times;</span>
                  </span>
                  <input ref="allowedInput" type="text" class="tag-input" placeholder="Add tool..." v-model="newAllowedTool" @keydown.enter.prevent="addAllowedTool" />
                </div>
                <div class="quick-add-btns mt-1">
                  <button v-for="t in commonTools" :key="t" type="button" class="btn btn-sm"
                    :class="allowedToolsList.includes(t) ? 'btn-success' : 'btn-outline-success'"
                    @click="toggleAllowedTool(t)">{{ allowedToolsList.includes(t) ? t : '+' + t }}</button>
                </div>
              </div>
            </div>
            <!-- disallowed_tools -->
            <div class="field-row">
              <div class="field-check">
                <input class="form-check-input" type="checkbox" id="f-disallowed-tools" v-model="included.disallowed_tools" />
                <label class="form-check-label small" for="f-disallowed-tools">disallowed_tools</label>
              </div>
              <div v-if="included.disallowed_tools" class="field-control mt-1">
                <div class="tag-editor" @click="$refs.disallowedInput?.focus()">
                  <span v-for="(t, i) in disallowedToolsList" :key="i" class="tag tag-disallowed">
                    {{ t }}<span class="tag-remove" @click.stop="removeDisallowedTool(i)">&times;</span>
                  </span>
                  <input ref="disallowedInput" type="text" class="tag-input" placeholder="Add tool..." v-model="newDisallowedTool" @keydown.enter.prevent="addDisallowedTool" />
                </div>
                <div class="quick-add-btns mt-1">
                  <button v-for="t in commonDeniedTools" :key="t" type="button" class="btn btn-sm"
                    :class="disallowedToolsList.includes(t) ? 'btn-danger' : 'btn-outline-danger'"
                    @click="toggleDisallowedTool(t)">{{ disallowedToolsList.includes(t) ? t : '+' + t }}</button>
                </div>
              </div>
            </div>
            <!-- additional_directories -->
            <div class="field-row">
              <div class="field-check">
                <input class="form-check-input" type="checkbox" id="f-add-dirs" v-model="included.additional_directories" />
                <label class="form-check-label small" for="f-add-dirs">additional_directories</label>
              </div>
              <div v-if="included.additional_directories" class="field-control mt-1">
                <div v-for="(d, i) in additionalDirsList" :key="i" class="dir-list-item mb-1">
                  <span class="dir-path small">{{ d }}</span>
                  <span class="dir-remove" @click="removeDir(i)">&times;</span>
                </div>
                <div class="d-flex gap-1">
                  <input type="text" class="form-control form-control-sm" v-model="newDir" @keydown.enter.prevent="addDir" placeholder="Add directory path..." />
                  <button type="button" class="btn btn-sm btn-outline-primary" @click="addDir" :disabled="!newDir.trim()">+</button>
                </div>
              </div>
            </div>
            <!-- setting_sources -->
            <div class="field-row">
              <div class="field-check">
                <input class="form-check-input" type="checkbox" id="f-setting-sources" v-model="included.setting_sources" />
                <label class="form-check-label small" for="f-setting-sources">setting_sources</label>
              </div>
              <div v-if="included.setting_sources" class="field-control mt-1">
                <div class="seg-btn-group">
                  <button type="button" class="seg-btn" :class="{ active: settingSourcesArray.includes('user') }" @click="toggleSettingSource('user')">User</button>
                  <button type="button" class="seg-btn" :class="{ active: settingSourcesArray.includes('project') }" @click="toggleSettingSource('project')">Project</button>
                  <button type="button" class="seg-btn" :class="{ active: settingSourcesArray.includes('local') }" @click="toggleSettingSource('local')">Local</button>
                </div>
              </div>
            </div>
          </div>

          <!-- SYSTEM PROMPT AREA -->
          <div v-else-if="currentArea === 'system_prompt'" class="area-fields">
            <!-- system_prompt -->
            <div class="field-row">
              <div class="field-check">
                <input class="form-check-input" type="checkbox" id="f-sysprompt" v-model="included.system_prompt" />
                <label class="form-check-label small" for="f-sysprompt">system_prompt</label>
              </div>
              <div v-if="included.system_prompt" class="field-control mt-1">
                <textarea class="form-control form-control-sm" rows="4" v-model="form.config.system_prompt" placeholder="System prompt content..."></textarea>
              </div>
            </div>
            <!-- override_system_prompt -->
            <div class="field-row">
              <div class="field-check">
                <input class="form-check-input" type="checkbox" id="f-override-sp" v-model="included.override_system_prompt" />
                <label class="form-check-label small" for="f-override-sp">override_system_prompt</label>
              </div>
              <div v-if="included.override_system_prompt" class="field-control mt-1">
                <div class="form-check form-switch">
                  <input class="form-check-input" type="checkbox" id="f-override-sp-val" v-model="form.config.override_system_prompt" />
                  <label class="form-check-label small" for="f-override-sp-val">Override system prompt</label>
                </div>
              </div>
            </div>
          </div>

          <!-- MCP AREA -->
          <div v-else-if="currentArea === 'mcp'" class="area-fields">
            <!-- mcp_server_ids -->
            <div class="field-row">
              <div class="field-check">
                <input class="form-check-input" type="checkbox" id="f-mcp-ids" v-model="included.mcp_server_ids" />
                <label class="form-check-label small" for="f-mcp-ids">mcp_server_ids</label>
              </div>
              <div v-if="included.mcp_server_ids" class="field-control mt-1">
                <div v-if="mcpConfigStore.configList().length === 0" class="text-muted small">
                  No MCP servers configured. Add them in Application Settings.
                </div>
                <div v-else class="mcp-server-list">
                  <div v-for="srv in mcpConfigStore.configList()" :key="srv.id" class="form-check">
                    <input
                      class="form-check-input" type="checkbox" :id="'mcp-' + srv.id"
                      :checked="mcpServerIds.includes(srv.id)"
                      @change="toggleMcpServer(srv.id, $event.target.checked)"
                    />
                    <label class="form-check-label small" :for="'mcp-' + srv.id">{{ srv.name }}</label>
                  </div>
                </div>
              </div>
            </div>
            <!-- enable_claudeai_mcp_servers -->
            <div class="field-row">
              <div class="field-check">
                <input class="form-check-input" type="checkbox" id="f-claudeai-mcp" v-model="included.enable_claudeai_mcp_servers" />
                <label class="form-check-label small" for="f-claudeai-mcp">enable_claudeai_mcp_servers</label>
              </div>
              <div v-if="included.enable_claudeai_mcp_servers" class="field-control mt-1">
                <div class="form-check form-switch">
                  <input class="form-check-input" type="checkbox" id="f-claudeai-mcp-val" v-model="form.config.enable_claudeai_mcp_servers" />
                  <label class="form-check-label small" for="f-claudeai-mcp-val">Enable Claude.ai MCP servers</label>
                </div>
              </div>
            </div>
            <!-- strict_mcp_config -->
            <div class="field-row">
              <div class="field-check">
                <input class="form-check-input" type="checkbox" id="f-strict-mcp" v-model="included.strict_mcp_config" />
                <label class="form-check-label small" for="f-strict-mcp">strict_mcp_config</label>
              </div>
              <div v-if="included.strict_mcp_config" class="field-control mt-1">
                <div class="form-check form-switch">
                  <input class="form-check-input" type="checkbox" id="f-strict-mcp-val" v-model="form.config.strict_mcp_config" />
                  <label class="form-check-label small" for="f-strict-mcp-val">Strict MCP config (disable local servers)</label>
                </div>
              </div>
            </div>
          </div>

          <!-- ISOLATION AREA -->
          <div v-else-if="currentArea === 'isolation'" class="area-fields">
            <!-- cli_path -->
            <div class="field-row">
              <div class="field-check">
                <input class="form-check-input" type="checkbox" id="f-cli-path" v-model="included.cli_path" />
                <label class="form-check-label small" for="f-cli-path">cli_path</label>
              </div>
              <div v-if="included.cli_path" class="field-control mt-1">
                <input type="text" class="form-control form-control-sm font-monospace" v-model="form.config.cli_path" placeholder="/path/to/claude-cli" />
              </div>
            </div>
            <!-- docker_enabled -->
            <div class="field-row">
              <div class="field-check">
                <input class="form-check-input" type="checkbox" id="f-docker-enabled" v-model="included.docker_enabled" />
                <label class="form-check-label small" for="f-docker-enabled">docker_enabled</label>
              </div>
              <div v-if="included.docker_enabled" class="field-control mt-1">
                <div class="form-check form-switch">
                  <input class="form-check-input" type="checkbox" id="f-docker-enabled-val" v-model="form.config.docker_enabled" />
                  <label class="form-check-label small" for="f-docker-enabled-val">Docker isolation</label>
                </div>
              </div>
            </div>
            <!-- docker_image -->
            <div class="field-row">
              <div class="field-check">
                <input class="form-check-input" type="checkbox" id="f-docker-image" v-model="included.docker_image" />
                <label class="form-check-label small" for="f-docker-image">docker_image</label>
              </div>
              <div v-if="included.docker_image" class="field-control mt-1">
                <input type="text" class="form-control form-control-sm font-monospace" v-model="form.config.docker_image" placeholder="claude-code:local" />
              </div>
            </div>
            <!-- docker_extra_mounts -->
            <div class="field-row">
              <div class="field-check">
                <input class="form-check-input" type="checkbox" id="f-docker-mounts" v-model="included.docker_extra_mounts" />
                <label class="form-check-label small" for="f-docker-mounts">docker_extra_mounts</label>
              </div>
              <div v-if="included.docker_extra_mounts" class="field-control mt-1">
                <textarea class="form-control form-control-sm font-monospace" rows="2" v-model="form.config.docker_extra_mounts" placeholder="/host/path:/container/path:ro (one per line)"></textarea>
              </div>
            </div>
            <!-- docker_home_directory -->
            <div class="field-row">
              <div class="field-check">
                <input class="form-check-input" type="checkbox" id="f-docker-home" v-model="included.docker_home_directory" />
                <label class="form-check-label small" for="f-docker-home">docker_home_directory</label>
              </div>
              <div v-if="included.docker_home_directory" class="field-control mt-1">
                <input type="text" class="form-control form-control-sm font-monospace" v-model="form.config.docker_home_directory" placeholder="/home/claude" />
              </div>
            </div>
            <!-- docker_proxy_enabled -->
            <div class="field-row">
              <div class="field-check">
                <input class="form-check-input" type="checkbox" id="f-proxy-enabled" v-model="included.docker_proxy_enabled" />
                <label class="form-check-label small" for="f-proxy-enabled">docker_proxy_enabled</label>
              </div>
              <div v-if="included.docker_proxy_enabled" class="field-control mt-1">
                <div class="form-check form-switch">
                  <input class="form-check-input" type="checkbox" id="f-proxy-enabled-val" v-model="form.config.docker_proxy_enabled" />
                  <label class="form-check-label small" for="f-proxy-enabled-val">Network proxy sidecar</label>
                </div>
              </div>
            </div>
            <!-- docker_proxy_image -->
            <div class="field-row">
              <div class="field-check">
                <input class="form-check-input" type="checkbox" id="f-proxy-image" v-model="included.docker_proxy_image" />
                <label class="form-check-label small" for="f-proxy-image">docker_proxy_image</label>
              </div>
              <div v-if="included.docker_proxy_image" class="field-control mt-1">
                <input type="text" class="form-control form-control-sm font-monospace" v-model="form.config.docker_proxy_image" placeholder="claude-proxy:local" />
              </div>
            </div>
            <!-- docker_proxy_credentials -->
            <div class="field-row">
              <div class="field-check">
                <input class="form-check-input" type="checkbox" id="f-proxy-creds" v-model="included.docker_proxy_credentials" />
                <label class="form-check-label small" for="f-proxy-creds">docker_proxy_credentials</label>
              </div>
              <div v-if="included.docker_proxy_credentials" class="field-control mt-1">
                <input type="text" class="form-control form-control-sm" v-model="form.config.docker_proxy_credentials" placeholder="Credential identifier" />
              </div>
            </div>
            <!-- sandbox_enabled -->
            <div class="field-row">
              <div class="field-check">
                <input class="form-check-input" type="checkbox" id="f-sandbox" v-model="included.sandbox_enabled" />
                <label class="form-check-label small" for="f-sandbox">sandbox_enabled</label>
              </div>
              <div v-if="included.sandbox_enabled" class="field-control mt-1">
                <div class="form-check form-switch">
                  <input class="form-check-input" type="checkbox" id="f-sandbox-val" v-model="form.config.sandbox_enabled" />
                  <label class="form-check-label small" for="f-sandbox-val">Enable sandbox mode</label>
                </div>
              </div>
            </div>
            <!-- bare_mode -->
            <div class="field-row">
              <div class="field-check">
                <input class="form-check-input" type="checkbox" id="f-bare-mode" v-model="included.bare_mode" />
                <label class="form-check-label small" for="f-bare-mode">bare_mode</label>
              </div>
              <div v-if="included.bare_mode" class="field-control mt-1">
                <div class="form-check form-switch">
                  <input class="form-check-input" type="checkbox" id="f-bare-mode-val" v-model="form.config.bare_mode" />
                  <label class="form-check-label small" for="f-bare-mode-val">Bare mode (skips hooks, LSP, skills)</label>
                </div>
              </div>
            </div>
            <!-- env_scrub_enabled -->
            <div class="field-row">
              <div class="field-check">
                <input class="form-check-input" type="checkbox" id="f-env-scrub" v-model="included.env_scrub_enabled" />
                <label class="form-check-label small" for="f-env-scrub">env_scrub_enabled</label>
              </div>
              <div v-if="included.env_scrub_enabled" class="field-control mt-1">
                <div class="form-check form-switch">
                  <input class="form-check-input" type="checkbox" id="f-env-scrub-val" v-model="form.config.env_scrub_enabled" />
                  <label class="form-check-label small" for="f-env-scrub-val">Scrub subprocess credentials</label>
                </div>
              </div>
            </div>
          </div>

          <!-- FEATURES AREA -->
          <div v-else-if="currentArea === 'features'" class="area-fields">
            <!-- history_distillation_enabled -->
            <div class="field-row">
              <div class="field-check">
                <input class="form-check-input" type="checkbox" id="f-history" v-model="included.history_distillation_enabled" />
                <label class="form-check-label small" for="f-history">history_distillation_enabled</label>
              </div>
              <div v-if="included.history_distillation_enabled" class="field-control mt-1">
                <div class="form-check form-switch">
                  <input class="form-check-input" type="checkbox" id="f-history-val" v-model="form.config.history_distillation_enabled" />
                  <label class="form-check-label small" for="f-history-val">Enable history distillation</label>
                </div>
              </div>
            </div>
            <!-- auto_memory_mode -->
            <div class="field-row">
              <div class="field-check">
                <input class="form-check-input" type="checkbox" id="f-auto-memory" v-model="included.auto_memory_mode" />
                <label class="form-check-label small" for="f-auto-memory">auto_memory_mode</label>
              </div>
              <div v-if="included.auto_memory_mode" class="field-control mt-1">
                <div class="seg-btn-group">
                  <button type="button" class="seg-btn" :class="{ active: form.config.auto_memory_mode === 'claude' }" @click="form.config.auto_memory_mode = 'claude'">Claude</button>
                  <button type="button" class="seg-btn" :class="{ active: form.config.auto_memory_mode === 'session' }" @click="form.config.auto_memory_mode = 'session'">Session</button>
                  <button type="button" class="seg-btn" :class="{ active: form.config.auto_memory_mode === 'disabled' }" @click="form.config.auto_memory_mode = 'disabled'">Disabled</button>
                </div>
              </div>
            </div>
            <!-- auto_memory_directory -->
            <div class="field-row">
              <div class="field-check">
                <input class="form-check-input" type="checkbox" id="f-auto-memory-dir" v-model="included.auto_memory_directory" />
                <label class="form-check-label small" for="f-auto-memory-dir">auto_memory_directory</label>
              </div>
              <div v-if="included.auto_memory_directory" class="field-control mt-1">
                <input type="text" class="form-control form-control-sm font-monospace" v-model="form.config.auto_memory_directory" placeholder="{session_data}/memory" />
              </div>
            </div>
            <!-- skill_creating_enabled -->
            <div class="field-row">
              <div class="field-check">
                <input class="form-check-input" type="checkbox" id="f-skill-creating" v-model="included.skill_creating_enabled" />
                <label class="form-check-label small" for="f-skill-creating">skill_creating_enabled</label>
              </div>
              <div v-if="included.skill_creating_enabled" class="field-control mt-1">
                <div class="form-check form-switch">
                  <input class="form-check-input" type="checkbox" id="f-skill-creating-val" v-model="form.config.skill_creating_enabled" />
                  <label class="form-check-label small" for="f-skill-creating-val">Enable skill creating</label>
                </div>
              </div>
            </div>
          </div>
        </div>

        <div v-if="formError" class="alert alert-danger py-2 small">{{ formError }}</div>

        <div class="d-flex justify-content-end gap-2">
          <button class="btn btn-secondary btn-sm" @click="closeForm">Cancel</button>
          <button class="btn btn-primary btn-sm" :disabled="formSubmitting" @click="submitForm">
            {{ formSubmitting ? 'Saving...' : (editingProfile ? 'Save Changes' : 'Create Profile') }}
          </button>
        </div>
      </div>
    </div>

    <!-- Delete Confirmation -->
    <div v-if="deleteTarget" class="alert alert-danger mt-3">
      <div class="mb-2 small">
        <strong>Delete profile "{{ deleteTarget.name }}"?</strong>
        <span v-if="deleteBlockers.length > 0" class="d-block mt-1">
          Cannot delete — referenced by: {{ deleteBlockers.join(', ') }}
        </span>
      </div>
      <div class="d-flex gap-2">
        <button class="btn btn-sm btn-outline-secondary" @click="deleteTarget = null">Cancel</button>
        <button
          v-if="deleteBlockers.length === 0"
          class="btn btn-sm btn-danger"
          :disabled="deleteSubmitting"
          @click="executeDelete"
        >
          {{ deleteSubmitting ? 'Deleting...' : 'Confirm Delete' }}
        </button>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { useProfileStore } from '@/stores/profile'
import { useMcpConfigStore } from '@/stores/mcpConfig'

const profileStore = useProfileStore()
const mcpConfigStore = useMcpConfigStore()

// ---- Area metadata ----
const AREA_META = {
  model: {
    label: 'Model',
    description: 'Model selection, thinking mode, and effort level',
    fields: ['model', 'thinking_mode', 'thinking_budget_tokens', 'effort'],
  },
  permissions: {
    label: 'Permissions',
    description: 'Permission mode, allowed/disallowed tools, and directories',
    fields: ['permission_mode', 'allowed_tools', 'disallowed_tools', 'additional_directories', 'setting_sources'],
  },
  system_prompt: {
    label: 'System Prompt',
    description: 'System prompt content and override behavior',
    fields: ['system_prompt', 'override_system_prompt'],
  },
  mcp: {
    label: 'MCP',
    description: 'MCP server IDs and configuration toggles',
    fields: ['mcp_server_ids', 'enable_claudeai_mcp_servers', 'strict_mcp_config'],
  },
  isolation: {
    label: 'Isolation',
    description: 'CLI path, sandbox, Docker, and environment settings',
    fields: [
      'cli_path', 'sandbox_enabled',
      'docker_enabled', 'docker_image', 'docker_extra_mounts',
      'docker_home_directory', 'docker_proxy_enabled', 'docker_proxy_image',
      'docker_proxy_credentials', 'bare_mode', 'env_scrub_enabled',
    ],
  },
  features: {
    label: 'Features',
    description: 'History distillation, auto-memory, and skill creation',
    fields: ['history_distillation_enabled', 'auto_memory_mode', 'auto_memory_directory', 'skill_creating_enabled'],
  },
}

// ---- Form state ----
const showForm = ref(false)
const editingProfile = ref(null)
const form = reactive({ name: '', area: '', config: {} })
// Tracks which fields are included (checked) in the profile
const included = reactive({})
const formError = ref('')
const formSubmitting = ref(false)

// Tag input state
const newAllowedTool = ref('')
const newDisallowedTool = ref('')
const newDir = ref('')
const commonTools = ['Bash', 'Read', 'Edit', 'Write', 'Glob', 'Grep', 'WebFetch']
const commonDeniedTools = ['Bash', 'Write', 'WebFetch']

const currentArea = computed(() => editingProfile.value?.area || form.area)

// When area changes in create mode, reset config and included flags
watch(() => form.area, (newArea) => {
  if (!editingProfile.value) {
    form.config = {}
    const fields = AREA_META[newArea]?.fields || []
    fields.forEach(f => { included[f] = false })
  }
})

// Computed tag lists (backed by form.config string fields)
const allowedToolsList = computed(() => {
  const v = form.config.allowed_tools
  if (!v || !v.trim()) return []
  return v.split(',').map(t => t.trim()).filter(Boolean)
})

const disallowedToolsList = computed(() => {
  const v = form.config.disallowed_tools
  if (!v || !v.trim()) return []
  return v.split(',').map(t => t.trim()).filter(Boolean)
})

const additionalDirsList = computed(() => {
  const v = form.config.additional_directories
  if (!v) return []
  return v.split('\n').map(d => d.trim()).filter(Boolean)
})

const settingSourcesArray = computed(() => {
  const v = form.config.setting_sources
  if (!v) return []
  if (Array.isArray(v)) return v
  return v.split(',').map(s => s.trim()).filter(Boolean)
})

const mcpServerIds = computed(() => {
  const v = form.config.mcp_server_ids
  if (!v) return []
  if (Array.isArray(v)) return v
  return v.split(',').map(s => s.trim()).filter(Boolean)
})

// Tool tag helpers
function addAllowedTool() {
  const t = newAllowedTool.value.trim()
  if (!t || allowedToolsList.value.includes(t)) { newAllowedTool.value = ''; return }
  form.config.allowed_tools = [...allowedToolsList.value, t].join(', ')
  newAllowedTool.value = ''
}
function removeAllowedTool(i) {
  const list = [...allowedToolsList.value]; list.splice(i, 1)
  form.config.allowed_tools = list.join(', ')
}
function toggleAllowedTool(t) {
  if (allowedToolsList.value.includes(t)) removeAllowedTool(allowedToolsList.value.indexOf(t))
  else form.config.allowed_tools = [...allowedToolsList.value, t].join(', ')
}

function addDisallowedTool() {
  const t = newDisallowedTool.value.trim()
  if (!t || disallowedToolsList.value.includes(t)) { newDisallowedTool.value = ''; return }
  form.config.disallowed_tools = [...disallowedToolsList.value, t].join(', ')
  newDisallowedTool.value = ''
}
function removeDisallowedTool(i) {
  const list = [...disallowedToolsList.value]; list.splice(i, 1)
  form.config.disallowed_tools = list.join(', ')
}
function toggleDisallowedTool(t) {
  if (disallowedToolsList.value.includes(t)) removeDisallowedTool(disallowedToolsList.value.indexOf(t))
  else form.config.disallowed_tools = [...disallowedToolsList.value, t].join(', ')
}

function addDir() {
  const d = newDir.value.trim()
  if (!d) return
  const list = [...additionalDirsList.value]
  if (!list.includes(d)) {
    list.push(d)
    form.config.additional_directories = list.join('\n')
  }
  newDir.value = ''
}
function removeDir(i) {
  const list = [...additionalDirsList.value]; list.splice(i, 1)
  form.config.additional_directories = list.join('\n')
}

function toggleSettingSource(src) {
  const current = [...settingSourcesArray.value]
  const idx = current.indexOf(src)
  if (idx >= 0) current.splice(idx, 1)
  else current.push(src)
  form.config.setting_sources = current
}

function toggleMcpServer(id, checked) {
  const current = [...mcpServerIds.value]
  if (checked && !current.includes(id)) current.push(id)
  else if (!checked) { const i = current.indexOf(id); if (i >= 0) current.splice(i, 1) }
  form.config.mcp_server_ids = current
}

// ---- Form open/close ----
function openCreate() {
  editingProfile.value = null
  form.name = ''
  form.area = ''
  form.config = {}
  Object.keys(included).forEach(k => { included[k] = false })
  formError.value = ''
  showForm.value = true
}

function openEdit(profile) {
  editingProfile.value = profile
  form.name = profile.name
  form.area = profile.area
  form.config = { ...profile.config }
  formError.value = ''
  // Mark fields as included if they have a value in the existing config
  const fields = AREA_META[profile.area]?.fields || []
  fields.forEach(f => {
    const v = profile.config[f]
    included[f] = v !== undefined && v !== null && v !== ''
  })
  showForm.value = true
}

function closeForm() {
  showForm.value = false
  editingProfile.value = null
  formError.value = ''
}

async function submitForm() {
  formError.value = ''
  if (!form.name.trim()) { formError.value = 'Name is required'; return }
  if (!editingProfile.value && !form.area) { formError.value = 'Area is required'; return }

  // Build config from only included fields
  const cleanConfig = {}
  const fields = AREA_META[currentArea.value]?.fields || []
  for (const f of fields) {
    if (!included[f]) continue
    const v = form.config[f]
    if (v !== undefined && v !== null && v !== '') cleanConfig[f] = v
    else if (typeof v === 'boolean') cleanConfig[f] = v
    else if (Array.isArray(v) && v.length > 0) cleanConfig[f] = v
  }

  formSubmitting.value = true
  try {
    if (editingProfile.value) {
      await profileStore.updateProfile(editingProfile.value.profile_id, {
        name: form.name.trim(),
        config: cleanConfig,
      })
    } else {
      await profileStore.createProfile(form.name.trim(), form.area, cleanConfig)
    }
    closeForm()
  } catch (err) {
    formError.value = err.message || 'Failed to save profile'
  } finally {
    formSubmitting.value = false
  }
}

// ---- Delete state ----
const deleteTarget = ref(null)
const deleteBlockers = ref([])
const deleteSubmitting = ref(false)

function confirmDelete(profile) {
  deleteTarget.value = profile
  deleteBlockers.value = []
}

async function executeDelete() {
  if (!deleteTarget.value) return
  deleteSubmitting.value = true
  try {
    const result = await profileStore.deleteProfile(deleteTarget.value.profile_id)
    if (result?.error === 'profile_in_use') {
      deleteBlockers.value = result.blocking_templates?.map(t => t.name) || []
    } else {
      deleteTarget.value = null
    }
  } catch (err) {
    if (err.data?.error === 'profile_in_use') {
      deleteBlockers.value = err.data.blocking_templates?.map(t => t.name) || []
    } else {
      console.error('Failed to delete profile:', err)
    }
  } finally {
    deleteSubmitting.value = false
  }
}

// ---- Helpers ----
function configPreview(config) {
  if (!config || Object.keys(config).length === 0) return '(empty — inheritable defaults)'
  return Object.entries(config)
    .slice(0, 3)
    .map(([k, v]) => `${k}: ${JSON.stringify(v)}`)
    .join(', ') + (Object.keys(config).length > 3 ? ` +${Object.keys(config).length - 3} more` : '')
}

onMounted(() => {
  profileStore.fetchProfiles()
  mcpConfigStore.fetchConfigs()
})
</script>

<style scoped>
.profile-manager-tab {
  min-height: 200px;
}

.area-header {
  border-bottom: 1px solid var(--bs-border-color);
  padding-bottom: 4px;
  text-transform: uppercase;
  letter-spacing: 0.05em;
  font-size: 0.7rem;
}

.profile-card {
  border-color: var(--bs-border-color);
}

.config-preview {
  font-size: 0.7rem;
  color: var(--bs-secondary-color);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  max-width: 300px;
}

/* Area fields */
.area-fields {
  background: var(--bs-light-bg-subtle);
  border-radius: 4px;
  padding: 8px;
  border: 1px solid var(--bs-border-color);
}

.field-row {
  padding: 6px 4px;
  border-bottom: 1px solid var(--bs-border-color-translucent);
}
.field-row:last-child {
  border-bottom: none;
}

.field-check {
  display: flex;
  align-items: center;
  gap: 6px;
}

.field-control {
  padding-left: 1.75rem;
}

/* Segmented button groups */
.seg-btn-group {
  display: flex;
  gap: 2px;
  flex-wrap: wrap;
}

.seg-btn {
  padding: 3px 10px;
  font-size: 0.75rem;
  border: 1px solid var(--bs-border-color);
  background: transparent;
  border-radius: 4px;
  cursor: pointer;
  color: var(--bs-body-color);
  transition: background-color 0.1s, color 0.1s;
}

.seg-btn:hover {
  background: var(--bs-secondary-bg);
}

.seg-btn.active {
  background: var(--bs-primary);
  border-color: var(--bs-primary);
  color: #fff;
}

/* Tag editor */
.tag-editor {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
  padding: 4px 8px;
  border: 1px solid var(--bs-border-color);
  border-radius: 4px;
  cursor: text;
  min-height: 32px;
  align-items: center;
  background: var(--bs-body-bg);
}

.tag {
  display: inline-flex;
  align-items: center;
  gap: 3px;
  padding: 1px 6px;
  border-radius: 3px;
  font-size: 0.75rem;
}

.tag-allowed { background: #d1f0d1; color: #1a5c1a; }
.tag-disallowed { background: #f8d7da; color: #842029; }

.tag-remove {
  cursor: pointer;
  font-size: 0.9rem;
  line-height: 1;
  opacity: 0.7;
}
.tag-remove:hover { opacity: 1; }

.tag-input {
  border: none;
  outline: none;
  background: transparent;
  font-size: 0.8rem;
  min-width: 80px;
  flex: 1;
}

.quick-add-btns {
  display: flex;
  flex-wrap: wrap;
  gap: 3px;
}
.quick-add-btns .btn {
  font-size: 0.7rem;
  padding: 1px 6px;
}

/* Directory list */
.dir-list-item {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 2px 4px;
  background: var(--bs-secondary-bg);
  border-radius: 3px;
  font-size: 0.75rem;
}
.dir-path { flex: 1; font-family: monospace; }
.dir-remove { cursor: pointer; color: var(--bs-danger); font-size: 1rem; }

/* MCP server list */
.mcp-server-list {
  display: flex;
  flex-direction: column;
  gap: 4px;
}
</style>
