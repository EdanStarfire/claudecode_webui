<template>
  <input
    type="text"
    :class="['form-control', 'form-control-sm', { 'font-monospace': monospace }]"
    :value="value || ''"
    :disabled="disabled"
    :placeholder="placeholder"
    @input="$emit('update:value', $event.target.value)"
  />
</template>

<script setup>
defineProps({
  value: { type: String, default: '' },
  disabled: { type: Boolean, default: false },
  placeholder: { type: String, default: '' },
  monospace: { type: Boolean, default: false },
})

defineEmits(['update:value'])
</script>
