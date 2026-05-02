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

      <!-- Application group -->
      <SettingsSidebarGroup title="Application">
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

      <!-- Library group — non-functional stubs for Phase 1 -->
      <SettingsSidebarGroup v-if="!settingsStore.searchQuery" title="Library">
        <SettingsSidebarItem
          v-for="item in libraryItems"
          :key="item.to"
          :to="item.to"
          :icon="item.icon"
          :label="item.label"
          :disabled="true"
        />
      </SettingsSidebarGroup>
    </div>
  </nav>
</template>

<script setup>
import { computed } from 'vue'
import { useSettingsStore } from '@/stores/settings'
import SettingsSidebarSearch from './SettingsSidebarSearch.vue'
import SettingsSidebarGroup from './SettingsSidebarGroup.vue'
import SettingsSidebarItem from './SettingsSidebarItem.vue'
import { settingsIndex } from './search/settingsIndex.js'

const settingsStore = useSettingsStore()

function toggleSidebar() {
  settingsStore.setSidebarExpanded(!settingsStore.sidebarExpanded)
}

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
</script>

<style scoped>
.settings-sidebar-nav {
  height: 100%;
  display: flex;
  flex-direction: column;
  background: #0f172a;
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
  border-bottom: 1px solid #1e293b;
  color: #94a3b8;
  font-size: 15px;
  cursor: pointer;
  text-align: left;
  white-space: nowrap;
  overflow: hidden;
  flex-shrink: 0;
  transition: color 0.12s;
}

.sidebar-mobile-toggle:hover {
  color: #e2e8f0;
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
  color: #475569;
  font-style: italic;
}
</style>
