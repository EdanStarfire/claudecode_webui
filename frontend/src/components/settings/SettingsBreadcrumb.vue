<template>
  <nav class="settings-breadcrumb" aria-label="Settings breadcrumb">
    <span class="bc-root">Settings</span>
    <template v-if="sectionLabel">
      <span class="bc-sep" aria-hidden="true">›</span>
      <span class="bc-section">{{ sectionLabel }}</span>
    </template>
  </nav>
</template>

<script setup>
import { computed } from 'vue'
import { useRoute } from 'vue-router'

const route = useRoute()

const LABELS = {
  '/settings/features':      'Features',
  '/settings/notifications': 'Notifications',
  '/settings/read-aloud':    'Read Aloud',
  '/settings/mcp-servers':   'MCP Servers',
  '/settings/templates':     'Templates',
  '/settings/profiles':      'Profiles',
  '/settings/secrets':       'Secrets',
}

const sectionLabel = computed(() => {
  if (LABELS[route.path]) return LABELS[route.path]
  if (route.path.startsWith('/settings/session/')) {
    const isNew = route.params.sessionId === '__new__'
    const s = route.params.section
    const prefix = isNew ? 'New Session' : 'Session'
    return s ? `${prefix} › ${humanize(s)}` : prefix
  }
  if (route.path.startsWith('/settings/template/')) {
    const s = route.params.section
    return s ? `Template › ${humanize(s)}` : 'Template'
  }
  if (route.path.startsWith('/settings/profile/')) {
    const s = route.params.section
    return s ? `Profile › ${humanize(s)}` : 'Profile'
  }
  if (route.path.startsWith('/settings/secret/')) {
    const s = route.params.section
    const isNew = route.params.secretName === '__new__'
    const prefix = isNew ? 'New Secret' : 'Secret'
    return s ? `${prefix} › ${humanize(s)}` : prefix
  }
  return ''
})

const SECTION_LABELS = {
  'tools-permissions': 'Tools & Permissions',
}

function humanize(slug) {
  if (SECTION_LABELS[slug]) return SECTION_LABELS[slug]
  return slug.replace(/-/g, ' ').replace(/\b\w/g, c => c.toUpperCase())
}
</script>

<style scoped>
.settings-breadcrumb {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 7px 16px;
  font-size: 12px;
  background: var(--mode-tint, var(--bs-tertiary-bg));
  border-bottom: 1px solid var(--mode-border, var(--bs-border-color));
  flex-shrink: 0;
}

.bc-root {
  color: var(--bs-secondary-color);
  font-weight: 500;
}

.bc-sep {
  color: var(--bs-tertiary-color);
}

.bc-section {
  color: var(--mode-fg, var(--bs-body-color));
  font-weight: 500;
}
</style>
