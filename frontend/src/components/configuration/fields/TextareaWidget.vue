<template>
  <textarea
    class="form-control form-control-sm"
    :value="value || ''"
    :disabled="disabled"
    :placeholder="placeholder"
    :rows="rows"
    @input="$emit('update:value', $event.target.value)"
  ></textarea>
</template>

<script setup>
defineProps({
  value: { type: String, default: '' },
  disabled: { type: Boolean, default: false },
  placeholder: { type: String, default: '' },
  rows: { type: Number, default: 3 },
})

defineEmits(['update:value'])
</script>
