<template>
  <div class="advanced-panel">
    <!-- Back button -->
    <button class="back-btn" type="button" @click="$emit('show-quick')">
      <i class="bi bi-arrow-left"></i> Back to Quick Settings
    </button>

    <!-- Card 1: Model Tuning (Blue) -->
    <div class="priority-card priority-blue">
      <div class="card-header-row">
        <button
          class="card-header-btn"
          :class="{ collapsed: !cardStates.tuning }"
          type="button"
          @click="cardStates.tuning = !cardStates.tuning"
        >
          <span class="dot dot-blue"></span>
          Model Tuning
          <span class="chevron"><i class="bi bi-chevron-down"></i></span>
        </button>
        <select
          v-if="isTemplateMode"
          class="form-select profile-select"
          :value="getProfileForArea('model')"
          @change="setProfileForArea('model', $event.target.value || null)"
        >
          <option value="">(no profile)</option>
          <option v-for="p in profilesForArea('model')" :key="p.profile_id" :value="p.profile_id">{{ p.name }}</option>
        </select>
      </div>
      <div v-show="cardStates.tuning" class="card-body-inner">
        <FieldSection
          :fields="FIELD_SCHEMAS.model"
          :config="aspConfig"
          :show-badges="true"
          :show-include-toggle="false"
          :field-states="props.fieldStates"
          @update:config="handleFieldUpdate"
        />
      </div>
    </div>

    <!-- Card 2: Tools & Permissions (Indigo) -->
    <div class="priority-card priority-indigo">
      <div class="card-header-row">
        <button
          class="card-header-btn"
          :class="{ collapsed: !cardStates.tools }"
          type="button"
          @click="cardStates.tools = !cardStates.tools"
        >
          <span class="dot dot-indigo"></span>
          Tools & Permissions
          <span class="chevron"><i class="bi bi-chevron-down"></i></span>
        </button>
        <select
          v-if="isTemplateMode"
          class="form-select profile-select"
          :value="getProfileForArea('permissions')"
          @change="setProfileForArea('permissions', $event.target.value || null)"
        >
          <option value="">(no profile)</option>
          <option v-for="p in profilesForArea('permissions')" :key="p.profile_id" :value="p.profile_id">{{ p.name }}</option>
        </select>
      </div>
      <div v-show="cardStates.tools" class="card-body-inner">
        <FieldSection
          :fields="permissionsFields"
          :config="aspConfig"
          :show-badges="true"
          :show-include-toggle="false"
          :field-states="props.fieldStates"
          @update:config="handleFieldUpdate"
          @browse="onBrowse"
        />

        <!-- Permission Preview (session modes only) -->
        <button
          v-if="isSessionMode"
          type="button"
          class="btn btn-sm btn-outline-info w-100 mt-2"
          @click="$emit('preview-permissions')"
        >
          <i class="bi bi-eye me-1"></i> Preview Effective Permissions
        </button>
      </div>
    </div>

    <!-- Card 3: MCP Servers (Purple) -->
    <div class="priority-card priority-purple">
      <div class="card-header-row">
        <button
          class="card-header-btn"
          :class="{ collapsed: !cardStates.mcp }"
          type="button"
          @click="cardStates.mcp = !cardStates.mcp"
        >
          <span class="dot dot-purple"></span>
          MCP Servers
          <span class="chevron"><i class="bi bi-chevron-down"></i></span>
        </button>
        <select
          v-if="isTemplateMode"
          class="form-select profile-select"
          :value="getProfileForArea('mcp')"
          @change="setProfileForArea('mcp', $event.target.value || null)"
        >
          <option value="">(no profile)</option>
          <option v-for="p in profilesForArea('mcp')" :key="p.profile_id" :value="p.profile_id">{{ p.name }}</option>
        </select>
      </div>
      <div v-show="cardStates.mcp" class="card-body-inner">
        <McpServerPanel
          :mcp-server-ids="formData.mcp_server_ids || []"
          @update:mcp-server-ids="$emit('update:form-data', 'mcp_server_ids', $event)"
          :claude-ai-enabled="formData.enable_claudeai_mcp_servers !== false"
          @update:claude-ai-enabled="$emit('update:form-data', 'enable_claudeai_mcp_servers', $event)"
          :local-enabled="!formData.strict_mcp_config"
          @update:local-enabled="$emit('update:form-data', 'strict_mcp_config', !$event)"
          :claude-ai-state="fieldState('enable_claudeai_mcp_servers')"
          :local-state="fieldState('strict_mcp_config')"
          :runtime-servers="mcpServers"
          :session-active="isEditSession && isSessionActive"
          @toggle="handleMcpToggle"
          @reconnect="handleMcpReconnect"
        />
      </div>
    </div>

    <!-- Card 4: Features (Amber) -->
    <div class="priority-card priority-amber">
      <div class="card-header-row">
        <button
          class="card-header-btn"
          :class="{ collapsed: !cardStates.knowledge }"
          type="button"
          @click="cardStates.knowledge = !cardStates.knowledge"
        >
          <span class="dot dot-amber"></span>
          Features
          <span class="chevron"><i class="bi bi-chevron-down"></i></span>
        </button>
        <select
          v-if="isTemplateMode"
          class="form-select profile-select"
          :value="getProfileForArea('features')"
          @change="setProfileForArea('features', $event.target.value || null)"
        >
          <option value="">(no profile)</option>
          <option v-for="p in profilesForArea('features')" :key="p.profile_id" :value="p.profile_id">{{ p.name }}</option>
        </select>
      </div>
      <div v-show="cardStates.knowledge" class="card-body-inner">
        <FieldSection
          :fields="featuresFieldsWithDescriptions"
          :config="aspConfig"
          :show-badges="true"
          :show-include-toggle="false"
          :field-states="props.fieldStates"
          @update:config="handleFeaturesUpdate"
        />
      </div>
    </div>

    <!-- Card 5: System Prompt (Teal) -->
    <div class="priority-card priority-teal">
      <div class="card-header-row">
        <button
          class="card-header-btn"
          :class="{ collapsed: !cardStates.prompt }"
          type="button"
          @click="cardStates.prompt = !cardStates.prompt"
        >
          <span class="dot dot-teal"></span>
          System Prompt & Context
          <span class="chevron"><i class="bi bi-chevron-down"></i></span>
        </button>
        <select
          v-if="isTemplateMode"
          class="form-select profile-select"
          :value="getProfileForArea('system_prompt')"
          @change="setProfileForArea('system_prompt', $event.target.value || null)"
        >
          <option value="">(no profile)</option>
          <option v-for="p in profilesForArea('system_prompt')" :key="p.profile_id" :value="p.profile_id">{{ p.name }}</option>
        </select>
      </div>
      <div v-show="cardStates.prompt" class="card-body-inner">
        <FieldSection
          :fields="systemPromptFields"
          :config="aspConfig"
          :show-badges="true"
          :show-include-toggle="false"
          :field-states="props.fieldStates"
          @update:config="handleFieldUpdate"
        />
      </div>
    </div>

    <!-- Card 6: Isolation (Red, collapsed by default) -->
    <div class="priority-card priority-danger">
      <div class="card-header-row">
        <button
          class="card-header-btn"
          :class="{ collapsed: !cardStates.isolation }"
          type="button"
          @click="cardStates.isolation = !cardStates.isolation"
        >
          <span class="dot dot-danger"></span>
          Isolation
          <span class="chevron"><i class="bi bi-chevron-down"></i></span>
        </button>
        <select
          v-if="isTemplateMode"
          class="form-select profile-select"
          :value="getProfileForArea('isolation')"
          @change="setProfileForArea('isolation', $event.target.value || null)"
        >
          <option value="">(no profile)</option>
          <option v-for="p in profilesForArea('isolation')" :key="p.profile_id" :value="p.profile_id">{{ p.name }}</option>
        </select>
      </div>
      <div v-show="cardStates.isolation" class="card-body-inner">
        <div
          v-if="formData.docker_enabled && dockerStatus && !dockerStatus.available"
          class="alert alert-warning py-1 mb-2"
        >
          <small>Docker is not available on this system.</small>
        </div>
        <div v-if="formData.docker_enabled && isEditSession" class="form-text text-warning d-block mb-2">
          Changes take effect after restarting the session.
        </div>

        <!-- docker_proxy_enabled description -->
        <FieldSection
          :fields="isolationFields"
          :config="aspConfig"
          :show-badges="true"
          :show-include-toggle="false"
          :field-states="props.fieldStates"
          @update:config="handleIsolationUpdate"
        />
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, reactive, computed, onMounted, watch } from 'vue'
import { api } from '@/utils/api'
import { validateTemplatePath, validateTemplatePathList } from '@/utils/templateVariables'
import { useMcpStore } from '../../stores/mcp'
import { useProfileStore } from '@/stores/profile'
import { useProfileSelector } from '@/composables/useProfileSelector'
import { COMMON_TOOLS, COMMON_DENIED_TOOLS } from '@/utils/toolConstants'
import McpServerPanel from './McpServerPanel.vue'
import FieldSection from './fields/FieldSection.vue'
import { FIELD_SCHEMAS } from './fields/fieldSchemas.js'

const mcpStore = useMcpStore()
const profileStore = useProfileStore()

const props = defineProps({
  mode: { type: String, required: true },
  formData: { type: Object, required: true },
  errors: { type: Object, required: true },
  session: { type: Object, default: null },
  fieldStates: {
    type: Object,
    default: () => ({
      allowed_tools: 'normal',
      disallowed_tools: 'normal',
      system_prompt: 'normal',
    }),
  },
  profileIds: { type: Object, default: () => ({}) },
})

const emit = defineEmits([
  'update:form-data',
  'update:profile-ids',
  'preview-permissions',
  'show-quick',
  'browse-additional-dir',
  'update:has-errors',
])

const { profilesForArea, getProfileForArea, setProfileForArea } = useProfileSelector(props, emit)

// Card collapse states
const cardStates = reactive({
  tuning: true,
  tools: true,
  mcp: true,
  prompt: false,
  knowledge: true,
  isolation: false,
})

const dockerStatus = ref(null)

// Template variable validation errors
const autoMemoryDirError = ref(null)
const dockerMountsError = ref(null)

const hasTemplateErrors = computed(() =>
  !!(autoMemoryDirError.value || dockerMountsError.value)
)
watch(hasTemplateErrors, (val) => emit('update:has-errors', val), { immediate: true })

// Computed mode helpers
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
  if (id && active) mcpStore.fetchMcpStatus(id)
}, { immediate: true })

// Flat "profile dict shape" config object consumed by FieldSection.
// Maps formData fields to the canonical shape expected by field schemas.
const aspConfig = computed(() => ({
  ...props.formData,
  // Flatten sandbox nested object as sandbox_config for SandboxSubSectionWidget
  sandbox_config: props.formData.sandbox || {},
}))

// Field state helper
function fieldState(field) {
  return props.fieldStates?.[field] || 'normal'
}

// Permissions card: inject quickAddItems at runtime from toolConstants
const permissionsFields = computed(() => {
  return FIELD_SCHEMAS.permissions.map((f) => {
    if (f.key === 'allowed_tools') return { ...f, quickAddItems: COMMON_TOOLS }
    if (f.key === 'disallowed_tools') return { ...f, quickAddItems: COMMON_DENIED_TOOLS }
    // setting_sources: only shown in session/template modes
    if (f.key === 'setting_sources' && !isSessionMode.value && !isTemplateMode.value) return null
    return f
  }).filter(Boolean)
})

// Features card: inject full descriptions
const featuresFieldsWithDescriptions = computed(() => {
  return FIELD_SCHEMAS.features.map((f) => {
    if (f.key === 'auto_memory_mode') {
      return {
        ...f,
        description: 'Claude: built-in working-directory memory. Session-Specific: per-session guidance file. Disabled: no auto-memory.',
      }
    }
    if (f.key === 'auto_memory_directory') {
      return {
        ...f,
        description: 'Custom directory for auto-memory storage. Leave blank to use Claude\'s default location. Supports template variables: {session_id}, {session_data}, {working_dir}.',
      }
    }
    if (f.key === 'skill_creating_enabled') {
      return {
        ...f,
        description: 'When enabled, the session\'s system prompt includes guidance on creating custom local skills using /skill-maker. Requires restart to apply.',
      }
    }
    if (f.key === 'history_distillation_enabled') {
      return {
        ...f,
        description: 'When enabled, session history is distilled to markdown on archive for context continuity.',
      }
    }
    return f
  })
})

// System prompt card: inject placeholder
const systemPromptFields = computed(() => {
  return FIELD_SCHEMAS.system_prompt.map((f) => {
    if (f.key === 'system_prompt') {
      return {
        ...f,
        placeholder: isTemplateMode.value
          ? 'Optional default system prompt'
          : 'Instructions and context for the session...',
      }
    }
    return f
  })
})

// Isolation card: inject docker_proxy description, docker-disabled CLI path
const isolationFields = computed(() => {
  return FIELD_SCHEMAS.isolation.map((f) => {
    if (f.key === 'docker_enabled') {
      return {
        ...f,
        // Docker toggle disabled when editing active session
        ...(isEditSession.value ? { disabledWhen: () => true } : {}),
      }
    }
    if (f.key === 'docker_proxy_enabled') {
      return {
        ...f,
        description: 'Intercepts outbound traffic via a dedicated proxy sidecar. Requires <code>claude-proxy:local</code> image.',
      }
    }
    if (f.key === 'docker_proxy_image') {
      return {
        ...f,
        description: 'Leave blank to use the server default.',
      }
    }
    if (f.key === 'docker_home_directory') {
      return {
        ...f,
        description: 'Home directory inside the container (for custom images)',
      }
    }
    if (f.key === 'docker_extra_mounts') {
      return {
        ...f,
        placeholder: '{session_data}/db:/app/db:ro or /host/path:/container/path:ro (one per line)',
        description: 'Supports template variables in host paths: {session_id}, {session_data}, {working_dir}.',
      }
    }
    if (f.key === 'bare_mode') {
      return {
        ...f,
        description: 'Skips hooks, LSP, plugin sync, and skill directory walks. Requires ANTHROPIC_API_KEY (OAuth/keychain auth disabled).',
      }
    }
    if (f.key === 'env_scrub_enabled') {
      return {
        ...f,
        description: 'Strips Anthropic API keys and cloud provider credentials from subprocess environments. Recommended for untrusted code execution.',
      }
    }
    return f
  })
})

// Generic field update — pass through to parent
function handleFieldUpdate(key, value) {
  emit('update:form-data', key, value)
}

// Features update — validate auto_memory_directory
function handleFeaturesUpdate(key, value) {
  if (key === 'auto_memory_directory') {
    autoMemoryDirError.value = validateTemplatePath(value || '')
  }
  emit('update:form-data', key, value)
}

// Isolation update — handle docker toggle side-effects, sandbox shape, docker_extra_mounts validation
function handleIsolationUpdate(key, value) {
  if (key === 'docker_enabled' && value) {
    emit('update:form-data', 'cli_path', '')
  }
  if (key === 'docker_extra_mounts') {
    dockerMountsError.value = validateTemplatePathList(value || '')
  }
  if (key === 'sandbox_config') {
    // Write back to formData.sandbox (ASP uses formData.sandbox.*)
    // We mutate the nested object directly since formData.sandbox is reactive
    Object.assign(props.formData.sandbox, value)
    return
  }
  emit('update:form-data', key, value)
}

// Browse directory
function onBrowse() {
  emit('browse-additional-dir')
}

// Expose addDirectoryPath for parent to call when directory is browsed
function addDirectoryPath(dir) {
  const raw = props.formData.additional_directories || ''
  const current = raw.split('\n').map(d => d.trim()).filter(Boolean)
  if (!current.includes(dir)) {
    current.push(dir)
    emit('update:form-data', 'additional_directories', current.join('\n'))
  }
}

defineExpose({ addDirectoryPath })

onMounted(async () => {
  try {
    dockerStatus.value = await api.get('/api/system/docker-status')
  } catch {
    dockerStatus.value = { available: false }
  }
  profileStore.fetchIfEmpty()
})

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

.field-indicator.autofilled { color: #856404; }
.field-indicator.modified { color: #cc5500; }
.field-indicator.profile { color: #0a6640; font-weight: bold; }
</style>
