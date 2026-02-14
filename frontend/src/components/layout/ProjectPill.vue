<template>
  <div
    class="project-pill"
    :class="{
      active: isActive,
      browsing: isBrowsing && !isActive
    }"
    @click="handleClick"
    @contextmenu.prevent="showEditModal"
    :title="project.working_directory || project.name"
  >
    <span class="pill-icon">{{ pillIcon }}</span>
    <span class="pill-name">{{ project.name }}</span>
    <span v-if="sessionCount > 0" class="pill-count">{{ sessionCount }}</span>
    <div class="pill-status-bar">
      <div
        v-for="(seg, idx) in statusSegments"
        :key="idx"
        class="seg"
        :class="seg.status"
        :style="{ flex: seg.flex }"
      ></div>
    </div>
  </div>
</template>

<script setup>
import { computed } from 'vue'
import { useProjectStore } from '@/stores/project'
import { useSessionStore } from '@/stores/session'
import { useUIStore } from '@/stores/ui'

const props = defineProps({
  project: { type: Object, required: true },
  isActive: { type: Boolean, default: false },
  isBrowsing: { type: Boolean, default: false }
})

const projectStore = useProjectStore()
const sessionStore = useSessionStore()
const uiStore = useUIStore()

const sessionCount = computed(() => props.project.session_ids?.length || 0)

const pillIcon = computed(() => {
  if (sessionCount.value > 1) return '🔷'
  if (sessionCount.value === 1) return '💬'
  return '📁'
})

const statusSegments = computed(() =>
  projectStore.getStatusBarSegments(props.project.project_id, sessionStore)
)

function handleClick() {
  uiStore.setBrowsingProject(props.project.project_id)
}

function showEditModal() {
  uiStore.showModal('edit-project', { project: props.project })
}
</script>

<style scoped>
.project-pill {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 6px 12px;
  border-radius: 10px;
  border: 1px solid #e2e8f0;
  cursor: pointer;
  transition: all 0.15s;
  white-space: nowrap;
  flex-shrink: 0;
  min-width: 120px;
  position: relative;
  background: transparent;
}

.project-pill:hover {
  background: #f1f5f9;
}

.project-pill.browsing {
  background: #f1f5f9;
  border-color: #e2e8f0;
}

.project-pill.active {
  background: #eff6ff;
  border-color: #3b82f6;
}

.pill-icon {
  font-size: 12px;
  flex-shrink: 0;
}

.pill-name {
  font-size: 12px;
  font-weight: 500;
  color: #1e293b;
  max-width: 120px;
  overflow: hidden;
  text-overflow: ellipsis;
}

.project-pill.active .pill-name {
  font-weight: 600;
  color: #1d4ed8;
}

.pill-count {
  font-size: 10px;
  font-weight: 600;
  color: #64748b;
  background: #e2e8f0;
  padding: 0 5px;
  border-radius: 8px;
  line-height: 1.6;
}

.project-pill.active .pill-count {
  background: #bfdbfe;
  color: #1d4ed8;
}

.pill-status-bar {
  position: absolute;
  bottom: 0;
  left: 8px;
  right: 8px;
  height: 3px;
  display: flex;
  gap: 1px;
  border-radius: 0 0 2px 2px;
  overflow: hidden;
}

.seg {
  transition: background-color 0.3s;
}

.seg.active { background: #8b5cf6; }
.seg.idle { background: #22c55e; }
.seg.waiting { background: #ffc107; }
.seg.error { background: #ef4444; }
.seg.none { background: #e2e8f0; }
</style>
