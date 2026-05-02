<template>
  <component
    :is="to ? 'button' : 'span'"
    class="settings-toolbar-chip"
    :class="[`chip-${type.toLowerCase()}`, { 'chip-clickable': !!to }]"
    :title="tooltip"
    :type="to ? 'button' : undefined"
    @click="handleClick"
  >
    <span class="chip-badge">{{ type }}:</span>
    <span class="chip-label">{{ label }}</span>
  </component>
</template>

<script setup>
import { computed } from 'vue'
import { useRouter } from 'vue-router'

const props = defineProps({
  type:  { type: String, required: true },
  label: { type: String, required: true },
  to:    { type: String, default: '' },
})

const router = useRouter()

const tooltip = computed(() => {
  if (props.type === 'T') return `Template: ${props.label}`
  if (props.type === 'P') return `Profile: ${props.label}`
  return props.label
})

function handleClick() {
  if (props.to) router.push(props.to)
}
</script>

<style scoped>
.settings-toolbar-chip {
  display: inline-flex;
  align-items: center;
  gap: 3px;
  padding: 2px 8px;
  border-radius: 12px;
  font-size: 11px;
  font-weight: 500;
  border: 1px solid transparent;
  cursor: default;
  user-select: none;
  background: none;
}

.chip-clickable {
  cursor: pointer;
  transition: opacity 0.12s;
}

.chip-clickable:hover {
  opacity: 0.8;
}

.chip-t {
  background: rgba(31, 111, 235, 0.1);
  border-color: rgba(31, 111, 235, 0.27);
  color: #58a6ff;
}

.chip-p {
  background: rgba(63, 185, 80, 0.1);
  border-color: rgba(63, 185, 80, 0.27);
  color: #3fb950;
}

.chip-badge {
  font-weight: 700;
  opacity: 0.75;
}
</style>
