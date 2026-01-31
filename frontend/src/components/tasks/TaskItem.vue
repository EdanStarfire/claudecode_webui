<template>
  <div
    class="task-item mb-2 rounded border"
    :class="statusClass"
    @click="$emit('toggle')"
  >
    <!-- Task Header -->
    <div class="task-header d-flex align-items-center gap-2 p-2">
      <span class="status-icon">{{ statusIcon }}</span>
      <span class="task-subject flex-grow-1" :title="task.subject">
        {{ task.subject }}
      </span>
      <span class="expand-icon">{{ isExpanded ? '▾' : '▸' }}</span>
    </div>

    <!-- Active Form (for in_progress tasks) -->
    <div v-if="task.status === 'in_progress' && task.activeForm" class="active-form px-2 pb-2">
      <span class="spinner-border spinner-border-sm me-1" role="status"></span>
      <span class="active-form-text">{{ task.activeForm }}</span>
    </div>

    <!-- Expanded Details -->
    <div v-if="isExpanded" class="task-details p-2 border-top">
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

const statusIcon = computed(() => {
  switch (props.task.status) {
    case 'completed':
      return '✅'
    case 'in_progress':
      return '🔄'
    case 'pending':
    default:
      return '⏳'
  }
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
  transition: all 0.2s ease;
  background: white;
}

.task-item:hover {
  box-shadow: 0 2px 4px rgba(0, 0, 0, 0.1);
}

.task-header {
  font-size: 0.9rem;
}

.status-icon {
  font-size: 1rem;
  flex-shrink: 0;
}

.task-subject {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-weight: 500;
}

.expand-icon {
  color: #6c757d;
  flex-shrink: 0;
}

.active-form {
  font-size: 0.85rem;
  color: #0d6efd;
}

.active-form-text {
  font-style: italic;
}

.task-details {
  background: #f8f9fa;
  font-size: 0.85rem;
}

.description-content {
  white-space: pre-wrap;
  word-break: break-word;
}

/* Status-based styling */
.status-pending {
  border-color: #dee2e6 !important;
}

.status-in-progress {
  border-color: #0d6efd !important;
  border-width: 2px !important;
}

.status-completed {
  border-color: #198754 !important;
  opacity: 0.8;
}

.status-completed .task-subject {
  text-decoration: line-through;
  color: #6c757d;
}
</style>
