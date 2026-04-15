<template>
  <div class="advanced-panel">
    <!-- Back button -->
    <button class="back-btn" type="button" @click="$emit('show-quick')">
      <i class="bi bi-arrow-left"></i> Back to Quick Settings
    </button>

    <!-- Card 1: Model Tuning (Blue) -->
    <div class="priority-card priority-blue">
      <button
        class="card-header-btn"
        :class="{ collapsed: !cardStates.tuning }"
        type="button"
        @click="cardStates.tuning = !cardStates.tuning"
      >
        <span class="dot dot-blue"></span>
        Model Tuning
        <span v-if="getProfileForArea('model')" class="profile-badge ms-1" title="Profile assigned">P</span>
        <span class="chevron"><i class="bi bi-chevron-down"></i></span>
      </button>
      <div v-show="cardStates.tuning" class="card-body-inner">
        <!-- Issue #1062: Profile selector for model area (template mode only) -->
        <div v-if="isTemplateMode" class="profile-selector mb-2">
          <label class="form-label small mb-1">
            Profile (model area)
            <span class="text-muted small ms-1">— inheritable defaults</span>
          </label>
          <select
            class="form-select form-select-sm"
            :value="getProfileForArea('model')"
            @change="setProfileForArea('model', $event.target.value || null)"
          >
            <option value="">(no profile)</option>
            <option v-for="p in profilesForArea('model')" :key="p.profile_id" :value="p.profile_id">
              {{ p.name }}
            </option>
          </select>
        </div>
        <div class="mb-2">
          <label class="form-label">Model</label>
          <div class="model-btn-group">
            <button
              type="button"
              class="model-btn"
              :class="{ active: formData.model === 'sonnet' }"
              @click="$emit('update:form-data', 'model', 'sonnet')"
            >Sonnet</button>
            <button
              type="button"
              class="model-btn"
              :class="{ active: formData.model === 'opus' }"
              @click="$emit('update:form-data', 'model', 'opus')"
            >Opus</button>
            <button
              type="button"
              class="model-btn"
              :class="{ active: formData.model === 'haiku' }"
              @click="$emit('update:form-data', 'model', 'haiku')"
            >Haiku</button>
            <button
              type="button"
              class="model-btn"
              :class="{ active: formData.model === 'opusplan' }"
              @click="$emit('update:form-data', 'model', 'opusplan')"
            >OpusPlan</button>
          </div>
        </div>
        <div class="mb-2">
          <label class="form-label">Thinking Mode</label>
          <div class="model-btn-group">
            <button
              type="button"
              class="model-btn"
              :class="{ active: !formData.thinking_mode }"
              @click="$emit('update:form-data', 'thinking_mode', '')"
            >Default</button>
            <button
              type="button"
              class="model-btn"
              :class="{ active: formData.thinking_mode === 'adaptive' }"
              @click="$emit('update:form-data', 'thinking_mode', 'adaptive')"
            >Adaptive</button>
            <button
              type="button"
              class="model-btn"
              :class="{ active: formData.thinking_mode === 'enabled' }"
              @click="$emit('update:form-data', 'thinking_mode', 'enabled')"
            >Enabled</button>
            <button
              type="button"
              class="model-btn"
              :class="{ active: formData.thinking_mode === 'disabled' }"
              @click="$emit('update:form-data', 'thinking_mode', 'disabled')"
            >Disabled</button>
          </div>
        </div>
        <div class="mb-2">
          <label class="form-label">Effort</label>
          <div class="model-btn-group">
            <button
              type="button"
              class="model-btn"
              :class="{ active: !formData.effort }"
              @click="$emit('update:form-data', 'effort', '')"
            >Default</button>
            <button
              type="button"
              class="model-btn"
              :class="{ active: formData.effort === 'low' }"
              @click="$emit('update:form-data', 'effort', 'low')"
            >Low</button>
            <button
              type="button"
              class="model-btn"
              :class="{ active: formData.effort === 'medium' }"
              @click="$emit('update:form-data', 'effort', 'medium')"
            >Med</button>
            <button
              type="button"
              class="model-btn"
              :class="{ active: formData.effort === 'high' }"
              @click="$emit('update:form-data', 'effort', 'high')"
            >High</button>
          </div>
        </div>
        <div v-if="formData.thinking_mode === 'enabled'">
          <label class="form-label">Budget Tokens</label>
          <div class="budget-slider-group">
            <input
              type="range"
              class="form-range"
              min="1024"
              max="32768"
              step="1024"
              :value="formData.thinking_budget_tokens || 10240"
              @input="$emit('update:form-data', 'thinking_budget_tokens', parseInt($event.target.value))"
            />
            <span class="budget-value">{{ (formData.thinking_budget_tokens || 10240).toLocaleString() }}</span>
          </div>
        </div>
      </div>
    </div>

    <!-- Card 2: Tools & Permissions (Indigo) -->
    <div class="priority-card priority-indigo">
      <button
        class="card-header-btn"
        :class="{ collapsed: !cardStates.tools }"
        type="button"
        @click="cardStates.tools = !cardStates.tools"
      >
        <span class="dot dot-indigo"></span>
        Tools & Permissions
        <span v-if="getProfileForArea('permissions')" class="profile-badge ms-1" title="Profile assigned">P</span>
        <span class="chevron"><i class="bi bi-chevron-down"></i></span>
      </button>
      <div v-show="cardStates.tools" class="card-body-inner">
        <!-- Issue #1062: Profile selector for permissions area (template mode only) -->
        <div v-if="isTemplateMode" class="profile-selector mb-2">
          <label class="form-label small mb-1">Profile (permissions area)</label>
          <select
            class="form-select form-select-sm"
            :value="getProfileForArea('permissions')"
            @change="setProfileForArea('permissions', $event.target.value || null)"
          >
            <option value="">(no profile)</option>
            <option v-for="p in profilesForArea('permissions')" :key="p.profile_id" :value="p.profile_id">
              {{ p.name }}
            </option>
          </select>
        </div>
        <!-- Allowed Tools -->
        <div class="mb-2">
          <label class="form-label">
            Allowed Tools
            <span v-if="fieldState('allowed_tools') === 'profile'" class="field-indicator profile" title="Value from profile">P</span>
            <span v-if="fieldState('allowed_tools') === 'autofilled'" class="field-indicator autofilled">&lt;</span>
            <span v-if="fieldState('allowed_tools') === 'modified'" class="field-indicator modified">*</span>
          </label>
          <div class="tag-editor" @click="focusInput('allowedToolInput')">
            <span
              v-for="(tool, index) in toolsList"
              :key="'a-' + index"
              class="tag tag-allowed"
            >
              {{ tool }}
              <span class="tag-remove" @click.stop="removeTool(index)">&times;</span>
            </span>
            <input
              ref="allowedToolInput"
              type="text"
              class="tag-input"
              placeholder="Add tool..."
              v-model="newTool"
              @keydown.enter.prevent="addTool"
            />
          </div>
          <div class="quick-add-btns">
            <button
              v-for="tool in commonTools"
              :key="tool"
              type="button"
              class="btn btn-sm"
              :class="toolsList.includes(tool) ? 'btn-success' : 'btn-outline-success'"
              @click="toggleTool(tool)"
            >{{ toolsList.includes(tool) ? tool : '+' + tool }}</button>
          </div>
        </div>

        <!-- Disallowed Tools -->
        <div class="mb-2">
          <label class="form-label">
            Disallowed Tools
            <span v-if="fieldState('disallowed_tools') === 'profile'" class="field-indicator profile" title="Value from profile">P</span>
            <span v-if="fieldState('disallowed_tools') === 'autofilled'" class="field-indicator autofilled">&lt;</span>
            <span v-if="fieldState('disallowed_tools') === 'modified'" class="field-indicator modified">*</span>
          </label>
          <div class="tag-editor" @click="focusInput('disallowedToolInput')">
            <span
              v-for="(tool, index) in deniedToolsList"
              :key="'d-' + index"
              class="tag tag-disallowed"
            >
              {{ tool }}
              <span class="tag-remove" @click.stop="removeDeniedTool(index)">&times;</span>
            </span>
            <input
              ref="disallowedToolInput"
              type="text"
              class="tag-input"
              placeholder="Add tool..."
              v-model="newDeniedTool"
              @keydown.enter.prevent="addDeniedTool"
            />
          </div>
          <div class="quick-add-btns">
            <button
              v-for="tool in commonDeniedTools"
              :key="tool"
              type="button"
              class="btn btn-sm"
              :class="deniedToolsList.includes(tool) ? 'btn-danger' : 'btn-outline-danger'"
              @click="toggleDeniedTool(tool)"
            >{{ deniedToolsList.includes(tool) ? tool : '+' + tool }}</button>
          </div>
        </div>

        <!-- Settings Sources (session modes only) -->
        <div v-if="isSessionMode" class="mb-2">
          <label class="form-label">Settings Sources</label>
          <div class="model-btn-group">
            <button
              type="button"
              class="model-btn"
              :class="{ active: settingSourcesArray.includes('user') }"
              @click="toggleSettingSource('user')"
            >User</button>
            <button
              type="button"
              class="model-btn"
              :class="{ active: settingSourcesArray.includes('project') }"
              @click="toggleSettingSource('project')"
            >Project</button>
            <button
              type="button"
              class="model-btn"
              :class="{ active: settingSourcesArray.includes('local') }"
              @click="toggleSettingSource('local')"
            >Local</button>
          </div>
        </div>

        <!-- Permission Preview (session modes only) -->
        <button
          v-if="isSessionMode"
          type="button"
          class="btn btn-sm btn-outline-info w-100"
          @click="$emit('preview-permissions')"
        >
          <i class="bi bi-eye me-1"></i> Preview Effective Permissions
        </button>
      </div>
    </div>

    <!-- Card 3: MCP Servers (Purple) -->
    <div class="priority-card priority-purple">
      <button
        class="card-header-btn"
        :class="{ collapsed: !cardStates.mcp }"
        type="button"
        @click="cardStates.mcp = !cardStates.mcp"
      >
        <span class="dot dot-purple"></span>
        MCP Servers
        <span v-if="getProfileForArea('mcp')" class="profile-badge ms-1" title="Profile assigned">P</span>
        <span class="chevron"><i class="bi bi-chevron-down"></i></span>
      </button>
      <div v-show="cardStates.mcp" class="card-body-inner">
        <!-- Issue #1062: Profile selector for mcp area (template mode only) -->
        <div v-if="isTemplateMode" class="profile-selector mb-2">
          <label class="form-label small mb-1">Profile (MCP area)</label>
          <select
            class="form-select form-select-sm"
            :value="getProfileForArea('mcp')"
            @change="setProfileForArea('mcp', $event.target.value || null)"
          >
            <option value="">(no profile)</option>
            <option v-for="p in profilesForArea('mcp')" :key="p.profile_id" :value="p.profile_id">
              {{ p.name }}
            </option>
          </select>
        </div>
        <McpServerPanel
          :mcp-server-ids="formData.mcp_server_ids || []"
          @update:mcp-server-ids="$emit('update:form-data', 'mcp_server_ids', $event)"
          :claude-ai-enabled="formData.enable_claudeai_mcp_servers !== false"
          @update:claude-ai-enabled="$emit('update:form-data', 'enable_claudeai_mcp_servers', $event)"
          :local-enabled="!formData.strict_mcp_config"
          @update:local-enabled="$emit('update:form-data', 'strict_mcp_config', !$event)"
          :runtime-servers="mcpServers"
          :session-active="isEditSession && isSessionActive"
          @toggle="handleMcpToggle"
          @reconnect="handleMcpReconnect"
        />
      </div>
    </div>

    <!-- Card 4: Knowledge Management (Amber) -->
    <div class="priority-card priority-amber">
      <button
        class="card-header-btn"
        :class="{ collapsed: !cardStates.knowledge }"
        type="button"
        @click="cardStates.knowledge = !cardStates.knowledge"
      >
        <span class="dot dot-amber"></span>
        Knowledge Management
        <span v-if="getProfileForArea('features')" class="profile-badge ms-1" title="Profile assigned">P</span>
        <span class="chevron"><i class="bi bi-chevron-down"></i></span>
      </button>
      <div v-show="cardStates.knowledge" class="card-body-inner">
        <!-- Issue #1062: Profile selector for features area (template mode only) -->
        <div v-if="isTemplateMode" class="profile-selector mb-2">
          <label class="form-label small mb-1">Profile (features area)</label>
          <select
            class="form-select form-select-sm"
            :value="getProfileForArea('features')"
            @change="setProfileForArea('features', $event.target.value || null)"
          >
            <option value="">(no profile)</option>
            <option v-for="p in profilesForArea('features')" :key="p.profile_id" :value="p.profile_id">
              {{ p.name }}
            </option>
          </select>
        </div>
        <div class="form-check form-switch mb-2">
          <input
            class="form-check-input"
            type="checkbox"
            id="adv-history-distillation"
            :checked="formData.history_distillation_enabled"
            @change="$emit('update:form-data', 'history_distillation_enabled', $event.target.checked)"
          />
          <label class="form-check-label" for="adv-history-distillation" style="text-transform: none; letter-spacing: normal;">
            History Distillation
          </label>
          <small class="form-text text-muted d-block">
            When enabled, session history is distilled to markdown on archive for context continuity.
          </small>
        </div>
        <div class="mb-2">
          <label class="form-label" for="adv-auto-memory-mode" style="text-transform: none; letter-spacing: normal;">
            Auto-Memory Mode
          </label>
          <select
            class="form-select form-select-sm"
            id="adv-auto-memory-mode"
            :value="formData.auto_memory_mode"
            @change="$emit('update:form-data', 'auto_memory_mode', $event.target.value)"
          >
            <option value="claude">Claude Auto-Memory</option>
            <option value="session">Session-Specific</option>
            <option value="disabled">Disabled</option>
          </select>
          <small class="form-text text-muted d-block">
            Claude: built-in working-directory memory. Session-Specific: per-session guidance file.
            Disabled: no auto-memory.
          </small>
        </div>
        <div v-if="formData.auto_memory_mode === 'claude'" class="mb-2">
          <label class="form-label" for="adv-auto-memory-dir" style="text-transform: none; letter-spacing: normal;">
            Memory Directory
          </label>
          <input
            type="text"
            :class="['form-control', 'form-control-sm', 'font-monospace', { 'is-invalid': autoMemoryDirError }]"
            id="adv-auto-memory-dir"
            :value="formData.auto_memory_directory || ''"
            placeholder="e.g. {session_data}/memory or /absolute/path/to/memory"
            @input="(e) => {
              $emit('update:form-data', 'auto_memory_directory', e.target.value || null)
              autoMemoryDirError = validateTemplatePath(e.target.value)
            }"
          />
          <div v-if="autoMemoryDirError" class="invalid-feedback d-block">{{ autoMemoryDirError }}</div>
          <small class="form-text text-muted d-block">
            Custom directory for auto-memory storage. Leave blank to use Claude's default location.
            Supports template variables: {session_id}, {session_data}, {working_dir}.
          </small>
        </div>
        <div class="form-check form-switch mb-2">
          <input
            class="form-check-input"
            type="checkbox"
            id="adv-skill-creating"
            :checked="formData.skill_creating_enabled"
            @change="$emit('update:form-data', 'skill_creating_enabled', $event.target.checked)"
          />
          <label class="form-check-label" for="adv-skill-creating"
            style="text-transform: none; letter-spacing: normal;">
            Skill Creating
          </label>
          <small class="form-text text-muted d-block">
            When enabled, the session's system prompt includes guidance on creating custom
            local skills using /skill-maker. Requires restart to apply.
          </small>
        </div>
      </div>
    </div>

    <!-- Card 5: System Prompt (Teal) -->
    <div class="priority-card priority-teal">
      <button
        class="card-header-btn"
        :class="{ collapsed: !cardStates.prompt }"
        type="button"
        @click="cardStates.prompt = !cardStates.prompt"
      >
        <span class="dot dot-teal"></span>
        System Prompt & Context
        <span v-if="getProfileForArea('system_prompt')" class="profile-badge ms-1" title="Profile assigned">P</span>
        <span class="chevron"><i class="bi bi-chevron-down"></i></span>
      </button>
      <div v-show="cardStates.prompt" class="card-body-inner">
        <!-- Issue #1062: Profile selector for system_prompt area (template mode only) -->
        <div v-if="isTemplateMode" class="profile-selector mb-2">
          <label class="form-label small mb-1">Profile (system prompt area)</label>
          <select
            class="form-select form-select-sm"
            :value="getProfileForArea('system_prompt')"
            @change="setProfileForArea('system_prompt', $event.target.value || null)"
          >
            <option value="">(no profile)</option>
            <option v-for="p in profilesForArea('system_prompt')" :key="p.profile_id" :value="p.profile_id">
              {{ p.name }}
            </option>
          </select>
        </div>
        <div class="mb-2">
          <label class="form-label">
            System Prompt
            <span v-if="fieldState('system_prompt') === 'profile'" class="field-indicator profile" title="Value from profile">P</span>
            <span v-if="fieldStates.system_prompt === 'autofilled'" class="field-indicator autofilled">&lt;</span>
            <span v-if="fieldStates.system_prompt === 'modified'" class="field-indicator modified">*</span>
          </label>
          <textarea
            class="form-control form-control-sm"
            :value="formData.system_prompt"
            @input="$emit('update:form-data', 'system_prompt', $event.target.value)"
            rows="3"
            :placeholder="isTemplateMode ? 'Optional default system prompt' : 'Instructions and context for the session...'"
          ></textarea>
        </div>
        <div class="form-check mb-2">
          <input
            class="form-check-input"
            type="checkbox"
            id="adv-override-prompt"
            :checked="formData.override_system_prompt"
            @change="$emit('update:form-data', 'override_system_prompt', $event.target.checked)"
          />
          <label class="form-check-label" for="adv-override-prompt" style="text-transform: none; letter-spacing: normal;">
            Override System Prompt
          </label>
        </div>
      </div>
    </div>

    <!-- Card 6: Extra Options (Gray, collapsed by default) -->
    <div class="priority-card priority-gray">
      <button
        class="card-header-btn"
        :class="{ collapsed: !cardStates.extra }"
        type="button"
        @click="cardStates.extra = !cardStates.extra"
      >
        <span class="dot dot-gray"></span>
        Extra Options
        <span v-if="getProfileForArea('isolation')" class="profile-badge ms-1" title="Profile assigned">P</span>
        <span class="chevron"><i class="bi bi-chevron-down"></i></span>
      </button>
      <div v-show="cardStates.extra" class="card-body-inner">
        <!-- Issue #1062: Profile selector for isolation area (template mode only) -->
        <div v-if="isTemplateMode" class="profile-selector mb-2">
          <label class="form-label small mb-1">Profile (isolation area)</label>
          <select
            class="form-select form-select-sm"
            :value="getProfileForArea('isolation')"
            @change="setProfileForArea('isolation', $event.target.value || null)"
          >
            <option value="">(no profile)</option>
            <option v-for="p in profilesForArea('isolation')" :key="p.profile_id" :value="p.profile_id">
              {{ p.name }}
            </option>
          </select>
        </div>
        <!-- CLI Path -->
        <div class="mb-2">
          <label class="form-label">CLI Path</label>
          <input
            type="text"
            class="form-control form-control-sm"
            :value="formData.cli_path"
            @input="$emit('update:form-data', 'cli_path', $event.target.value)"
            :disabled="formData.docker_enabled"
            placeholder="/path/to/claude-cli"
          />
        </div>

        <!-- Additional Directories -->
        <div class="mb-2">
          <label class="form-label">Additional Directories</label>
          <div v-if="additionalDirsList.length > 0">
            <div
              v-for="(dir, index) in additionalDirsList"
              :key="index"
              class="dir-list-item"
            >
              <span class="dir-path">{{ dir }}</span>
              <span class="dir-remove" @click="removeDirectory(index)">&times;</span>
            </div>
          </div>
          <div v-if="additionalDirsError" class="text-danger small mb-1">{{ additionalDirsError }}</div>
          <div class="d-flex gap-2 mt-1">
            <input
              type="text"
              :class="['form-control', 'form-control-sm', { 'is-invalid': additionalDirsError }]"
              v-model="newDirectory"
              @keydown.enter.prevent="addDirectory"
              placeholder="Add directory path... Supports {session_id}, {session_data}, {working_dir}"
              style="flex: 1;"
            />
            <button
              type="button"
              class="btn btn-sm btn-outline-secondary"
              @click="$emit('browse-additional-dir')"
              title="Browse"
            >&#x1F4C2;</button>
            <button
              type="button"
              class="btn btn-sm btn-outline-primary"
              @click="addDirectory"
              :disabled="!newDirectory.trim()"
            >+</button>
          </div>
        </div>

        <!-- Capabilities -->
        <div class="mb-2">
          <label class="form-label">Capabilities</label>
          <div class="tag-editor" @click="focusInput('capabilityInput')">
            <span
              v-for="(cap, index) in capabilitiesList"
              :key="'c-' + index"
              class="tag tag-capability"
            >
              {{ cap }}
              <span class="tag-remove" @click.stop="removeCapability(index)">&times;</span>
            </span>
            <input
              ref="capabilityInput"
              type="text"
              class="tag-input"
              placeholder="Add capability..."
              v-model="newCapability"
              @keydown.enter.prevent="addCapability"
            />
          </div>
        </div>

        <!-- Issue #902: Bare mode -->
        <div class="mb-3">
          <div class="form-check form-switch">
            <input
              class="form-check-input"
              type="checkbox"
              id="bare-mode"
              :checked="formData.bare_mode"
              @change="$emit('update:form-data', 'bare_mode', $event.target.checked)"
            />
            <label class="form-check-label" for="bare-mode">
              Bare mode
            </label>
          </div>
          <div class="form-text text-warning-emphasis">
            Skips hooks, LSP, plugin sync, and skill directory walks.
            Requires <code>ANTHROPIC_API_KEY</code> (OAuth/keychain auth disabled).
          </div>
        </div>
      </div>
    </div>

    <!-- Card 7: Sandbox & Security (Red, collapsed by default) -->
    <div class="priority-card priority-danger">
      <button
        class="card-header-btn"
        :class="{ collapsed: !cardStates.sandbox }"
        type="button"
        @click="cardStates.sandbox = !cardStates.sandbox"
      >
        <span class="dot dot-danger"></span>
        <i class="bi bi-shield-exclamation text-danger" style="font-size: 0.8rem;"></i>
        Sandbox & Security
        <span class="chevron"><i class="bi bi-chevron-down"></i></span>
      </button>
      <div v-show="cardStates.sandbox" class="card-body-inner">
        <!-- Docker Isolation -->
        <div class="form-check mb-2">
          <input
            class="form-check-input"
            type="checkbox"
            id="adv-docker-toggle"
            :checked="formData.docker_enabled"
            @change="handleDockerToggle($event.target.checked)"
            :disabled="isEditSession"
          />
          <label class="form-check-label" for="adv-docker-toggle" style="text-transform: none; letter-spacing: normal;">
            Docker Isolation
          </label>
        </div>
        <!-- Docker status warnings -->
        <div v-if="formData.docker_enabled && dockerStatus && !dockerStatus.available" class="alert alert-warning py-1 mb-2">
          <small>Docker is not available on this system.</small>
        </div>
        <div v-if="formData.docker_enabled" class="ms-4 mb-2">
          <div class="mb-2">
            <label class="form-label">Docker Image</label>
            <input type="text" class="form-control form-control-sm"
              :value="formData.docker_image"
              @input="$emit('update:form-data', 'docker_image', $event.target.value)"
              placeholder="claude-code:local" />
          </div>
          <div class="mb-2">
            <label class="form-label">Mounts</label>
            <textarea
              :class="['form-control', 'form-control-sm', { 'is-invalid': dockerMountsError }]"
              :value="formData.docker_extra_mounts"
              @input="(e) => {
                $emit('update:form-data', 'docker_extra_mounts', e.target.value)
                dockerMountsError = validateTemplatePathList(e.target.value)
              }"
              rows="2"
              placeholder="{session_data}/db:/app/db:ro or /host/path:/container/path:ro (one per line)" />
            <div v-if="dockerMountsError" class="invalid-feedback d-block">{{ dockerMountsError }}</div>
            <small class="form-text text-muted d-block">
              Supports template variables in host paths: {session_id}, {session_data}, {working_dir}.
            </small>
          </div>
          <div class="mb-2">
            <label class="form-label">Home Directory</label>
            <input type="text" class="form-control form-control-sm"
              :value="formData.docker_home_directory"
              @input="$emit('update:form-data', 'docker_home_directory', $event.target.value)"
              placeholder="/home/claude" />
            <small class="form-text text-muted d-block">
              Home directory inside the container (for custom images)
            </small>
          </div>
          <!-- Proxy Sidecar (issue #1050) -->
          <div class="mt-3 mb-2">
            <div class="form-check mb-2">
              <input
                class="form-check-input"
                type="checkbox"
                id="adv-proxy-toggle"
                :checked="formData.docker_proxy_enabled"
                @change="$emit('update:form-data', 'docker_proxy_enabled', $event.target.checked)"
              />
              <label class="form-check-label" for="adv-proxy-toggle" style="text-transform: none; letter-spacing: normal;">
                Network Proxy
              </label>
              <small class="form-text text-muted d-block">
                Intercepts outbound traffic via a dedicated proxy sidecar. Requires <code>claude-proxy:local</code> image.
              </small>
            </div>
            <div v-if="formData.docker_proxy_enabled" class="ms-4 mb-2">
              <label class="form-label">Proxy Image</label>
              <input type="text" class="form-control form-control-sm"
                :value="formData.docker_proxy_image"
                @input="$emit('update:form-data', 'docker_proxy_image', $event.target.value || null)"
                placeholder="claude-proxy:local (default)" />
              <small class="form-text text-muted d-block">
                Leave blank to use the server default.
              </small>
            </div>
          </div>

          <small v-if="isEditSession" class="form-text text-warning d-block mt-1">
            Changes take effect after restarting the session.
          </small>
        </div>

        <!-- Sandbox Enable -->
        <div class="form-check mb-2">
          <input
            class="form-check-input"
            type="checkbox"
            id="adv-sandbox-enable"
            :checked="formData.sandbox_enabled"
            @change="$emit('update:form-data', 'sandbox_enabled', $event.target.checked)"
          />
          <label class="form-check-label fw-semibold" for="adv-sandbox-enable" style="text-transform: none; letter-spacing: normal;">
            Enable Sandbox Mode
          </label>
        </div>

        <div v-if="formData.sandbox_enabled">
          <!-- Bash Permissions -->
          <div class="sandbox-section-label">Bash Permissions</div>
          <div class="form-check mb-1 ms-3">
            <input class="form-check-input" type="checkbox" id="adv-sb-auto-bash"
              :checked="formData.sandbox.autoAllowBashIfSandboxed"
              @change="updateSandboxField('autoAllowBashIfSandboxed', $event.target.checked)" />
            <label class="form-check-label" for="adv-sb-auto-bash" style="text-transform: none; letter-spacing: normal;">Auto-allow Bash when sandboxed</label>
          </div>
          <div class="form-check mb-1 ms-3">
            <input class="form-check-input" type="checkbox" id="adv-sb-unsandboxed"
              :checked="formData.sandbox.allowUnsandboxedCommands"
              @change="updateSandboxField('allowUnsandboxedCommands', $event.target.checked)" />
            <label class="form-check-label" for="adv-sb-unsandboxed" style="text-transform: none; letter-spacing: normal;">Allow unsandboxed commands</label>
          </div>
          <div class="mb-2 ms-3">
            <label class="form-label">Excluded Commands</label>
            <input type="text" class="form-control form-control-sm"
              :value="formData.sandbox.excludedCommands"
              @input="updateSandboxField('excludedCommands', $event.target.value)"
              placeholder="rm, dd, mkfs..." />
          </div>
          <div class="form-check mb-1 ms-3">
            <input class="form-check-input" type="checkbox" id="adv-sb-weaker"
              :checked="formData.sandbox.enableWeakerNestedSandbox"
              @change="updateSandboxField('enableWeakerNestedSandbox', $event.target.checked)" />
            <label class="form-check-label" for="adv-sb-weaker" style="text-transform: none; letter-spacing: normal;">Enable weaker nested sandbox</label>
          </div>

          <!-- Network -->
          <div class="sandbox-section-label">Network</div>
          <div class="mb-1 ms-3">
            <label class="form-label">Allowed Domains</label>
            <input type="text" class="form-control form-control-sm"
              :value="formData.sandbox.network.allowedDomains"
              @input="updateNetworkField('allowedDomains', $event.target.value)"
              placeholder="github.com, api.example.com" />
          </div>
          <div class="form-check mb-1 ms-3">
            <input class="form-check-input" type="checkbox" id="adv-sb-local-binding"
              :checked="formData.sandbox.network.allowLocalBinding"
              @change="updateNetworkField('allowLocalBinding', $event.target.checked)" />
            <label class="form-check-label" for="adv-sb-local-binding" style="text-transform: none; letter-spacing: normal;">Allow local binding</label>
          </div>
          <div class="mb-1 ms-3">
            <label class="form-label">Allow Unix Sockets</label>
            <input type="text" class="form-control form-control-sm"
              :value="formData.sandbox.network.allowUnixSockets"
              @input="updateNetworkField('allowUnixSockets', $event.target.value)"
              placeholder="/var/run/docker.sock" />
          </div>
          <div class="form-check mb-1 ms-3">
            <input class="form-check-input" type="checkbox" id="adv-sb-all-unix"
              :checked="formData.sandbox.network.allowAllUnixSockets"
              @change="updateNetworkField('allowAllUnixSockets', $event.target.checked)" />
            <label class="form-check-label" for="adv-sb-all-unix" style="text-transform: none; letter-spacing: normal;">Allow all Unix sockets</label>
          </div>

          <!-- Violation Handling -->
          <div class="sandbox-section-label">Violation Handling</div>
          <div class="mb-1 ms-3">
            <label class="form-label">Ignore File Violations</label>
            <input type="text" class="form-control form-control-sm"
              :value="formData.sandbox.ignoreViolations.file"
              @input="updateViolationField('file', $event.target.value)"
              placeholder="File paths to ignore" />
          </div>
          <div class="mb-1 ms-3">
            <label class="form-label">Ignore Network Violations</label>
            <input type="text" class="form-control form-control-sm"
              :value="formData.sandbox.ignoreViolations.network"
              @input="updateViolationField('network', $event.target.value)"
              placeholder="Network patterns to ignore" />
          </div>
        </div>

        <!-- Issue #957: Subprocess credential scrubbing -->
        <div class="mb-3">
          <div class="form-check form-switch">
            <input
              class="form-check-input"
              type="checkbox"
              id="env-scrub-toggle"
              :checked="formData.env_scrub_enabled"
              @change="$emit('update:form-data', 'env_scrub_enabled', $event.target.checked)"
            />
            <label class="form-check-label" for="env-scrub-toggle">
              Scrub subprocess credentials
            </label>
          </div>
          <div class="form-text text-warning-emphasis">
            Strips Anthropic API keys and cloud provider credentials from subprocess
            environments. Recommended for untrusted code execution.
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, reactive, computed, onMounted, watch, useTemplateRef } from 'vue'
import { api } from '@/utils/api'
import { validateTemplatePath, validateTemplatePathList } from '@/utils/templateVariables'
import { useMcpStore } from '../../stores/mcp'
import { useProfileStore } from '@/stores/profile'  // Issue #1062
import McpServerPanel from './McpServerPanel.vue'

const mcpStore = useMcpStore()
const profileStore = useProfileStore()  // Issue #1062

const props = defineProps({
  mode: {
    type: String,
    required: true
  },
  formData: {
    type: Object,
    required: true
  },
  errors: {
    type: Object,
    required: true
  },
  session: {
    type: Object,
    default: null
  },
  fieldStates: {
    type: Object,
    default: () => ({
      allowed_tools: 'normal',
      disallowed_tools: 'normal',
      system_prompt: 'normal'
    })
  },
  // Issue #1062: profile_ids for template composition (area -> profile_id)
  profileIds: {
    type: Object,
    default: () => ({})
  }
})

const emit = defineEmits([
  'update:form-data',
  'update:profile-ids',
  'preview-permissions',
  'show-quick',
  'browse-additional-dir',
  'update:has-errors'
])

// Card collapse states (expanded by default for first 4, collapsed for 5 & 6)
const cardStates = reactive({
  tuning: true,
  tools: true,
  mcp: true,
  prompt: false,
  knowledge: true,
  extra: false,
  sandbox: false
})

// Local state
const newTool = ref('')
const newDeniedTool = ref('')
const newCapability = ref('')
const newDirectory = ref('')
const dockerStatus = ref(null)

// Issue #917: Template variable validation errors
const autoMemoryDirError = ref(null)
const dockerMountsError = ref(null)
const additionalDirsError = ref(null)

const hasTemplateErrors = computed(() =>
  !!(autoMemoryDirError.value || dockerMountsError.value || additionalDirsError.value)
)
watch(hasTemplateErrors, (val) => emit('update:has-errors', val), { immediate: true })

// Template refs for focusing
const allowedToolInput = ref(null)
const disallowedToolInput = ref(null)
const capabilityInput = ref(null)

// Common tools
const commonTools = ['Bash', 'Read', 'Edit', 'Write', 'Glob', 'Grep', 'WebFetch']
const commonDeniedTools = ['Bash', 'Write', 'WebFetch']

// Issue #1062: Profile selector helpers
function profilesForArea(area) {
  return profileStore.profilesForArea(area)
}

function getProfileForArea(area) {
  return props.profileIds?.[area] || null
}

function setProfileForArea(area, profileId) {
  const updated = { ...(props.profileIds || {}) }
  if (profileId) {
    updated[area] = profileId
  } else {
    delete updated[area]
  }
  emit('update:profile-ids', updated)
}

// Field state helper including profile source
function fieldState(field) {
  return props.fieldStates?.[field] || 'normal'
}

// Computed
const isSessionMode = computed(() => props.mode === 'create-session' || props.mode === 'edit-session')
const isTemplateMode = computed(() => props.mode === 'create-template' || props.mode === 'edit-template')
const isEditSession = computed(() => props.mode === 'edit-session')

const isSessionActive = computed(() => {
  return props.session?.state === 'active' || props.session?.state === 'starting'
})

const sessionId = computed(() => props.session?.session_id)
const mcpServers = computed(() => {
  if (!sessionId.value) return []
  return mcpStore.mcpServers(sessionId.value)
})

// Fetch MCP status when session becomes active
watch([sessionId, isSessionActive], ([id, active]) => {
  if (id && active) {
    mcpStore.fetchMcpStatus(id)
  }
}, { immediate: true })

const settingSourcesArray = computed(() => {
  return props.formData.setting_sources || ['user', 'project', 'local']
})

const toolsList = computed(() => {
  if (!props.formData.allowed_tools || !props.formData.allowed_tools.trim()) return []
  return props.formData.allowed_tools.split(',').map(t => t.trim()).filter(t => t.length > 0)
})

const deniedToolsList = computed(() => {
  if (!props.formData.disallowed_tools || !props.formData.disallowed_tools.trim()) return []
  return props.formData.disallowed_tools.split(',').map(t => t.trim()).filter(t => t.length > 0)
})

const capabilitiesList = computed(() => {
  if (!props.formData.capabilities || !props.formData.capabilities.trim()) return []
  return props.formData.capabilities.split(',').map(c => c.trim()).filter(c => c.length > 0)
})

const additionalDirsList = computed(() => {
  const raw = props.formData.additional_directories || ''
  return raw.split('\n').map(d => d.trim()).filter(d => d)
})

// Docker status fetch + profile prefetch
onMounted(async () => {
  try {
    dockerStatus.value = await api.get('/api/system/docker-status')
  } catch {
    dockerStatus.value = { available: false }
  }
  profileStore.fetchProfiles()
})

// Methods
function focusInput(refName) {
  if (refName === 'allowedToolInput' && allowedToolInput.value) allowedToolInput.value.focus()
  else if (refName === 'disallowedToolInput' && disallowedToolInput.value) disallowedToolInput.value.focus()
  else if (refName === 'capabilityInput' && capabilityInput.value) capabilityInput.value.focus()
}

function addTool() {
  const tool = newTool.value.trim()
  if (!tool || toolsList.value.includes(tool)) { newTool.value = ''; return }
  emit('update:form-data', 'allowed_tools', [...toolsList.value, tool].join(', '))
  newTool.value = ''
}

function removeTool(index) {
  const list = [...toolsList.value]
  list.splice(index, 1)
  emit('update:form-data', 'allowed_tools', list.join(', '))
}

function toggleTool(tool) {
  if (toolsList.value.includes(tool)) {
    removeTool(toolsList.value.indexOf(tool))
  } else {
    emit('update:form-data', 'allowed_tools', [...toolsList.value, tool].join(', '))
  }
}

function addDeniedTool() {
  const tool = newDeniedTool.value.trim()
  if (!tool || deniedToolsList.value.includes(tool)) { newDeniedTool.value = ''; return }
  emit('update:form-data', 'disallowed_tools', [...deniedToolsList.value, tool].join(', '))
  newDeniedTool.value = ''
}

function removeDeniedTool(index) {
  const list = [...deniedToolsList.value]
  list.splice(index, 1)
  emit('update:form-data', 'disallowed_tools', list.join(', '))
}

function toggleDeniedTool(tool) {
  if (deniedToolsList.value.includes(tool)) {
    removeDeniedTool(deniedToolsList.value.indexOf(tool))
  } else {
    emit('update:form-data', 'disallowed_tools', [...deniedToolsList.value, tool].join(', '))
  }
}

function addCapability() {
  const cap = newCapability.value.trim().toLowerCase()
  if (!cap || capabilitiesList.value.includes(cap)) { newCapability.value = ''; return }
  emit('update:form-data', 'capabilities', [...capabilitiesList.value, cap].join(', '))
  newCapability.value = ''
}

function removeCapability(index) {
  const list = [...capabilitiesList.value]
  list.splice(index, 1)
  emit('update:form-data', 'capabilities', list.join(', '))
}

function toggleSettingSource(source) {
  const current = [...settingSourcesArray.value]
  const index = current.indexOf(source)
  if (index >= 0) current.splice(index, 1)
  else current.push(source)
  emit('update:form-data', 'setting_sources', current)
}

function addDirectory() {
  const dir = newDirectory.value.trim()
  if (!dir) return
  // Issue #917: validate template variables before adding
  const err = validateTemplatePath(dir)
  if (err) {
    additionalDirsError.value = err
    return
  }
  additionalDirsError.value = null
  const current = additionalDirsList.value
  if (!current.includes(dir)) {
    current.push(dir)
    emit('update:form-data', 'additional_directories', current.join('\n'))
  }
  newDirectory.value = ''
}

function addDirectoryPath(dir) {
  const current = additionalDirsList.value
  if (!current.includes(dir)) {
    current.push(dir)
    emit('update:form-data', 'additional_directories', current.join('\n'))
  }
}

defineExpose({ addDirectoryPath })

function removeDirectory(index) {
  const current = [...additionalDirsList.value]
  current.splice(index, 1)
  emit('update:form-data', 'additional_directories', current.join('\n'))
}

function handleDockerToggle(checked) {
  emit('update:form-data', 'docker_enabled', checked)
  if (checked) {
    emit('update:form-data', 'cli_path', '')
  }
}

function updateSandboxField(field, value) {
  props.formData.sandbox[field] = value
}

function updateNetworkField(field, value) {
  props.formData.sandbox.network[field] = value
}

function updateViolationField(field, value) {
  props.formData.sandbox.ignoreViolations[field] = value
}

function handleMcpToggle(name, enabled) {
  if (sessionId.value) mcpStore.toggleServer(sessionId.value, name, enabled)
}

function handleMcpReconnect(name) {
  if (sessionId.value) mcpStore.reconnectServer(sessionId.value, name)
}
</script>

<style scoped>
.field-indicator {
  margin-left: 0.25rem;
  font-size: 0.75rem;
  font-weight: bold;
  cursor: help;
  text-transform: none;
  letter-spacing: normal;
}

.field-indicator.autofilled {
  color: #856404;
}

.field-indicator.modified {
  color: #cc5500;
}

.field-indicator.profile {
  color: #0a6640;
  font-weight: bold;
}

.profile-selector {
  background: var(--bs-light-bg-subtle);
  border-radius: 4px;
  padding: 6px 8px;
  border: 1px solid var(--bs-border-color);
}

.profile-badge {
  font-size: 0.65rem;
  background: #0a6640;
  color: white;
  border-radius: 3px;
  padding: 0 3px;
  vertical-align: middle;
}
</style>
