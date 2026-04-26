<template>
  <div class="audit-filter-bar d-flex flex-wrap gap-2 align-items-center py-2 px-3 border-bottom">
    <!-- Time range presets -->
    <div class="btn-group btn-group-sm">
      <button
        v-for="preset in timePresets"
        :key="preset.seconds"
        class="btn btn-outline-secondary"
        :class="{ active: activePreset === preset.seconds }"
        @click="selectPreset(preset.seconds)"
      >{{ preset.label }}</button>
    </div>

    <!-- Project filter -->
    <select
      class="form-select form-select-sm"
      style="width:auto;min-width:120px"
      :value="filters.projectId"
      @change="e => updateFilter('projectId', e.target.value || null)"
    >
      <option value="">All projects</option>
      <option v-for="p in projects" :key="p.project_id" :value="p.project_id">{{ p.name }}</option>
    </select>

    <!-- Event type filter chips -->
    <div class="d-flex gap-1 flex-wrap">
      <button
        v-for="et in eventTypeOptions"
        :key="et.value"
        class="btn btn-sm px-2 py-0"
        :class="isEtActive(et.value) ? et.activeClass : 'btn-outline-secondary'"
        style="font-size:0.75rem"
        @click="toggleEventType(et.value)"
      >{{ et.label }}</button>
    </div>

    <!-- Refresh button -->
    <button class="btn btn-sm btn-outline-secondary ms-auto" @click="$emit('refresh')" title="Refresh">
      ↺
    </button>
  </div>
</template>

<script setup>
import { ref, computed } from 'vue'
import { useAuditStore } from '../../stores/audit.js'
import { useProjectStore } from '../../stores/project.js'

const emit = defineEmits(['refresh'])

const auditStore = useAuditStore()
const projectStore = useProjectStore()

const filters = computed(() => auditStore.filters)
const projects = computed(() => Array.from(projectStore.projects.values()))

const timePresets = [
  { label: '15m', seconds: 900 },
  { label: '1h', seconds: 3600 },
  { label: '6h', seconds: 21600 },
  { label: '24h', seconds: 86400 },
]

const eventTypeOptions = [
  { value: 'tool_call', label: 'Tools', activeClass: 'btn-success' },
  { value: 'permission', label: 'Perms', activeClass: 'btn-info' },
  { value: 'lifecycle', label: 'Lifecycle', activeClass: 'btn-secondary' },
  { value: 'comm', label: 'Comms', activeClass: 'btn-primary' },
  { value: 'watchdog', label: 'Watchdog', activeClass: 'btn-warning' },
]

const activePreset = ref(3600)

function selectPreset(seconds) {
  activePreset.value = seconds
  auditStore.setTimeRange(seconds)
  emit('refresh')
}

function updateFilter(key, value) {
  auditStore.setFilters({ [key]: value })
  emit('refresh')
}

function isEtActive(value) {
  const types = filters.value.eventTypes
  return types.length === 0 || types.includes(value)
}

function toggleEventType(value) {
  const current = [...filters.value.eventTypes]
  if (current.length === 0) {
    // All active → deactivate all except this one
    auditStore.setFilters({ eventTypes: eventTypeOptions.map(e => e.value).filter(e => e !== value) })
  } else if (current.includes(value)) {
    const next = current.filter(e => e !== value)
    auditStore.setFilters({ eventTypes: next.length === eventTypeOptions.length ? [] : next })
  } else {
    const next = [...current, value]
    auditStore.setFilters({ eventTypes: next.length === eventTypeOptions.length ? [] : next })
  }
  emit('refresh')
}
</script>

<style scoped>
.audit-filter-bar { background: var(--bs-body-bg); }
</style>
