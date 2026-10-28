<template>
  <div class="task-list-panel">
    <div class="task-list p-2">
      <!-- Issue #2030: the list comes from the server snapshot, never from the loaded message
           window — so until it lands (typically one RTT after the window) say so, and if it
           failed say that, instead of showing an empty or partial list. -->
      <div v-if="isHydrating && tasks.length === 0" class="empty-placeholder" data-testid="tasks-loading">
        <span>Loading tasks…</span>
      </div>
      <div v-else-if="hydrationError && tasks.length === 0" class="empty-placeholder" data-testid="tasks-error">
        <span>Couldn't load tasks.</span>
        <button class="btn btn-sm btn-link py-0" @click="retryHydration">Retry</button>
      </div>
      <div v-else-if="tasks.length === 0 && backgroundCount === 0" class="empty-placeholder">
        <span>Tasks will appear here as the agent works</span>
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

      <BackgroundTasksSection />
    </div>
  </div>
</template>

<script setup>
import { ref, computed } from 'vue'
import { useTaskStore } from '@/stores/task'
import { useBackgroundTasksStore } from '@/stores/backgroundTasks'
import { useSessionStore } from '@/stores/session'
import TaskItem from './TaskItem.vue'
import BackgroundTasksSection from './BackgroundTasksSection.vue'

const taskStore = useTaskStore()

const expandedTasks = ref(new Set())
const tasks = computed(() => taskStore.currentTasks)
const backgroundCount = computed(() => useBackgroundTasksStore().currentCount)
const sessionStore = useSessionStore()
const isHydrating = computed(() => taskStore.isHydrating(sessionStore.currentSessionId))
const hydrationError = computed(() => taskStore.hasHydrationError(sessionStore.currentSessionId))

function retryHydration() {
  taskStore.hydrate(sessionStore.currentSessionId)
}

function toggleTaskExpansion(taskId) {
  if (expandedTasks.value.has(taskId)) {
    expandedTasks.value.delete(taskId)
  } else {
    expandedTasks.value.add(taskId)
  }
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

.empty-placeholder {
  text-align: center;
  padding: 24px 16px;
  color: var(--bs-secondary-color);
  font-size: 12px;
  font-style: italic;
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
