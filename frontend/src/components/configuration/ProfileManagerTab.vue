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

        <!-- Config fields -->
        <div v-if="currentArea" class="mb-3">
          <div class="d-flex justify-content-between align-items-center mb-2">
            <label class="form-label small mb-0">Config Values</label>
            <span class="form-text small text-muted">Toggle fields to include them in this profile</span>
          </div>

          <div class="area-fields">

            <!-- MODEL AREA -->
            <template v-if="currentArea === 'model'">
              <!-- model -->
              <div class="field-row">
                <div class="field-toggle">
                  <div class="form-check form-switch mb-0">
                    <input class="form-check-input" type="checkbox" v-model="included.model" />
                  </div>
                </div>
                <div class="field-body" :class="{ 'field-disabled': !included.model }">
                  <label class="form-label small mb-1">model</label>
                  <div class="model-btn-group">
                    <button type="button" class="model-btn" :class="{ active: form.config.model === 'sonnet' }" :disabled="!included.model" @click="form.config.model = 'sonnet'">Sonnet</button>
                    <button type="button" class="model-btn" :class="{ active: form.config.model === 'opus' }" :disabled="!included.model" @click="form.config.model = 'opus'">Opus</button>
                    <button type="button" class="model-btn" :class="{ active: form.config.model === 'haiku' }" :disabled="!included.model" @click="form.config.model = 'haiku'">Haiku</button>
                    <button type="button" class="model-btn" :class="{ active: form.config.model === 'opusplan' }" :disabled="!included.model" @click="form.config.model = 'opusplan'">OpusPlan</button>
                  </div>
                </div>
              </div>
              <!-- thinking_mode -->
              <div class="field-row">
                <div class="field-toggle">
                  <div class="form-check form-switch mb-0">
                    <input class="form-check-input" type="checkbox" v-model="included.thinking_mode" />
                  </div>
                </div>
                <div class="field-body" :class="{ 'field-disabled': !included.thinking_mode }">
                  <label class="form-label small mb-1">thinking_mode</label>
                  <div class="model-btn-group">
                    <button type="button" class="model-btn" :class="{ active: !form.config.thinking_mode }" :disabled="!included.thinking_mode" @click="form.config.thinking_mode = ''">Default</button>
                    <button type="button" class="model-btn" :class="{ active: form.config.thinking_mode === 'adaptive' }" :disabled="!included.thinking_mode" @click="form.config.thinking_mode = 'adaptive'">Adaptive</button>
                    <button type="button" class="model-btn" :class="{ active: form.config.thinking_mode === 'enabled' }" :disabled="!included.thinking_mode" @click="form.config.thinking_mode = 'enabled'">Enabled</button>
                    <button type="button" class="model-btn" :class="{ active: form.config.thinking_mode === 'disabled' }" :disabled="!included.thinking_mode" @click="form.config.thinking_mode = 'disabled'">Disabled</button>
                  </div>
                </div>
              </div>
              <!-- thinking_budget_tokens (only when thinking_mode=enabled) -->
              <div v-if="included.thinking_mode && form.config.thinking_mode === 'enabled'" class="field-row">
                <div class="field-toggle">
                  <div class="form-check form-switch mb-0">
                    <input class="form-check-input" type="checkbox" v-model="included.thinking_budget_tokens" />
                  </div>
                </div>
                <div class="field-body" :class="{ 'field-disabled': !included.thinking_budget_tokens }">
                  <label class="form-label small mb-1">thinking_budget_tokens</label>
                  <div class="budget-slider-group">
                    <input
                      type="range" class="form-range"
                      min="1024" max="32768" step="1024"
                      :value="form.config.thinking_budget_tokens || 10240"
                      :disabled="!included.thinking_budget_tokens"
                      @input="form.config.thinking_budget_tokens = parseInt($event.target.value)"
                    />
                    <span class="budget-value small">{{ (form.config.thinking_budget_tokens || 10240).toLocaleString() }}</span>
                  </div>
                </div>
              </div>
              <!-- effort -->
              <div class="field-row">
                <div class="field-toggle">
                  <div class="form-check form-switch mb-0">
                    <input class="form-check-input" type="checkbox" v-model="included.effort" />
                  </div>
                </div>
                <div class="field-body" :class="{ 'field-disabled': !included.effort }">
                  <label class="form-label small mb-1">effort</label>
                  <div class="model-btn-group">
                    <button type="button" class="model-btn" :class="{ active: !form.config.effort }" :disabled="!included.effort" @click="form.config.effort = ''">Default</button>
                    <button type="button" class="model-btn" :class="{ active: form.config.effort === 'low' }" :disabled="!included.effort" @click="form.config.effort = 'low'">Low</button>
                    <button type="button" class="model-btn" :class="{ active: form.config.effort === 'medium' }" :disabled="!included.effort" @click="form.config.effort = 'medium'">Med</button>
                    <button type="button" class="model-btn" :class="{ active: form.config.effort === 'high' }" :disabled="!included.effort" @click="form.config.effort = 'high'">High</button>
                  </div>
                </div>
              </div>
            </template>

            <!-- PERMISSIONS AREA -->
            <template v-else-if="currentArea === 'permissions'">
              <!-- permission_mode -->
              <div class="field-row">
                <div class="field-toggle">
                  <div class="form-check form-switch mb-0">
                    <input class="form-check-input" type="checkbox" v-model="included.permission_mode" />
                  </div>
                </div>
                <div class="field-body" :class="{ 'field-disabled': !included.permission_mode }">
                  <label class="form-label small mb-1">permission_mode</label>
                  <div class="model-btn-group">
                    <button type="button" class="model-btn" :class="{ active: form.config.permission_mode === 'default' }" :disabled="!included.permission_mode" @click="form.config.permission_mode = 'default'">Default</button>
                    <button type="button" class="model-btn" :class="{ active: form.config.permission_mode === 'acceptEdits' }" :disabled="!included.permission_mode" @click="form.config.permission_mode = 'acceptEdits'">Accept Edits</button>
                    <button type="button" class="model-btn" :class="{ active: form.config.permission_mode === 'dontAsk' }" :disabled="!included.permission_mode" @click="form.config.permission_mode = 'dontAsk'">Don't Ask</button>
                    <button type="button" class="model-btn" :class="{ active: form.config.permission_mode === 'bypassPermissions' }" :disabled="!included.permission_mode" @click="form.config.permission_mode = 'bypassPermissions'">Bypass</button>
                  </div>
                </div>
              </div>
              <!-- allowed_tools -->
              <div class="field-row">
                <div class="field-toggle">
                  <div class="form-check form-switch mb-0">
                    <input class="form-check-input" type="checkbox" v-model="included.allowed_tools" />
                  </div>
                </div>
                <div class="field-body" :class="{ 'field-disabled': !included.allowed_tools }">
                  <label class="form-label small mb-1">allowed_tools</label>
                  <div class="tag-editor" @click="included.allowed_tools && $refs.allowedInput?.focus()">
                    <span v-for="(t, i) in allowedToolsList" :key="i" class="tag tag-allowed">
                      {{ t }}<span class="tag-remove" @click.stop="included.allowed_tools && removeAllowedTool(i)">&times;</span>
                    </span>
                    <input ref="allowedInput" type="text" class="tag-input" placeholder="Add tool..." v-model="newAllowedTool" :disabled="!included.allowed_tools" @keydown.enter.prevent="addAllowedTool" />
                  </div>
                  <div class="quick-add-btns mt-1">
                    <button v-for="t in commonTools" :key="t" type="button" class="btn btn-sm" :disabled="!included.allowed_tools"
                      :class="allowedToolsList.includes(t) ? 'btn-success' : 'btn-outline-success'"
                      @click="toggleAllowedTool(t)">{{ allowedToolsList.includes(t) ? t : '+' + t }}</button>
                  </div>
                </div>
              </div>
              <!-- disallowed_tools -->
              <div class="field-row">
                <div class="field-toggle">
                  <div class="form-check form-switch mb-0">
                    <input class="form-check-input" type="checkbox" v-model="included.disallowed_tools" />
                  </div>
                </div>
                <div class="field-body" :class="{ 'field-disabled': !included.disallowed_tools }">
                  <label class="form-label small mb-1">disallowed_tools</label>
                  <div class="tag-editor" @click="included.disallowed_tools && $refs.disallowedInput?.focus()">
                    <span v-for="(t, i) in disallowedToolsList" :key="i" class="tag tag-disallowed">
                      {{ t }}<span class="tag-remove" @click.stop="included.disallowed_tools && removeDisallowedTool(i)">&times;</span>
                    </span>
                    <input ref="disallowedInput" type="text" class="tag-input" placeholder="Add tool..." v-model="newDisallowedTool" :disabled="!included.disallowed_tools" @keydown.enter.prevent="addDisallowedTool" />
                  </div>
                  <div class="quick-add-btns mt-1">
                    <button v-for="t in commonDeniedTools" :key="t" type="button" class="btn btn-sm" :disabled="!included.disallowed_tools"
                      :class="disallowedToolsList.includes(t) ? 'btn-danger' : 'btn-outline-danger'"
                      @click="toggleDisallowedTool(t)">{{ disallowedToolsList.includes(t) ? t : '+' + t }}</button>
                  </div>
                </div>
              </div>
              <!-- additional_directories -->
              <div class="field-row">
                <div class="field-toggle">
                  <div class="form-check form-switch mb-0">
                    <input class="form-check-input" type="checkbox" v-model="included.additional_directories" />
                  </div>
                </div>
                <div class="field-body" :class="{ 'field-disabled': !included.additional_directories }">
                  <label class="form-label small mb-1">additional_directories</label>
                  <div v-for="(d, i) in additionalDirsList" :key="i" class="dir-list-item mb-1">
                    <span class="dir-path small">{{ d }}</span>
                    <span class="dir-remove" @click="included.additional_directories && removeDir(i)">&times;</span>
                  </div>
                  <div class="d-flex gap-1">
                    <input type="text" class="form-control form-control-sm" v-model="newDir" :disabled="!included.additional_directories" @keydown.enter.prevent="addDir" placeholder="Add directory path..." />
                    <button type="button" class="btn btn-sm btn-outline-primary" :disabled="!included.additional_directories || !newDir.trim()" @click="addDir">+</button>
                  </div>
                </div>
              </div>
              <!-- setting_sources -->
              <div class="field-row">
                <div class="field-toggle">
                  <div class="form-check form-switch mb-0">
                    <input class="form-check-input" type="checkbox" v-model="included.setting_sources" />
                  </div>
                </div>
                <div class="field-body" :class="{ 'field-disabled': !included.setting_sources }">
                  <label class="form-label small mb-1">setting_sources</label>
                  <div class="model-btn-group">
                    <button type="button" class="model-btn" :class="{ active: settingSourcesArray.includes('user') }" :disabled="!included.setting_sources" @click="toggleSettingSource('user')">User</button>
                    <button type="button" class="model-btn" :class="{ active: settingSourcesArray.includes('project') }" :disabled="!included.setting_sources" @click="toggleSettingSource('project')">Project</button>
                    <button type="button" class="model-btn" :class="{ active: settingSourcesArray.includes('local') }" :disabled="!included.setting_sources" @click="toggleSettingSource('local')">Local</button>
                  </div>
                </div>
              </div>
            </template>

            <!-- SYSTEM PROMPT AREA -->
            <template v-else-if="currentArea === 'system_prompt'">
              <!-- system_prompt -->
              <div class="field-row">
                <div class="field-toggle">
                  <div class="form-check form-switch mb-0">
                    <input class="form-check-input" type="checkbox" v-model="included.system_prompt" />
                  </div>
                </div>
                <div class="field-body" :class="{ 'field-disabled': !included.system_prompt }">
                  <label class="form-label small mb-1">system_prompt</label>
                  <textarea class="form-control form-control-sm" rows="4" v-model="form.config.system_prompt" :disabled="!included.system_prompt" placeholder="System prompt content..."></textarea>
                </div>
              </div>
              <!-- override_system_prompt -->
              <div class="field-row">
                <div class="field-toggle">
                  <div class="form-check form-switch mb-0">
                    <input class="form-check-input" type="checkbox" v-model="included.override_system_prompt" />
                  </div>
                </div>
                <div class="field-body" :class="{ 'field-disabled': !included.override_system_prompt }">
                  <label class="form-label small mb-1">override_system_prompt</label>
                  <div class="form-check form-switch">
                    <input class="form-check-input" type="checkbox" id="f-override-sp-val" v-model="form.config.override_system_prompt" :disabled="!included.override_system_prompt" />
                    <label class="form-check-label small" for="f-override-sp-val">Override system prompt</label>
                  </div>
                </div>
              </div>
            </template>

            <!-- MCP AREA -->
            <template v-else-if="currentArea === 'mcp'">
              <!-- mcp_server_ids -->
              <div class="field-row">
                <div class="field-toggle">
                  <div class="form-check form-switch mb-0">
                    <input class="form-check-input" type="checkbox" v-model="included.mcp_server_ids" />
                  </div>
                </div>
                <div class="field-body" :class="{ 'field-disabled': !included.mcp_server_ids }">
                  <label class="form-label small mb-1">mcp_server_ids</label>
                  <div v-if="mcpConfigStore.configList().length === 0" class="text-muted small">
                    No MCP servers configured. Add them in Application Settings.
                  </div>
                  <div v-else class="mcp-server-list">
                    <div v-for="srv in mcpConfigStore.configList()" :key="srv.id" class="form-check">
                      <input class="form-check-input" type="checkbox" :id="'mcp-' + srv.id"
                        :checked="mcpServerIds.includes(srv.id)"
                        :disabled="!included.mcp_server_ids"
                        @change="toggleMcpServer(srv.id, $event.target.checked)"
                      />
                      <label class="form-check-label small" :for="'mcp-' + srv.id">{{ srv.name }}</label>
                    </div>
                  </div>
                </div>
              </div>
              <!-- enable_claudeai_mcp_servers -->
              <div class="field-row">
                <div class="field-toggle">
                  <div class="form-check form-switch mb-0">
                    <input class="form-check-input" type="checkbox" v-model="included.enable_claudeai_mcp_servers" />
                  </div>
                </div>
                <div class="field-body" :class="{ 'field-disabled': !included.enable_claudeai_mcp_servers }">
                  <label class="form-label small mb-1">enable_claudeai_mcp_servers</label>
                  <div class="form-check form-switch">
                    <input class="form-check-input" type="checkbox" id="f-claudeai-mcp-val" v-model="form.config.enable_claudeai_mcp_servers" :disabled="!included.enable_claudeai_mcp_servers" />
                    <label class="form-check-label small" for="f-claudeai-mcp-val">Enable Claude.ai MCP servers</label>
                  </div>
                </div>
              </div>
              <!-- strict_mcp_config -->
              <div class="field-row">
                <div class="field-toggle">
                  <div class="form-check form-switch mb-0">
                    <input class="form-check-input" type="checkbox" v-model="included.strict_mcp_config" />
                  </div>
                </div>
                <div class="field-body" :class="{ 'field-disabled': !included.strict_mcp_config }">
                  <label class="form-label small mb-1">strict_mcp_config</label>
                  <div class="form-check form-switch">
                    <input class="form-check-input" type="checkbox" id="f-strict-mcp-val" v-model="form.config.strict_mcp_config" :disabled="!included.strict_mcp_config" />
                    <label class="form-check-label small" for="f-strict-mcp-val">Strict MCP config (disable local servers)</label>
                  </div>
                </div>
              </div>
            </template>

            <!-- ISOLATION AREA -->
            <template v-else-if="currentArea === 'isolation'">
              <div v-for="isoField in isolationFields" :key="isoField.key" class="field-row">
                <div class="field-toggle">
                  <div class="form-check form-switch mb-0">
                    <input class="form-check-input" type="checkbox" v-model="included[isoField.key]"
                      :disabled="isoField.dependsOn && !isIsoParentActive(isoField.dependsOn)" />
                  </div>
                </div>
                <div class="field-body" :class="{ 'field-disabled': !included[isoField.key] || (isoField.dependsOn && !isIsoParentActive(isoField.dependsOn)) }">
                  <label class="form-label small mb-1">{{ isoField.key }}</label>
                  <div v-if="isoField.type === 'toggle'" class="form-check form-switch">
                    <input class="form-check-input" type="checkbox" :id="'f-iso-' + isoField.key" v-model="form.config[isoField.key]"
                      :disabled="!included[isoField.key] || (isoField.dependsOn && !isIsoParentActive(isoField.dependsOn))" />
                    <label class="form-check-label small" :for="'f-iso-' + isoField.key">{{ isoField.label }}</label>
                  </div>
                  <textarea v-else-if="isoField.type === 'textarea'" class="form-control form-control-sm font-monospace" rows="2" v-model="form.config[isoField.key]"
                    :disabled="!included[isoField.key] || (isoField.dependsOn && !isIsoParentActive(isoField.dependsOn))" :placeholder="isoField.placeholder"></textarea>
                  <input v-else type="text" class="form-control form-control-sm font-monospace" v-model="form.config[isoField.key]"
                    :disabled="!included[isoField.key] || (isoField.dependsOn && !isIsoParentActive(isoField.dependsOn))" :placeholder="isoField.placeholder" />
                </div>
              </div>
            </template>

            <!-- FEATURES AREA -->
            <template v-else-if="currentArea === 'features'">
              <!-- history_distillation_enabled -->
              <div class="field-row">
                <div class="field-toggle">
                  <div class="form-check form-switch mb-0">
                    <input class="form-check-input" type="checkbox" v-model="included.history_distillation_enabled" />
                  </div>
                </div>
                <div class="field-body" :class="{ 'field-disabled': !included.history_distillation_enabled }">
                  <label class="form-label small mb-1">history_distillation_enabled</label>
                  <div class="form-check form-switch">
                    <input class="form-check-input" type="checkbox" id="f-history-val" v-model="form.config.history_distillation_enabled" :disabled="!included.history_distillation_enabled" />
                    <label class="form-check-label small" for="f-history-val">Enable history distillation</label>
                  </div>
                </div>
              </div>
              <!-- auto_memory_mode -->
              <div class="field-row">
                <div class="field-toggle">
                  <div class="form-check form-switch mb-0">
                    <input class="form-check-input" type="checkbox" v-model="included.auto_memory_mode" />
                  </div>
                </div>
                <div class="field-body" :class="{ 'field-disabled': !included.auto_memory_mode }">
                  <label class="form-label small mb-1">auto_memory_mode</label>
                  <div class="model-btn-group">
                    <button type="button" class="model-btn" :class="{ active: form.config.auto_memory_mode === 'claude' }" :disabled="!included.auto_memory_mode" @click="form.config.auto_memory_mode = 'claude'">Claude</button>
                    <button type="button" class="model-btn" :class="{ active: form.config.auto_memory_mode === 'session' }" :disabled="!included.auto_memory_mode" @click="form.config.auto_memory_mode = 'session'">Session</button>
                    <button type="button" class="model-btn" :class="{ active: form.config.auto_memory_mode === 'disabled' }" :disabled="!included.auto_memory_mode" @click="form.config.auto_memory_mode = 'disabled'">Disabled</button>
                  </div>
                </div>
              </div>
              <!-- auto_memory_directory -->
              <div class="field-row">
                <div class="field-toggle">
                  <div class="form-check form-switch mb-0">
                    <input class="form-check-input" type="checkbox" v-model="included.auto_memory_directory" />
                  </div>
                </div>
                <div class="field-body" :class="{ 'field-disabled': !included.auto_memory_directory }">
                  <label class="form-label small mb-1">auto_memory_directory</label>
                  <input type="text" class="form-control form-control-sm font-monospace" v-model="form.config.auto_memory_directory" :disabled="!included.auto_memory_directory" placeholder="{session_data}/memory" />
                </div>
              </div>
              <!-- skill_creating_enabled -->
              <div class="field-row">
                <div class="field-toggle">
                  <div class="form-check form-switch mb-0">
                    <input class="form-check-input" type="checkbox" v-model="included.skill_creating_enabled" />
                  </div>
                </div>
                <div class="field-body" :class="{ 'field-disabled': !included.skill_creating_enabled }">
                  <label class="form-label small mb-1">skill_creating_enabled</label>
                  <div class="form-check form-switch">
                    <input class="form-check-input" type="checkbox" id="f-skill-creating-val" v-model="form.config.skill_creating_enabled" :disabled="!included.skill_creating_enabled" />
                    <label class="form-check-label small" for="f-skill-creating-val">Enable skill creating</label>
                  </div>
                </div>
              </div>
            </template>

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

const isolationFields = [
  { key: 'cli_path', type: 'text', placeholder: '/path/to/claude-cli' },
  { key: 'sandbox_enabled', type: 'toggle', label: 'Enable sandbox mode' },
  { key: 'docker_enabled', type: 'toggle', label: 'Docker isolation' },
  { key: 'docker_image', type: 'text', placeholder: 'claude-code:local', dependsOn: 'docker_enabled' },
  { key: 'docker_extra_mounts', type: 'textarea', placeholder: '/host/path:/container/path:ro (one per line)', dependsOn: 'docker_enabled' },
  { key: 'docker_home_directory', type: 'text', placeholder: '/home/claude', dependsOn: 'docker_enabled' },
  { key: 'docker_proxy_enabled', type: 'toggle', label: 'Network proxy sidecar', dependsOn: 'docker_enabled' },
  { key: 'docker_proxy_image', type: 'text', placeholder: 'claude-proxy:local', dependsOn: 'docker_proxy_enabled' },
  { key: 'docker_proxy_credentials', type: 'text', placeholder: 'Credential identifier', dependsOn: 'docker_proxy_enabled' },
  { key: 'bare_mode', type: 'toggle', label: 'Bare mode (skips hooks, LSP, skills)' },
  { key: 'env_scrub_enabled', type: 'toggle', label: 'Scrub subprocess credentials' },
]

// ---- Form state ----
const showForm = ref(false)
const editingProfile = ref(null)
const form = reactive({ name: '', area: '', config: {} })
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

const AREA_DEFAULTS = {
  model: { model: 'sonnet' },
  permissions: { permission_mode: 'default', setting_sources: ['user', 'project', 'local'] },
  mcp: { enable_claudeai_mcp_servers: true, strict_mcp_config: true },
  features: { history_distillation_enabled: true, auto_memory_mode: 'claude' },
}

watch(() => form.area, (newArea) => {
  if (!editingProfile.value) {
    form.config = { ...(AREA_DEFAULTS[newArea] || {}) }
    const fields = AREA_META[newArea]?.fields || []
    fields.forEach(f => { included[f] = false })
    isolationFields.forEach(f => { included[f.key] = false })
  }
})

// Computed tag lists
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
  if (!included.allowed_tools) return
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
  if (!included.disallowed_tools) return
  if (disallowedToolsList.value.includes(t)) removeDisallowedTool(disallowedToolsList.value.indexOf(t))
  else form.config.disallowed_tools = [...disallowedToolsList.value, t].join(', ')
}

function addDir() {
  if (!included.additional_directories) return
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
  if (!included.setting_sources) return
  const current = [...settingSourcesArray.value]
  const idx = current.indexOf(src)
  if (idx >= 0) current.splice(idx, 1)
  else current.push(src)
  form.config.setting_sources = current
}

function isIsoParentActive(parentKey) {
  return included[parentKey] && form.config[parentKey] === true
}

function toggleMcpServer(id, checked) {
  const current = [...mcpServerIds.value]
  if (checked && !current.includes(id)) current.push(id)
  else if (!checked) { const i = current.indexOf(id); if (i >= 0) current.splice(i, 1) }
  form.config.mcp_server_ids = current
}

// ---- Form open/close ----
function initIncluded(area, existingConfig = {}) {
  const fields = AREA_META[area]?.fields || []
  fields.forEach(f => {
    const v = existingConfig[f]
    included[f] = v !== undefined && v !== null && v !== ''
  })
  isolationFields.forEach(f => {
    const v = existingConfig[f.key]
    included[f.key] = v !== undefined && v !== null && v !== ''
  })
}

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
  initIncluded(profile.area, profile.config)
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

  const cleanConfig = {}
  const area = currentArea.value
  const fields = AREA_META[area]?.fields || []
  for (const f of fields) {
    if (!included[f]) continue
    const v = form.config[f]
    if (v !== undefined && v !== null) {
      if (typeof v === 'boolean' || (Array.isArray(v) && v.length > 0) || (typeof v === 'number') || (typeof v === 'string' && v !== '')) {
        cleanConfig[f] = v
      }
    }
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

/* Area fields container */
.area-fields {
  background: var(--bs-light-bg-subtle);
  border-radius: 4px;
  border: 1px solid var(--bs-border-color);
  overflow: hidden;
}

/* Two-column field row */
.field-row {
  display: flex;
  align-items: flex-start;
  gap: 0;
  padding: 8px;
  border-bottom: 1px solid var(--bs-border-color-translucent);
}
.field-row:last-child {
  border-bottom: none;
}

/* Left column: toggle only, ~40px */
.field-toggle {
  width: 40px;
  flex-shrink: 0;
  display: flex;
  align-items: center;
  padding-top: 2px;
}

/* Right column: label + input, fills remaining width */
.field-body {
  flex: 1;
  min-width: 0;
}

/* Greyed-out state when toggle is off */
.field-body.field-disabled {
  opacity: 0.4;
  pointer-events: none;
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

/* Budget slider */
.budget-slider-group {
  display: flex;
  align-items: center;
  gap: 8px;
}
.budget-slider-group input[type="range"] {
  flex: 1;
}

/* Directory list */
.dir-list-item {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 2px 4px;
  background: var(--bs-secondary-bg);
  border-radius: 3px;
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
