<template>
  <div
    class="settings-takeover"
    :class="{ 'sidebar-expanded': settingsStore.sidebarExpanded }"
    :style="modeTintVars"
  >
    <!-- Sidebar -->
    <div class="settings-sidebar">
      <SettingsSidebar />
    </div>

    <!-- Backdrop — tapping dimmed content area collapses mobile sidebar -->
    <div
      v-if="settingsStore.sidebarExpanded"
      class="settings-backdrop"
      aria-hidden="true"
      @click="settingsStore.setSidebarExpanded(false)"
    />

    <!-- Content area -->
    <div class="settings-content">
      <SettingsBreadcrumb />
      <component
        :is="sectionComponent"
        v-if="sectionComponent"
        :key="route.path"
        ref="sectionHostRef"
        class="section-host"
      />
      <div v-else class="settings-coming-soon">
        <p class="coming-soon-label">Coming soon</p>
        <p class="coming-soon-route">{{ $route.path }}</p>
      </div>
    </div>

    <!-- Dirty guard modal -->
    <DirtyGuardModal
      :visible="settingsStore.pendingNavigation !== null"
      @apply="onGuardApply"
      @discard="onGuardDiscard"
      @cancel="onGuardCancel"
    />
  </div>
</template>

<script setup>
import { computed, ref, watch, nextTick, onMounted, onUnmounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useSettingsStore } from '@/stores/settings'
import SettingsSidebar from './SettingsSidebar.vue'
import SettingsBreadcrumb from './SettingsBreadcrumb.vue'
import DirtyGuardModal from './DirtyGuardModal.vue'
import ApplicationFeaturesSection from './sections/ApplicationFeaturesSection.vue'
import ApplicationPricingSection from './sections/ApplicationPricingSection.vue'
import ApplicationNotifsSection from './sections/ApplicationNotifsSection.vue'
import ApplicationReadAloudSection from './sections/ApplicationReadAloudSection.vue'
import LibraryMcpServersSection from './sections/LibraryMcpServersSection.vue'
import LibraryProvidersSection from './sections/LibraryProvidersSection.vue'
import LibraryTemplatesSection from './sections/LibraryTemplatesSection.vue'
import LibraryProfilesSection from './sections/LibraryProfilesSection.vue'
import LibrarySecretsSection from './sections/LibrarySecretsSection.vue'
import LibrarySchedulesSection from './sections/LibrarySchedulesSection.vue'
import GeneralSection from './sections/GeneralSection.vue'
import ScheduleGeneralSection from './sections/ScheduleGeneralSection.vue'
import SecretGeneralSection from './sections/SecretGeneralSection.vue'
import ModelTuningSection from './sections/ModelTuningSection.vue'
import ToolsPermissionsSection from './sections/ToolsPermissionsSection.vue'
import McpServersSection from './sections/McpServersSection.vue'
import FeaturesSection from './sections/FeaturesSection.vue'
import SystemPromptSection from './sections/SystemPromptSection.vue'
import IsolationSection from './sections/IsolationSection.vue'

const route = useRoute()
const router = useRouter()
const settingsStore = useSettingsStore()

const modeTintVars = computed(() => {
  const p = route.path
  if (p.startsWith('/settings/session/'))  return { '--mode-tint': '#1f6feb18', '--mode-border': '#1f6feb44', '--mode-fg': '#58a6ff' }
  if (p.startsWith('/settings/template/')) return { '--mode-tint': '#d2992215', '--mode-border': '#d2992244', '--mode-fg': '#d29922' }
  if (p.startsWith('/settings/profile/'))  return { '--mode-tint': '#3fb95018', '--mode-border': '#3fb95044', '--mode-fg': '#3fb950' }
  if (p.startsWith('/settings/schedule/')) return { '--mode-tint': '#7c3aed18', '--mode-border': '#7c3aed44', '--mode-fg': '#7c3aed' }
  if (p.startsWith('/settings/secret/'))  return { '--mode-tint': '#06b6d418', '--mode-border': '#06b6d444', '--mode-fg': '#06b6d4' }
  return {}
})

const EDIT_SECTION_MAP = {
  'general':           GeneralSection,
  'model-tuning':      ModelTuningSection,
  'tools-permissions': ToolsPermissionsSection,
  'mcp-servers':       McpServersSection,
  'features':          FeaturesSection,
  'system-prompt':     SystemPromptSection,
  'isolation':         IsolationSection,
}

const sectionComponent = computed(() => {
  const p = route.path
  const isSchedule = p.startsWith('/settings/schedule/')
  const isSecret   = p.startsWith('/settings/secret/')
  if (p.startsWith('/settings/session/') || p.startsWith('/settings/template/') ||
      p.startsWith('/settings/profile/') || isSchedule || isSecret) {
    const section = route.params.section || 'general'
    if (section === 'general' && isSchedule) return ScheduleGeneralSection
    if (section === 'general' && isSecret)   return SecretGeneralSection
    return EDIT_SECTION_MAP[section] ?? null
  }
  switch (p) {
    case '/settings/features':      return ApplicationFeaturesSection
    case '/settings/pricing':       return ApplicationPricingSection
    case '/settings/notifications': return ApplicationNotifsSection
    case '/settings/read-aloud':    return ApplicationReadAloudSection
    case '/settings/mcp-servers':   return LibraryMcpServersSection
    case '/settings/providers':     return LibraryProvidersSection
    case '/settings/templates':     return LibraryTemplatesSection
    case '/settings/profiles':      return LibraryProfilesSection
    case '/settings/secrets':       return LibrarySecretsSection
    case '/settings/schedules':     return LibrarySchedulesSection
    default:                        return null
  }
})

// ── Content-area search highlighting ──────────────────────────────────────
const sectionHostRef = ref(null)
let highlightTimer = null

function clearHighlights(root) {
  const marks = root.querySelectorAll('mark.settings-hl')
  marks.forEach(mark => {
    const parent = mark.parentNode
    parent.replaceChild(document.createTextNode(mark.textContent), mark)
    parent.normalize()
  })
}

function applyHighlights(root, query) {
  const q = query.trim()
  if (!q) return
  const lq = q.toLowerCase()

  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, {
    acceptNode(node) {
      const tag = node.parentElement?.tagName?.toLowerCase()
      if (['script', 'style', 'input', 'textarea', 'button', 'select', 'mark'].includes(tag)) {
        return NodeFilter.FILTER_REJECT
      }
      if (!node.textContent.toLowerCase().includes(lq)) return NodeFilter.FILTER_SKIP
      return NodeFilter.FILTER_ACCEPT
    },
  })

  const nodes = []
  let n
  while ((n = walker.nextNode())) nodes.push(n)

  nodes.forEach(textNode => {
    const text = textNode.textContent
    const idx = text.toLowerCase().indexOf(lq)
    if (idx === -1) return
    const before = document.createTextNode(text.slice(0, idx))
    const mark = document.createElement('mark')
    mark.className = 'settings-hl'
    mark.textContent = text.slice(idx, idx + q.length)
    const after = document.createTextNode(text.slice(idx + q.length))
    const parent = textNode.parentNode
    parent.replaceChild(after, textNode)
    parent.insertBefore(mark, after)
    parent.insertBefore(before, mark)
  })
}

function scheduleHighlight() {
  clearTimeout(highlightTimer)
  highlightTimer = setTimeout(async () => {
    await nextTick()
    const el = sectionHostRef.value?.$el ?? sectionHostRef.value
    if (!el) return
    clearHighlights(el)
    const q = settingsStore.searchQuery
    if (q.trim()) applyHighlights(el, q)
  }, 80)
}

watch(() => settingsStore.searchQuery, scheduleHighlight)
watch(sectionComponent, () => {
  // Section changed — clear old marks; reapply after render
  nextTick(scheduleHighlight)
})
// ──────────────────────────────────────────────────────────────────────────

async function onGuardApply() {
  // Save current section before navigating
  if (sectionHostRef.value?.save) {
    await sectionHostRef.value.save()
  }
  const dest = settingsStore.confirmNavigation('apply')
  if (dest) router.push(dest)
}

function onGuardDiscard() {
  const dest = settingsStore.confirmNavigation('discard')
  if (dest) router.push(dest)
}

function onGuardCancel() {
  settingsStore.confirmNavigation('cancel')
}

// ── Keyboard shortcuts ─────────────────────────────────────────────────────

function handleKeydown(e) {
  const tag = document.activeElement?.tagName?.toLowerCase()
  const isMeta = e.metaKey || e.ctrlKey

  // Escape: cancel current section (unless focused in input where Escape clears it)
  if (e.key === 'Escape' && !['input', 'textarea', 'select'].includes(tag)) {
    e.preventDefault()
    sectionHostRef.value?.cancel?.()
    return
  }

  // Don't fire shortcuts when user is typing
  if (['input', 'textarea', 'select'].includes(tag)) return

  if (isMeta && e.key === 's') {
    e.preventDefault()
    sectionHostRef.value?.save?.()
    return
  }

  if (isMeta && e.key === 'k') {
    e.preventDefault()
    document.querySelector('.search-input')?.focus()
    return
  }

  if (e.key === 'ArrowUp' || e.key === 'ArrowDown') {
    const items = Array.from(document.querySelectorAll('.settings-sidebar-item'))
    if (!items.length) return
    const active = items.findIndex(el => el.classList.contains('is-active'))
    if (active === -1) return
    const next = e.key === 'ArrowDown' ? active + 1 : active - 1
    const target = items[next]
    if (target) {
      e.preventDefault()
      target.click()
    }
  }
}

onMounted(() => {
  document.addEventListener('keydown', handleKeydown)
})

onUnmounted(() => {
  document.removeEventListener('keydown', handleKeydown)
})
</script>

<style scoped>
.settings-takeover {
  flex: 1;
  display: flex;
  min-height: 0;
  overflow: hidden;
  position: relative;
  border-top: 2px solid var(--mode-border, transparent);
  /* Named container so @container rules in children can reference it */
  container: settings-area / inline-size;

  /* Per-type marker colors — consumed by SourceMarker regardless of current mode */
  --s-fg:     #58a6ff;
  --s-border: rgba(88, 166, 255, 0.30);
  --s-tint:   rgba(88, 166, 255, 0.12);
  --t-fg:     #d29922;
  --t-border: rgba(210, 153, 34, 0.30);
  --t-tint:   rgba(210, 153, 34, 0.12);
  --p-fg:     #3fb950;
  --p-border: rgba(63, 185, 80, 0.30);
  --p-tint:   rgba(63, 185, 80, 0.12);
}

/* ── Desktop sidebar (static 240px) ── */
.settings-sidebar {
  width: 240px;
  flex-shrink: 0;
  display: flex;
  flex-direction: column;
  overflow: hidden;
  background: var(--bs-tertiary-bg);
  border-right: 1px solid var(--bs-border-color);
}

.settings-content {
  flex: 1;
  display: flex;
  flex-direction: column;
  min-width: 0;
  overflow: hidden;
  background: var(--bs-body-bg);
  color: var(--bs-body-color);
}

.section-host {
  flex: 1;
  min-height: 0;
  overflow: hidden;
}

/* Backdrop is never shown on desktop (sidebarExpanded stays false) */
.settings-backdrop {
  display: none;
  position: absolute;
  inset: 0;
  background: rgba(0, 0, 0, 0.45);
  z-index: 9;
}

.settings-coming-soon {
  flex: 1;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
}

.coming-soon-label {
  font-size: 18px;
  font-weight: 600;
  color: var(--bs-secondary-color);
  margin: 0 0 8px;
}

.coming-soon-route {
  font-size: 12px;
  color: var(--bs-tertiary-color);
  font-family: monospace;
  margin: 0;
}

/* ── Mobile container query (<600px) ── */
@container settings-area (max-width: 599px) {
  .settings-sidebar {
    position: absolute;
    top: 0;
    bottom: 0;
    left: 0;
    width: 44px;
    transition: width 0.22s ease;
    z-index: 10;
    /* prevent clipping of sidebar content during expand animation */
    overflow: hidden;
  }

  .settings-takeover.sidebar-expanded .settings-sidebar {
    width: 75cqw;
  }

  .settings-content {
    position: absolute;
    top: 0;
    bottom: 0;
    left: 44px;
    width: calc(100cqw - 44px);
    transition: left 0.22s ease, opacity 0.22s ease;
  }

  .settings-takeover.sidebar-expanded .settings-content {
    left: 75cqw;
    opacity: 0.45;
    pointer-events: none;
  }

  .settings-backdrop {
    display: block;
  }
}

:deep(mark.settings-hl) {
  background: rgba(99, 102, 241, 0.25);
  color: inherit;
  border-radius: 2px;
  padding: 0 1px;
  font-weight: 700;
}
</style>
