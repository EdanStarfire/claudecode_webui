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
  background: #1e3a5f;
  color: #58a6ff;
  border: 1px solid #1f6feb44;
}

.source-marker--t {
  background: #2d1f0a;
  color: #d29922;
  border: 1px solid #d2992244;
}

.source-marker--p {
  background: #0a2a14;
  color: #3fb950;
  border: 1px solid #3fb95044;
}

.source-marker--empty {
  background: #1e2633;
  color: #4a5568;
  border: 1px solid #2d3748;
}
</style>
