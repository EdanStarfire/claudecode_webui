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
import { computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useSettingsStore } from '@/stores/settings'
import SettingsSidebar from './SettingsSidebar.vue'
import SettingsBreadcrumb from './SettingsBreadcrumb.vue'
import DirtyGuardModal from './DirtyGuardModal.vue'
import ApplicationFeaturesSection from './sections/ApplicationFeaturesSection.vue'
import ApplicationNotifsSection from './sections/ApplicationNotifsSection.vue'
import ApplicationReadAloudSection from './sections/ApplicationReadAloudSection.vue'
import ApplicationMcpSection from './sections/ApplicationMcpSection.vue'

const route = useRoute()
const router = useRouter()
const settingsStore = useSettingsStore()

const modeTintVars = computed(() => {
  const p = route.path
  if (p.startsWith('/settings/session/'))  return { '--mode-tint': '#1f6feb18', '--mode-border': '#1f6feb44', '--mode-fg': '#58a6ff' }
  if (p.startsWith('/settings/template/')) return { '--mode-tint': '#d2992215', '--mode-border': '#d2992244', '--mode-fg': '#d29922' }
  if (p.startsWith('/settings/profile/'))  return { '--mode-tint': '#3fb95018', '--mode-border': '#3fb95044', '--mode-fg': '#3fb950' }
  return {}
})

const sectionComponent = computed(() => {
  switch (route.path) {
    case '/settings/features':      return ApplicationFeaturesSection
    case '/settings/notifications': return ApplicationNotifsSection
    case '/settings/read-aloud':    return ApplicationReadAloudSection
    case '/settings/mcp-servers':   return ApplicationMcpSection
    default:                        return null
  }
})

function onGuardApply() {
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
}

/* ── Desktop sidebar (static 240px) ── */
.settings-sidebar {
  width: 240px;
  flex-shrink: 0;
  display: flex;
  flex-direction: column;
  overflow: hidden;
  /* background is on the inner SettingsSidebar component */
  background: #0f172a;
  border-right: 1px solid #1e293b;
}

.settings-content {
  flex: 1;
  display: flex;
  flex-direction: column;
  min-width: 0;
  overflow: hidden;
  background: #131c2e;
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
  color: #94a3b8;
  margin: 0 0 8px;
}

.coming-soon-route {
  font-size: 12px;
  color: #475569;
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
</style>
