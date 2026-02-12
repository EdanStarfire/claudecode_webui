<template>
  <div class="permissions-tab">
    <!-- Allowed Tools -->
    <div class="mb-3">
      <label for="config-allowed-tools" class="form-label">
        Allowed Tools
        <span v-if="fieldStates.allowed_tools === 'autofilled'" class="field-indicator autofilled" title="Auto-filled from template">
          &lt;
        </span>
        <span v-if="fieldStates.allowed_tools === 'modified'" class="field-indicator modified" title="Modified from template">
          *
        </span>
      </label>
      <div class="allowed-tools-editor" :class="allowedToolsEditorClass">
        <!-- Current tools as tags -->
        <div class="tools-list mb-2" v-if="toolsList.length > 0">
          <span
            v-for="(tool, index) in toolsList"
            :key="index"
            class="badge bg-primary me-1 mb-1 tool-badge"
          >
            {{ tool }}
            <button
              type="button"
              class="btn-close btn-close-white ms-1"
              @click="removeTool(index)"
              aria-label="Remove tool"
            ></button>
          </span>
        </div>

        <!-- Add tool input -->
        <div class="input-group input-group-sm">
          <input
            type="text"
            class="form-control"
            v-model="newTool"
            @keydown.enter.prevent="addTool"
            placeholder="Add tool (e.g., Bash, Read, Edit)"
          />
          <button
            type="button"
            class="btn btn-outline-primary"
            @click="addTool"
            :disabled="!newTool.trim()"
          >
            Add
          </button>
        </div>

        <!-- Quick add common tools -->
        <div class="mt-2">
          <span class="form-text me-2">Quick add:</span>
          <button
            v-for="tool in commonTools"
            :key="tool"
            type="button"
            class="btn btn-outline-secondary btn-sm me-1 mb-1"
            :class="{ 'btn-secondary text-white': toolsList.includes(tool) }"
            @click="toggleTool(tool)"
          >
            {{ tool }}
          </button>
        </div>
      </div>
      <div class="form-text">
        {{ isTemplateMode ? 'Default tools allowed for sessions using this template' : 'Leave empty to allow all tools' }}
      </div>
    </div>

    <!-- Disallowed Tools -->
    <div class="mb-3">
      <label for="config-disallowed-tools" class="form-label">
        Disallowed Tools
        <span v-if="fieldStates.disallowed_tools === 'autofilled'" class="field-indicator autofilled" title="Auto-filled from template">
          &lt;
        </span>
        <span v-if="fieldStates.disallowed_tools === 'modified'" class="field-indicator modified" title="Modified from template">
          *
        </span>
      </label>
      <div class="disallowed-tools-editor" :class="disallowedToolsEditorClass">
        <!-- Current denied tools as tags -->
        <div class="tools-list mb-2" v-if="deniedToolsList.length > 0">
          <span
            v-for="(tool, index) in deniedToolsList"
            :key="index"
            class="badge bg-danger me-1 mb-1 denied-tool-badge"
          >
            {{ tool }}
            <button
              type="button"
              class="btn-close btn-close-white ms-1"
              @click="removeDeniedTool(index)"
              aria-label="Remove denied tool"
            ></button>
          </span>
        </div>

        <!-- Add denied tool input -->
        <div class="input-group input-group-sm">
          <input
            type="text"
            class="form-control"
            v-model="newDeniedTool"
            @keydown.enter.prevent="addDeniedTool"
            placeholder="Add denied tool (e.g., Bash, Write)"
          />
          <button
            type="button"
            class="btn btn-outline-danger"
            @click="addDeniedTool"
            :disabled="!newDeniedTool.trim()"
          >
            Deny
          </button>
        </div>

        <!-- Quick add common tools -->
        <div class="mt-2">
          <span class="form-text me-2">Quick deny:</span>
          <button
            v-for="tool in commonTools"
            :key="tool"
            type="button"
            class="btn btn-outline-secondary btn-sm me-1 mb-1"
            :class="{ 'btn-danger text-white': deniedToolsList.includes(tool) }"
            @click="toggleDeniedTool(tool)"
          >
            {{ tool }}
          </button>
        </div>
      </div>
      <div class="form-text">
        {{ isTemplateMode ? 'Tools explicitly denied for sessions using this template' : 'Tools to explicitly deny regardless of other permissions' }}
      </div>
    </div>

    <!-- Settings Sources (Issue #36) -->
    <div class="mb-3" v-if="isSessionMode">
      <label class="form-label">Settings Sources</label>
      <div class="settings-sources-editor">
        <div class="form-check">
          <input
            type="checkbox"
            class="form-check-input"
            id="setting-source-user"
            :checked="settingSourcesArray.includes('user')"
            @change="toggleSettingSource('user')"
          />
          <label class="form-check-label" for="setting-source-user">
            User <code class="small">~/.claude/settings.json</code>
          </label>
        </div>
        <div class="form-check">
          <input
            type="checkbox"
            class="form-check-input"
            id="setting-source-project"
            :checked="settingSourcesArray.includes('project')"
            @change="toggleSettingSource('project')"
          />
          <label class="form-check-label" for="setting-source-project">
            Project <code class="small">.claude/settings.json</code>
          </label>
        </div>
        <div class="form-check">
          <input
            type="checkbox"
            class="form-check-input"
            id="setting-source-local"
            :checked="settingSourcesArray.includes('local')"
            @change="toggleSettingSource('local')"
          />
          <label class="form-check-label" for="setting-source-local">
            Local <code class="small">.claude/settings.local.json</code>
          </label>
        </div>
      </div>
      <div class="form-text">
        Select which settings files to load permissions from
      </div>
    </div>

    <!-- Permission Preview Button (Issue #36) -->
    <div class="mb-3" v-if="isSessionMode">
      <button
        type="button"
        class="btn btn-outline-secondary btn-sm"
        @click="$emit('preview-permissions')"
      >
        Preview Effective Permissions
      </button>
    </div>

    <!-- Raw input for advanced editing -->
    <div class="mb-3">
      <a
        class="text-muted small"
        role="button"
        @click="showRawInput = !showRawInput"
      >
        {{ showRawInput ? 'Hide' : 'Show' }} raw input
      </a>

      <div v-if="showRawInput" class="mt-2">
        <label class="form-label small text-muted">Allowed Tools (comma-separated)</label>
        <input
          type="text"
          class="form-control form-control-sm"
          :class="allowedToolsFieldClass"
          :value="formData.allowed_tools"
          @input="$emit('update:form-data', 'allowed_tools', $event.target.value)"
          placeholder="bash, read, edit, write, glob, grep"
        />

        <label class="form-label small text-muted mt-2">Disallowed Tools (comma-separated)</label>
        <input
          type="text"
          class="form-control form-control-sm"
          :class="disallowedToolsFieldClass"
          :value="formData.disallowed_tools"
          @input="$emit('update:form-data', 'disallowed_tools', $event.target.value)"
          placeholder="Bash, Write, WebFetch"
        />
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed } from 'vue'

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
      disallowed_tools: 'normal'
    })
  }
})

const emit = defineEmits(['update:form-data', 'preview-permissions'])

// Local state
const newTool = ref('')
const newDeniedTool = ref('')
const showRawInput = ref(false)

// Common tools for quick add
const commonTools = ['Bash', 'Read', 'Edit', 'Write', 'Glob', 'Grep', 'WebFetch', 'WebSearch', 'Task', 'TodoWrite']

// Computed
const isTemplateMode = computed(() => props.mode === 'create-template' || props.mode === 'edit-template')
const isSessionMode = computed(() => props.mode === 'create-session' || props.mode === 'edit-session')
// Issue #36: Setting sources computed
const settingSourcesArray = computed(() => {
  return props.formData.setting_sources || ['user', 'project', 'local']
})

const toolsList = computed(() => {
  if (!props.formData.allowed_tools || !props.formData.allowed_tools.trim()) return []
  return props.formData.allowed_tools
    .split(',')
    .map(t => t.trim())
    .filter(t => t.length > 0)
})

const deniedToolsList = computed(() => {
  if (!props.formData.disallowed_tools || !props.formData.disallowed_tools.trim()) return []
  return props.formData.disallowed_tools
    .split(',')
    .map(t => t.trim())
    .filter(t => t.length > 0)
})

// Field highlighting classes
const allowedToolsFieldClass = computed(() => ({
  'field-autofilled': props.fieldStates.allowed_tools === 'autofilled',
  'field-modified': props.fieldStates.allowed_tools === 'modified'
}))

const allowedToolsEditorClass = computed(() => ({
  'editor-autofilled': props.fieldStates.allowed_tools === 'autofilled',
  'editor-modified': props.fieldStates.allowed_tools === 'modified'
}))

const disallowedToolsFieldClass = computed(() => ({
  'field-autofilled': props.fieldStates.disallowed_tools === 'autofilled',
  'field-modified': props.fieldStates.disallowed_tools === 'modified'
}))

const disallowedToolsEditorClass = computed(() => ({
  'editor-autofilled': props.fieldStates.disallowed_tools === 'autofilled',
  'editor-modified': props.fieldStates.disallowed_tools === 'modified'
}))

// Methods
function addTool() {
  const tool = newTool.value.trim()
  if (!tool) return

  if (!toolsList.value.includes(tool)) {
    const newList = [...toolsList.value, tool]
    emit('update:form-data', 'allowed_tools', newList.join(', '))
  }
  newTool.value = ''
}

function removeTool(index) {
  const newList = [...toolsList.value]
  newList.splice(index, 1)
  emit('update:form-data', 'allowed_tools', newList.join(', '))
}

function toggleTool(tool) {
  if (toolsList.value.includes(tool)) {
    const index = toolsList.value.indexOf(tool)
    removeTool(index)
  } else {
    const newList = [...toolsList.value, tool]
    emit('update:form-data', 'allowed_tools', newList.join(', '))
  }
}

function addDeniedTool() {
  const tool = newDeniedTool.value.trim()
  if (!tool) return

  if (!deniedToolsList.value.includes(tool)) {
    const newList = [...deniedToolsList.value, tool]
    emit('update:form-data', 'disallowed_tools', newList.join(', '))
  }
  newDeniedTool.value = ''
}

function removeDeniedTool(index) {
  const newList = [...deniedToolsList.value]
  newList.splice(index, 1)
  emit('update:form-data', 'disallowed_tools', newList.join(', '))
}

function toggleDeniedTool(tool) {
  if (deniedToolsList.value.includes(tool)) {
    const index = deniedToolsList.value.indexOf(tool)
    removeDeniedTool(index)
  } else {
    const newList = [...deniedToolsList.value, tool]
    emit('update:form-data', 'disallowed_tools', newList.join(', '))
  }
}

// Issue #36: Toggle setting source
function toggleSettingSource(source) {
  const current = [...settingSourcesArray.value]
  const index = current.indexOf(source)

  if (index >= 0) {
    current.splice(index, 1)
  } else {
    current.push(source)
  }

  emit('update:form-data', 'setting_sources', current)
}
</script>

<style scoped>
.tool-badge,
.denied-tool-badge {
  font-size: 0.875rem;
  padding: 0.35em 0.65em;
  display: inline-flex;
  align-items: center;
}

.tool-badge .btn-close,
.denied-tool-badge .btn-close {
  font-size: 0.5rem;
  padding: 0.25em;
}

.allowed-tools-editor,
.disallowed-tools-editor,
.settings-sources-editor {
  background-color: var(--bs-gray-100);
  padding: 0.75rem;
  border-radius: 0.375rem;
  transition: background-color 0.3s ease;
}

.tools-list {
  min-height: 1.5rem;
}

/* Editor highlighting states */
.editor-autofilled {
  background-color: #fffbea !important; /* Light yellow */
}

.editor-modified {
  background-color: #ffe4cc !important; /* Darker orange */
}

/* Field highlighting states */
.field-autofilled {
  background-color: #fffbea !important;
  transition: background-color 0.3s ease;
}

.field-modified {
  background-color: #ffe4cc !important;
  transition: background-color 0.3s ease;
}

/* Field indicators */
.field-indicator {
  margin-left: 0.5rem;
  font-size: 0.75rem;
  font-weight: bold;
  cursor: help;
}

.field-indicator.autofilled {
  color: #856404;
}

.field-indicator.modified {
  color: #cc5500;
}
</style>
