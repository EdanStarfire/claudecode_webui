<template>
  <div v-if="minion" class="minion-tree-node" :style="{ marginLeft: `${indent}px` }">
    <div class="minion-card p-2 border-start border-2 mb-2">
      <div class="d-flex align-items-center">
        <!-- State Icon -->
        <span class="me-2">{{ stateIcon }}</span>

        <!-- Overseer Icon -->
        <span v-if="isOverseerWithChildren" class="me-2">👑</span>

        <!-- Minion Name -->
        <strong>{{ minion.name }}</strong>

        <!-- Role -->
        <span v-if="minion.role" class="text-muted ms-2">{{ minion.role }}</span>

        <!-- Children Count Badge -->
        <span v-if="isOverseerWithChildren" class="badge bg-secondary ms-2">
          {{ minion.child_minion_ids.length }} {{ minion.child_minion_ids.length === 1 ? 'child' : 'children' }}
        </span>
      </div>

      <!-- Metadata -->
      <div class="text-muted small ms-4">
        Level {{ minion.overseer_level || 0 }} • {{ minion.state }}
      </div>
    </div>

    <!-- Recursively Render Children -->
    <div v-if="hasChildren" class="minion-children">
      <MinionTreeNode
        v-for="childId in minion.child_minion_ids"
        :key="childId"
        :minion-id="childId"
        :level="level + 1"
      />
    </div>
  </div>

  <!-- Debug: Show if minion data is missing -->
  <div v-else-if="props.minionId" class="text-danger small ms-4">
    ⚠ Minion data not found for ID: {{ props.minionId }}
  </div>
</template>

<script setup>
import { computed } from 'vue'
import { useSessionStore } from '../../stores/session'

const props = defineProps({
  minionId: {
    type: String,
    required: true
  },
  level: {
    type: Number,
    default: 0
  }
})

const sessionStore = useSessionStore()

// Get minion session
const minion = computed(() => sessionStore.sessions.get(props.minionId))

// Indentation (24px per level)
const indent = computed(() => props.level * 24)

// Check if minion has children
const hasChildren = computed(() => {
  const result = (
    minion.value &&
    minion.value.child_minion_ids &&
    minion.value.child_minion_ids.length > 0
  )

  // Debug logging
  if (minion.value && minion.value.child_minion_ids && minion.value.child_minion_ids.length > 0) {
    console.log(`Minion ${minion.value.name} has ${minion.value.child_minion_ids.length} children:`, minion.value.child_minion_ids)
  }

  return result
})

// Check if minion is an overseer with children
const isOverseerWithChildren = computed(() => {
  return (
    minion.value &&
    minion.value.is_overseer &&
    hasChildren.value
  )
})

// State icons
const stateIcons = {
  created: '○',
  starting: '◐',
  active: '●',
  paused: '⏸',
  terminating: '◍',
  terminated: '✗',
  error: '⚠'
}

// Get state icon
const stateIcon = computed(() => {
  return stateIcons[minion.value?.state] || '○'
})
</script>

<style scoped>
.minion-card {
  background-color: white;
  border-color: #dee2e6;
}

.minion-tree-node {
  transition: margin-left 0.2s ease;
}
</style>
