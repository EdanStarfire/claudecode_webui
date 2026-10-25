<template>
  <div v-if="tasks.length > 0" class="background-tasks-section" data-testid="background-tasks-section">
    <div class="section-header">Background ({{ tasks.length }})</div>
    <ul class="background-task-list">
      <li
        v-for="task in tasks"
        :key="task.task_id"
        class="background-task-row"
        :class="{ expanded: expanded.has(task.task_id) }"
        :title="task.description"
        data-testid="background-task-row"
        @click="toggle(task.task_id)"
      >
        <span class="type-chip" :class="chipClass(task.task_type)">{{ task.task_type || 'task' }}</span>
        <span class="task-description">{{ task.description }}</span>
      </li>
    </ul>
  </div>
</template>

<script setup>
import { ref, computed } from 'vue'
import { useBackgroundTasksStore } from '@/stores/backgroundTasks'

const store = useBackgroundTasksStore()
const tasks = computed(() => store.currentTasks)
const expanded = ref(new Set())

const KNOWN_TYPES = new Set(['local_agent', 'local_bash', 'local_workflow'])

function chipClass(type) {
  return KNOWN_TYPES.has(type) ? `type-${type}` : 'type-unknown'
}

function toggle(taskId) {
  const next = new Set(expanded.value)
  if (next.has(taskId)) next.delete(taskId)
  else next.add(taskId)
  expanded.value = next
}
</script>

<style scoped>
.background-tasks-section {
  margin-top: 8px;
}

.section-header {
  font-size: 11px;
  font-weight: 600;
  text-transform: uppercase;
  color: var(--bs-secondary-color);
  padding: 4px 4px;
}

.background-task-list {
  list-style: none;
  margin: 0;
  padding: 0;
  max-height: 240px;
  overflow-y: auto;
}

.background-task-row {
  display: flex;
  align-items: baseline;
  gap: 6px;
  padding: 3px 4px;
  font-size: 12px;
  cursor: pointer;
}

.background-task-row:hover {
  background: var(--bs-tertiary-bg);
}

.type-chip {
  flex: 0 0 auto;
  font-size: 10px;
  padding: 0 6px;
  border-radius: 8px;
  background: var(--bs-secondary-bg);
  color: var(--bs-secondary-color);
  border: 1px solid var(--bs-border-color);
}

.type-local_agent { color: var(--bs-primary); border-color: var(--bs-primary); }
.type-local_bash { color: var(--bs-success); border-color: var(--bs-success); }
.type-local_workflow { color: var(--bs-info); border-color: var(--bs-info); }

.task-description {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.background-task-row.expanded .task-description {
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}
</style>
