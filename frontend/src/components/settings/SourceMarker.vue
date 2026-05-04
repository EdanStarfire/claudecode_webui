<template>
  <span
    class="source-marker"
    :class="`source-marker--${kind.toLowerCase()}`"
    :title="tooltip"
    :aria-label="tooltip"
  >{{ glyph }}</span>
</template>

<script setup>
import { computed } from 'vue'

const props = defineProps({
  kind: {
    type: String,
    required: true,
    validator: v => ['S', 'T', 'P', 'EMPTY'].includes(v),
  },
  templateName: {
    type: String,
    default: null,
  },
  profileName: {
    type: String,
    default: null,
  },
})

const glyph = computed(() => {
  switch (props.kind) {
    case 'S': return 'S'
    case 'T': return 'T'
    case 'P': return 'P'
    case 'EMPTY': return '∅'
    default: return '?'
  }
})

const tooltip = computed(() => {
  switch (props.kind) {
    case 'S': return 'Set at this level'
    case 'T': return props.templateName ? `Inherited from template: ${props.templateName}` : 'Inherited from template'
    case 'P': return props.profileName ? `Inherited from profile: ${props.profileName}` : 'Inherited from profile'
    case 'EMPTY': return 'No upstream value — set here to apply'
    default: return ''
  }
})
</script>

<style scoped>
.source-marker {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 16px;
  height: 16px;
  border-radius: 3px;
  font-size: 9px;
  font-weight: 700;
  line-height: 1;
  flex-shrink: 0;
  cursor: default;
  user-select: none;
}

.source-marker--s {
  background: var(--s-tint, rgba(88, 166, 255, 0.12));
  color: var(--s-fg, #58a6ff);
  border: 1px solid var(--s-border, rgba(88, 166, 255, 0.30));
}

.source-marker--t {
  background: var(--t-tint, rgba(210, 153, 34, 0.12));
  color: var(--t-fg, #d29922);
  border: 1px solid var(--t-border, rgba(210, 153, 34, 0.30));
}

.source-marker--p {
  background: var(--p-tint, rgba(63, 185, 80, 0.12));
  color: var(--p-fg, #3fb950);
  border: 1px solid var(--p-border, rgba(63, 185, 80, 0.30));
}

.source-marker--empty {
  background: var(--bs-secondary-bg, rgba(0,0,0,0.06));
  color: var(--bs-tertiary-color, #6b7280);
  border: 1px solid var(--bs-border-color, rgba(0,0,0,0.12));
}
</style>
