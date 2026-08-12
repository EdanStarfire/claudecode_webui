<template>
  <div
    class="bc-project-card"
    :class="{ current: isBrowsing }"
    role="button"
    :aria-label="`Open project overview for ${project.name}`"
    :title="`${project.name} — click to open project overview`"
    @click="handleBodyClick"
  >
    <span class="p-icon">{{ pillIcon }}</span>
    <span class="p-name">{{ project.name }}</span>
    <span v-if="sessionCount > 0" class="p-count">{{ sessionCount }}</span>
    <div v-if="sessionCount > 0" class="card-status-bar">
      <div
        v-for="(seg, idx) in statusSegments"
        :key="idx"
        class="seg"
        :class="[seg.status, { unread: seg.unread }]"
        :style="{ flex: seg.flex }"
        :title="segTooltip(seg)"
      ></div>
    </div>
    <span
      class="bc-project-chevron"
      role="button"
      title="Switch breadcrumb to this project without navigating"
      :aria-label="`Switch breadcrumb to ${project.name} without navigating`"
      @click.stop="handleChevronClick"
    >
      <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="9 6 15 12 9 18"></polyline></svg>
    </span>
  </div>
</template>

<script setup>
import { computed } from 'vue'
import { useRouter } from 'vue-router'
import { useProjectStore } from '@/stores/project'
import { useSessionStore } from '@/stores/session'
import { useUIStore } from '@/stores/ui'

const props = defineProps({
  project: { type: Object, required: true }
})

const emit = defineEmits(['close'])

const router = useRouter()
const projectStore = useProjectStore()
const sessionStore = useSessionStore()
const uiStore = useUIStore()

const sessionCount = computed(() => props.project.session_ids?.length || 0)
const isBrowsing = computed(() => props.project.project_id === uiStore.browsingProjectId)

const pillIcon = computed(() => {
  if (sessionCount.value > 1) return '🔷'
  if (sessionCount.value === 1) return '💬'
  return '📁'
})

const statusSegments = computed(() =>
  projectStore.getStatusBarSegments(props.project.project_id, sessionStore, uiStore.agentSort)
)

function segTooltip(seg) {
  const name = seg.name || 'Session'
  const state = seg.status.charAt(0).toUpperCase() + seg.status.slice(1)
  return `${name}: ${state}`
}

function handleBodyClick() {
  uiStore.setBrowsingProject(props.project.project_id)
  router.push(`/project/${props.project.project_id}`)
  emit('close')
}

function handleChevronClick() {
  uiStore.setBrowsingProject(props.project.project_id)
  emit('close')
}
</script>

<style scoped>
.bc-project-card {
  position: relative;
  display: flex;
  align-items: center;
  gap: 8px;
  width: 100%;
  padding: 8px 34px 12px 10px;
  margin-bottom: 4px;
  border-radius: 3px;
  border: 1px solid var(--bs-border-color);
  background: var(--bs-tertiary-bg);
  cursor: pointer;
  transition: border-color 0.15s, background 0.15s;
}

.bc-project-card:hover {
  border-color: #93c5fd;
  background: var(--bs-secondary-bg);
}

.bc-project-card.current {
  border-color: #3b82f6;
  background: rgba(59, 130, 246, 0.12);
}

.p-icon {
  font-size: 14px;
  flex-shrink: 0;
}

.p-name {
  flex: 1;
  font-size: 12.5px;
  font-weight: 500;
  color: var(--bs-emphasis-color);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.p-count {
  font-size: 10.5px;
  font-weight: 600;
  color: var(--bs-secondary-color);
  background: var(--bs-tertiary-bg);
  border-radius: 4px;
  padding: 1px 6px;
  flex-shrink: 0;
}

.card-status-bar {
  position: absolute;
  left: 8px;
  right: 34px;
  bottom: 3px;
  height: 3px;
  display: flex;
  gap: 1px;
  border-radius: 2px;
  overflow: hidden;
}

.seg {
  transition: background-color 0.3s;
}

.seg.active { background: #8b5cf6; }
.seg.idle { background: #22c55e; }
.seg.waiting { background: #ffc107; }
.seg.error { background: #ef4444; }
.seg.none { background: var(--bs-border-color); }
.seg.unread { background: var(--color-unread); }

.bc-project-chevron {
  position: absolute;
  right: 4px;
  top: 50%;
  transform: translateY(-50%);
  width: 26px;
  height: 26px;
  display: flex;
  align-items: center;
  justify-content: center;
  border-radius: 5px;
  color: var(--bs-secondary-color);
  cursor: pointer;
  transition: background 0.15s, color 0.15s;
}

.bc-project-chevron:hover {
  background: var(--bs-secondary-bg);
  color: #3b82f6;
}
</style>
