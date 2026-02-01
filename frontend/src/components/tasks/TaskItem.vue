<template>
  <div
    class="task-item"
    :class="statusClass"
    @click="$emit('toggle')"
  >
    <!-- Task Row -->
    <div class="task-row d-flex align-items-center gap-2">
      <!-- Status Icon or Spinner -->
      <span v-if="showSpinner" class="status-icon">
        <span class="spinner-border spinner-border-sm" role="status" aria-label="Task in progress"></span>
      </span>
      <span v-else class="status-icon">{{ statusIcon }}</span>

      <!-- Task Text (activeForm when in_progress, otherwise subject) -->
      <span class="task-text flex-grow-1" :title="task.subject">
        {{ displayText }}
      </span>

      <!-- Chevron -->
      <svg
        class="chevron-icon"
        :class="{ expanded: isExpanded }"
        width="12"
        height="12"
        viewBox="0 0 12 12"
      >
        <path
          d="M4.5 2L8.5 6L4.5 10"
          stroke="currentColor"
          stroke-width="2"
          stroke-linecap="round"
          stroke-linejoin="round"
          fill="none"
        />
      </svg>
    </div>

    <!-- Expanded Details -->
    <div v-if="isExpanded" class="task-details">
      <!-- Description -->
      <div v-if="task.description" class="task-description mb-2">
        <small class="text-muted d-block mb-1">Description:</small>
        <div class="description-content">{{ task.description }}</div>
      </div>

      <!-- Owner -->
      <div v-if="task.owner" class="task-owner mb-2">
        <small class="text-muted">Owner:</small>
        <span class="ms-1">{{ task.owner }}</span>
      </div>

      <!-- Dependencies -->
      <div v-if="task.blockedBy?.length > 0" class="task-blocked-by mb-2">
        <small class="text-muted d-block">Blocked by:</small>
        <span
          v-for="depId in task.blockedBy"
          :key="depId"
          class="badge bg-warning text-dark me-1"
        >
          #{{ depId }}
        </span>
      </div>

      <div v-if="task.blocks?.length > 0" class="task-blocks">
        <small class="text-muted d-block">Blocks:</small>
        <span
          v-for="depId in task.blocks"
          :key="depId"
          class="badge bg-info text-dark me-1"
        >
          #{{ depId }}
        </span>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed } from 'vue'

const props = defineProps({
  task: {
    type: Object,
    required: true
  },
  isExpanded: {
    type: Boolean,
    default: false
  }
})

defineEmits(['toggle'])

const showSpinner = computed(() => {
  return props.task.status === 'in_progress'
})

const statusIcon = computed(() => {
  switch (props.task.status) {
    case 'completed':
      return '✅'
    case 'pending':
    default:
      return '⏳'
  }
})

const displayText = computed(() => {
  if (props.task.status === 'in_progress' && props.task.activeForm) {
    return props.task.activeForm
  }
  return props.task.subject
})

const statusClass = computed(() => {
  switch (props.task.status) {
    case 'completed':
      return 'status-completed'
    case 'in_progress':
      return 'status-in-progress'
    case 'pending':
    default:
      return 'status-pending'
  }
})
</script>

<style scoped>
.task-item {
  cursor: pointer;
  transition: background-color 0.2s ease;
}

.task-item:hover {
  background-color: #f8f9fa;
}

.task-row {
  padding: 0.5rem 0.75rem;
  font-size: 0.9rem;
}

.status-icon {
  font-size: 1rem;
  flex-shrink: 0;
  width: 1.25rem;
  text-align: center;
}

.spinner-border-sm {
  width: 1rem;
  height: 1rem;
  border-width: 0.15em;
}

.task-text {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-weight: 500;
}

.chevron-icon {
  color: #6c757d;
  flex-shrink: 0;
  transition: transform 0.2s ease;
}

.chevron-icon.expanded {
  transform: rotate(90deg);
}

.task-details {
  padding: 0.5rem 0.75rem 0.5rem 2.5rem;
  background: #f8f9fa;
  font-size: 0.85rem;
  border-left: 3px solid #0d6efd;
  margin-left: 0.75rem;
}

.description-content {
  white-space: pre-wrap;
  word-break: break-word;
}

/* Status-based styling */
.status-pending .task-text {
  color: #495057;
}

.status-in-progress .task-text {
  color: #0d6efd;
  font-style: italic;
}

.status-in-progress .spinner-border {
  color: #0d6efd;
}

.status-completed {
  opacity: 0.7;
}

.status-completed .task-text {
  text-decoration: line-through;
  color: #6c757d;
}
</style>
