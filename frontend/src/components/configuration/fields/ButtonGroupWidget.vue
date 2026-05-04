<template>
  <div class="model-btn-group">
    <button
      v-for="opt in options"
      :key="opt.value"
      type="button"
      class="model-btn"
      :class="{
        active: isActive(opt.value),
        'active-default': isActiveDefault(opt.value),
      }"
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
  defaultValue: { type: String, default: null },
})

const emit = defineEmits(['update:value'])

function isActive(optValue) {
  if (props.multiple) {
    const arr = Array.isArray(props.value) ? props.value : []
    return arr.includes(optValue)
  }
  if (props.value) return props.value === optValue
  // No explicit value: highlight the schema default if one is defined
  if (props.defaultValue) return optValue === props.defaultValue
  // Legacy: '' option as Default button
  return !optValue
}

// True when the button is active only because it's the schema default (not explicitly set)
function isActiveDefault(optValue) {
  if (props.multiple) return false
  return !props.value && !!props.defaultValue && optValue === props.defaultValue
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
