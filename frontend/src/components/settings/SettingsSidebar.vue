<template>
  <nav class="settings-sidebar-nav" aria-label="Settings navigation">
    <!-- Mobile hamburger toggle (only visible at narrow container widths) -->
    <button
      class="sidebar-mobile-toggle"
      :class="{ 'is-expanded': settingsStore.sidebarExpanded }"
      :title="settingsStore.sidebarExpanded ? 'Collapse menu' : 'Expand menu'"
      :aria-expanded="settingsStore.sidebarExpanded"
      @click="toggleSidebar"
    >
      <span class="toggle-icon" aria-hidden="true">☰</span>
      <span class="toggle-label">Settings</span>
    </button>

    <div class="sidebar-inner">
      <SettingsSidebarSearch
        :model-value="settingsStore.searchQuery"
        @update:model-value="settingsStore.setSearchQuery"
      />

      <!-- Edit group: "This Template" or "This Profile" (shown when on an edit route) -->
      <SettingsSidebarGroup
        v-if="isEditMode && (filteredEditItems.length > 0 || !settingsStore.searchQuery)"
        :title="editGroupTitle"
        :short-title="editGroupShort"
        :tinted="true"
      >
        <SettingsSidebarItem
          v-for="item in filteredEditItems"
          :key="item.to"
          :to="item.to"
          :icon="item.icon"
          :label="item.label"
          :tinted="true"
        />
        <div v-if="settingsStore.searchQuery && !filteredEditItems.length" class="no-results">
          No results
        </div>
      </SettingsSidebarGroup>

      <!-- Application group -->
      <SettingsSidebarGroup title="Application" short-title="App">
        <SettingsSidebarItem
          v-for="item in filteredAppItems"
          :key="item.to"
          :to="item.to"
          :icon="item.icon"
          :label="item.label"
        />
        <div v-if="settingsStore.searchQuery && !filteredAppItems.length" class="no-results">
          No results
        </div>
      </SettingsSidebarGroup>

      <!-- Library group -->
      <SettingsSidebarGroup v-if="filteredLibItems.length > 0 || !settingsStore.searchQuery" title="Library" short-title="Lib">
        <SettingsSidebarItem
          v-for="item in filteredLibItems"
          :key="item.to"
          :to="item.to"
          :icon="item.icon"
          :label="item.label"
        />
        <div v-if="settingsStore.searchQuery && !filteredLibItems.length" class="no-results">
          No results
        </div>
      </SettingsSidebarGroup>
    </div>
  </nav>
</template>

<script setup>
import { computed } from 'vue'
import { useRoute } from 'vue-router'
import { useSettingsStore } from '@/stores/settings'
import SettingsSidebarSearch from './SettingsSidebarSearch.vue'
import SettingsSidebarGroup from './SettingsSidebarGroup.vue'
import SettingsSidebarItem from './SettingsSidebarItem.vue'
import { settingsIndex } from './search/settingsIndex.js'

const route = useRoute()
const settingsStore = useSettingsStore()

function toggleSidebar() {
  settingsStore.setSidebarExpanded(!settingsStore.sidebarExpanded)
}

// ── Edit-mode (template / profile) dynamic group ──────────────────────────

const isTemplateEdit = computed(() => route.path.startsWith('/settings/template/'))
const isProfileEdit  = computed(() => route.path.startsWith('/settings/profile/'))
const isEditMode     = computed(() => isTemplateEdit.value || isProfileEdit.value)

const editEntityId = computed(() => {
  return route.params.templateId || route.params.profileId || ''
})

const editGroupTitle = computed(() => {
  if (isTemplateEdit.value) return 'This Template'
  if (isProfileEdit.value)  return 'This Profile'
  return ''
})

const editGroupShort = computed(() => {
  if (isTemplateEdit.value) return 'Tmpl'
  if (isProfileEdit.value)  return 'Prof'
  return ''
})

const EDIT_SECTIONS = [
  { section: 'general',           icon: '◉', label: 'General',             sectionKey: 'edit-general' },
  { section: 'model-tuning',      icon: '⬡', label: 'Model Tuning',        sectionKey: 'edit-model-tuning' },
  { section: 'tools-permissions', icon: '⬡', label: 'Tools & Permissions', sectionKey: 'edit-tools-permissions' },
  { section: 'mcp-servers',       icon: '◈', label: 'MCP Servers',         sectionKey: 'edit-mcp-servers' },
  { section: 'features',          icon: '⚡', label: 'Features',            sectionKey: 'edit-features' },
  { section: 'system-prompt',     icon: '☰', label: 'System Prompt',       sectionKey: 'edit-system-prompt' },
  { section: 'isolation',         icon: '⊞', label: 'Isolation',           sectionKey: 'edit-isolation' },
]

const editSectionItems = computed(() => {
  if (!editEntityId.value) return []
  const base = isTemplateEdit.value
    ? `/settings/template/${editEntityId.value}`
    : `/settings/profile/${editEntityId.value}`
  return EDIT_SECTIONS.map(s => ({
    to: `${base}/${s.section}`,
    icon: s.icon,
    label: s.label,
    sectionKey: s.sectionKey,
  }))
})

const filteredEditItems = computed(() => {
  const q = settingsStore.searchQuery.toLowerCase().trim()
  if (!q) return editSectionItems.value
  return editSectionItems.value.filter(item => {
    if (item.label.toLowerCase().includes(q)) return true
    return settingsIndex
      .filter(e => e.section === item.sectionKey)
      .some(e => e.label.toLowerCase().includes(q))
  })
})

// ── Static sidebar groups ─────────────────────────────────────────────────

const APP_ITEMS = [
  { to: '/settings/features',      icon: '⚡', label: 'Features',      sectionKey: 'features' },
  { to: '/settings/notifications', icon: '◉',  label: 'Notifications',  sectionKey: 'notifications' },
  { to: '/settings/read-aloud',    icon: '♪',  label: 'Read Aloud',     sectionKey: 'read-aloud' },
  { to: '/settings/mcp-servers',   icon: '◈',  label: 'MCP Servers',    sectionKey: 'mcp-servers' },
]

const libraryItems = [
  { to: '/settings/templates', icon: '◧', label: 'Templates', sectionKey: 'templates' },
  { to: '/settings/profiles',  icon: '◎', label: 'Profiles',  sectionKey: 'profiles' },
  { to: '/settings/secrets',   icon: '◍', label: 'Secrets',   sectionKey: 'secrets' },
]

const filteredAppItems = computed(() => {
  const q = settingsStore.searchQuery.toLowerCase().trim()
  if (!q) return APP_ITEMS
  return APP_ITEMS.filter(item => {
    if (item.label.toLowerCase().includes(q)) return true
    return settingsIndex
      .filter(e => e.section === item.sectionKey)
      .some(e => e.label.toLowerCase().includes(q))
  })
})

const filteredLibItems = computed(() => {
  const q = settingsStore.searchQuery.toLowerCase().trim()
  if (!q) return libraryItems
  return libraryItems.filter(item => {
    if (item.label.toLowerCase().includes(q)) return true
    return settingsIndex
      .filter(e => e.section === item.sectionKey)
      .some(e => e.label.toLowerCase().includes(q))
  })
})
</script>

<style scoped>
.settings-sidebar-nav {
  height: 100%;
  display: flex;
  flex-direction: column;
  background: var(--bs-tertiary-bg);
  overflow: hidden;
}

/* Mobile toggle button — hidden on desktop, shown via container query */
.sidebar-mobile-toggle {
  display: none;
  align-items: center;
  gap: 10px;
  width: 100%;
  padding: 10px 12px;
  background: none;
  border: none;
  border-bottom: 1px solid var(--bs-border-color);
  color: var(--bs-secondary-color);
  font-size: 15px;
  cursor: pointer;
  text-align: left;
  white-space: nowrap;
  overflow: hidden;
  flex-shrink: 0;
  transition: color 0.12s;
}

.sidebar-mobile-toggle:hover {
  color: var(--bs-emphasis-color);
}

.toggle-icon {
  width: 20px;
  text-align: center;
  flex-shrink: 0;
}

.toggle-label {
  font-size: 13px;
}

/* Show mobile toggle when container is narrow (settings-area named container) */
@container settings-area (max-width: 599px) {
  .sidebar-mobile-toggle {
    display: flex;
  }
}

/* Fallback for browsers that don't support named containers */
@container (max-width: 599px) {
  .sidebar-mobile-toggle {
    display: flex;
  }
}

.sidebar-inner {
  flex: 1;
  overflow-y: auto;
  overflow-x: hidden;
}

.no-results {
  padding: 6px 12px;
  font-size: 12px;
  color: var(--bs-tertiary-color);
  font-style: italic;
}
</style>
