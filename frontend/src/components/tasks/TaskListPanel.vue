<template>
  <div class="task-list-panel">
    <!-- Task List -->
    <div class="task-list p-2">
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
import TaskItem from './TaskItem.vue'

const taskStore = useTaskStore()

// Track expanded task details
const expandedTasks = ref(new Set())

// Computed properties
const tasks = computed(() => taskStore.currentTasks)
const taskStats = computed(() => taskStore.currentTaskStats)

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
  display: flex;
  flex-direction: column;
  height: 100%;
}

.task-list {
  flex: 1 1 auto;
  min-height: 0;
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
