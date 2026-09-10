<template>
  <div class="breadcrumb-bar" :class="{ 'theme-red': uiStore.isRedBackground }" ref="headerEl">
    <template v-if="!browsingProject">
      <div class="crumb-segment">
        <div class="crumb-placeholder">Select a project</div>
        <span
          class="crumb-caret"
          :class="{ open: projectDropdownOpen }"
          role="button"
          aria-label="Choose a project"
          @click.stop="toggleProjectDropdown"
        >
          <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="6 9 12 15 18 9"></polyline></svg>
        </span>
        <BreadcrumbProjectDropdown v-if="projectDropdownOpen" @close="projectDropdownOpen = false" />
      </div>
    </template>

    <template v-else>
      <div class="crumb-segment">
        <div
          class="crumb-main project-crumb"
          role="button"
          :title="`${browsingProject.name} — click to open project overview`"
          @click="goToProjectOverview"
        >
          <span class="crumb-text">{{ truncatedProjectName }}</span>
          <span v-if="projectSessionCount > 0" class="crumb-count">{{ projectSessionCount }}</span>
          <div v-if="projectSessionCount > 0" class="crumb-project-bar">
            <div
              v-for="(seg, idx) in projectStatusSegments"
              :key="idx"
              class="seg"
              :class="[seg.status, { unread: seg.unread }]"
              :style="{ flex: seg.flex }"
            ></div>
          </div>
        </div>
        <span
          class="crumb-caret"
          :class="{ open: projectDropdownOpen }"
          role="button"
          aria-label="Switch project"
          @click.stop="toggleProjectDropdown"
          @mouseenter="showAttentionTip"
          @mouseleave="hideAttentionTip"
        >
          <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="6 9 12 15 18 9"></polyline></svg>
          <span v-if="attentionReasonsPresent.length" class="crumb-caret-dots">
            <span
              v-for="reason in attentionReasonsPresent"
              :key="reason"
              class="dot"
              :class="reason"
            ></span>
          </span>
        </span>
        <div v-if="attentionTipVisible && attentionSummary.length" class="attention-tip">
          <div class="attention-tip-title">{{ attentionSummary.length }} project{{ attentionSummary.length > 1 ? 's' : '' }} need attention</div>
          <div v-for="entry in attentionSummary" :key="entry.projectId" class="attention-tip-row">
            <span class="attention-tip-name">{{ truncate(entry.name) }}</span>
            <span class="attention-tip-tags">
              <span v-for="reason in entry.reasons" :key="reason" class="attention-tag" :class="reason">{{ reason }}</span>
            </span>
          </div>
        </div>
        <BreadcrumbProjectDropdown v-if="projectDropdownOpen" @close="projectDropdownOpen = false" />
      </div>

      <span class="crumb-sep">›</span>

      <div class="crumb-segment">
        <div
          v-if="currentSessionInBrowsingProject"
          class="crumb-main"
          role="button"
          :title="currentSession.name || currentSession.role || 'Agent'"
          @click.stop="toggleSessionDropdown"
        >
          <span class="crumb-status-dot" :class="sessionStatusClass"></span>
          <span class="crumb-text">{{ truncatedSessionName }}</span>
        </div>
        <div v-else class="crumb-placeholder" role="button" @click.stop="toggleSessionDropdown">
          Select a session
        </div>
        <span
          class="crumb-caret"
          :class="{ open: sessionDropdownOpen }"
          role="button"
          aria-label="Switch session"
          @click.stop="toggleSessionDropdown"
        >
          <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="6 9 12 15 18 9"></polyline></svg>
        </span>
        <BreadcrumbSessionDropdown v-if="sessionDropdownOpen" @close="sessionDropdownOpen = false" />
      </div>
    </template>

    <div class="crumb-spacer"></div>

    <button
      class="crumb-sidebar-toggle"
      :class="{ 'panel-open': uiStore.rightPanelVisible }"
      @click.stop="uiStore.toggleRightPanel()"
      title="Toggle right panel"
      aria-label="Toggle right panel"
      :aria-expanded="uiStore.rightPanelVisible"
    >☰</button>
  </div>
</template>

<script setup>
import { computed, ref, watch, onMounted, onUnmounted } from 'vue'
import { useRouter } from 'vue-router'
import { useProjectStore, ATTENTION_REASON_ORDER } from '@/stores/project'
import { useSessionStore } from '@/stores/session'
import { useScheduleStore } from '@/stores/schedule'
import { useUIStore } from '@/stores/ui'
import BreadcrumbProjectDropdown from './BreadcrumbProjectDropdown.vue'
import BreadcrumbSessionDropdown from './BreadcrumbSessionDropdown.vue'

const MAX_CRUMB_LENGTH = 100

const router = useRouter()
const projectStore = useProjectStore()
const sessionStore = useSessionStore()
const scheduleStore = useScheduleStore()
const uiStore = useUIStore()

const headerEl = ref(null)
const projectDropdownOpen = ref(false)
const sessionDropdownOpen = ref(false)
const attentionTipVisible = ref(false)

const orderedProjects = computed(() => projectStore.orderedProjects)
const browsingProjectId = computed(() => uiStore.browsingProjectId)
const browsingProject = computed(() => browsingProjectId.value ? projectStore.getProject(browsingProjectId.value) : null)

const activeProjectId = computed(() => sessionStore.currentSession?.project_id || null)

// Mirrors ProjectPillBar's init/tracking watches so the breadcrumb works standalone
// (it fully replaces ProjectPillBar+AgentStrip via v-if, so their watches don't run).
watch(activeProjectId, (newId) => {
  if (newId) uiStore.setBrowsingProject(newId)
})

watch(orderedProjects, (projects) => {
  if (!browsingProjectId.value && projects.length > 0) {
    uiStore.setBrowsingProject(projects[0].project_id)
  }
}, { immediate: true })

// Mirrors AgentStrip's eager schedule load so session-row schedule badges render immediately
watch(browsingProjectId, (projectId) => {
  if (projectId) scheduleStore.loadSchedules(projectId)
}, { immediate: true })

const projectSessionCount = computed(() => browsingProject.value?.session_ids?.length || 0)

const projectStatusSegments = computed(() => {
  if (!browsingProject.value) return []
  return projectStore.getStatusBarSegments(browsingProject.value.project_id, sessionStore, uiStore.agentSort)
})

const truncatedProjectName = computed(() => truncate(browsingProject.value?.name))

// Issue #1828: cross-project attention badge on the project-switcher caret.
const attentionSummary = computed(() =>
  projectStore.getAttentionSummary(sessionStore, uiStore.agentSort, browsingProjectId.value)
)

const attentionReasonsPresent = computed(() => {
  const present = new Set()
  attentionSummary.value.forEach(entry => entry.reasons.forEach(r => present.add(r)))
  return ATTENTION_REASON_ORDER.filter(r => present.has(r))
})

const currentSession = computed(() => sessionStore.currentSession)

const currentSessionInBrowsingProject = computed(() =>
  !!currentSession.value && currentSession.value.project_id === browsingProjectId.value
)

const truncatedSessionName = computed(() =>
  truncate(currentSession.value?.name || currentSession.value?.role || 'Agent')
)

const sessionStatusClass = computed(() => {
  const session = currentSession.value
  if (!session) return 'created'
  if (sessionStore.isUnreviewed(session.session_id)) return 'unread'
  if (session.state === 'error') return 'error'
  if (session.state === 'starting') return 'starting'
  if (session.state === 'active' && session.is_processing) return 'active-processing'
  if (session.state === 'active') return 'active'
  if (session.state === 'paused') return 'paused'
  if (session.state === 'terminating') return 'terminating'
  if (session.state === 'terminated') return 'terminated'
  return 'created'
})

function truncate(str) {
  if (!str) return ''
  return str.length > MAX_CRUMB_LENGTH ? str.slice(0, MAX_CRUMB_LENGTH) + '…' : str
}

function toggleProjectDropdown() {
  projectDropdownOpen.value = !projectDropdownOpen.value
  sessionDropdownOpen.value = false
  attentionTipVisible.value = false
}

function toggleSessionDropdown() {
  sessionDropdownOpen.value = !sessionDropdownOpen.value
  projectDropdownOpen.value = false
}

function showAttentionTip() {
  attentionTipVisible.value = true
}

function hideAttentionTip() {
  attentionTipVisible.value = false
}

function goToProjectOverview() {
  if (!browsingProject.value) return
  router.push(`/project/${browsingProject.value.project_id}`)
}

function handleDocumentClick(e) {
  if (headerEl.value && headerEl.value.contains(e.target)) return
  projectDropdownOpen.value = false
  sessionDropdownOpen.value = false
  uiStore.collapseAllStacks()
  if (activeProjectId.value && uiStore.browsingProjectId !== activeProjectId.value) {
    uiStore.setBrowsingProject(activeProjectId.value)
  }
}

onMounted(() => {
  document.addEventListener('click', handleDocumentClick, true)
})

onUnmounted(() => {
  document.removeEventListener('click', handleDocumentClick, true)
})
</script>

<style scoped>
.breadcrumb-bar {
  display: flex;
  align-items: center;
  height: 42px;
  padding: 0 10px;
  gap: 4px;
  background: var(--bs-secondary-bg);
  border-bottom: 1px solid var(--bs-border-color);
  position: relative;
  flex-shrink: 0;
}

.crumb-segment {
  position: relative;
  display: flex;
  align-items: center;
}

.crumb-main {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 4px 6px;
  border-radius: 5px;
  cursor: pointer;
  max-width: 240px;
  position: relative;
}

.crumb-main:hover {
  background: var(--bs-tertiary-bg);
}

.crumb-main.project-crumb {
  padding-bottom: 8px;
  gap: 5px;
}

.crumb-text {
  font-size: 13px;
  font-weight: 500;
  color: var(--bs-emphasis-color);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.crumb-count {
  font-size: 10px;
  color: var(--bs-secondary-color);
  background: var(--bs-tertiary-bg);
  border-radius: 4px;
  padding: 0 5px;
  flex-shrink: 0;
}

.crumb-project-bar {
  position: absolute;
  left: 6px;
  right: 6px;
  bottom: 2px;
  height: 2px;
  display: flex;
  gap: 1px;
  border-radius: 2px;
  overflow: hidden;
}

.crumb-project-bar .seg.active { background: #8b5cf6; }
.crumb-project-bar .seg.idle { background: #22c55e; }
.crumb-project-bar .seg.waiting { background: #ffc107; }
.crumb-project-bar .seg.error { background: #ef4444; }
.crumb-project-bar .seg.none { background: var(--bs-border-color); }
.crumb-project-bar .seg.unread { background: var(--color-unread); }

.crumb-caret {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 22px;
  height: 22px;
  border-radius: 4px;
  cursor: pointer;
  color: var(--bs-secondary-color);
  flex-shrink: 0;
  position: relative;
}

.crumb-caret-dots {
  position: absolute;
  top: -4px;
  left: 50%;
  transform: translateX(-50%);
  display: flex;
  align-items: center;
  gap: 2px;
  background: var(--bs-tertiary-bg);
  border: 1.5px solid var(--bs-secondary-bg);
  border-radius: 8px;
  padding: 2px 3px;
  pointer-events: none;
}

.crumb-caret-dots .dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
}

.crumb-caret-dots .dot.waiting { background: #f59e0b; }
.crumb-caret-dots .dot.unread { background: var(--color-unread); }
.crumb-caret-dots .dot.error { background: #ef4444; }

.attention-tip {
  position: absolute;
  top: calc(100% + 8px);
  left: 0;
  background: var(--bs-body-bg);
  color: var(--bs-emphasis-color);
  border: 1px solid var(--bs-border-color);
  border-radius: 6px;
  padding: 8px 11px;
  font-size: 11.5px;
  white-space: nowrap;
  box-shadow: 0 12px 28px rgba(0, 0, 0, 0.45);
  z-index: 65;
  line-height: 1.7;
}

.attention-tip-title {
  font-weight: 700;
  margin-bottom: 4px;
  color: var(--bs-emphasis-color);
}

.attention-tip-row {
  display: flex;
  align-items: center;
  gap: 6px;
}

.attention-tip-name {
  color: var(--bs-emphasis-color);
  font-weight: 500;
}

.attention-tip-tags {
  display: flex;
  gap: 3px;
}

.attention-tag {
  font-size: 9px;
  padding: 0 5px;
  border-radius: 3px;
  font-weight: 700;
}

.attention-tag.waiting { background: rgba(245, 158, 11, 0.18); color: #fbbf24; }
.attention-tag.unread { background: rgba(249, 115, 22, 0.18); color: #fb923c; }
.attention-tag.error { background: rgba(239, 68, 68, 0.18); color: #f87171; }

.crumb-caret svg {
  transition: transform 0.15s;
}

.crumb-caret:hover {
  background: var(--bs-tertiary-bg);
  color: var(--bs-body-color);
}

.crumb-caret.open {
  background: rgba(59, 130, 246, 0.15);
  color: #3b82f6;
}

.crumb-caret.open svg {
  transform: rotate(180deg);
}

.crumb-sep {
  color: var(--bs-secondary-color);
  font-size: 14px;
  padding: 0 1px;
  flex-shrink: 0;
}

.crumb-status-dot {
  width: 8px;
  height: 8px;
  border-radius: 2px;
  flex-shrink: 0;
}

.crumb-status-dot.created      { background: #94a3b8; }
.crumb-status-dot.starting     { background: #3b82f6; }
.crumb-status-dot.active       { background: #22c55e; }
.crumb-status-dot.active-processing { background: #8b5cf6; }
.crumb-status-dot.paused       { background: #f59e0b; }
.crumb-status-dot.terminating  { background: #f97316; }
.crumb-status-dot.terminated   { background: #cbd5e1; }
.crumb-status-dot.error        { background: #ef4444; }
.crumb-status-dot.unread       { background: var(--color-unread); }

.crumb-placeholder {
  color: var(--bs-secondary-color);
  font-style: italic;
  font-size: 13px;
  padding: 4px 6px;
  cursor: pointer;
}

.crumb-spacer {
  flex: 1;
}

.crumb-sidebar-toggle {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 30px;
  height: 30px;
  border-radius: 8px;
  border: 1px solid var(--bs-border-color);
  background: var(--bs-secondary-bg);
  cursor: pointer;
  color: var(--bs-secondary-color);
  font-size: 14px;
  flex-shrink: 0;
  transition: all 0.15s;
}

.crumb-sidebar-toggle:hover {
  border-color: var(--bs-secondary-color);
  color: var(--bs-body-color);
  background: var(--bs-tertiary-bg);
}

.crumb-sidebar-toggle.panel-open {
  background: rgba(var(--bs-link-color-rgb), 0.2);
  border-color: var(--bs-link-color);
  color: var(--bs-link-color);
}

@media (max-width: 767px) {
  .breadcrumb-bar {
    padding: 0 8px;
  }
  .crumb-main {
    max-width: 140px;
  }
}
</style>
