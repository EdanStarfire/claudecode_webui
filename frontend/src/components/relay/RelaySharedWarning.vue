<template>
  <div v-if="entries.length" class="shared-warning" role="note" data-testid="relay-shared-warning">
    <strong>This secret is also exposed to the listed {{ plural }}.</strong>
    Those sessions can call Slack directly with it, bypassing the relay's channel policy, footer,
    trigger refusal and audit log.
    <ul>
      <li v-for="e in entries" :key="e.label">
        <span class="shared-kind">{{ e.label }}:</span> {{ e.names.join(', ') }}
      </li>
    </ul>
  </div>
</template>

<script setup>
import { computed } from 'vue'

// `shared`: { sessions: [name], templates: [name], profiles: [name], mcp_servers: [name] } (names only)
const props = defineProps({ shared: { type: Object, default: null } })

const LABELS = { sessions: 'Sessions', templates: 'Templates', profiles: 'Profiles', mcp_servers: 'MCP servers' }
const entries = computed(() =>
  Object.entries(LABELS)
    .map(([key, label]) => ({ label, names: props.shared?.[key] || [] }))
    .filter(e => e.names.length))
const plural = computed(() => entries.value.map(e => e.label.toLowerCase()).join(' / '))
</script>

<style scoped>
.shared-warning {
  font-size: 12px; line-height: 1.5; padding: 8px 12px; border-radius: 6px;
  color: #d29922; background: rgba(210, 153, 34, 0.1); border: 1px solid rgba(210, 153, 34, 0.4);
}
.shared-warning ul { margin: 6px 0 0; padding-left: 18px; }
.shared-kind { font-weight: 600; }
</style>
