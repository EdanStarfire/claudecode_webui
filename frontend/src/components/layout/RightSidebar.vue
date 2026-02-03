<template>
  <aside
    id="right-sidebar"
    class="bg-light border-start d-flex flex-column overflow-auto"
    :class="{
      'collapsed': rightSidebarCollapsed,
      'mobile-open': !rightSidebarCollapsed && isMobile,
      'resizing': isResizing
    }"
    :style="sidebarStyle"
  >
    <!-- Task List Panel -->
    <div class="flex-grow-1 overflow-auto">
      <TaskListPanel />

      <!-- Image Gallery (Issue #404) -->
      <div v-if="hasImages" class="border-top">
        <ImageGallery />
      </div>
    </div>

    <!-- Resize Handle -->
    <div
      class="sidebar-resize-handle"
      @mousedown="startResize"
    ></div>

    <!-- Image Full View Modal (teleported to body) -->
    <ImageFullView />
  </aside>
</template>

<script setup>
import { computed, ref } from 'vue'
import { useUIStore } from '@/stores/ui'
import { useImageStore } from '@/stores/image'
import TaskListPanel from '../tasks/TaskListPanel.vue'
import ImageGallery from '../tasks/ImageGallery.vue'
import ImageFullView from '../common/ImageFullView.vue'

const uiStore = useUIStore()
const imageStore = useImageStore()

const rightSidebarCollapsed = computed(() => uiStore.rightSidebarCollapsed)
const isMobile = computed(() => uiStore.isMobile)
const hasImages = computed(() => imageStore.currentHasImages)

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

@media (max-width: 768px) {
  #right-sidebar {
    position: fixed;
    right: 0;
    top: 56px; /* Header height */
    height: calc(100vh - 56px);
    width: 80vw !important;
    max-width: 300px !important;
    z-index: 1042;
    transform: translateX(100%);
  }

  #right-sidebar.mobile-open {
    transform: translateX(0);
  }

  #right-sidebar.collapsed {
    transform: translateX(100%);
  }
}
</style>
