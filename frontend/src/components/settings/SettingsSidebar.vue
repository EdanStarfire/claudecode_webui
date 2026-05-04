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

      <!-- Edit group: "Editing Session" (shown when on a session edit route) -->
      <SettingsSidebarGroup
        v-if="isSessionEdit && (filteredSessionItems.length > 0 || !settingsStore.searchQuery)"
        title="Editing Session"
        short-title="Sess"
        :subtitle="sessionEntityName"
        :tinted="true"
      >
        <SettingsSidebarItem
          v-for="item in filteredSessionItems"
          :key="item.to"
          :to="item.to"
          :icon="item.icon"
          :label="item.label"
          :disabled="item.disabled"
          :tinted="true"
        />
        <div v-if="settingsStore.searchQuery && !filteredSessionItems.length" class="no-results">
          No results
        </div>
      </SettingsSidebarGroup>

      <!-- Edit group: "Editing Template" or "Editing Profile" (shown when on an edit route) -->
      <SettingsSidebarGroup
        v-if="isEditMode && (filteredEditItems.length > 0 || !settingsStore.searchQuery)"
        :title="editGroupTitle"
        :short-title="editGroupShort"
        :subtitle="editEntityName"
        :tinted="true"
      >
        <SettingsSidebarItem
          v-for="item in filteredEditItems"
          :key="item.to"
          :to="item.to"
          :icon="item.icon"
          :label="item.label"
          :disabled="item.disabled"
          :tinted="true"
        />
        <div v-if="settingsStore.searchQuery && !filteredEditItems.length" class="no-results">
          No results
        </div>
      </SettingsSidebarGroup>

      <!-- Ghost edit group: shown on Application/Library routes to prevent sidebar bounce -->
      <SettingsSidebarGroup
        v-if="ghostItems.length"
        :title="ghostGroupTitle"
        :short-title="ghostGroupShort"
        :subtitle="ghostEntityName"
        :tinted="true"
      >
        <SettingsSidebarItem
          v-for="item in ghostItems"
          :key="item.to"
          :to="item.to"
          :icon="item.icon"
          :label="item.label"
          :disabled="true"
          :tinted="true"
        />
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
import { computed, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { useSettingsStore } from '@/stores/settings'
import { useTemplateStore } from '@/stores/template'
import { useProfileStore } from '@/stores/profile'
import { useSessionStore } from '@/stores/session'
import SettingsSidebarSearch from './SettingsSidebarSearch.vue'
import SettingsSidebarGroup from './SettingsSidebarGroup.vue'
import SettingsSidebarItem from './SettingsSidebarItem.vue'
import { settingsIndex } from './search/settingsIndex.js'

const route = useRoute()
const settingsStore  = useSettingsStore()
const templateStore  = useTemplateStore()
const profileStore   = useProfileStore()
const sessionStore   = useSessionStore()

function toggleSidebar() {
  settingsStore.setSidebarExpanded(!settingsStore.sidebarExpanded)
}

// ── Session edit group ─────────────────────────────────────────────────────

const isSessionEdit = computed(() => route.path.startsWith('/settings/session/'))

const sessionEntityId = computed(() => route.params.sessionId || '')

const sessionEntityName = computed(() => {
  if (sessionEntityId.value === '__new__') return 'New Session'
  return sessionStore.getSession(sessionEntityId.value)?.name || ''
})

const sessionSectionItems = computed(() => {
  if (!sessionEntityId.value) return []
  const base = `/settings/session/${sessionEntityId.value}`
  const isNew = sessionEntityId.value === '__new__'
  const q = isNew && route.query.project_id ? `?project_id=${route.query.project_id}` : ''
  return EDIT_SECTIONS.map(s => ({
    to: s.section === 'general' && isNew ? `${base}/general${q}` : `${base}/${s.section}`,
    icon: s.icon,
    label: s.label,
    sectionKey: s.sectionKey,
    disabled: isNew && s.section !== 'general',
  }))
})

const filteredSessionItems = computed(() => {
  const q = settingsStore.searchQuery.toLowerCase().trim()
  if (!q) return sessionSectionItems.value
  return sessionSectionItems.value.filter(item => {
    if (item.label.toLowerCase().includes(q)) return true
    return settingsIndex
      .filter(e => e.section === item.sectionKey)
      .some(e => e.label.toLowerCase().includes(q))
  })
})

// ── Edit-mode (template / profile) dynamic group ──────────────────────────

const isTemplateEdit = computed(() => route.path.startsWith('/settings/template/'))
const isProfileEdit  = computed(() => route.path.startsWith('/settings/profile/'))
const isEditMode     = computed(() => isTemplateEdit.value || isProfileEdit.value)


const editEntityId = computed(() => {
  return route.params.templateId || route.params.profileId || ''
})

const editGroupTitle = computed(() => {
  if (isTemplateEdit.value) return 'Editing Template'
  if (isProfileEdit.value)  return 'Editing Profile'
  return ''
})

const editEntityName = computed(() => {
  if (editEntityId.value === '__new__') return ''
  if (isTemplateEdit.value) return templateStore.getTemplate(editEntityId.value)?.name || ''
  if (isProfileEdit.value)  return profileStore.getProfile(editEntityId.value)?.name || ''
  return ''
})

const editGroupShort = computed(() => {
  if (isTemplateEdit.value) return 'Tmpl'
  if (isProfileEdit.value)  return 'Prof'
  return ''
})

const EDIT_SECTIONS = [
  { section: 'general',           icon: '◉',  label: 'General',             sectionKey: 'edit-general' },
  { section: 'model-tuning',      icon: '🧠', label: 'Model Tuning',        sectionKey: 'edit-model-tuning' },
  { section: 'tools-permissions', icon: '🔧', label: 'Tools & Permissions', sectionKey: 'edit-tools-permissions' },
  { section: 'mcp-servers',       icon: '🔌', label: 'MCP Servers',         sectionKey: 'edit-mcp-servers' },
  { section: 'features',          icon: '✨', label: 'Features',            sectionKey: 'edit-features' },
  { section: 'system-prompt',     icon: '💭', label: 'System Prompt',       sectionKey: 'edit-system-prompt' },
  { section: 'isolation',         icon: '🛡️', label: 'Isolation',           sectionKey: 'edit-isolation' },
]

// Which section corresponds to each profile area
const AREA_SECTION = {
  model:         'model-tuning',
  permissions:   'tools-permissions',
  mcp:           'mcp-servers',
  features:      'features',
  system_prompt: 'system-prompt',
  isolation:     'isolation',
}

const editSectionItems = computed(() => {
  if (!editEntityId.value) return []

  const isNew = editEntityId.value === '__new__'
  const base = isNew
    ? (isTemplateEdit.value ? '/settings/template/__new__' : '/settings/profile/__new__')
    : (isTemplateEdit.value ? `/settings/template/${editEntityId.value}` : `/settings/profile/${editEntityId.value}`)

  if (isNew) {
    const areaQuery = route.query.area ? `?area=${route.query.area}` : ''
    return EDIT_SECTIONS.map(s => ({
      to: s.section === 'general' ? `${base}/general${areaQuery}` : `${base}/${s.section}`,
      icon: s.icon,
      label: s.label,
      sectionKey: s.sectionKey,
      disabled: s.section !== 'general',
    }))
  }

  // Profiles: only General + the section for their area are navigable
  let enabledSections = null
  if (isProfileEdit.value) {
    const area = profileStore.getProfile(editEntityId.value)?.area
    const areaSection = area ? AREA_SECTION[area] : null
    enabledSections = new Set(['general', ...(areaSection ? [areaSection] : [])])
  }

  return EDIT_SECTIONS.map(s => ({
    to: `${base}/${s.section}`,
    icon: s.icon,
    label: s.label,
    sectionKey: s.sectionKey,
    disabled: enabledSections !== null && !enabledSections.has(s.section),
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
  { to: '/settings/features',      icon: '✳️', label: 'Features',      sectionKey: 'features' },
  { to: '/settings/notifications', icon: '🎵', label: 'Notifications',  sectionKey: 'notifications' },
  { to: '/settings/read-aloud',    icon: '👄', label: 'Read Aloud',     sectionKey: 'read-aloud' },
  { to: '/settings/mcp-servers',   icon: '🌐', label: 'MCP Servers',    sectionKey: 'mcp-servers' },
]

const libraryItems = [
  { to: '/settings/templates', icon: '📄', label: 'Templates', sectionKey: 'templates' },
  { to: '/settings/profiles',  icon: '📋', label: 'Profiles',  sectionKey: 'profiles' },
  { to: '/settings/secrets',   icon: '🔑', label: 'Secrets',   sectionKey: 'secrets' },
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

// ── Ghost edit group (sidebar bounce prevention) ──────────────────────────
// When on Application/Library routes, keep the last-visited edit group visible
// but with all items disabled, so the sidebar layout stays stable.

const lastEditState = ref({ type: null, id: null })

watch(() => route.path, () => {
  if (isSessionEdit.value && sessionEntityId.value) {
    lastEditState.value = { type: 'session', id: sessionEntityId.value }
  } else if (isTemplateEdit.value && editEntityId.value) {
    lastEditState.value = { type: 'template', id: editEntityId.value }
  } else if (isProfileEdit.value && editEntityId.value) {
    lastEditState.value = { type: 'profile', id: editEntityId.value }
  }
}, { immediate: true })

const isOnNonEditRoute = computed(() =>
  route.path.startsWith('/settings/') && !isSessionEdit.value && !isEditMode.value
)

const ghostItems = computed(() => {
  if (!isOnNonEditRoute.value || !lastEditState.value.type) return []
  const { type, id } = lastEditState.value
  const prefix = type === 'session' ? 'session' : (type === 'template' ? 'template' : 'profile')
  const base = `/settings/${prefix}/${id}`
  return EDIT_SECTIONS.map(s => ({
    to: `${base}/${s.section}`,
    icon: s.icon,
    label: s.label,
    sectionKey: s.sectionKey,
    disabled: true,
  }))
})

const ghostGroupTitle = computed(() => {
  const t = lastEditState.value.type
  if (t === 'session')  return 'Editing Session'
  if (t === 'template') return 'Editing Template'
  if (t === 'profile')  return 'Editing Profile'
  return ''
})

const ghostGroupShort = computed(() => {
  const t = lastEditState.value.type
  if (t === 'session')  return 'Sess'
  if (t === 'template') return 'Tmpl'
  if (t === 'profile')  return 'Prof'
  return ''
})

const ghostEntityName = computed(() => {
  const { type, id } = lastEditState.value
  if (!id || id === '__new__') return ''
  if (type === 'session')  return sessionStore.getSession(id)?.name || ''
  if (type === 'template') return templateStore.getTemplate(id)?.name || ''
  if (type === 'profile')  return profileStore.getProfile(id)?.name || ''
  return ''
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
