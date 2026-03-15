<template>
  <div class="mcp-server-picker">
    <label class="form-label">MCP Servers</label>
    <div v-if="configStore.loading" class="text-muted small">Loading...</div>
    <div v-else-if="configStore.configList().length === 0" class="text-muted small">
      No global MCP servers configured. Add them in Application Settings.
    </div>
    <div v-else>
      <div
        v-for="config in configStore.configList()"
        :key="config.id"
        class="form-check"
      >
        <input
          class="form-check-input"
          type="checkbox"
          :id="`mcp-pick-${config.id}`"
          :checked="isSelected(config.id)"
          :disabled="!config.enabled"
          @change="toggleServer(config.id, $event.target.checked)"
        />
        <label class="form-check-label" :for="`mcp-pick-${config.id}`">
          <span class="badge bg-info me-1" style="font-size: 0.65rem;">{{ config.type }}</span>
          {{ config.name }}
          <span v-if="!config.enabled" class="text-muted">(disabled)</span>
        </label>
      </div>
    </div>
  </div>
</template>

<script setup>
import { onMounted } from 'vue'
import { useMcpConfigStore } from '@/stores/mcpConfig'

const configStore = useMcpConfigStore()

const props = defineProps({
  modelValue: {
    type: Array,
    default: () => []
  }
})

const emit = defineEmits(['update:modelValue'])

onMounted(() => {
  if (configStore.configList().length === 0) {
    configStore.fetchConfigs()
  }
})

function isSelected(id) {
  return (props.modelValue || []).includes(id)
}

function toggleServer(id, checked) {
  const current = [...(props.modelValue || [])]
  if (checked) {
    if (!current.includes(id)) current.push(id)
  } else {
    const idx = current.indexOf(id)
    if (idx >= 0) current.splice(idx, 1)
  }
  emit('update:modelValue', current)
}
</script>
