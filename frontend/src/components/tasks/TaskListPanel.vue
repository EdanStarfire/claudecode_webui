<template>
  <div class="task-list-panel" ref="panelRef">
    <!-- Task List -->
    <div class="task-list p-2">
      <div v-if="tasks.length === 0" class="empty-placeholder">
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
    </div>

    <!-- Resize Handle (issue #552) -->
    <div
      v-if="showResizeHandle"
      class="queue-resize-handle"
      :class="{ active: isResizingQueue }"
      @pointerdown="startQueueResize"
    ></div>

    <!-- Message Queue Section (Issue #500) -->
    <QueueSection :height="uiStore.queuePanelHeight" />
  </div>
</template>

<script setup>
import { ref, computed, onBeforeUnmount } from 'vue'
import { useTaskStore } from '@/stores/task'
import { useUIStore } from '@/stores/ui'
import { useQueueStore } from '@/stores/queue'
import { useSessionStore } from '@/stores/session'
import TaskItem from './TaskItem.vue'
import QueueSection from './QueueSection.vue'

const taskStore = useTaskStore()
const uiStore = useUIStore()
const queueStore = useQueueStore()
const sessionStore = useSessionStore()

const panelRef = ref(null)

// Track expanded task details
const expandedTasks = ref(new Set())

// Computed properties
const tasks = computed(() => taskStore.currentTasks)
const taskStats = computed(() => taskStore.currentTaskStats)

// Queue visibility — matches QueueSection's v-if condition
const queueVisible = computed(() => {
  const sessionId = sessionStore.currentSessionId
  if (!sessionId) return false
  const items = queueStore.getItems(sessionId)
  const isPaused = queueStore.isPaused(sessionId) ||
    (sessionStore.sessions.get(sessionId)?.queue_paused || false)
  return items.length > 0 || isPaused
})

const showResizeHandle = computed(() => {
  return queueVisible.value && !uiStore.isMobile
})

function toggleTaskExpansion(taskId) {
  if (expandedTasks.value.has(taskId)) {
    expandedTasks.value.delete(taskId)
  } else {
    expandedTasks.value.add(taskId)
  }
  // Trigger reactivity
  expandedTasks.value = new Set(expandedTasks.value)
}

// --- Queue resize (issue #552) ---
const isResizingQueue = ref(false)
let resizeStartY = 0
let resizeStartHeight = 0

function startQueueResize(e) {
  e.preventDefault()
  isResizingQueue.value = true
  resizeStartY = e.clientY
  resizeStartHeight = uiStore.queuePanelHeight
  document.addEventListener('pointermove', handleQueueResize)
  document.addEventListener('pointerup', stopQueueResize)
  document.body.style.userSelect = 'none'
  document.body.style.cursor = 'row-resize'
}

function handleQueueResize(e) {
  if (!isResizingQueue.value) return
  // Dragging up increases queue height (startY > clientY = positive delta)
  const deltaY = resizeStartY - e.clientY
  const newHeight = resizeStartHeight + deltaY
  const containerHeight = panelRef.value?.clientHeight || 600
  uiStore.setQueuePanelHeight(newHeight, containerHeight)
}

function stopQueueResize() {
  isResizingQueue.value = false
  document.removeEventListener('pointermove', handleQueueResize)
  document.removeEventListener('pointerup', stopQueueResize)
  document.body.style.userSelect = ''
  document.body.style.cursor = ''
}

onBeforeUnmount(() => {
  if (isResizingQueue.value) {
    stopQueueResize()
  }
})
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
  color: #94a3b8;
  font-size: 12px;
  font-style: italic;
}

/* Resize handle (issue #552) */
.queue-resize-handle {
  flex-shrink: 0;
  height: 4px;
  cursor: row-resize;
  background-color: transparent;
  transition: background-color 0.15s ease;
}

.queue-resize-handle:hover,
.queue-resize-handle.active {
  background-color: rgba(0, 0, 0, 0.1);
}

@media (max-width: 767px) {
  .queue-resize-handle {
    display: none;
  }
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
