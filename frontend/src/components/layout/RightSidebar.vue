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
    <!-- Stacked Panels Container -->
    <div class="panels-container">
      <!-- Task List Panel -->
      <TaskListPanel />

      <!-- Resource Gallery (Issue #404) -->
      <ResourceGallery />
    </div>

    <!-- Resize Handle -->
    <div
      class="sidebar-resize-handle"
      @mousedown="startResize"
    ></div>

    <!-- Resource Full View Modal (teleported to body) -->
    <ResourceFullView />
  </aside>
</template>

<script setup>
import { computed, ref } from 'vue'
import { useUIStore } from '@/stores/ui'
import TaskListPanel from '../tasks/TaskListPanel.vue'
import ResourceGallery from '../tasks/ResourceGallery.vue'
import ResourceFullView from '../common/ResourceFullView.vue'

const uiStore = useUIStore()

const rightSidebarCollapsed = computed(() => uiStore.rightSidebarCollapsed)
const isMobile = computed(() => uiStore.isMobile)

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

/* Panels container - stacked collapsible panels */
.panels-container {
  display: flex;
  flex-direction: column;
  height: 100%;
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
