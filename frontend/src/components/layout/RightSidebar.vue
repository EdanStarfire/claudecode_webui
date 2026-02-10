<template>
  <aside
    id="right-sidebar"
    class="border-start d-flex flex-column overflow-auto"
    :class="{
      'collapsed': rightSidebarCollapsed,
      'mobile-open': !rightSidebarCollapsed && isMobile,
      'resizing': isResizing,
      'theme-red-panel': uiStore.isRedBackground,
      'bg-light': !uiStore.isRedBackground
    }"
    :style="sidebarStyle"
  >
    <!-- Tab Navigation -->
    <ul class="nav nav-tabs sidebar-tabs" role="tablist">
      <li class="nav-item" role="presentation">
        <button
          class="nav-link"
          :class="{ active: activeTab === 'tasks' }"
          type="button"
          @click="uiStore.setRightSidebarTab('tasks')"
        >
          Tasks
          <span v-if="taskStats.total > 0" class="tab-badge">
            {{ taskStats.completed }}/{{ taskStats.total }}
          </span>
        </button>
      </li>
      <li class="nav-item" role="presentation">
        <button
          class="nav-link"
          :class="{ active: activeTab === 'diff' }"
          type="button"
          @click="uiStore.setRightSidebarTab('diff')"
        >
          Diff
          <span v-if="diffFileCount > 0" class="tab-badge">
            {{ diffFileCount }}
          </span>
        </button>
      </li>
      <li class="nav-item" role="presentation">
        <button
          class="nav-link"
          :class="{ active: activeTab === 'resources' }"
          type="button"
          @click="uiStore.setRightSidebarTab('resources')"
        >
          Resources
          <span v-if="resourceCount > 0" class="tab-badge">
            {{ resourceCount }}
          </span>
        </button>
      </li>
      <li class="nav-item" role="presentation">
        <button
          class="nav-link"
          :class="{ active: activeTab === 'files' }"
          type="button"
          @click="uiStore.setRightSidebarTab('files')"
        >
          Files
        </button>
      </li>
    </ul>

    <!-- Tab Content -->
    <div class="tab-content-container">
      <TaskListPanel v-show="activeTab === 'tasks'" />
      <DiffPanel v-show="activeTab === 'diff'" />
      <ResourceGallery v-show="activeTab === 'resources'" />
      <FileBrowserPanel v-show="activeTab === 'files'" />
    </div>

    <!-- Resize Handle -->
    <div
      class="sidebar-resize-handle"
      @mousedown="startResize"
    ></div>

    <!-- Resource Full View Modal (teleported to body) -->
    <ResourceFullView />

    <!-- Diff Full View Modal (teleported to body, Issue #435) -->
    <DiffFullView />

    <!-- File Viewer Modal (teleported to body, Issue #437) -->
    <FileViewerModal />
  </aside>
</template>

<script setup>
import { computed, ref } from 'vue'
import { useUIStore } from '@/stores/ui'
import { useTaskStore } from '@/stores/task'
import { useResourceStore } from '@/stores/resource'
import { useDiffStore } from '@/stores/diff'
import TaskListPanel from '../tasks/TaskListPanel.vue'
import ResourceGallery from '../tasks/ResourceGallery.vue'
import ResourceFullView from '../common/ResourceFullView.vue'
import DiffPanel from '../tasks/DiffPanel.vue'
import DiffFullView from '../common/DiffFullView.vue'
import FileBrowserPanel from '../tasks/FileBrowserPanel.vue'
import FileViewerModal from '../common/FileViewerModal.vue'

const uiStore = useUIStore()
const taskStore = useTaskStore()
const resourceStore = useResourceStore()
const diffStore = useDiffStore()

const rightSidebarCollapsed = computed(() => uiStore.rightSidebarCollapsed)
const isMobile = computed(() => uiStore.isMobile)
const activeTab = computed(() => uiStore.rightSidebarActiveTab)

// Badge counts for tabs
const taskStats = computed(() => taskStore.currentTaskStats)
const resourceCount = computed(() => resourceStore.currentResourceCount)
const diffFileCount = computed(() => diffStore.fileCount)

const sidebarStyle = computed(() => {
  // On mobile, let CSS handle the transform via classes
  if (isMobile.value) {
    return {}
  }
  // On desktop, apply custom width
  const width = `${uiStore.rightSidebarWidth}px`
  return {
    width: width,
    minWidth: '200px',
    maxWidth: '30vw',
    '--sidebar-width': width
  }
})

// Sidebar resize functionality
const isResizing = ref(false)

function startResize(event) {
  event.preventDefault()
  isResizing.value = true
  document.addEventListener('mousemove', handleResize)
  document.addEventListener('mouseup', stopResize)
}

function handleResize(event) {
  if (isResizing.value) {
    // For right sidebar, calculate width from right edge
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
#right-sidebar {
  position: relative;
  transition: transform 0.3s ease, width 0.3s ease;
}

#right-sidebar.collapsed {
  width: 0 !important;
  min-width: 0 !important;
  overflow: hidden;
}

#right-sidebar.resizing {
  transition: none;
}

/* Tab navigation */
.sidebar-tabs {
  flex-shrink: 0;
  border-bottom: 1px solid #dee2e6;
  padding: 0 4px;
  background-color: #f8f9fa;
}

.sidebar-tabs .nav-item {
  flex: 1;
}

.sidebar-tabs .nav-link {
  font-size: 0.78rem;
  padding: 8px 4px;
  text-align: center;
  white-space: nowrap;
  color: #6c757d;
  border: none;
  border-bottom: 2px solid transparent;
  border-radius: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 4px;
}

.sidebar-tabs .nav-link:hover {
  color: #495057;
  border-bottom-color: #dee2e6;
}

.sidebar-tabs .nav-link.active {
  color: #0d6efd;
  border-bottom-color: #0d6efd;
  background: transparent;
}

.tab-badge {
  font-size: 0.65rem;
  background: #6c757d;
  color: white;
  padding: 1px 5px;
  border-radius: 10px;
  line-height: 1.2;
}

.nav-link.active .tab-badge {
  background: #0d6efd;
}

/* Tab content - fills remaining space and scrolls */
.tab-content-container {
  flex: 1 1 auto;
  min-height: 0;
  overflow-y: auto;
  overflow-x: hidden;
}

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

/* Mobile styles moved to styles.css to match left sidebar pattern */
</style>
