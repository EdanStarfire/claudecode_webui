<template>
  <div
    class="project-pill"
    :class="{
      active: isActive,
      browsing: isBrowsing && !isActive
    }"
    role="button"
    :aria-label="`Select project ${project.name}`"
    @click="handleClick"
    @contextmenu.prevent="showEditModal"
    :title="project.working_directory || project.name"
  >
    <span class="pill-icon">{{ pillIcon }}</span>
    <span class="pill-name">{{ project.name }}</span>
    <span
      v-if="sessionCount > 0"
      class="pill-count"
      @click.stop="navigateToOverview"
      title="View project overview"
    >{{ sessionCount }}</span>
    <div class="pill-status-bar">
      <div
        v-for="(seg, idx) in statusSegments"
        :key="idx"
        class="seg"
        :class="seg.status"
        :style="{ flex: seg.flex }"
        :title="segTooltip(seg)"
      ></div>
    </div>
  </div>
</template>

<script setup>
import { computed } from 'vue'
import { useRouter } from 'vue-router'
import { useProjectStore } from '@/stores/project'
import { useSessionStore } from '@/stores/session'
import { useUIStore } from '@/stores/ui'

const props = defineProps({
  project: { type: Object, required: true },
  isActive: { type: Boolean, default: false },
  isBrowsing: { type: Boolean, default: false }
})

const router = useRouter()
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

function navigateToOverview() {
  uiStore.setBrowsingProject(props.project.project_id)
  router.push(`/project/${props.project.project_id}`)
}

function segTooltip(seg) {
  const name = seg.name || 'Session'
  const state = seg.status.charAt(0).toUpperCase() + seg.status.slice(1)
  return `${name}: ${state}`
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
  border: 1px solid var(--bs-border-color);
  cursor: pointer;
  transition: all 0.15s;
  white-space: nowrap;
  flex-shrink: 0;
  min-width: 120px;
  position: relative;
  background: transparent;
}

.project-pill:hover {
  background: var(--bs-secondary-bg);
}

.project-pill.browsing {
  background: var(--bs-secondary-bg);
  border-color: var(--bs-border-color);
}

.project-pill.active {
  background: rgba(59, 130, 246, 0.15);
  border-color: #3b82f6;
}

.pill-icon {
  font-size: 12px;
  flex-shrink: 0;
}

.pill-name {
  font-size: 12px;
  font-weight: 500;
  color: var(--bs-emphasis-color);
  max-width: 120px;
  overflow: hidden;
  text-overflow: ellipsis;
}

.project-pill.active .pill-name {
  font-weight: 600;
  color: var(--bs-link-color);
}

.pill-count {
  font-size: 10px;
  font-weight: 600;
  color: var(--bs-secondary-color);
  background: var(--bs-tertiary-bg);
  padding: 0 5px;
  border-radius: 8px;
  line-height: 1.6;
  cursor: pointer;
}

.pill-count:hover {
  background: var(--bs-secondary-bg);
}

.project-pill.active .pill-count {
  background: rgba(59, 130, 246, 0.2);
  color: var(--bs-link-color);
}

.project-pill.active .pill-count:hover {
  background: rgba(59, 130, 246, 0.3);
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
  cursor: help;
}

.seg.active { background: #8b5cf6; }
.seg.idle { background: #22c55e; }
.seg.waiting { background: #ffc107; }
.seg.error { background: #ef4444; }
.seg.none { background: var(--bs-border-color); }
</style>
