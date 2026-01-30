<template>
  <div class="permissions-tab">
    <!-- Allowed Tools -->
    <div class="mb-3">
      <label for="config-allowed-tools" class="form-label">Allowed Tools</label>
      <div class="allowed-tools-editor">
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
            placeholder="Add tool (e.g., bash, read, edit)"
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

    <!-- Capabilities (template and session) -->
    <div class="mb-3">
      <label for="config-capabilities" class="form-label">Capabilities</label>
      <div class="capabilities-editor">
        <!-- Current capabilities as tags -->
        <div class="capabilities-list mb-2" v-if="capabilitiesList.length > 0">
          <span
            v-for="(cap, index) in capabilitiesList"
            :key="index"
            class="badge bg-info me-1 mb-1 capability-badge"
          >
            {{ cap }}
            <button
              type="button"
              class="btn-close btn-close-white ms-1"
              @click="removeCapability(index)"
              aria-label="Remove capability"
            ></button>
          </span>
        </div>

        <!-- Add capability input -->
        <div class="input-group input-group-sm">
          <input
            type="text"
            class="form-control"
            v-model="newCapability"
            @keydown.enter.prevent="addCapability"
            placeholder="Add capability (e.g., python, testing)"
          />
          <button
            type="button"
            class="btn btn-outline-info"
            @click="addCapability"
            :disabled="!newCapability.trim()"
          >
            Add
          </button>
        </div>
      </div>
      <div class="form-text">Comma-separated list of capability keywords for discovery</div>
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
          :value="formData.allowed_tools"
          @input="$emit('update:form-data', 'allowed_tools', $event.target.value)"
          placeholder="bash, read, edit, write, glob, grep"
        />

        <label class="form-label small text-muted mt-2">Capabilities (comma-separated)</label>
        <input
          type="text"
          class="form-control form-control-sm"
          :value="formData.capabilities"
          @input="$emit('update:form-data', 'capabilities', $event.target.value)"
          placeholder="python, testing, debugging"
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
  }
})

const emit = defineEmits(['update:form-data'])

// Local state
const newTool = ref('')
const newCapability = ref('')
const showRawInput = ref(false)

// Common tools for quick add
const commonTools = ['bash', 'read', 'edit', 'write', 'glob', 'grep', 'webfetch', 'websearch', 'task', 'todo']

// Computed
const isTemplateMode = computed(() => props.mode === 'create-template' || props.mode === 'edit-template')

const toolsList = computed(() => {
  if (!props.formData.allowed_tools || !props.formData.allowed_tools.trim()) return []
  return props.formData.allowed_tools
    .split(',')
    .map(t => t.trim())
    .filter(t => t.length > 0)
})

const capabilitiesList = computed(() => {
  if (!props.formData.capabilities || !props.formData.capabilities.trim()) return []
  return props.formData.capabilities
    .split(',')
    .map(c => c.trim())
    .filter(c => c.length > 0)
})

// Methods
function addTool() {
  const tool = newTool.value.trim().toLowerCase()
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

function addCapability() {
  const cap = newCapability.value.trim().toLowerCase()
  if (!cap) return

  if (!capabilitiesList.value.includes(cap)) {
    const newList = [...capabilitiesList.value, cap]
    emit('update:form-data', 'capabilities', newList.join(', '))
  }
  newCapability.value = ''
}

function removeCapability(index) {
  const newList = [...capabilitiesList.value]
  newList.splice(index, 1)
  emit('update:form-data', 'capabilities', newList.join(', '))
}
</script>

<style scoped>
.tool-badge,
.capability-badge {
  font-size: 0.875rem;
  padding: 0.35em 0.65em;
  display: inline-flex;
  align-items: center;
}

.tool-badge .btn-close,
.capability-badge .btn-close {
  font-size: 0.5rem;
  padding: 0.25em;
}

.allowed-tools-editor,
.capabilities-editor {
  background-color: var(--bs-gray-100);
  padding: 0.75rem;
  border-radius: 0.375rem;
}

.tools-list,
.capabilities-list {
  min-height: 1.5rem;
}
</style>
