<template>
  <aside
    id="right-sidebar"
    class="right-sidebar"
    :class="{
      'resizing': isResizing,
      'theme-red-panel': uiStore.isRedBackground
    }"
    :style="sidebarStyle"
  >
    <!-- Agent Overview Section -->
    <AgentOverview />

    <!-- Tab Navigation (Diff, Tasks, Resources, Comms) -->
    <div class="sidebar-tabs">
      <button
        v-for="tab in tabs"
        :key="tab.id"
        class="sidebar-tab"
        :class="{ active: activeTab === tab.id }"
        @click="uiStore.setRightSidebarTab(tab.id)"
      >
        {{ tab.label }}
        <span v-if="tab.badge > 0" class="tab-badge">{{ tab.badge }}</span>
      </button>
    </div>

    <!-- Tab Content -->
    <div class="tab-content-container">
      <!-- Diff Tab -->
      <DiffPanel v-show="activeTab === 'diff'" />

      <!-- Tasks Tab -->
      <TaskListPanel v-show="activeTab === 'tasks'" />

      <!-- Resources Tab -->
      <ResourceGallery v-show="activeTab === 'resources'" />

      <!-- Comms Tab -->
      <div v-show="activeTab === 'comms'" class="tab-pane comms-pane">
        <div class="comms-placeholder">
          <span>Comms will appear here when agent is part of a Legion</span>
        </div>
      </div>
    </div>

    <!-- Resize Handle -->
    <div class="sidebar-resize-handle" @mousedown="startResize"></div>

    <!-- Modals (teleported to body) -->
    <ResourceFullView />
    <DiffFullView />
  </aside>
</template>

<script setup>
import { computed, ref } from 'vue'
import { useUIStore } from '@/stores/ui'
import { useTaskStore } from '@/stores/task'
import { useResourceStore } from '@/stores/resource'
import { useDiffStore } from '@/stores/diff'
import AgentOverview from './AgentOverview.vue'
import TaskListPanel from '../tasks/TaskListPanel.vue'
import ResourceGallery from '../tasks/ResourceGallery.vue'
import ResourceFullView from '../common/ResourceFullView.vue'
import DiffPanel from '../tasks/DiffPanel.vue'
import DiffFullView from '../common/DiffFullView.vue'

const uiStore = useUIStore()
const taskStore = useTaskStore()
const resourceStore = useResourceStore()
const diffStore = useDiffStore()

const activeTab = computed(() => uiStore.rightSidebarActiveTab)

// Badge counts
const taskStats = computed(() => taskStore.currentTaskStats)
const resourceCount = computed(() => resourceStore.currentResourceCount)
const diffFileCount = computed(() => diffStore.fileCount)

// Tab definitions
const tabs = computed(() => [
  { id: 'diff', label: 'Diff', badge: diffFileCount.value },
  { id: 'tasks', label: 'Tasks', badge: taskStats.value.total > 0 ? taskStats.value.total : 0 },
  { id: 'resources', label: 'Resources', badge: resourceCount.value },
  { id: 'comms', label: 'Comms', badge: 0 }
])

const isOverlay = computed(() => uiStore.windowWidth <= 1024)

const sidebarStyle = computed(() => {
  // In overlay mode, sizing is handled by App.vue CSS
  if (isOverlay.value) return {}
  return {
    width: `${uiStore.rightSidebarWidth}px`,
    minWidth: '200px',
    maxWidth: '30vw'
  }
})

// Resize
const isResizing = ref(false)

function startResize(event) {
  event.preventDefault()
  isResizing.value = true
  document.addEventListener('mousemove', handleResize)
  document.addEventListener('mouseup', stopResize)
}

function handleResize(event) {
  if (isResizing.value) {
    const newWidth = window.innerWidth - event.clientX
    uiStore.setRightSidebarWidth(newWidth)
  }
}

function stopResize() {
  isResizing.value = false
  document.removeEventListener('mousemove', handleResize)
  document.removeEventListener('mouseup', stopResize)
}
</script>

<style scoped>
.right-sidebar {
  display: flex;
  flex-direction: column;
  overflow: hidden;
  background: #ffffff;
  border-left: 1px solid #e2e8f0;
  position: relative;
  transition: width 0.3s ease;
}

.right-sidebar.resizing {
  transition: none;
}

.right-sidebar.theme-red-panel {
  background: #fef2f2;
}

/* Tabs */
.sidebar-tabs {
  display: flex;
  border-bottom: 1px solid #e2e8f0;
  flex-shrink: 0;
}

.sidebar-tab {
  flex: 1;
  padding: 8px 4px;
  border: none;
  border-bottom: 2px solid transparent;
  background: transparent;
  font-size: 12px;
  font-weight: 500;
  color: #64748b;
  cursor: pointer;
  text-align: center;
  transition: all 0.15s;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 4px;
}

.sidebar-tab:hover {
  color: #334155;
  background: #f8fafc;
}

.sidebar-tab.active {
  color: #3b82f6;
  border-bottom-color: #3b82f6;
}

.tab-badge {
  font-size: 10px;
  background: #94a3b8;
  color: white;
  padding: 0 5px;
  border-radius: 8px;
  line-height: 16px;
  min-width: 16px;
}

.sidebar-tab.active .tab-badge {
  background: #3b82f6;
}

/* Tab content */
.tab-content-container {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  overflow-x: hidden;
}

/* Comms placeholder */
.comms-pane {
  padding: 16px;
}

.comms-placeholder {
  text-align: center;
  padding: 32px 16px;
  color: #94a3b8;
  font-size: 12px;
  font-style: italic;
}

/* Resize handle */
.sidebar-resize-handle {
  position: absolute;
  left: 0;
  top: 0;
  width: 4px;
  height: 100%;
  cursor: col-resize;
  z-index: 10;
}

.sidebar-resize-handle:hover {
  background-color: rgba(0, 0, 0, 0.1);
}
</style>
