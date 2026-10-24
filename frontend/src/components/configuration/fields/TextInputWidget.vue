<template>
  <input
    :type="numeric ? 'number' : 'text'"
    :min="numeric ? 1 : undefined"
    :class="['form-control', 'form-control-sm', { 'font-monospace': monospace }]"
    :value="value ?? ''"
    :disabled="disabled"
    :placeholder="placeholder"
    @input="onInput"
  />
</template>

<script setup>
const props = defineProps({
  value: { type: [String, Number], default: '' },
  numeric: { type: Boolean, default: false },
  disabled: { type: Boolean, default: false },
  placeholder: { type: String, default: '' },
  monospace: { type: Boolean, default: false },
})

const emit = defineEmits(['update:value'])

// Numeric mode (issue #2287): emit an integer, or null when cleared/invalid.
function onInput(event) {
  const raw = event.target.value
  if (!props.numeric) return emit('update:value', raw)
  const n = parseInt(raw, 10)
  emit('update:value', Number.isFinite(n) && n >= 1 ? n : null)
}
</script>
