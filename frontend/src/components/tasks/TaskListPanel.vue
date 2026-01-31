<template>
  <div class="task-list-panel">
    <!-- Panel Header -->
    <div class="panel-header d-flex align-items-center justify-content-between p-3 border-bottom">
      <div class="d-flex align-items-center gap-2">
        <span class="panel-icon">📋</span>
        <h6 class="mb-0">Tasks</h6>
        <span v-if="taskStats.total > 0" class="badge bg-secondary">
          {{ taskStats.completed }}/{{ taskStats.total }}
        </span>
      </div>
      <button
        class="btn btn-sm btn-outline-secondary"
        @click="togglePanel"
        :title="isCollapsed ? 'Expand' : 'Collapse'"
      >
        {{ isCollapsed ? '◀' : '▶' }}
      </button>
    </div>

    <!-- Active Task Highlight (if any) -->
    <div v-if="activeTask && !isCollapsed" class="active-task-banner p-2 bg-primary text-white">
      <div class="d-flex align-items-center gap-2">
        <span class="spinner-border spinner-border-sm" role="status"></span>
        <span class="active-text">{{ activeTask.activeForm || activeTask.subject }}</span>
      </div>
    </div>

    <!-- Task List -->
    <div v-if="!isCollapsed" class="task-list p-2">
      <div v-if="tasks.length === 0" class="text-muted text-center py-4">
        <span class="empty-icon">📝</span>
        <p class="mb-0 small">No tasks yet</p>
      </div>

      <TransitionGroup name="task-list" tag="div">
        <TaskItem
          v-for="task in tasks"
          :key="task.id"
          :task="task"
          :isExpanded="expandedTasks.has(task.id)"
          @toggle="toggleTaskExpansion(task.id)"
        />
      </TransitionGroup>
    </div>
  </div>
</template>

<script setup>
import { ref, computed } from 'vue'
import { useTaskStore } from '@/stores/task'
import { useUIStore } from '@/stores/ui'
import TaskItem from './TaskItem.vue'

const taskStore = useTaskStore()
const uiStore = useUIStore()

// Track expanded task details
const expandedTasks = ref(new Set())

// Panel collapse state (different from sidebar collapse)
const isCollapsed = ref(false)

// Computed properties
const tasks = computed(() => taskStore.currentTasks)
const activeTask = computed(() => taskStore.currentActiveTask)
const taskStats = computed(() => taskStore.currentTaskStats)

function togglePanel() {
  isCollapsed.value = !isCollapsed.value
}

function toggleTaskExpansion(taskId) {
  if (expandedTasks.value.has(taskId)) {
    expandedTasks.value.delete(taskId)
  } else {
    expandedTasks.value.add(taskId)
  }
  // Trigger reactivity
  expandedTasks.value = new Set(expandedTasks.value)
}
</script>

<style scoped>
.task-list-panel {
  height: 100%;
  display: flex;
  flex-direction: column;
}

.panel-header {
  background-color: #f8f9fa;
  flex-shrink: 0;
}

.panel-icon {
  font-size: 1.2rem;
}

.active-task-banner {
  flex-shrink: 0;
  font-size: 0.9rem;
}

.active-text {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.task-list {
  flex-grow: 1;
  overflow-y: auto;
}

.empty-icon {
  font-size: 2rem;
  display: block;
  margin-bottom: 0.5rem;
}

/* Task list transition animations */
.task-list-enter-active,
.task-list-leave-active {
  transition: all 0.3s ease;
}

.task-list-enter-from,
.task-list-leave-to {
  opacity: 0;
  transform: translateX(20px);
}

.task-list-move {
  transition: transform 0.3s ease;
}
</style>
