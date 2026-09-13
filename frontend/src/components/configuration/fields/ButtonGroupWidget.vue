<template>
  <div class="model-btn-group-wrapper">
    <div class="model-btn-group" :class="{ 'model-btn-group--default': isGroupDefault }">
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
      <button
        v-if="allowCustom"
        type="button"
        class="model-btn"
        :class="{ active: customMode }"
        :disabled="disabled"
        @click="enableCustomMode"
      >Custom...</button>
    </div>
    <input
      v-if="allowCustom && customMode"
      type="text"
      class="form-control form-control-sm mt-2"
      :value="value || ''"
      :disabled="disabled"
      placeholder="Enter model name..."
      @input="emitValue($event.target.value)"
      @blur="handleCustomBlur"
    />
  </div>
</template>

<script setup>
import { computed, ref, watch } from 'vue'

const props = defineProps({
  value: { type: [String, Array], default: null },
  disabled: { type: Boolean, default: false },
  options: { type: Array, default: () => [] },
  multiple: { type: Boolean, default: false },
  defaultValue: { default: null },
  allowCustom: { type: Boolean, default: false },
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

// Options may declare `aliases` — other raw values that should match this option
// (e.g. a legacy stored value that now shares a label with its canonical replacement).
function matchesOption(opt, val) {
  return opt.value === val || (opt.aliases && opt.aliases.includes(val))
}

function isActive(optValue) {
  const opt = props.options.find(o => o.value === optValue)
  if (props.multiple) {
    const arr = Array.isArray(props.value) ? props.value : []
    return arr.includes(optValue)
  }
  if (!noExplicitValue()) return opt ? matchesOption(opt, props.value) : props.value === optValue
  if (hasSchemaDefault()) return optValue === props.defaultValue
  // Legacy fallback: '' option acts as Default button
  return optValue === '' || optValue === null || optValue === undefined
}

// Dimmed style: button is active only because it's the schema default, not explicitly set
function isActiveDefault(optValue) {
  if (props.multiple) return false
  return noExplicitValue() && hasSchemaDefault() && optValue === props.defaultValue
}

const isGroupDefault = computed(() => {
  if (props.multiple) return props.value === null || props.value === undefined
  return noExplicitValue() && hasSchemaDefault()
})

const manualCustomMode = ref(false)
const lastEmitted = ref(props.value)

const matchesAnyOption = computed(() =>
  props.options.some(opt => matchesOption(opt, props.value))
)

const isCustomValue = computed(() =>
  props.allowCustom && !noExplicitValue() && !matchesAnyOption.value
)

const customMode = computed(() => manualCustomMode.value || isCustomValue.value)

// If the value changes to match a preset option from outside this widget (e.g. a
// "reset to inherited" action elsewhere in the settings editor), drop manual custom
// mode so the preset button reflects reality instead of leaving the custom input
// stuck open. Self-initiated changes (typing, blur-trim) are tracked via lastEmitted
// so mid-typing matches (e.g. typing "opus" into the custom box) don't close it.
watch(() => props.value, (newVal) => {
  if (newVal !== lastEmitted.value && matchesAnyOption.value) {
    manualCustomMode.value = false
  }
  lastEmitted.value = newVal
})

function emitValue(v) {
  lastEmitted.value = v
  emit('update:value', v)
}

function handleClick(optValue) {
  manualCustomMode.value = false
  if (props.multiple) {
    const arr = Array.isArray(props.value) ? [...props.value] : []
    const idx = arr.indexOf(optValue)
    if (idx >= 0) arr.splice(idx, 1)
    else arr.push(optValue)
    emitValue(arr)
  } else {
    emitValue(optValue)
  }
}

function enableCustomMode() {
  manualCustomMode.value = true
  if (matchesAnyOption.value) {
    emitValue('')
  }
}

function handleCustomBlur(e) {
  const trimmed = e.target.value.trim()
  if (trimmed !== e.target.value) emitValue(trimmed)
}
</script>
