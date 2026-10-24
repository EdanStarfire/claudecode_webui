<template>
  <input
    :type="numeric ? 'number' : 'text'"
    :min="numeric ? (min ?? 1) : undefined"
    :max="numeric ? max : undefined"
    :class="['form-control', 'form-control-sm', { 'font-monospace': monospace }]"
    :value="value ?? ''"
    :disabled="disabled"
    :placeholder="placeholder"
    @input="onInput"
  />
  <div v-if="hint" class="form-text small mt-0">{{ hint }}</div>
</template>

<script setup>
import { computed } from 'vue'

const props = defineProps({
  value: { type: [String, Number], default: '' },
  numeric: { type: Boolean, default: false },
  min: { type: Number, default: undefined },
  max: { type: Number, default: undefined },
  // 'ms' shows a human-readable duration hint under the input (issue #2287)
  unit: { type: String, default: '' },
  disabled: { type: Boolean, default: false },
  placeholder: { type: String, default: '' },
  monospace: { type: Boolean, default: false },
})

const emit = defineEmits(['update:value'])

// Numeric mode (issue #2287): emit an integer, or null when cleared/invalid.
// Range enforcement is left to the backend; min/max only drive the input's own attributes.
function onInput(event) {
  const raw = event.target.value
  if (!props.numeric) return emit('update:value', raw)
  const n = parseInt(raw, 10)
  emit('update:value', Number.isFinite(n) && n >= 1 ? n : null)
}

function formatMs(ms) {
  if (ms < 1000) return `${ms} ms`
  const units = [[3600000, 'h'], [60000, 'min'], [1000, 's']]
  const [size, label] = units.find(([s]) => ms >= s)
  return `${Number((ms / size).toFixed(2))} ${label}`
}

const hint = computed(() => {
  if (props.unit !== 'ms' || typeof props.value !== 'number') return ''
  return `= ${formatMs(props.value)}`
})
</script>
