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
  value: { type: [String, Array], default: null },
  disabled: { type: Boolean, default: false },
  options: { type: Array, default: () => [] },
  multiple: { type: Boolean, default: false },
  defaultValue: { default: null },
})

const emit = defineEmits(['update:value'])

// True when no explicit value has been set at this level
function noExplicitValue() {
  return props.value === null || props.value === undefined || props.value === ''
}

// True when a schema default is defined (including empty string)
function hasSchemaDefault() {
  return props.defaultValue !== null && props.defaultValue !== undefined
}

function isActive(optValue) {
  if (props.multiple) {
    const arr = Array.isArray(props.value) ? props.value : []
    return arr.includes(optValue)
  }
  if (!noExplicitValue()) return props.value === optValue
  if (hasSchemaDefault()) return optValue === props.defaultValue
  // Legacy fallback: '' option acts as Default button
  return optValue === '' || optValue === null || optValue === undefined
}

// Dimmed style: button is active only because it's the schema default, not explicitly set
function isActiveDefault(optValue) {
  if (props.multiple) return false
  return noExplicitValue() && hasSchemaDefault() && optValue === props.defaultValue
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
