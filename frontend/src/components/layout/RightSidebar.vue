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

    <!-- Collapsible Panel Stack -->
    <div class="panel-stack">
      <!-- Tasks -->
      <CollapsiblePanel
        ref="tasksPanelRef"
        id="tasks"
        title="Tasks"
        :expanded="panels.tasks.expanded"
        :badge="taskBadge"
        :flex-weight="panels.tasks.weight"
        :show-resize-handle="resizeHandleVisible.tasks"
        @update:expanded="uiStore.togglePanel('tasks')"
        @resize-start="onResizeStart"
        @resize-move="onResizeMove"
        @resize-end="onResizeEnd"
      >
        <TaskListPanel />
      </CollapsiblePanel>

      <!-- Resources -->
      <CollapsiblePanel
        ref="resourcesPanelRef"
        id="resources"
        title="Resources"
        :expanded="panels.resources.expanded"
        :badge="resourceBadge"
        :flex-weight="panels.resources.weight"
        :show-resize-handle="resizeHandleVisible.resources"
        @update:expanded="uiStore.togglePanel('resources')"
        @resize-start="onResizeStart"
        @resize-move="onResizeMove"
        @resize-end="onResizeEnd"
      >
        <ResourceGallery />
      </CollapsiblePanel>

      <!-- Queued Messages — QueueSection wraps CollapsiblePanel internally -->
      <QueueSection
        ref="queuePanelRef"
        :expanded="panels.queue.expanded"
        :flex-weight="panels.queue.weight"
        :show-resize-handle="resizeHandleVisible.queue"
        :badge="queueBadge"
        @update:expanded="uiStore.togglePanel('queue')"
        @resize-start="onResizeStart"
        @resize-move="onResizeMove"
        @resize-end="onResizeEnd"
      />

      <!-- Diffs -->
      <CollapsiblePanel
        ref="diffsPanelRef"
        id="diffs"
        title="Diffs"
        :expanded="panels.diffs.expanded"
        :badge="diffBadge"
        :flex-weight="panels.diffs.weight"
        :show-resize-handle="resizeHandleVisible.diffs"
        @update:expanded="uiStore.togglePanel('diffs')"
        @resize-start="onResizeStart"
        @resize-move="onResizeMove"
        @resize-end="onResizeEnd"
      >
        <DiffPanel />
      </CollapsiblePanel>

      <!-- Edits -->
      <CollapsiblePanel
        ref="editsPanelRef"
        id="edits"
        title="Edits"
        :expanded="panels.edits.expanded"
        :badge="editsBadge"
        :flex-weight="panels.edits.weight"
        :show-resize-handle="resizeHandleVisible.edits"
        @update:expanded="uiStore.togglePanel('edits')"
        @resize-start="onResizeStart"
        @resize-move="onResizeMove"
        @resize-end="onResizeEnd"
      >
        <EditHistoryPanel />
      </CollapsiblePanel>

      <!-- Proxy — always mounted; shows empty state when proxy is not configured -->
      <CollapsiblePanel
        ref="proxyPanelRef"
        id="proxy"
        title="Proxy"
        :expanded="panels.proxy.expanded"
        :badge="proxyBadge"
        :flex-weight="panels.proxy.weight"
        :show-resize-handle="resizeHandleVisible.proxy"
        @update:expanded="uiStore.togglePanel('proxy')"
        @resize-start="onResizeStart"
        @resize-move="onResizeMove"
        @resize-end="onResizeEnd"
      >
        <ProxyPanel />
      </CollapsiblePanel>

      <!-- Schedules — wraps CollapsiblePanel internally, like QueueSection -->
      <SchedulesPanel
        ref="schedulesPanelRef"
        :expanded="panels.schedules.expanded"
        :flex-weight="panels.schedules.weight"
        :show-resize-handle="false"
        :badge="scheduleBadge"
        @update:expanded="uiStore.togglePanel('schedules')"
        @resize-start="onResizeStart"
        @resize-move="onResizeMove"
        @resize-end="onResizeEnd"
      />
    </div>

    <!-- Left-edge resize handle (sidebar width) -->
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
import { useSessionStore } from '@/stores/session'
import { useEditHistoryStore } from '@/stores/editHistory'
import { useQueueStore } from '@/stores/queue'
import { useProxyStore } from '@/stores/proxy'
import { useScheduleStore } from '@/stores/schedule'
import AgentOverview from './AgentOverview.vue'
import CollapsiblePanel from './CollapsiblePanel.vue'
import TaskListPanel from '../tasks/TaskListPanel.vue'
import ResourceGallery from '../tasks/ResourceGallery.vue'
import ResourceFullView from '../common/ResourceFullView.vue'
import DiffPanel from '../tasks/DiffPanel.vue'
import DiffFullView from '../common/DiffFullView.vue'
import EditHistoryPanel from '../tasks/EditHistoryPanel.vue'
import QueueSection from '../tasks/QueueSection.vue'
import ProxyPanel from '../tasks/ProxyPanel.vue'
import SchedulesPanel from '../tasks/SchedulesPanel.vue'

const uiStore = useUIStore()
const taskStore = useTaskStore()
const resourceStore = useResourceStore()
const diffStore = useDiffStore()
const sessionStore = useSessionStore()
const editHistoryStore = useEditHistoryStore()
const queueStore = useQueueStore()
const proxyStore = useProxyStore()
const scheduleStore = useScheduleStore()

const panels = computed(() => uiStore.rightSidebarPanels)

const PANEL_IDS = ['tasks', 'resources', 'queue', 'diffs', 'edits', 'proxy', 'schedules']
const MIN_PANEL_HEIGHT_PX = 60

// Badge counts — only non-null when count > 0 (CollapsiblePanel only renders badge when truthy)
const taskBadge = computed(() => {
  const total = taskStore.currentTaskStats.total
  return total > 0 ? total : null
})
const resourceBadge = computed(() => {
  const total = resourceStore.currentPagination.total
  const count = total > 0 ? total : resourceStore.currentResourceCount
  return count > 0 ? count : null
})
const diffBadge = computed(() => {
  const c = diffStore.fileCount
  return c > 0 ? c : null
})
const editsBadge = computed(() => {
  const c = editHistoryStore.entryCount(sessionStore.currentSessionId)
  return c > 0 ? c : null
})
const proxyBadge = computed(() => {
  const c = proxyStore.currentTotalCount
  return c > 0 ? c : null
})
const queueBadge = computed(() => {
  const sid = sessionStore.currentSessionId
  if (!sid) return null
  const c = queueStore.getPendingCount(sid)
  return c > 0 ? c : null
})
const scheduleBadge = computed(() => {
  const session = sessionStore.currentSession
  if (!session) return null
  const sched = scheduleStore.getSchedules(session.project_id) || []
  const sid = session.session_id
  const active = sched.filter(s =>
    s.status === 'active' &&
    (s.minion_id === sid || s.ephemeral_agent_id === sid)
  ).length
  return active > 0 ? active : null
})

// Whether each panel should show the inter-panel resize handle:
// true only when this panel is expanded AND any subsequent panel is also expanded
const resizeHandleVisible = computed(() => {
  const p = uiStore.rightSidebarPanels
  const result = {}
  for (let i = 0; i < PANEL_IDS.length; i++) {
    const id = PANEL_IDS[i]
    if (!p[id]?.expanded) {
      result[id] = false
      continue
    }
    let hasFollowingOpen = false
    for (let j = i + 1; j < PANEL_IDS.length; j++) {
      if (p[PANEL_IDS[j]]?.expanded) {
        hasFollowingOpen = true
        break
      }
    }
    result[id] = hasFollowingOpen
  }
  return result
})

// Template refs for measuring panel heights during drag-resize
const tasksPanelRef = ref(null)
const resourcesPanelRef = ref(null)
const queuePanelRef = ref(null)
const diffsPanelRef = ref(null)
const editsPanelRef = ref(null)
const proxyPanelRef = ref(null)
const schedulesPanelRef = ref(null)

const panelRefMap = {
  tasks: tasksPanelRef,
  resources: resourcesPanelRef,
  queue: queuePanelRef,
  diffs: diffsPanelRef,
  edits: editsPanelRef,
  proxy: proxyPanelRef,
  schedules: schedulesPanelRef,
}

function getPanelHeight(id) {
  const el = panelRefMap[id]?.value?.$el
  return el ? el.getBoundingClientRect().height : 0
}

function getNextOpenPanel(panelId) {
  const idx = PANEL_IDS.indexOf(panelId)
  const p = uiStore.rightSidebarPanels
  for (let i = idx + 1; i < PANEL_IDS.length; i++) {
    if (p[PANEL_IDS[i]]?.expanded) return PANEL_IDS[i]
  }
  return null
}

// --- Inter-panel drag-resize state ---
let _resizingPanelId = null
let _resizingNextId = null
let _resizeStartH = {}    // { panelId: px } captured at drag start
let _resizeAccumY = 0
let _pendingResizeWeights = null
let _resizeRafId = null

function onResizeStart({ panelId }) {
  _resizingPanelId = panelId
  _resizingNextId = getNextOpenPanel(panelId)
  _resizeAccumY = 0
  if (_resizingPanelId && _resizingNextId) {
    _resizeStartH[_resizingPanelId] = getPanelHeight(_resizingPanelId)
    _resizeStartH[_resizingNextId] = getPanelHeight(_resizingNextId)
  }
}

function onResizeMove({ deltaY }) {
  if (!_resizingPanelId || !_resizingNextId) return
  _resizeAccumY += deltaY

  const Ha = _resizeStartH[_resizingPanelId] || 0
  const Hb = _resizeStartH[_resizingNextId] || 0
  const HTotal = Ha + Hb
  if (HTotal <= 0) return

  const Ha_new = Math.max(MIN_PANEL_HEIGHT_PX, Ha + _resizeAccumY)
  const Hb_new = Math.max(MIN_PANEL_HEIGHT_PX, Hb - _resizeAccumY)

  const p = uiStore.rightSidebarPanels
  const WTotal = (p[_resizingPanelId]?.weight ?? 1) + (p[_resizingNextId]?.weight ?? 1)

  _pendingResizeWeights = {
    [_resizingPanelId]: Math.max(0.1, (Ha_new / HTotal) * WTotal),
    [_resizingNextId]: Math.max(0.1, (Hb_new / HTotal) * WTotal),
  }

  if (_resizeRafId === null) {
    _resizeRafId = requestAnimationFrame(() => {
      if (_pendingResizeWeights) uiStore.setPanelWeights(_pendingResizeWeights)
      _resizeRafId = null
      _pendingResizeWeights = null
    })
  }
}

function onResizeEnd() {
  if (_resizingPanelId && _resizingNextId) {
    const Ha = _resizeStartH[_resizingPanelId] || 0
    const Hb = _resizeStartH[_resizingNextId] || 0
    // Auto-collapse if dragged past minimum threshold
    if (Ha + _resizeAccumY < MIN_PANEL_HEIGHT_PX) {
      uiStore.setPanelExpanded(_resizingPanelId, false)
    }
    if (Hb - _resizeAccumY < MIN_PANEL_HEIGHT_PX) {
      uiStore.setPanelExpanded(_resizingNextId, false)
    }
  }
  uiStore.commitPanelState()
  _resizingPanelId = null
  _resizingNextId = null
  _resizeAccumY = 0
}

// --- Sidebar width resize (left edge) ---
const isResizing = ref(false)
const isOverlay = computed(() => uiStore.windowWidth < 768)

const sidebarStyle = computed(() => {
  if (isOverlay.value) return {}
  return {
    width: `${uiStore.rightSidebarWidth}px`,
    minWidth: '200px',
    maxWidth: '30vw'
  }
})

function startResize(event) {
  event.preventDefault()
  isResizing.value = true
  document.addEventListener('mousemove', handleResize)
  document.addEventListener('mouseup', stopResize)
}

function handleResize(event) {
  if (isResizing.value) {
    uiStore.setRightSidebarWidth(window.innerWidth - event.clientX)
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
  background: var(--bs-secondary-bg);
  border-left: 1px solid var(--bs-border-color);
  position: relative;
  transition: width 0.3s ease;
}

.right-sidebar.resizing {
  transition: none;
}

/* Panel stack fills remaining space below AgentOverview */
.panel-stack {
  display: flex;
  flex-direction: column;
  flex: 1;
  min-height: 0;
  overflow: hidden;
}

/* Left-edge resize handle (sidebar width) */
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
  background-color: var(--bs-border-color);
}
</style>
