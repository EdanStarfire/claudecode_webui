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
    const s = route.params.section
    return s ? `Session › ${humanize(s)}` : 'Session'
  }
  if (route.path.startsWith('/settings/template/')) {
    const s = route.params.section
    return s ? `Template › ${humanize(s)}` : 'Template'
  }
  if (route.path.startsWith('/settings/profile/')) {
    const s = route.params.section
    return s ? `Profile › ${humanize(s)}` : 'Profile'
  }
  return ''
})

function humanize(slug) {
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
  background: var(--mode-tint, rgba(15, 23, 42, 0.5));
  border-bottom: 1px solid var(--mode-border, #1e293b);
  flex-shrink: 0;
}

.bc-root {
  color: #475569;
  font-weight: 500;
}

.bc-sep {
  color: #334155;
}

.bc-section {
  color: var(--mode-fg, #94a3b8);
  font-weight: 500;
}
</style>
