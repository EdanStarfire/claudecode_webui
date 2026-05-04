<template>
  <div
    class="form-check form-switch mb-0"
    :class="{ 'toggle-default': isShowingDefault }"
  >
    <input
      class="form-check-input"
      type="checkbox"
      :id="inputId"
      :checked="displayValue"
      :disabled="disabled"
      @change="$emit('update:value', $event.target.checked)"
    />
    <label v-if="label" class="form-check-label" :for="inputId" style="text-transform: none; letter-spacing: normal;">
      {{ label }}
    </label>
  </div>
</template>

<script setup>
import { computed } from 'vue'

const props = defineProps({
  value: { default: null },
  disabled: { type: Boolean, default: false },
  label: { type: String, default: '' },
  defaultValue: { default: null },
})

defineEmits(['update:value'])

const inputId = computed(() => `toggle-${Math.random().toString(36).slice(2, 9)}`)

const isShowingDefault = computed(() =>
  (props.value === null || props.value === undefined) && props.defaultValue !== null
)

const displayValue = computed(() => {
  if (props.value !== null && props.value !== undefined) return props.value
  return props.defaultValue ?? false
})
</script>

<style scoped>
.toggle-default .form-check-input {
  opacity: 0.45;
}
</style>
