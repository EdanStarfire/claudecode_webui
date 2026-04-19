<template>
  <div class="model-btn-group">
    <button
      v-for="opt in options"
      :key="opt.value"
      type="button"
      class="model-btn"
      :class="{ active: isActive(opt.value) }"
      :disabled="disabled"
      @click="handleClick(opt.value)"
    >{{ opt.label }}</button>
  </div>
</template>

<script setup>
const props = defineProps({
  value: { type: [String, Array], default: '' },
  disabled: { type: Boolean, default: false },
  options: { type: Array, default: () => [] },
  multiple: { type: Boolean, default: false },
})

const emit = defineEmits(['update:value'])

function isActive(optValue) {
  if (props.multiple) {
    const arr = Array.isArray(props.value) ? props.value : []
    return arr.includes(optValue)
  }
  // Single-select: treat '' as falsy for Default buttons
  return props.value === optValue || (!props.value && !optValue)
}

function handleClick(optValue) {
  if (props.multiple) {
    const arr = Array.isArray(props.value) ? [...props.value] : []
    const idx = arr.indexOf(optValue)
    if (idx >= 0) arr.splice(idx, 1)
    else arr.push(optValue)
    emit('update:value', arr)
  } else {
    emit('update:value', optValue)
  }
}
</script>
